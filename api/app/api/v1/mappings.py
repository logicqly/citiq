"""
Translation tables between the engine's internal vocabulary and the external
/v1 contract. The external pipeline depends on these exact names.
"""
from app.models.recommendation import RecommendationType
from app.models.response import Platform
from app.models.run import RunStatus

# ── Engines ───────────────────────────────────────────────────────────────────
# Internal Platform enum → external engine name used everywhere in /v1 payloads.
# WIRED ENGINES: chatgpt (openai), perplexity, gemini, claude (anthropic).
PLATFORM_TO_ENGINE: dict[Platform, str] = {
    Platform.openai: "chatgpt",
    Platform.anthropic: "claude",
    Platform.gemini: "gemini",
    Platform.perplexity: "perplexity",
}
ENGINE_TO_PLATFORM: dict[str, Platform] = {v: k for k, v in PLATFORM_TO_ENGINE.items()}


def engine_name(platform: Platform | str) -> str:
    """External engine name for an internal Platform (accepts enum or its value)."""
    if isinstance(platform, str):
        platform = Platform(platform)
    return PLATFORM_TO_ENGINE[platform]


# ── Audit status ──────────────────────────────────────────────────────────────
# Internal RunStatus → external audit status. RunStatus.partial maps directly;
# the failed_engines fallback below remains for runs finalized before the
# persisted partial state existed (a completed run with failures → "partial").
_RUN_STATUS_TO_AUDIT: dict[RunStatus, str] = {
    RunStatus.pending: "queued",
    RunStatus.running: "running",
    # Staged run awaiting its analysis click — the audit is genuinely mid-
    # flight, so it reports as running. (/v1-triggered audits always run the
    # full pipeline; this only surfaces if an admin stages a run for a client
    # that is also polled via /v1.)
    RunStatus.responses_ready: "running",
    RunStatus.completed: "complete",
    RunStatus.partial: "partial",
    RunStatus.failed: "failed",
    # The external contract has no "cancelled" vocabulary; an admin-cancelled
    # audit reports as failed (terminal, no trustworthy scores) to callers.
    RunStatus.cancelled: "failed",
}


def audit_status(run_status: RunStatus, failed_engines: list[str]) -> str:
    """Map a RunStatus to the external audit status vocabulary."""
    base = _RUN_STATUS_TO_AUDIT.get(run_status, "queued")
    if base == "complete" and failed_engines:
        return "partial"
    return base


# ── Recommendation buckets ────────────────────────────────────────────────────
# Internal RecommendationType → external bucket. The external contract's four
# buckets are content_creation | content_optimization | technical |
# authority_building; content_optimization has no internal producer (the
# on_page_optimization type was removed as dead code — no generator ever
# emitted it), so it never appears in /v1 payloads.
_TYPE_TO_BUCKET: dict[str, str] = {
    RecommendationType.content_brief.value: "content_creation",
    RecommendationType.schema_markup.value: "technical",
    RecommendationType.llms_txt.value: "technical",
    RecommendationType.authority_building.value: "authority_building",
}


def recommendation_bucket(rec_type: str) -> str:
    """External bucket for a recommendation type value (defaults to technical)."""
    return _TYPE_TO_BUCKET.get(rec_type, "technical")
