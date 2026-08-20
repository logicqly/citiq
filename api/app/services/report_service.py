"""
Report assembly service — JSON and PDF run reports.

For client reports (include_internal=False):
  - Raw AI responses included (visible in webapp)
  - All analysis fields included (cited, prominence, sentiment, characterization,
    competitors, gaps, opportunity, reasoning)
  - Pending/approved/revision_requested/implemented recommendations included
  - Rejected recommendations excluded
  - Cost/latency fields excluded (internal pricing info)

For admin reports (include_internal=True):
  - Everything above plus rejected recommendations, cost_usd, latency_ms,
    platform_errors

Both PDFs print the client's uploaded brand logo on the cover when the admin has
set one (see logo_service); without one the cover is text only, exactly as before.
"""
import functools
import io
import pathlib
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.aggregator import (
    compute_citation_trend,
    compute_run_summary,
    compute_run_visibility_score,
    get_prompt_details,
)


async def assemble_run_report(
    session: AsyncSession,
    run_id: uuid.UUID,
    include_internal: bool = False,
) -> dict:
    """Build the full run report dict (used for both JSON export and PDF data source)."""
    from app.models.recommendation import Recommendation, RecommendationStatus

    summary = await compute_run_summary(run_id, session)
    prompts = await get_prompt_details(run_id, session)
    run = summary.run

    # The two figures the report leads with. The score is the same one the client
    # dashboard shows (shared helper), and the trend is this client's recent runs
    # so a reader can see whether the headline number is moving.
    visibility_score = await compute_run_visibility_score(run_id, session)
    citation_trend = await compute_citation_trend(run.client_id, session)

    # Fetch visible recommendations for this run.
    # Client-visible statuses match /client/recommendations (hides rejected).
    # Admin gets rejected too.
    visible_statuses = [
        RecommendationStatus.pending.value,
        RecommendationStatus.approved.value,
        RecommendationStatus.revision_requested.value,
        RecommendationStatus.implemented.value,
    ]
    if include_internal:
        visible_statuses += [
            RecommendationStatus.rejected.value,
        ]

    rec_rows = (
        await session.execute(
            select(Recommendation).where(
                Recommendation.run_id == run_id,
                Recommendation.status.in_(visible_statuses),
            ).order_by(Recommendation.priority, Recommendation.created_at)
        )
    ).scalars().all()

    recommendations = [
        {
            "id": str(r.id),
            "type": r.type.value,
            "status": r.status.value,
            "priority": r.priority.value,
            "title": r.title,
            "content": r.content,
            "platform": r.platform,
            "target_query": r.target_query,
            **({"generation_model": r.generation_model} if include_internal else {}),
        }
        for r in rec_rows
    ]

    # Build per-prompt results — all analysis fields visible in webapp
    prompt_data = []
    for p in prompts:
        results = []
        for r in p.results:
            entry: dict = {
                "platform": r.platform.value,
                "model_used": r.model_used,
                "raw_response": r.raw_response,
                "grounding_status": r.grounding_status,
                "client_cited": r.client_cited,
                "client_prominence": r.client_prominence,
                "client_sentiment": r.client_sentiment,
                "citation_type": r.citation_type,
                "client_characterization": r.client_characterization,
                "citation_opportunity": r.citation_opportunity,
                "opportunity_score": r.opportunity_score,
                "reasoning": r.reasoning,
                "competitors_cited": r.competitors_cited,
                "content_gaps": r.content_gaps,
            }
            if include_internal:
                entry["latency_ms"] = r.latency_ms
                entry["cost_usd"] = r.cost_usd
            results.append(entry)
        prompt_data.append({
            "prompt_id": str(p.prompt_id),
            "prompt_text": p.prompt_text,
            "category": p.category,
            "results": results,
        })

    report: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run": {
            "id": str(run.id),
            "display_id": run.display_id,
            "status": run.status.value,
            "created_at": run.created_at.isoformat(),
            "total_prompts": run.total_prompts,
            "completed_prompts": run.completed_prompts,
        },
        "summary": {
            "total_analyses": summary.total_analyses,
            "overall_citation_rate": summary.overall_citation_rate,
            "hollow_citation_count": summary.hollow_citation_count,
            "citation_quality": summary.citation_quality.model_dump(),
            "ungrounded_count": summary.ungrounded_count,
            "ungrounded_by_platform": summary.ungrounded_by_platform,
            "partial_count": summary.partial_count,
            "partial_by_platform": summary.partial_by_platform,
            "visibility_score": visibility_score,
        },
        "platform_stats": [_platform_stat(ps) for ps in summary.platform_stats],
        "competitor_stats": [
            {
                "brand": cs.brand,
                "cited_count": cs.cited_count,
                "share_of_voice": cs.share_of_voice,
            }
            for cs in summary.competitor_stats
        ],
        "recommendations": recommendations,
        "prompts": prompt_data,
        # Effective citation rate per recent results-bearing run, oldest first.
        # The last point is this run, so it matches the headline rate above.
        "citation_trend": citation_trend,
    }

    if include_internal and summary.platform_errors:
        report["platform_errors"] = summary.platform_errors

    return report


