"""
Shared helper for the S | M | L implementation-effort tag emitted by generators.

Every recommendation carries an ``effort`` (small / medium / large). The
generators ask the LLM to return it in their JSON; this normaliser guarantees a
valid value, defaulting to "M" when the field is missing or malformed so the
external contract (recommendations[].effort ∈ S | M | L) always holds.
"""
VALID_EFFORTS = ("S", "M", "L")
DEFAULT_EFFORT = "M"

# Reusable snippet appended to every generator's JSON schema so the LLM returns
# the field. Keep the wording identical across generators.
EFFORT_PROMPT_LINE = (
    'effort must be one of: S, M, L '
    "(S = small/quick change, M = moderate effort, L = large/multi-week effort)"
)


def parse_effort(content: dict) -> str:
    """Return a valid S|M|L effort from an LLM's returned JSON.

    Defaults to "M" when the ``effort`` field is absent or not one of the three
    allowed values (case-insensitive).
    """
    raw = content.get("effort") if isinstance(content, dict) else None
    if isinstance(raw, str):
        val = raw.strip().upper()
        if val in VALID_EFFORTS:
            return val
    return DEFAULT_EFFORT
