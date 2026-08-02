"""Prompt assembly for the single-call recommendation engine.

Separated from the engine itself because this is where the judgement lives:
what the model is shown, in what order, and how much of it. The engine handles
calling, parsing and persistence; this module decides what the call is about.

Two things changed here in the 2026-07-29 spec, and both were correctness
fixes rather than tuning:

1. Responses are sent WHOLE (point 1). They were capped at 2,000 characters
   against a 3-6k typical length, cut from the front, so every recommendation
   ever written reasoned about roughly the first third of each answer. In
   ranked "best X for Y" answers the brand and competitor mentions sit
   mid-to-late, which is precisely what the cut removed.

2. The model is shown gap CLUSTERS grouped by the client's own service lines
   and ordered by their commercial tier (points 3, 4, 6), not a flat ranked
   list. A flat list ordered by opportunity score selected for winnable over
   important, which is how a criminal defence firm got a brief on condominium
   paperwork. Tier ordering is the only part of this the engine cannot derive
   for itself: it comes from the client knowledge base.
"""
import structlog

from app.config import settings
from app.generation.selection import ServiceLineCluster
from app.generation.service_tiers import BONUS, CORE, SECONDARY, UNTIERED

logger = structlog.get_logger()

# Rough chars-per-token for budgeting. Deliberately conservative (real English
# prose runs ~4); over-estimating input size costs a little trimming, while
# under-estimating costs the whole call to a context-length error.
_CHARS_PER_TOKEN = 3.5

# Never trim a response below this. Past it a block stops being evidence and
# the call would be better off dropping the response honestly.
_MIN_EXCERPT_CHARS = 1_500

_TIER_LABEL = {
    CORE: "CORE (the client's primary revenue)",
    SECONDARY: "SECONDARY",
    BONUS: "BONUS (quick win, low commercial weight)",
    UNTIERED: "UNTIERED (no commercial tier on record)",
}

SYSTEM_INSTRUCTIONS = """\
You are a GEO (Generative Engine Optimization) strategist. You are given the
results of one monitoring run for a client: the AI queries that were asked, how
four AI engines answered them, and an analysis of each answer.

Every response below represents a CITATION GAP. A gap means the client is
absent from the answer, OR present but weakly: named without substance, cited
neutrally rather than recommended, buried, or described negatively. Weak and
negative citations are frequently the more urgent fix, because the client is
already visible and being undersold. Treat them with the same seriousness as
absence.

The responses are grouped into clusters by the client's SERVICE LINE, and the
clusters are ordered by the client's own commercial tier: CORE service lines
earn the client's revenue, SECONDARY matter, BONUS are quick wins with little
commercial weight.

## How to decide what to write
1. Work through the clusters in the order given. A gap in a CORE service line
   outranks a gap in a BONUS one even when the bonus gap is objectively easier
   to close and more obviously winnable. Ease of closing is not a reason to
   promote work, and it is the single most common way this goes wrong.
2. Within a cluster, identify the DISTINCT gaps. Several queries failing for
   the same missing content is ONE gap, and one recommendation should fix it.
   Several queries failing for genuinely different reasons are different gaps
   and get one recommendation each.
3. Write one recommendation per distinct gap. This is a target, not a cap: if a
   service line has four genuinely distinct gaps, write four. Do NOT compress
   the run into a fixed number of recommendations, and do NOT merge gaps across
   different service lines just to shorten the list.
4. Do not drop a service line because it is hard. A CORE service line where the
   client is invisible against large established competitors is the most
   valuable work on the list, even when the honest recommendation is slow,
   effortful, or authority-building rather than a single page.
5. BONUS-tier gaps are worth keeping. They are cheap, they move the citation
   rate early, and they give the client a visible result while core work lands.
   Include them; just never let them be the headline of the run.
6. Check the live site inventory before recommending anything. If a page,
   schema type, or llms.txt section already exists, do NOT recommend creating
   it. Recommend improving it only if you can say specifically what is missing.
7. Check the list of existing recommendations. Do not repeat work that has
   already been recommended, approved, or implemented.

## What to produce
You decide the mix and the total. There is no requirement to produce any
particular type, or any of a type at all.

Quality bar: a recommendation that could have been written without reading
these responses is worthless. Cite the actual queries and what the engines
actually said. Every recommendation must name the service line it serves.

Available types:
- "content_brief": a new or rewritten page targeting queries where the client
  is absent or weak.
- "schema_markup": structured data to add or fix, when its absence is what
  keeps the client out of answers.
- "llms_txt": additions or changes to the client's llms.txt file.
- "authority_building": OFF-page work — earned mentions, expert contributions,
  digital PR, presence on the review and comparison sources these engines cite.

## Output
Return ONLY valid JSON, no markdown fences, in exactly this shape:
{{
  "recommendations": [
    {{
      "type": "content_brief",
      "title": "short specific title",
      "service_line": "the service line cluster this addresses",
      "priority": "high",
      "effort": "M",
      "target_query": "the primary query this addresses, or null",
      "addresses_queries": ["the queries this helps with"],
      "reasoning": "why this matters, referencing what you saw in the responses",
      "content": {{}}
    }}
  ],
  "summary": "one paragraph on what this run says overall and why you chose these"
}}

priority must be one of: high, medium, low
effort must be one of: S, M, L (S = small/quick change, M = moderate effort,
L = large/multi-week effort)

Per-type "content" shapes:
- content_brief: {{"target_query": "...", "content_type": "...", \
"headline_suggestion": "...", "key_questions": [...], "eeat_signals": [...], \
"competitor_analysis": "what cited competitors do that this must match or \
exceed", "recommended_word_count": 1500, "recommended_structure": ["Intro", \
"Section 1"], "schema_types": ["Article", "FAQPage"]}}
- schema_markup: {{"recommended_schemas": [{{"schema_type": "Organization", \
"purpose": "why this helps citation", "example_jsonld": {{}}, \
"implementation_notes": "where and how to add it"}}]}}
- llms_txt: {{"new_sections": [{{"section_title": "...", "content": "the text \
to add", "addresses_queries": [...]}}], "modifications": [{{"existing_section": \
"...", "suggested_change": "..."}}]}}
- authority_building: {{"authority_actions": [{{"action": "...", \
"target_sources": [...], "addresses_queries": [...], "rationale": "..."}}]}}

If this run genuinely warrants no new work, return {{"recommendations": [], \
"summary": "why nothing is needed"}}."""