def _platform_stat(ps) -> dict:
    """One platform's stats, as the report and its charts consume them.

    Kept as its own function because the PDF's citation-quality chart is drawn
    from citation_type_breakdown: when that key was missing from this projection
    the chart silently drew nothing, which is exactly the kind of omission a
    dict literal inline in a 100-line function hides.
    """
    return {
        "platform": ps.platform.value,
        "model_used": ps.model_used,
        "total_responses": ps.total_responses,
        "cited_count": ps.cited_count,
        "citation_rate": ps.citation_rate,
        "hollow_count": ps.hollow_count,
        "prominence_breakdown": ps.prominence_breakdown,
        # Counts per citation type; the "how the brand is described" chart is
        # drawn entirely from this.
        "citation_type_breakdown": ps.citation_type_breakdown,
    }


def _opportunity_cell(result: dict) -> str:
    """The Opportunity column: the 1.0-5.0 score, with the bucket as context.

    Analyses written before scoring existed have no score and still show their
    bucket alone, so historical reports keep rendering.
    """
    bucket = result.get("citation_opportunity") or "-"
    score = result.get("opportunity_score")
    if score is None:
        return str(bucket)
    return f"{float(score):.1f} ({bucket})"

# ── PDF rendering ─────────────────────────────────────────────────────────────
#
# The report is a client-facing document, so it is laid out as one: an executive
# page that answers "how visible are we, and is it improving" in numbers and
# charts, then the evidence behind each answer, then the raw prompt-level detail
# for whoever wants to check the work.
#
# Chrome is deliberately quiet — near-black ink, hairline rules, one accent hue
# for data — because the brand is monochrome and because a printed report full of
# coloured furniture reads as a template. All chart drawing lives in
# report_charts; everything here is document structure.

# The Citiq wordmark, printed on the cover as the maker's mark beside the
# client's own logo. Vendored into the package (app/assets/README.md explains
# why it is not read from docs/brand) and read once per process.
_CITIQ_LOGO_PATH = pathlib.Path(__file__).resolve().parent.parent / "assets" / "citiq-logo.svg"


@functools.lru_cache(maxsize=1)
def _citiq_logo_bytes() -> bytes | None:
    """The wordmark's bytes, or None if the asset is missing.

    Missing is survivable: a report without the Citiq mark is still a valid
    report, and failing the client's download over branding would be the wrong
    trade.
    """
    try:
        return _CITIQ_LOGO_PATH.read_bytes()
    except OSError:
        return None


def _citiq_logo_flowable(max_width_pt: float, max_height_pt: float):
    from app.services.logo_service import SVG_MIME, build_logo_flowable

    data = _citiq_logo_bytes()
    if data is None:
        return None
    return build_logo_flowable(data, SVG_MIME, max_width_pt, max_height_pt)


_PRIORITY_COLORS = {
    "high": "#D03B3B",
    "medium": "#B57614",
    "low": "#898781",
}

# Text long enough to bury the reader is cut at these lengths.
_MAX_PROMPT_CHARS = 190
_MAX_VALUE_CHARS = 420
_MAX_RESPONSE_CHARS = 700


