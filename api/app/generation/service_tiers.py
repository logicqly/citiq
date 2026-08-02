"""Commercial tiering of a client's service lines.

Client spec (2026-07-29), point 6: recommendation clusters are ordered by
service tier first (Core, then Secondary, then Bonus) and by breadth second.
The tiers are supplied by Origo on the client knowledge base, because which
practice areas actually earn the money is private commercial information the
engine has no way to infer.

Parsing is deliberately liberal. This field is hand-populated per client, so
both natural shapes are accepted:

    {"core": ["criminal defence"], "secondary": ["divorce"]}   tier -> lines
    {"criminal defence": "core", "divorce": "secondary"}       line -> tier

A bare string is accepted wherever a list is expected. Unknown tier names are
ignored rather than raising: a typo in the KB must not take a run down, and
``unmatched_tier_entries`` exists so the mistake is reported instead of
silently changing the output.
"""
import structlog

logger = structlog.get_logger()

CORE = "core"
SECONDARY = "secondary"
BONUS = "bonus"
UNTIERED = "untiered"

# Ordering rank. Untiered sorts last: an unassigned service line has no stated
# commercial value, so it must not outrank one Origo explicitly called Core.
_RANK = {CORE: 0, SECONDARY: 1, BONUS: 2, UNTIERED: 3}

# Spelling variants accepted for each canonical tier.
_TIER_ALIASES = {
    CORE: {"core", "primary", "tier_1", "tier1", "tier 1", "1"},
    SECONDARY: {"secondary", "tier_2", "tier2", "tier 2", "2"},
    BONUS: {"bonus", "tertiary", "tier_3", "tier3", "tier 3", "3"},
}
_ALIAS_TO_TIER = {
    alias: tier for tier, aliases in _TIER_ALIASES.items() for alias in aliases
}


def normalize_service_line(value: object) -> str:
    """Canonical key for matching a service line across prompts and the KB.

    Lowercased with collapsed whitespace, so "Criminal Defence" on a prompt
    matches "criminal  defence" in the KB. Matching stays exact beyond that:
    guessing that "defense" means "defence" would silently retier a client's
    revenue, which is worse than reporting the mismatch.
    """
    if not isinstance(value, str):
        return ""
    return " ".join(value.strip().lower().split())


def _canonical_tier(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return _ALIAS_TO_TIER.get(" ".join(value.strip().lower().split()))


def _as_list(value: object) -> list[object]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return []


def parse_service_tiers(raw: object) -> dict[str, str]:
    """Normalized service line -> canonical tier name.

    Accepts either orientation (see module docstring). When both orientations
    appear in one object, the tier -> lines form wins for that key, because it
    is the shape the admin UI writes.
    """
    if not isinstance(raw, dict) or not raw:
        return {}

    mapping: dict[str, str] = {}
    for key, value in raw.items():
        tier_from_key = _canonical_tier(key)
        if tier_from_key is not None:
            # {"core": ["criminal defence", ...]}
            for line in _as_list(value):
                normalized = normalize_service_line(line)
                if normalized:
                    mapping[normalized] = tier_from_key
            continue

        tier_from_value = _canonical_tier(value)
        if tier_from_value is not None:
            # {"criminal defence": "core"}
            normalized = normalize_service_line(key)
            if normalized:
                mapping.setdefault(normalized, tier_from_value)

    return mapping


def tier_for(service_line: str, tier_map: dict[str, str]) -> str:
    """The tier a service line belongs to, or UNTIERED when unlisted."""
    return tier_map.get(normalize_service_line(service_line), UNTIERED)


def tier_rank(tier: str) -> int:
    """Sort rank for a tier; unknown tiers sort with untiered."""
    return _RANK.get(tier, _RANK[UNTIERED])


def unmatched_tier_entries(
    tier_map: dict[str, str], present_service_lines: set[str]
) -> list[str]:
    """Tiered service lines that no prompt in this run actually uses.

    Almost always a spelling drift between the KB and the prompt rows ("legal
    advice" vs "general legal advice"). The effect is invisible in the output —
    the run still produces recommendations, just ordered as though the tier was
    never set — so it is surfaced rather than left to be noticed later.
    """
    present = {normalize_service_line(s) for s in present_service_lines}
    return sorted(line for line in tier_map if line not in present)
