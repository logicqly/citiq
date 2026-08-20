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


def test_page_furniture_numbers_every_page():
    pdf = build_pdf(_report(), client_name="Acme")
    text = _pdf_text(pdf)
    assert "Page 1 of" in text
    assert "RUN-1" in text and "Acme" in text


def _pdf_text(pdf: bytes) -> str:
    pymupdf = pytest.importorskip("pymupdf", reason="PDF text extraction is a dev-only check")
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        return "\n".join(page.get_text() for page in doc)