def _esc(text) -> str:
    """Prepare arbitrary text for a reportlab Paragraph.

    Two jobs, in order. First reduce it to characters the report's fonts can
    draw, so a Japanese brand name or an emoji in a model's answer does not
    print as a row of boxes. Then escape it: prompts, brand names and model
    answers are arbitrary strings, and an unescaped ``&`` or ``<`` raises inside
    the paragraph parser and takes the whole report down with it.
    """
    from app.services.report_charts import pdf_safe

    return (
        pdf_safe(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _clip(text, limit: int) -> str:
    """Escaped text, cut to `limit` with an ASCII ellipsis."""
    raw = str(text or "")
    return _esc(raw[:limit] + ("..." if len(raw) > limit else ""))


def _pct(value, decimals: int = 0) -> str:
    return f"{value * 100:.{decimals}f}%"


def _platform_label(name: str) -> str:
    """Platform names as their vendors write them."""
    return {
        "openai": "OpenAI",
        "anthropic": "Anthropic",
        "gemini": "Gemini",
        "perplexity": "Perplexity",
    }.get(name, name.capitalize())


def _headline_readout(report: dict, client_name: str) -> list[str]:
    """Two or three plain sentences stating what the numbers say.

    Every clause is read straight off the report dict. Nothing here interprets,
    predicts or recommends: the recommendations section does that, and a summary
    that editorialises is a summary a client cannot check.
    """
    summary = report["summary"]
    lines: list[str] = []

    rate = summary["overall_citation_rate"]
    total = summary["total_analyses"]
    cited = round(rate * total)
    lines.append(
        f"{_esc(client_name)} was cited in <b>{_pct(rate, 1)}</b> of the {total} AI "
        f"responses analysed in this run ({cited} of {total}, hollow citations excluded)."
    )

    trend = report.get("citation_trend") or []
    if len(trend) >= 2:
        first, last = trend[0]["citation_rate"], trend[-1]["citation_rate"]
        delta = round((last - first) * 100)
        runs = len(trend)
        if delta > 0:
            lines.append(
                f"That is <b>{delta} points higher</b> than the oldest of the last "
                f"{runs} runs shown ({_pct(first)})."
            )
        elif delta < 0:
            lines.append(
                f"That is <b>{abs(delta)} points lower</b> than the oldest of the last "
                f"{runs} runs shown ({_pct(first)})."
            )
        else:
            lines.append(
                f"That is unchanged against the oldest of the last {runs} runs shown."
            )

    stats = report.get("platform_stats") or []
    if len(stats) >= 2:
        best = max(stats, key=lambda s: s["citation_rate"])
        worst = min(stats, key=lambda s: s["citation_rate"])
        lines.append(
            f"Coverage is strongest on {_platform_label(best['platform'])} "
            f"({_pct(best['citation_rate'])}) and weakest on "
            f"{_platform_label(worst['platform'])} ({_pct(worst['citation_rate'])})."
        )

    competitors = report.get("competitor_stats") or []
    if competitors:
        leader = competitors[0]
        if leader["share_of_voice"] > rate:
            lines.append(
                f"{_esc(leader['brand'])} leads share of voice at "
                f"{_pct(leader['share_of_voice'])}, ahead of the brand's {_pct(rate)}."
            )
        else:
            lines.append(
                f"The brand leads share of voice, ahead of {_esc(leader['brand'])} "
                f"at {_pct(leader['share_of_voice'])}."
            )

    return lines


def _styles():
    """The document's type scale. Helvetica throughout: it is the built-in
    closest to the brand's Inter, and embedding a font would bloat every PDF."""
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

    base = getSampleStyleSheet()
    ink = colors.HexColor("#0B0B0B")
    ink2 = colors.HexColor("#52514E")
    muted = colors.HexColor("#898781")

    return {
        "cover_title": ParagraphStyle(
            "cover_title", parent=base["Title"], fontName="Helvetica-Bold",
            fontSize=30, leading=33, alignment=0, textColor=ink, spaceAfter=0,
        ),
        "eyebrow": ParagraphStyle(
            "eyebrow", parent=base["BodyText"], fontName="Helvetica-Bold",
            fontSize=6.6, leading=10, textColor=muted, spaceAfter=6,
        ),
        "lede": ParagraphStyle(
            "lede", parent=base["BodyText"], fontSize=10.5, leading=16,
            textColor=ink2, spaceAfter=4,
        ),
        "h2": ParagraphStyle(
            "h2", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=14,
            leading=17, textColor=ink, spaceBefore=2, spaceAfter=2,
        ),
        "h3": ParagraphStyle(
            "h3", parent=base["Heading3"], fontName="Helvetica-Bold", fontSize=9.5,
            leading=13, textColor=ink, spaceBefore=7, spaceAfter=2,
        ),
        "body": ParagraphStyle(
            "body", parent=base["BodyText"], fontSize=8.8, leading=13,
            textColor=ink2, spaceAfter=4,
        ),
        "small": ParagraphStyle(
            "small", parent=base["BodyText"], fontSize=7.6, leading=11,
            textColor=muted, spaceAfter=3,
        ),
        "quote": ParagraphStyle(
            "quote", parent=base["BodyText"], fontSize=8, leading=11.5,
            textColor=ink2, leftIndent=9, spaceAfter=1,
        ),
        # The model's own words, set as a quoted card rather than in a monospace
        # face: this is prose a client reads, not a log.
        "answer": ParagraphStyle(
            "answer", parent=base["BodyText"], fontSize=7.6, leading=11.4,
            textColor=ink2, backColor=colors.HexColor("#FAFAF9"),
            borderColor=colors.HexColor("#E1E0D9"), borderWidth=0.4,
            borderPadding=(5, 7, 5, 7), spaceBefore=2, spaceAfter=7,
        ),
        "th": ParagraphStyle(
            "th", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=7.6,
            leading=10, textColor=colors.white, spaceAfter=0,
        ),
        "td": ParagraphStyle(
            "td", parent=base["BodyText"], fontSize=7.8, leading=10.5,
            textColor=ink2, spaceAfter=0,
        ),
    }


def _table_style(align_from: int = 1):
    """One table look for the whole report: ink header, hairline grid, faint zebra."""
    from reportlab.lib import colors
    from reportlab.platypus import TableStyle

    return TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0B0B0B")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.8),
        ("TEXTCOLOR", (0, 1), (-1, -1), colors.HexColor("#52514E")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#FAFAF9")]),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, colors.HexColor("#E1E0D9")),
        ("BOX", (0, 0), (-1, -1), 0.4, colors.HexColor("#E1E0D9")),
        ("ALIGN", (align_from, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
    ])


def _section(styles, eyebrow, title, subtitle=None) -> list:
    """A section opener: tracked eyebrow, title, hairline rule.

    Returned rather than appended so the caller can bind it to the first block
    beneath it — a section heading stranded at the foot of a page reads as a
    layout bug in a document a client is paying for.
    """
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import HRFlowable, Paragraph, Spacer

    from app.services.report_charts import tracked_xml

    out = [
        Paragraph(tracked_xml(eyebrow), styles["eyebrow"]),
        Paragraph(_esc(title), styles["h2"]),
    ]
    if subtitle:
        out.append(Paragraph(_esc(subtitle), styles["small"]))
    out.append(Spacer(1, 2 * mm))
    out.append(HRFlowable(width="100%", thickness=0.4,
                          color=colors.HexColor("#E1E0D9"), spaceAfter=6))
    return out


def _numbered_canvas(client_name: str, run_label: str):
    """A canvas that stamps the footer once the total page count is known.

    reportlab draws pages as it goes, so "Page 2 of 9" needs the whole document
    buffered first: pages are held, counted, then replayed with the footer drawn.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as pdfcanvas

    from app.services.report_charts import pdf_safe

    # Drawn straight onto the canvas, so it never passes through _esc.
    footer_name = pdf_safe(client_name)
    footer_run = pdf_safe(run_label)

    class NumberedCanvas(pdfcanvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._saved_states = []

        def showPage(self):
            self._saved_states.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._saved_states)
            for state in self._saved_states:
                self.__dict__.update(state)
                self._draw_footer(total)
                super().showPage()
            super().save()

        def _draw_footer(self, total):
            page = self._pageNumber
            width, _height = A4
            y = 12 * mm
            self.setStrokeColor(colors.HexColor("#E1E0D9"))
            self.setLineWidth(0.4)
            self.line(18 * mm, y + 6, width - 18 * mm, y + 6)
            self.setFont("Helvetica", 7)
            self.setFillColor(colors.HexColor("#898781"))
            self.drawString(18 * mm, y, footer_name)
            self.drawCentredString(width / 2, y, footer_run)
            self.drawRightString(width - 18 * mm, y, f"Page {page} of {total}")

    return NumberedCanvas


def build_pdf(
    report: dict,
    client_name: str,
    logo: tuple[bytes, str] | None = None,
) -> bytes:
    """Render a run report to PDF bytes.

    `logo` is the client's brand logo as (bytes, mime) — see
    logo_service.fetch_client_logo. A logo that cannot be rendered is skipped
    rather than failing the report.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        KeepTogether, PageBreak, Paragraph, SimpleDocTemplate,
        Spacer, Table,
    )

    from app.services import report_charts as charts
    from app.services.report_charts import tracked_xml

    styles = _styles()
    run = report["run"]
    summary = report["summary"]
    run_label = run.get("display_id") or str(run["id"])[:8]

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=20 * mm,
        title=f"GEO monitoring report - {client_name} - {run_label}",
        author="Citiq",
    )
    content_w = doc.width
    story: list = []

    # ── Page 1: the executive view ────────────────────────────────────────────
    story.append(_masthead(logo, content_w))
    story.append(Spacer(1, 7 * mm))

    story.append(Paragraph(tracked_xml("GEO monitoring report"), styles["eyebrow"]))
    story.append(Paragraph(_esc(client_name), styles["cover_title"]))
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph("Citation visibility across AI answers", styles["lede"]))
    story.append(Spacer(1, 5 * mm))

    meta = [
        [Paragraph(tracked_xml("Run"), styles["eyebrow"]),
         Paragraph(tracked_xml("Run date"), styles["eyebrow"]),
         Paragraph(tracked_xml("Prompts"), styles["eyebrow"]),
         Paragraph(tracked_xml("Generated"), styles["eyebrow"])],
        [Paragraph(f"<b>{_esc(run_label)}</b>", styles["body"]),
         Paragraph(f"<b>{run['created_at'][:10]}</b>", styles["body"]),
         Paragraph(f"<b>{run['completed_prompts']} of {run['total_prompts']}</b>",
                   styles["body"]),
         Paragraph(f"<b>{report['generated_at'][:10]}</b>", styles["body"])],
    ]
    meta_tbl = Table(meta, colWidths=[content_w / 4] * 4)
    meta_tbl.setStyle(_meta_style())
    story.append(meta_tbl)
    story.append(Spacer(1, 6 * mm))

    # Headline numbers. The visibility score leads when it exists, because it is
    # the one figure that blends every signal into something comparable run over run.
    quality = summary.get("citation_quality") or {}
    tiles = []
    score = summary.get("visibility_score")
    if score is not None:
        tiles.append(("Visibility score", f"{score:g}", "Weighted across six signals"))
    tiles.append((
        "Citation rate",
        _pct(summary["overall_citation_rate"], 1),
        f"{round(summary['overall_citation_rate'] * summary['total_analyses'])}"
        f" of {summary['total_analyses']} responses",
    ))
    tiles.append((
        "Recommended",
        _pct(quality.get("recommended_pct", 0.0)),
        "of citations recommend it",
    ))
    tiles.append((
        "Recommendations",
        str(len(report.get("recommendations") or [])),
        "actions in this report",
    ))
    strip = charts.kpi_strip(content_w, tiles)
    if strip is not None:
        story.append(strip)
        story.append(Spacer(1, 4 * mm))

    for line in _headline_readout(report, client_name):
        story.append(Paragraph(line, styles["body"]))
    story.append(Spacer(1, 3 * mm))

    trend_points = [
        {"label": p.get("display_id") or (p.get("date") or "")[:10],
         "rate": p["citation_rate"]}
        for p in (report.get("citation_trend") or [])
    ]
    trend_chart = charts.citation_trend(content_w, trend_points)
    if trend_chart is not None:
        story.append(trend_chart)
        story.append(Spacer(1, 4 * mm))

    _coverage_notes(story, styles, summary, content_w)

    # ── Page 2: platforms and citation quality ────────────────────────────────
    story.append(PageBreak())
    section = _section(styles, "Platforms", "Where the brand is cited",
                       "Each platform was asked the same prompts, so the rates are directly comparable.")

    platform_chart = charts.platform_rates(content_w, [
        {"label": _platform_label(ps["platform"]), "rate": ps["citation_rate"],
         "cited": ps["cited_count"], "total": ps["total_responses"]}
        for ps in report["platform_stats"]
    ])
    if platform_chart is not None:
        story.append(KeepTogether(section + [platform_chart]))
        story.append(Spacer(1, 4 * mm))
    else:
        story.extend(section)

    if report["platform_stats"]:
        data = [["Platform", "Model", "Responses", "Cited", "Citation rate"]]
        for ps in report["platform_stats"]:
            data.append([
                _platform_label(ps["platform"]),
                _clip(ps["model_used"] or "-", 40),
                str(ps["total_responses"]),
                str(ps["cited_count"]),
                _pct(ps["citation_rate"], 1),
            ])
        tbl = Table(data, colWidths=[content_w * 0.20, content_w * 0.34,
                                     content_w * 0.15, content_w * 0.13,
                                     content_w * 0.18])
        tbl.setStyle(_table_style(align_from=2))
        story.append(tbl)
        story.append(Spacer(1, 6 * mm))

    quality_chart = charts.sentiment_mix(content_w, [
        {
            "label": _platform_label(ps["platform"]),
            "recommended": (ps.get("citation_type_breakdown") or {}).get("recommended", 0),
            "mentioned": (ps.get("citation_type_breakdown") or {}).get("mentioned", 0),
            "negative": (ps.get("citation_type_breakdown") or {}).get("negative", 0),
        }
        for ps in report["platform_stats"]
    ])
    if quality_chart is not None:
        story.append(quality_chart)
        story.append(Spacer(1, 3 * mm))
        story.append(Paragraph(
            "A recommendation is worth more than a mention: it is the answer telling the "
            "reader to choose this brand. Hollow citations, where the brand appears without "
            "substance, are excluded from every figure in this report.",
            styles["small"],
        ))
    elif quality.get("effective_total"):
        # No per-platform split to chart (older runs carry none), but the mix is
        # still the second most important thing on the page, so it is stated.
        story.append(Paragraph(
            f"Of {quality['effective_total']} citations, "
            f"<b>{_pct(quality.get('recommended_pct', 0.0))}</b> recommend the brand, "
            f"{_pct(quality.get('mentioned_pct', 0.0))} mention it neutrally and "
            f"{_pct(quality.get('negative_pct', 0.0))} are negative.",
            styles["body"],
        ))

    # ── Competitors ───────────────────────────────────────────────────────────
    if report["competitor_stats"]:
        story.append(Spacer(1, 8 * mm))
        section = _section(styles, "Competitors", "Share of voice",
                           "How often each brand appears across the same set of analysed responses.")

        sov_chart = charts.share_of_voice(
            content_w, client_name, summary["overall_citation_rate"],
            report["competitor_stats"],
        )
        if sov_chart is not None:
            story.append(KeepTogether(section + [sov_chart]))
            story.append(Spacer(1, 4 * mm))
        else:
            story.extend(section)

        data = [["Competitor", "Responses citing", "Share of voice"]]
        for cs in report["competitor_stats"]:
            data.append([
                _clip(cs["brand"], 60),
                str(cs["cited_count"]),
                _pct(cs["share_of_voice"], 1),
            ])
        tbl = Table(data, colWidths=[content_w * 0.54, content_w * 0.23, content_w * 0.23])
        tbl.setStyle(_table_style(align_from=1))
        story.append(tbl)

    # ── Recommendations ───────────────────────────────────────────────────────
    recommendations = report.get("recommendations") or []
    if recommendations:
        story.append(PageBreak())
        section = _section(
            styles, "Actions", "Recommendations",
            f"{len(recommendations)} actions generated from this run's results, highest priority first.",
        )
        blocks = [_recommendation_block(rec, styles, content_w) for rec in recommendations]
        story.append(KeepTogether(section + blocks[:1]))
        story.extend(blocks[1:])

    # ── Prompt-level detail ───────────────────────────────────────────────────
    story.append(PageBreak())
    section = _section(styles, "Evidence", "Prompt-level analysis",
                       "Every prompt, what each platform answered, and how that answer was scored.")

    blocks = [_prompt_block(prompt, styles, content_w) for prompt in report["prompts"]]
    if blocks:
        story.append(KeepTogether(section + blocks[0][:1]))
        story.extend(blocks[0][1:])
        for block in blocks[1:]:
            story.extend(block)
    else:
        story.extend(section)

    doc.build(story, canvasmaker=_numbered_canvas(client_name, run_label))
    return buf.getvalue()


