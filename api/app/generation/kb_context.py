"""
Shared helpers for rendering the client knowledge base into generator prompts.

Why this exists: with no KB content the generators used to interpolate the
literal strings "Not provided" (kb row missing) or "{}"/"None" (blank kb row —
one is auto-created with every client) into their prompts and generate anyway.
The LLM filled those gaps with plausible generic content, which shipped —
the root cause of weeks of generic briefs. Generation now refuses up front
(``kb_has_content`` gate in the orchestrator) instead of failing silently,
and individual optional fields render honestly via ``kb_field``.
"""
from app.models.client_knowledge_base import ClientKnowledgeBase


def kb_has_content(kb: ClientKnowledgeBase | None) -> bool:
    """True when the knowledge base carries any real content.

    A blank KB row is auto-created with every client, so a ``kb is None``
    check alone never fires in practice — all four KB objects default to
    empty dicts and must be checked for content.
    """
    return bool(
        kb is not None
        and (
            kb.brand_profile
            or kb.target_audience
            or kb.industry_context
            or kb.differentiators
        )
    )


def kb_field(value, fallback: str = "Not provided") -> str:
    """Render one KB field for prompt inclusion.

    Empty values (None or {}) render as the fallback instead of the literal
    "None" / "{}" strings that ``str()`` would produce.
    """
    return str(value) if value else fallback
