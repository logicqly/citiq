"""
Tests for the PDF run report: the charts, the derived summary text, and the
document itself.

The report is the deliverable a client actually reads, so the bar here is that
it always builds. Every test that feeds it degenerate data (no competitors, one
run, a prompt with no results, text carrying XML metacharacters) is asserting
that the reader still gets a document rather than a 500.
"""
import pytest

from app.services import report_charts as charts
from app.services.report_service import (
    _clip,
    _esc,
    _headline_readout,
    _platform_label,
    build_pdf,
)


# -- Chart forms --------------------------------------------------------------

WIDTH = 493.0  # A4 minus the report's margins


def test_trend_needs_two_points_to_be_a_trend():
    assert charts.citation_trend(WIDTH, []) is None
    assert charts.citation_trend(WIDTH, [{"label": "R-1", "rate": 0.4}]) is None
    assert charts.citation_trend(
        WIDTH, [{"label": "R-1", "rate": 0.4}, {"label": "R-2", "rate": 0.5}]
    ) is not None


def test_trend_survives_an_all_zero_history():
    """A client with no citations yet still gets a chart, not a divide by zero."""
    points = [{"label": f"R-{i}", "rate": 0.0} for i in range(4)]
    assert charts.citation_trend(WIDTH, points) is not None


def test_platform_chart_orders_by_rate_and_drops_empty_platforms():
    rows = [
        {"label": "Gemini", "rate": 0.44, "cited": 11, "total": 25},
        {"label": "OpenAI", "rate": 0.52, "cited": 13, "total": 25},
        {"label": "Perplexity", "rate": 0.0, "cited": 0, "total": 0},
    ]
    drawing = charts.platform_rates(WIDTH, rows)
    assert drawing is not None
    # A platform with no responses is not a 0% result, it is an absence.
    labels = [c.text for c in drawing.contents if hasattr(c, "text")]
    assert "Perplexity" not in labels
    assert labels.index("OpenAI") < labels.index("Gemini")


def test_platform_chart_returns_none_when_nothing_ran():
    assert charts.platform_rates(WIDTH, []) is None
    assert charts.platform_rates(WIDTH, [{"label": "OpenAI", "rate": 0, "cited": 0, "total": 0}]) is None


def test_sentiment_mix_skips_platforms_with_no_citations():
    rows = [
        {"label": "OpenAI", "recommended": 9, "mentioned": 3, "negative": 1},
        {"label": "Perplexity", "recommended": 0, "mentioned": 0, "negative": 0},
    ]
    drawing = charts.sentiment_mix(WIDTH, rows)
    assert drawing is not None
    labels = [c.text for c in drawing.contents if hasattr(c, "text")]
    assert "Perplexity" not in labels


def test_sentiment_mix_returns_none_when_nothing_was_cited():
    assert charts.sentiment_mix(WIDTH, [
        {"label": "OpenAI", "recommended": 0, "mentioned": 0, "negative": 0},
    ]) is None


def test_share_of_voice_needs_someone_to_compare_against():
    assert charts.share_of_voice(WIDTH, "Acme", 0.4, []) is None
    assert charts.share_of_voice(
        WIDTH, "Acme", 0.4, [{"brand": "Rival", "share_of_voice": 0.6}]
    ) is not None


def test_share_of_voice_caps_the_field_rather_than_growing_the_chart():
    competitors = [{"brand": f"Rival {i}", "share_of_voice": 0.5 - i * 0.05} for i in range(12)]
    drawing = charts.share_of_voice(WIDTH, "Acme", 0.4, competitors, limit=6)
    labels = [c.text for c in drawing.contents if hasattr(c, "text")]
    assert "Rival 0" in labels and "Rival 5" in labels
    assert "Rival 6" not in labels


def test_tracked_labels_keep_their_word_gaps_in_a_paragraph():
    """A Paragraph collapses whitespace, which once printed DATAQUALITY."""
    assert charts.tracked("data quality") == "D A T A   Q U A L I T Y"
    assert "&nbsp;&nbsp;&nbsp;" in charts.tracked_xml("data quality")


def test_long_labels_are_truncated_not_overrun():
    out = charts._truncate("A very long competitor brand name indeed", charts.FONT, 8, 40)
    assert out.endswith("...")
    assert charts.stringWidth(out, charts.FONT, 8) <= 40