def _masthead(logo, content_w):
    """The cover's top row: the client's logo left, the Citiq wordmark right.

    Read as "this report is for them, produced by us". The Citiq mark is set
    smaller than the space allowed for the client's, because on their report
    their brand leads. Either side may be absent (a client with no uploaded logo,
    or a missing asset) and the row still lays out.
    """
    from reportlab.lib.units import mm
    from reportlab.platypus import Spacer, Table, TableStyle

    from app.services.logo_service import build_logo_flowable

    client_cell = Spacer(1, 1)
    if logo is not None:
        client_flowable = build_logo_flowable(
            logo[0], logo[1], max_width_pt=52 * mm, max_height_pt=16 * mm
        )
        if client_flowable is not None:
            client_cell = client_flowable

    citiq_cell = _citiq_logo_flowable(max_width_pt=29 * mm, max_height_pt=8.5 * mm)
    if citiq_cell is None:
        citiq_cell = Spacer(1, 1)
    else:
        citiq_cell.hAlign = "RIGHT"

    row = Table([[client_cell, citiq_cell]], colWidths=[content_w * 0.6, content_w * 0.4])
    row.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return row


def _meta_style():
    """The cover's metadata strip: hairline above and below, nothing else."""
    from reportlab.lib import colors
    from reportlab.platypus import TableStyle

    return TableStyle([
        ("LINEABOVE", (0, 0), (-1, 0), 0.4, colors.HexColor("#E1E0D9")),
        ("LINEBELOW", (0, -1), (-1, -1), 0.4, colors.HexColor("#E1E0D9")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, 0), 8),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (0, -1), 0),
    ])


