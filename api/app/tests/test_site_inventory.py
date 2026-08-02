"""
Tests for the live site inventory (2026-07-25 agreement, point 8).

"The recommendation engine must check what already exists before recommending.
It pulls live data from the client website to see current content and schema,
so it does not re-recommend work that has already been implemented."

The parsing must survive real-world HTML (malformed JSON-LD, @graph wrappers,
missing tags), and the rendering must tell the model the difference between
"nothing is implemented" and "we could not find out" — conflating those two is
exactly how the engine would start inventing work or skipping needed work.
"""
from types import SimpleNamespace

import pytest

from app.services.site_inventory import (
    _normalize_root,
    _parse_page,
    _same_host,
    _schema_types,
    render_for_prompt,
)


# ── Parsing ───────────────────────────────────────────────────────────────────

def test_extracts_page_metadata():
    html = (
        "<html><head><title>  Payroll Guide </title>"
        '<meta name="description" content="How payroll works">'
        "</head><body><h1>Payroll  Guide</h1><h2>Step &amp; one</h2></body></html>"
    )
    page = _parse_page("https://acme.test/guide", html)
    assert page["title"] == "Payroll Guide"          # whitespace collapsed
    assert page["description"] == "How payroll works"
    assert page["headings"] == ["Payroll Guide", "Step & one"]   # entities decoded


def test_missing_metadata_is_none_not_a_crash():
    page = _parse_page("https://acme.test/", "<html><body>bare</body></html>")
    assert page["title"] is None
    assert page["description"] is None
    assert page["headings"] == []
    assert page["schema_types"] == []


@pytest.mark.parametrize("block,expected", [
    ('{"@type": "FAQPage"}', ["FAQPage"]),
    ('[{"@type": "Article"}, {"@type": "Organization"}]', ["Article", "Organization"]),
    ('{"@graph": [{"@type": "WebSite"}, {"@type": "Product"}]}', ["WebSite", "Product"]),
    ('{"@type": ["Article", "BlogPosting"]}', ["Article", "BlogPosting"]),
])
def test_schema_shapes_real_sites_actually_use(block, expected):
    html = f'<script type="application/ld+json">{block}</script>'
    assert _schema_types(html) == expected


def test_malformed_jsonld_is_skipped_not_fatal():
    # Plenty of production sites ship invalid JSON-LD; one bad block must not
    # cost us the valid ones on the same page.
    html = (
        '<script type="application/ld+json">{not json at all</script>'
        '<script type="application/ld+json">{"@type": "FAQPage"}</script>'
    )
    assert _schema_types(html) == ["FAQPage"]


def test_schema_types_are_deduped():
    html = (
        '<script type="application/ld+json">{"@type": "Article"}</script>'
        '<script type="application/ld+json">{"@type": "Article"}</script>'
    )
    assert _schema_types(html) == ["Article"]


# ── URL handling ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("acme.test", "https://acme.test"),        # bare domains are what clients store
    ("https://acme.test/", "https://acme.test"),
    ("http://acme.test", "http://acme.test"),
    ("", ""),
])
def test_root_normalization(raw, expected):
    assert _normalize_root(raw) == expected


@pytest.mark.parametrize("url,root,same", [
    ("https://acme.test/a", "https://acme.test", True),
    ("https://www.acme.test/a", "https://acme.test", True),   # www counts as same
    ("https://acme.test/a", "https://www.acme.test", True),
    ("https://other.test/a", "https://acme.test", False),     # no wandering off-site
    ("not a url", "https://acme.test", False),
])
def test_host_scoping(url, root, same):
    assert _same_host(url, root) is same


# ── Rendering for the prompt ──────────────────────────────────────────────────

def _snapshot(**overrides):
    base = dict(
        root_url="https://acme.test",
        pages=[{
            "url": "https://acme.test/payroll",
            "title": "Payroll",
            "headings": ["What is payroll"],
            "schema_types": ["FAQPage"],
        }],
        page_count=1,
        llms_txt_present=False,
        llms_txt_content=None,
        error=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_no_snapshot_says_unknown_not_empty():
    # The critical distinction: with no data the model must NOT conclude that
    # nothing is implemented.
    text = render_for_prompt(None)
    assert "Do not assume" in text


def test_failed_crawl_says_unknown_not_empty():
    text = render_for_prompt(_snapshot(pages=[], page_count=0, error="timeout"))
    assert "could not be read" in text
    assert "Do not assume" in text


def test_successful_crawl_lists_what_exists():
    text = render_for_prompt(_snapshot())
    assert "https://acme.test/payroll" in text
    assert "Payroll" in text
    assert "FAQPage" in text
    assert "llms.txt: not present" in text


def test_partial_crawl_is_labelled_as_partial():
    text = render_for_prompt(_snapshot(error="crawl exceeded 90s"))
    assert "partial" in text.lower()
    # It still reports the pages it did read.
    assert "https://acme.test/payroll" in text


def test_existing_llms_txt_is_shown_so_it_is_not_recreated():
    text = render_for_prompt(
        _snapshot(llms_txt_present=True, llms_txt_content="# Acme\nWe do payroll.")
    )
    assert "llms.txt EXISTS" in text
    assert "We do payroll." in text


def test_rendering_is_bounded():
    big = _snapshot(
        pages=[
            {"url": f"https://acme.test/{i}", "title": "T" * 200,
             "headings": ["H" * 100], "schema_types": ["Article"]}
            for i in range(500)
        ],
        page_count=500,
    )
    assert len(render_for_prompt(big, max_chars=5000)) <= 5000