# -- Text helpers -------------------------------------------------------------

def test_escaping_protects_the_paragraph_parser():
    """Model answers and prompts contain angle brackets; unescaped they raise."""
    assert _esc("Tom & Jerry <b>x</b>") == "Tom &amp; Jerry &lt;b&gt;x&lt;/b&gt;"


def test_clip_truncates_with_an_ascii_ellipsis_and_escapes():
    assert _clip("abcdef & <x>", 6) == "abcdef..."
    assert _clip("a & b", 99) == "a &amp; b"


def test_platform_labels_use_the_vendors_own_capitalisation():
    assert _platform_label("openai") == "OpenAI"
    assert _platform_label("anthropic") == "Anthropic"
    assert _platform_label("something_new") == "Something_new"


# -- The derived summary ------------------------------------------------------

def _report(**overrides) -> dict:
    report = {
        "generated_at": "2026-08-19T00:00:00+00:00",
        "run": {
            "id": "11111111-1111-1111-1111-111111111111",
            "display_id": "RUN-1",
            "status": "completed",
            "created_at": "2026-08-19T00:00:00+00:00",
            "total_prompts": 2,
            "completed_prompts": 2,
        },
        "summary": {
            "total_analyses": 50,
            "overall_citation_rate": 0.4,
            "hollow_citation_count": 2,
            "citation_quality": {"recommended_pct": 0.6, "mentioned_pct": 0.3,
                                 "negative_pct": 0.1, "effective_total": 20},
            "ungrounded_count": 0,
            "ungrounded_by_platform": {},
            "partial_count": 0,
            "partial_by_platform": {},
            "visibility_score": 51.2,
        },
        "platform_stats": [
            {"platform": "openai", "model_used": "gpt-5.2", "total_responses": 25,
             "cited_count": 13, "citation_rate": 0.52,
             "prominence_breakdown": {}, "citation_type_breakdown":
                 {"recommended": 9, "mentioned": 3, "negative": 1}},
            {"platform": "perplexity", "model_used": "sonar", "total_responses": 25,
             "cited_count": 7, "citation_rate": 0.28,
             "prominence_breakdown": {}, "citation_type_breakdown":
                 {"recommended": 4, "mentioned": 2, "negative": 1}},
        ],
        "competitor_stats": [{"brand": "Rival", "cited_count": 30, "share_of_voice": 0.6}],
        "recommendations": [],
        "prompts": [],
        "citation_trend": [
            {"run_id": "a", "display_id": "RUN-0", "date": "2026-08-01T00:00:00+00:00",
             "citation_rate": 0.25, "total_analyses": 48},
            {"run_id": "b", "display_id": "RUN-1", "date": "2026-08-19T00:00:00+00:00",
             "citation_rate": 0.4, "total_analyses": 50},
        ],
    }
    report.update(overrides)
    return report


def test_readout_states_the_rate_the_direction_and_the_extremes():
    lines = _headline_readout(_report(), "Acme")
    joined = " ".join(lines)
    assert "40.0%" in joined and "50 AI responses" in joined
    assert "15 points higher" in joined       # 0.40 against 0.25
    assert "OpenAI" in joined and "Perplexity" in joined
    assert "Rival leads share of voice" in joined


def test_readout_reports_a_fall_as_a_fall():
    report = _report()
    report["citation_trend"][0]["citation_rate"] = 0.55
    assert "15 points lower" in " ".join(_headline_readout(report, "Acme"))


def test_readout_says_unchanged_rather_than_zero_points():
    report = _report()
    report["citation_trend"][0]["citation_rate"] = 0.4
    assert "unchanged" in " ".join(_headline_readout(report, "Acme"))


def test_readout_credits_the_client_when_it_leads():
    report = _report(competitor_stats=[
        {"brand": "Rival", "cited_count": 5, "share_of_voice": 0.1},
    ])
    assert "The brand leads share of voice" in " ".join(_headline_readout(report, "Acme"))