def _coverage_notes(story, styles, summary, content_w):
    """The caveats, stated on the face of the report rather than buried.

    A reader has to be able to see how much of the run measured the live web.
    The alternative (silently excluding responses) is how the 2026-07-31 Whip
    Around report came to show a confident 44% built partly on recollection.
    """
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Spacer, Table

    from app.services.report_charts import tracked_xml

    notes = []

    hollow = summary.get("hollow_citation_count", 0)
    if hollow:
        notes.append(
            f"<b>{hollow} hollow citation(s)</b> were excluded from every rate. The brand "
            f"appeared in those answers without substance behind the mention."
        )

    ungrounded = summary.get("ungrounded_count", 0)
    if ungrounded:
        by_platform = summary.get("ungrounded_by_platform") or {}
        detail = ", ".join(
            f"{_platform_label(name)}: {count}"
            for name, count in sorted(by_platform.items(), key=lambda kv: -kv[1])
        )
        notes.append(
            f"<b>{ungrounded} response(s) could not reach live web search</b> and were "
            f"answered from the model's training data ({detail}). They are excluded from "
            f"every figure above, because whether a model remembers a brand is not a "
            f"measurement of that brand's visibility."
        )

    partial = summary.get("partial_count", 0)
    if partial:
        by_platform = summary.get("partial_by_platform") or {}
        detail = ", ".join(
            f"{_platform_label(name)}: {count}"
            for name, count in sorted(by_platform.items(), key=lambda kv: -kv[1])
        )
        notes.append(
            f"<b>{partial} response(s) used their full web search allowance</b> ({detail}). "
            f"These cited live sources and are included in every figure; the later part of "
            f"each answer was written without further lookup."
        )

    if not notes:
        return

    story.append(Spacer(1, 1 * mm))
    body = [[Paragraph(tracked_xml("Data quality"), styles["eyebrow"])]]
    for note in notes:
        body.append([Paragraph(note, styles["small"])])
    tbl = Table(body, colWidths=[content_w])
    tbl.setStyle(_note_style())
    story.append(tbl)