def _response_block(index: int, item, excerpt_chars: int) -> str:
    """One gap response, rendered for the prompt."""
    from app.generation.selection import gap_reason

    analysis = item.analysis
    competitors = ", ".join(
        f"{c.get('brand', '?')} ({c.get('prominence', '?')})"
        for c in (analysis.competitors_cited or [])
    ) or "none"
    gaps = ", ".join(analysis.content_gaps or []) or "none identified"
    full = item.response.raw_response or ""
    # excerpt_chars <= 0 means no limit, which is the normal path.
    if excerpt_chars > 0 and len(full) > excerpt_chars:
        body = full[:excerpt_chars] + "\n[response truncated to fit the context window]"
    else:
        body = full
    return (
        f"[{index}] score {item.score:.1f} | engine: {item.response.platform.value} "
        f"| gap: {gap_reason(analysis) or 'none'}\n"
        f"query: {item.prompt.text}\n"
        f"buyer intent: {item.prompt.category or 'unclassified'}\n"
        f"client cited: {analysis.client_cited} "
        f"(prominence: {analysis.client_prominence.value}, "
        f"sentiment: {analysis.client_sentiment.value}, "
        f"type: {analysis.citation_type.value})\n"
        f"characterization: {analysis.client_characterization or 'none'}\n"
        f"competitors cited: {competitors}\n"
        f"content gaps: {gaps}\n"
        f"full response:\n{body}\n"
    )


def _cluster_block(
    cluster: ServiceLineCluster, start_index: int, excerpt_chars: int
) -> str:
    """One service-line cluster: its tier, its breadth, then its responses."""
    header = (
        f"\n### Service line: {cluster.service_line}\n"
        f"Commercial tier: {_TIER_LABEL.get(cluster.tier, cluster.tier)}\n"
        f"Breadth: this gap appears across {cluster.prompt_count} distinct "
        f"queries ({cluster.response_count} responses, "
        f"{cluster.uncited_count} with the client entirely absent).\n"
    )
    blocks = [
        _response_block(start_index + offset, item, excerpt_chars)
        for offset, item in enumerate(cluster.items)
    ]
    return header + "\n".join(blocks)


def _estimate_tokens(text: str) -> int:
    return int(len(text) / _CHARS_PER_TOKEN)


def _fit_excerpt_cap(lengths: list[int], target_chars: int) -> int:
    """Largest per-response character cap whose total fits ``target_chars``.

    Water-filling rather than the old halve-everything loop: it trims only the
    responses long enough to be over the cap and leaves short ones whole, so
    the evidence lost is concentrated in the few outliers instead of spread
    across every response in the run. Returns 0 when nothing needs trimming.
    """
    if not lengths or sum(lengths) <= target_chars:
        return 0
    low, high = _MIN_EXCERPT_CHARS, max(lengths)
    best = _MIN_EXCERPT_CHARS
    while low <= high:
        mid = (low + high) // 2
        if sum(min(length, mid) for length in lengths) <= target_chars:
            best = mid
            low = mid + 1
        else:
            high = mid - 1
    return best


