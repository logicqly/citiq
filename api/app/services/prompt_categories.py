"""
Prompt category configuration.

Prompt categories are admin-managed (stored in system_settings.prompt_categories)
rather than a hardcoded enum. Each category is an object with a required `name`,
a required `color` (hex), and an optional `description`. An empty / unset stored
list falls back to DEFAULT_PROMPT_CATEGORIES here — the same resolve-vs-default
semantics used by visibility weights.

Kept as pure functions (no DB) so they are trivially unit-testable and reusable
by the settings API and the prompt service.
"""
import re

# ── Defaults ──────────────────────────────────────────────────────────────────
# The default taxonomy. Admins may add / edit / delete these from Global
# Settings; once a non-empty list is saved it becomes authoritative.
DEFAULT_PROMPT_CATEGORIES: list[dict] = [
    {"name": "Discovery", "color": "#f59e0b",
     "description": "Are you surfaced at all when someone describes the problem"},
    {"name": "Criteria", "color": "#8b5cf6",
     "description": "Do you own the framing of how to decide"},
    {"name": "Shortlist", "color": "#3b82f6",
     "description": "Are you on the list, and where do you rank"},
    {"name": "Fit", "color": "#10b981",
     "description": "Do you own a niche / specific use case"},
    {"name": "Social proof", "color": "#ec4899",
     "description": "Do you show up as proven / backed at decision time"},
    {"name": "Comparison", "color": "#6366f1",
     "description": "Do you appear in head-to-heads"},
]

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_MAX_NAME_LEN = 100


def resolve_prompt_categories(stored: list | None) -> list[dict]:
    """Return the stored category list, falling back to the code defaults when
    it is empty / unset."""
    if isinstance(stored, list) and stored:
        return stored
    return [dict(c) for c in DEFAULT_PROMPT_CATEGORIES]


def resolve_category_names(stored: list | None) -> dict[str, str]:
    """Map lowercased category name → canonical name, for case-insensitive
    coercion of incoming prompt categories."""
    return {c["name"].lower(): c["name"] for c in resolve_prompt_categories(stored)}


def coerce_category(value: str | None, names: dict[str, str]) -> str:
    """Normalise an incoming category to its canonical configured name, or "" if
    it is blank / unknown. `names` comes from resolve_category_names()."""
    if not value:
        return ""
    return names.get(value.strip().lower(), "")


def validate_prompt_categories(categories: list) -> list[str]:
    """Return a list of human-readable validation errors (empty == valid).

    Each category must have a non-empty unique (case-insensitive) name (≤100
    chars), a valid #rrggbb color, and an optional string description. The list
    must contain at least one category.
    """
    errors: list[str] = []
    if not isinstance(categories, list):
        return ["prompt_categories must be a list"]
    if not categories:
        return ["at least one category is required"]

    seen: set[str] = set()
    for i, cat in enumerate(categories):
        label = f"category {i + 1}"
        if not isinstance(cat, dict):
            errors.append(f"{label} must be an object")
            continue

        name = cat.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{label}: name is required")
        else:
            label = f"category '{name}'"
            if len(name) > _MAX_NAME_LEN:
                errors.append(f"{label}: name must be at most {_MAX_NAME_LEN} characters")
            key = name.strip().lower()
            if key in seen:
                errors.append(f"{label}: duplicate name")
            seen.add(key)

        color = cat.get("color")
        if not isinstance(color, str) or not _HEX_COLOR_RE.match(color):
            errors.append(f"{label}: color must be a hex value like #3b82f6")

        description = cat.get("description")
        if description is not None and not isinstance(description, str):
            errors.append(f"{label}: description must be a string")

    return errors