def _note_style():
    from reportlab.lib import colors
    from reportlab.platypus import TableStyle

    return TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FAFAF9")),
        ("BOX", (0, 0), (-1, -1), 0.4, colors.HexColor("#E1E0D9")),
        ("LINEBEFORE", (0, 0), (0, -1), 2.2, colors.HexColor("#898781")),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
    ])


def _recommendation_block(rec: dict, styles, content_w):
    """One recommendation, tagged by priority with a coloured rule and a word.

    The colour never carries the priority on its own — the label spells it out —
    so the block still reads in grayscale and to a colour-blind reader.
    """
    from reportlab.lib import colors
    from reportlab.platypus import Paragraph, Table, TableStyle

    priority = rec.get("priority", "low")
    color = colors.HexColor(_PRIORITY_COLORS.get(priority, "#898781"))

    inner = [
        Paragraph(
            f"<b>{_clip(rec['title'], 160)}</b>", styles["h3"],
        ),
        Paragraph(
            f"{priority.upper()} PRIORITY &nbsp;&nbsp; "
            f"{_esc(rec['type'].replace('_', ' ').title())} &nbsp;&nbsp; {_esc(rec['status'])}",
            styles["small"],
        ),
    ]
    if rec.get("target_query"):
        inner.append(Paragraph(
            f"Target query: <i>{_clip(rec['target_query'], 160)}</i>", styles["small"],
        ))

    for key, value in (rec.get("content") or {}).items():
        label = _esc(key.replace("_", " ").capitalize())
        if isinstance(value, list) and value:
            inner.append(Paragraph(f"<b>{label}</b>", styles["body"]))
            for item in value[:10]:
                inner.append(Paragraph(f"- {_clip(item, 240)}", styles["quote"]))
        elif isinstance(value, str) and value.strip():
            inner.append(Paragraph(
                f"<b>{label}:</b> {_clip(value, _MAX_VALUE_CHARS)}", styles["body"],
            ))

    # splitInRow is what makes this safe: the block is a single table row (that
    # is how the priority rule is drawn down the left edge), and a row that
    # cannot split is a row that must fit on one page. A real content brief runs
    # longer than a page, and without this the whole report died with
    # "Flowable too large on page" rather than the recommendation simply
    # continuing overleaf.
    tbl = Table([[inner]], colWidths=[content_w], splitInRow=1, repeatRows=0)
    tbl.setStyle(TableStyle([
        ("LINEBEFORE", (0, 0), (0, -1), 2.2, color),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    # Flowables carry their own trailing space; a Spacer would have to live
    # inside a container, which is what caused the problem above.
    tbl.spaceAfter = 6
    return tbl


def _prompt_block(prompt: dict, styles, content_w) -> list:
    """One prompt: the question, a verdict table, then each platform's evidence."""
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import HRFlowable, KeepTogether, Paragraph, Spacer, Table

    out: list = []
    category = _esc((prompt.get("category") or "").capitalize())

    # An ungrounded result is not evidence the client was absent from the web,
    # so it must not appear in "Cited by" either way.
    grounded = [r for r in prompt["results"] if r.get("grounding_status") != "ungrounded"]
    cited_platforms = [_platform_label(r["platform"]) for r in grounded if r.get("client_cited")]

    header = [
        Paragraph(f"<b>{_clip(prompt['prompt_text'], _MAX_PROMPT_CHARS)}</b>", styles["h3"]),
        Paragraph(
            f"{category} prompt &nbsp;&nbsp; Cited by: "
            f"<b>{_esc(', '.join(cited_platforms)) if cited_platforms else 'no platform'}</b>",
            styles["small"],
        ),
    ]

    rows = [["Platform", "Cited", "Prominence", "Sentiment", "Opportunity"]]
    for r in prompt["results"]:
        if r.get("grounding_status") == "ungrounded":
            rows.append([_platform_label(r["platform"]), "-", "no live search", "-", "excluded"])
            continue
        rows.append([
            _platform_label(r["platform"]),
            "Yes" if r.get("client_cited") else "No",
            _esc((r.get("client_prominence") or "-").replace("_", " ")),
            _esc((r.get("client_sentiment") or "-").replace("_", " ")),
            _esc(_opportunity_cell(r)),
        ])
    tbl = Table(rows, colWidths=[content_w * 0.20, content_w * 0.12, content_w * 0.26,
                                 content_w * 0.20, content_w * 0.22])
    tbl.setStyle(_table_style(align_from=1))

    out.append(KeepTogether(header + [tbl]))

    for r in prompt["results"]:
        label = _platform_label(r["platform"])
        details = []
        if r.get("client_characterization"):
            details.append(f"<i>{_clip(r['client_characterization'], 280)}</i>")
        if r.get("reasoning"):
            details.append(f"Reasoning: {_clip(r['reasoning'], 280)}")
        if r.get("competitors_cited"):
            names = ", ".join(str(c.get("brand", "")) for c in r["competitors_cited"][:5])
            details.append(f"Competitors cited: {_clip(names, 160)}")
        if r.get("content_gaps"):
            gaps = "; ".join(str(g) for g in r["content_gaps"][:3])
            details.append(f"Gaps: {_clip(gaps, 220)}")
        if details:
            out.append(Paragraph(f"<b>{label}.</b> " + "<br/>".join(details), styles["small"]))

        # Ungrounded text is still shown (it is the evidence for why the row was
        # excluded) but never without the label, so a reader cannot mistake a
        # remembered answer for a live-web one.
        if r.get("grounding_status") == "ungrounded":
            out.append(Paragraph(
                f"<b>{label}.</b> Web search failed on every attempt, so this answer came "
                f"from the model's training data. Excluded from all figures in this report; "
                f"shown for reference only.",
                styles["small"],
            ))
        if r.get("raw_response"):
            out.append(Paragraph(_clip(r["raw_response"], _MAX_RESPONSE_CHARS), styles["answer"]))

    out.append(Spacer(1, 2 * mm))
    out.append(HRFlowable(width="100%", thickness=0.4,
                          color=colors.HexColor("#E1E0D9"), spaceAfter=4 * mm))
    return out