def build_prompt(
    *,
    client_name: str,
    client_website: str | None,
    industry_context: str,
    brand_profile: str,
    target_audience: str,
    differentiators: str,
    competitor_names: list[str],
    clusters: list[ServiceLineCluster],
    site_inventory: str,
    existing_recommendations: list[str],
    service_tier_summary: str,
    budget_tokens: int,
    custom_instructions: str | None = None,
) -> tuple[str, int, int]:
    """Assemble the single call's prompt.

    Returns (prompt, responses_used, excerpt_cap_applied). ``excerpt_cap_applied``
    is 0 on the normal path, meaning every response was sent whole.

    Fitting is a genuine last resort now, not a routine step. Sending whole
    responses is the point of this design, and the budget is sized so a normal
    run never approaches it. If a run does overflow, responses are capped by
    water-filling (longest first) and only then, if that still does not fit, are
    the weakest-tier responses dropped — both loudly, because either one means
    the model is reasoning about less than the run actually found.
    """
    header = (custom_instructions or SYSTEM_INSTRUCTIONS).format()
    context = (
        f"\n\n## Client\n"
        f"Name: {client_name}\n"
        f"Website: {client_website or 'Not provided'}\n"
        f"Industry: {industry_context}\n"
        f"Brand profile: {brand_profile}\n"
        f"Target audience: {target_audience}\n"
        f"Differentiators: {differentiators}\n"
        f"Known competitors: {', '.join(competitor_names) or 'none on record'}\n"
    )
    tier_block = (
        f"\n## Commercial priority of this client's service lines\n"
        f"{service_tier_summary}\n"
    )
    site_block = f"\n## What already exists on the client's site\n{site_inventory}\n"
    existing_block = "\n## Already recommended (do not repeat)\n" + (
        "\n".join(f"- {t}" for t in existing_recommendations[:60])
        if existing_recommendations
        else "Nothing recommended for this client yet.\n"
    )

    used = list(clusters)
    fixed = header + context + tier_block + site_block + existing_block
    fixed_tokens = _estimate_tokens(fixed)
    target_chars = max(0, (budget_tokens - fixed_tokens)) * _CHARS_PER_TOKEN

    lengths = [
        len(item.response.raw_response or "")
        for cluster in used
        for item in cluster.items
    ]
    # Per-response metadata (query, competitors, gaps, headers) costs roughly
    # 500 chars a block; charge for it so the cap is computed against the space
    # the responses will actually have.
    overhead = 500 * len(lengths) + 200 * len(used)
    excerpt_cap = _fit_excerpt_cap(lengths, int(target_chars) - overhead)

    configured = settings.recommendation_response_max_chars
    if configured > 0:
        # An explicit cost control is still honoured, but it is not the default
        # and it reintroduces the bug this redesign fixed.
        excerpt_cap = configured if excerpt_cap == 0 else min(excerpt_cap, configured)
        logger.warning(
            "recommendation_response_cap_configured",
            cap_chars=configured,
            hint="RECOMMENDATION_RESPONSE_MAX_CHARS is set; responses are being "
                 "truncated. 0 sends them whole.",
        )

    def _render(cluster_list: list[ServiceLineCluster], cap: int) -> str:
        parts, index = [], 1
        for cluster in cluster_list:
            parts.append(_cluster_block(cluster, index, cap))
            index += len(cluster.items)
        return "\n".join(parts)

    body = _render(used, excerpt_cap)

    # Last resort: still over budget with responses capped at the floor. Drop
    # whole clusters from the back, which is the lowest tier and the narrowest
    # breadth, so what is lost is what the client said matters least.
    dropped_clusters = 0
    while fixed_tokens + _estimate_tokens(body) > budget_tokens and len(used) > 1:
        used = used[:-1]
        dropped_clusters += 1
        body = _render(used, excerpt_cap)

    responses_used = sum(len(c.items) for c in used)
    if excerpt_cap > 0:
        logger.warning(
            "recommendation_responses_truncated",
            excerpt_cap_chars=excerpt_cap,
            budget_tokens=budget_tokens,
            responses=responses_used,
            hint="run exceeded the input budget; responses were capped",
        )
    if dropped_clusters:
        logger.error(
            "recommendation_clusters_dropped",
            dropped=dropped_clusters,
            kept=len(used),
            budget_tokens=budget_tokens,
            hint="input budget exhausted; lowest-tier service lines were "
                 "excluded from this run's recommendations",
        )

    prompt = (
        f"{fixed}\n"
        f"## Monitoring results: {responses_used} responses with a citation gap, "
        f"in {len(used)} service-line clusters, highest commercial priority "
        f"first\n{body}"
    )
    return prompt, responses_used, excerpt_cap


def render_tier_summary(clusters: list[ServiceLineCluster]) -> str:
    """Plain-language statement of the tiering, for the prompt.

    Stated explicitly rather than left implicit in the cluster order, because
    the ordering alone does not tell the model that the order is commercial
    rather than incidental.
    """
    if not clusters:
        return "No service lines on record for this client."
    by_tier: dict[str, list[str]] = {}
    for cluster in clusters:
        by_tier.setdefault(cluster.tier, []).append(cluster.service_line)
    lines = []
    for tier in (CORE, SECONDARY, BONUS, UNTIERED):
        if tier in by_tier:
            lines.append(f"- {_TIER_LABEL[tier]}: {', '.join(by_tier[tier])}")
    if set(by_tier) == {UNTIERED}:
        lines.append(
            "No commercial tiers are on record, so these clusters are ordered by "
            "how many queries each gap spans. Weigh them on the evidence."
        )
    return "\n".join(lines)