def test_readout_holds_up_on_a_first_ever_run():
    """One run means no trend sentence, and no crash reaching for one."""
    report = _report(citation_trend=[], competitor_stats=[], platform_stats=[])
    lines = _headline_readout(report, "Acme")
    assert len(lines) == 1
    assert "was cited in" in lines[0]


def test_readout_escapes_the_client_name():
    lines = _headline_readout(_report(), "Tom & Jerry")
    assert "Tom &amp; Jerry" in lines[0]


# -- The report dict the charts are drawn from --------------------------------

def test_platform_stat_projection_carries_what_the_charts_need():
    """Regression: the citation-quality chart is drawn from
    citation_type_breakdown. When that key was missing from this projection the
    chart drew nothing at all, and nothing failed to say so."""
    from app.models.response import Platform
    from app.schemas.aggregator import PlatformStats
    from app.services.report_service import _platform_stat

    stat = _platform_stat(PlatformStats(
        platform=Platform.openai, model_used="gpt-5.2", total_responses=25,
        cited_count=13, citation_rate=0.52, hollow_count=2,
        prominence_breakdown={"primary": 7},
        citation_type_breakdown={"recommended": 9, "mentioned": 3, "negative": 1},
    ))

    # Exactly the keys build_pdf and report_charts read.
    for key in ("platform", "model_used", "total_responses", "cited_count",
                "citation_rate", "citation_type_breakdown"):
        assert key in stat, f"the report drops {key}, which the charts read"

    # And the projection feeds a chart that actually renders.
    drawing = charts.sentiment_mix(WIDTH, [{
        "label": _platform_label(stat["platform"]),
        "recommended": stat["citation_type_breakdown"].get("recommended", 0),
        "mentioned": stat["citation_type_breakdown"].get("mentioned", 0),
        "negative": stat["citation_type_breakdown"].get("negative", 0),
    }])
    assert drawing is not None


# -- The document -------------------------------------------------------------

def test_full_report_builds():
    pdf = build_pdf(_report(), client_name="Acme")
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 5000  # a real document, not an empty shell


def test_report_builds_with_no_data_at_all():
    """A run that produced nothing still has to produce a readable report."""
    empty = _report(
        platform_stats=[], competitor_stats=[], recommendations=[], prompts=[],
        citation_trend=[],
    )
    empty["summary"]["total_analyses"] = 0
    empty["summary"]["overall_citation_rate"] = 0.0
    empty["summary"]["visibility_score"] = None
    assert build_pdf(empty, client_name="Acme").startswith(b"%PDF")


def test_report_builds_with_hostile_text_in_every_field():
    """Prompts, brands and answers are arbitrary strings from the open web."""
    hostile = "<b>drop & </table> — tags"
    report = _report(
        competitor_stats=[{"brand": hostile, "cited_count": 3, "share_of_voice": 0.2}],
        recommendations=[{
            "id": "r1", "type": "content_brief", "status": "pending", "priority": "high",
            "title": hostile, "content": {"summary": hostile, "points": [hostile]},
            "platform": "openai", "target_query": hostile,
        }],
        prompts=[{
            "prompt_id": "p1", "prompt_text": hostile, "category": "comparison",
            "results": [{
                "platform": "openai", "model_used": hostile, "raw_response": hostile,
                "grounding_status": "grounded", "client_cited": True,
                "client_prominence": "primary", "client_sentiment": "positive",
                "citation_type": "recommended", "client_characterization": hostile,
                "citation_opportunity": "low", "opportunity_score": 1.5,
                "reasoning": hostile, "competitors_cited": [{"brand": hostile}],
                "content_gaps": [hostile],
            }],
        }],
    )
    assert build_pdf(report, client_name=hostile).startswith(b"%PDF")


def test_report_builds_for_a_prompt_no_platform_answered():
    report = _report(prompts=[{
        "prompt_id": "p1", "prompt_text": "Who is best?", "category": "comparison",
        "results": [],
    }])
    assert build_pdf(report, client_name="Acme").startswith(b"%PDF")


def test_ungrounded_results_are_labelled_not_scored():
    """An ungrounded answer must never be printed as a citation verdict."""
    report = _report(prompts=[{
        "prompt_id": "p1", "prompt_text": "Who is best?", "category": "comparison",
        "results": [{
            "platform": "anthropic", "model_used": "claude", "raw_response": "From memory...",
            "grounding_status": "ungrounded", "client_cited": True,
            "client_prominence": "primary", "client_sentiment": "positive",
            "citation_type": "recommended", "reasoning": None,
            "competitors_cited": [], "content_gaps": [],
        }],
    }])
    pdf = build_pdf(report, client_name="Acme")
    assert pdf.startswith(b"%PDF")

    text = _pdf_text(pdf)
    assert "no live search" in text
    assert "training data" in text
    # The prompt header must not claim the platform cited the brand.
    assert "Cited by: no platform" in text


def test_a_recommendation_longer_than_a_page_does_not_kill_the_report():
    """Regression: the PDF download 500'd for any client with a real content brief.

    Each recommendation is drawn as a one-row table so the priority rule can run
    down its left edge, and a table row that cannot split has to fit on one page.
    Test fixtures were short; actual briefs run over a page, and reportlab raised
    LayoutError("too large on page") for the whole document.
    """
    long_text = "AI answers name the brand but never quote a price. " * 8
    report = _report(recommendations=[{
        "id": "r1", "type": "content_brief", "status": "pending", "priority": "high",
        "title": "Publish a pricing comparison page", "platform": "openai",
        "target_query": "cheapest payroll software",
        "content": {
            "summary": long_text, "why_it_matters": long_text, "audience": long_text,
            "outline": [f"Section {i}: {long_text[:200]}" for i in range(10)],
            "key_points": [f"Point {i}: {long_text[:200]}" for i in range(10)],
        },
    }])
    pdf = build_pdf(report, client_name="Acme")
    assert pdf.startswith(b"%PDF")
    # It has to actually run over a page, or the test is not exercising the split.
    assert _pdf_page_count(pdf) >= 2


def test_many_long_recommendations_all_render():
    long_text = "Publish per-seat pricing and a comparison table. " * 8
    report = _report(recommendations=[{
        "id": f"r{i}", "type": "content_brief", "status": "pending",
        "priority": ["high", "medium", "low"][i % 3], "title": f"Action {i}",
        "content": {"summary": long_text, "points": [long_text[:200]] * 8},
    } for i in range(6)])
    assert build_pdf(report, client_name="Acme").startswith(b"%PDF")


def test_recommendation_content_of_any_type_is_survivable():
    """Generators are free to put anything in content; the report is not."""
    report = _report(recommendations=[{
        "id": "r1", "type": "schema", "status": "pending", "priority": "medium",
        "title": "T", "content": {
            "a_dict": {"nested": "value"}, "a_number": 42, "a_none": None,
            "empty_list": [], "empty_str": "", "list_of_dicts": [{"x": 1}],
        },
    }])
    assert build_pdf(report, client_name="Acme").startswith(b"%PDF")


# -- The Citiq wordmark on the cover -----------------------------------------

def test_citiq_wordmark_renders_for_the_cover():
    """The cover prints the Citiq lockup beside the client's own logo.

    Asserts the vendored copy actually parses: a truncated or corrupt asset
    would otherwise fail silently, since a logo that cannot be drawn is skipped
    rather than raised.
    """
    from app.services.report_service import _citiq_logo_bytes, _citiq_logo_flowable

    data = _citiq_logo_bytes()
    assert data is not None, "app/assets/citiq-logo.svg is missing"
    assert b"<svg" in data[:4096]

    drawing = _citiq_logo_flowable(max_width_pt=82.0, max_height_pt=24.0)
    assert drawing is not None, "the wordmark did not render; is svglib installed?"
    assert 0 < drawing.width <= 82.0
    assert 0 < drawing.height <= 24.0


def test_citiq_mark_renders_for_the_page_footer():
    from app.services.report_service import _citiq_mark_drawing

    drawing = _citiq_mark_drawing(height_pt=9)
    assert drawing is not None, "the footer mark did not render"
    assert 0 < drawing.height <= 9


@pytest.mark.parametrize("source_name,vendored_attr", [
    ("citiq-ful-logo.svg", "_CITIQ_LOGO_PATH"),
    ("citiq-colored-logo.svg", "_CITIQ_MARK_PATH"),
])
def test_vendored_brand_assets_match_their_source(source_name, vendored_attr):
    """The API copies must not drift from docs/brand.

    Skipped where docs/ is not on disk: the API image is built from api/ alone,
    so this check only runs in the repo, which is where drift happens.
    """
    import pathlib

    from app.services import report_service

    source = pathlib.Path(__file__).resolve().parents[3] / "docs" / "brand" / source_name
    if not source.exists():
        pytest.skip("docs/brand is not present in this build context")
    vendored = getattr(report_service, vendored_attr)
    assert source.read_bytes() == vendored.read_bytes(), (
        f"{vendored.name} has drifted from docs/brand/{source_name}"
    )


def test_report_still_builds_when_the_brand_assets_are_missing(monkeypatch):
    """Branding is never worth failing a client's download over.

    Covers the cover lockup and the footer mark together: the footer is stamped
    onto every page, so a missing asset there would break every page, not one.
    """
    import pathlib

    from app.services import report_service

    report_service._brand_asset_bytes.cache_clear()
    monkeypatch.setattr(report_service, "_CITIQ_LOGO_PATH", pathlib.Path("no/such/logo.svg"))
    monkeypatch.setattr(report_service, "_CITIQ_MARK_PATH", pathlib.Path("no/such/mark.svg"))
    try:
        pdf = build_pdf(_report(), client_name="Acme")
        assert pdf.startswith(b"%PDF")
        # The footer text still lands, just without the mark in front of it.
        assert "Page 1 of" in _pdf_text(pdf)
    finally:
        report_service._brand_asset_bytes.cache_clear()


def test_masthead_lays_out_with_and_without_a_client_logo():
    """Either side of the cover row may be absent."""
    from app.services.report_service import _masthead

    png = _png_bytes()
    assert _masthead(None, 493.0) is not None
    assert _masthead((png, "image/png"), 493.0) is not None


def _png_bytes() -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGBA", (200, 50), (11, 11, 11, 255)).save(buf, format="PNG")
    return buf.getvalue()


# -- Text the fonts cannot draw ----------------------------------------------

def test_pdf_safe_transliterates_typography_and_drops_the_undrawable():
    """reportlab prints a black box for anything the base-14 fonts lack."""
    assert charts.pdf_safe("“smart” — dash…") == '"smart" - dash...'
    assert charts.pdf_safe("CJK 日本語 emoji 🚀 end") == "CJK emoji end"
    assert charts.pdf_safe("line one\nline 日本 two") == "line one\nline two"
    assert charts.pdf_safe("plain ascii  kept") == "plain ascii  kept"


def test_unicode_in_a_report_never_reaches_the_page_as_a_box():
    report = _report(
        competitor_stats=[{"brand": "Rival 日本語 🚀",
                           "cited_count": 3, "share_of_voice": 0.2}],
        prompts=[{
            "prompt_id": "p1", "prompt_text": "Best tool? 日本語 🚀",
            "category": "comparison",
            "results": [{
                "platform": "openai", "model_used": "gpt-5",
                "raw_response": "Answer “quoted” — with 🚀 emoji.",
                "grounding_status": "grounded", "client_cited": True,
                "client_prominence": "primary", "client_sentiment": "positive",
                "citation_type": "recommended", "client_characterization": None,
                "citation_opportunity": "low", "opportunity_score": 1.0,
                "reasoning": None, "competitors_cited": [], "content_gaps": [],
            }],
        }],
    )
    text = _pdf_text(build_pdf(report, client_name="Acme"))
    # The replacement/notdef glyph extracts as U+FFFD; nothing should carry one.
    assert "�" not in text
    assert "Rival" in text and "Best tool?" in text


def test_page_furniture_numbers_every_page():
    pdf = build_pdf(_report(), client_name="Acme")
    text = _pdf_text(pdf)
    assert "Page 1 of" in text
    assert "RUN-1" in text and "Acme" in text


def _pdf_page_count(pdf: bytes) -> int:
    pymupdf = pytest.importorskip("pymupdf", reason="PDF inspection is a dev-only check")
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        return doc.page_count


def _pdf_text(pdf: bytes) -> str:
    pymupdf = pytest.importorskip("pymupdf", reason="PDF text extraction is a dev-only check")
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        return "\n".join(page.get_text() for page in doc)
