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
import io
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.aggregator import compute_run_summary, get_prompt_details


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
        },
        "platform_stats": [
            {
                "platform": ps.platform.value,
                "model_used": ps.model_used,
                "total_responses": ps.total_responses,
                "cited_count": ps.cited_count,
                "citation_rate": ps.citation_rate,
                "prominence_breakdown": ps.prominence_breakdown,
            }
            for ps in summary.platform_stats
        ],
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
    }

    if include_internal and summary.platform_errors:
        report["platform_errors"] = summary.platform_errors

    return report


def _opportunity_cell(result: dict) -> str:
    """The Opportunity column: the 1.0-5.0 score, with the bucket as context.

    Analyses written before scoring existed have no score and still show their
    bucket alone, so historical reports keep rendering.
    """
    bucket = result.get("citation_opportunity") or "—"
    score = result.get("opportunity_score")
    if score is None:
        return str(bucket)
    return f"{float(score):.1f} ({bucket})"


def build_pdf(
    report: dict,
    client_name: str,
    logo: tuple[bytes, str] | None = None,
) -> bytes:
    """Render report dict to PDF bytes using reportlab.

    `logo` is the client's brand logo as (bytes, mime) — see
    logo_service.fetch_client_logo. A logo that cannot be rendered is skipped
    rather than failing the report.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
    )

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
    )

    styles = getSampleStyleSheet()
    h1 = styles["h1"]
    h2 = ParagraphStyle("h2", parent=styles["h2"], spaceAfter=4, spaceBefore=8)
    h3 = ParagraphStyle("h3", parent=styles["h3"], spaceAfter=2, spaceBefore=6, fontSize=10)
    body = styles["BodyText"]
    small = ParagraphStyle("small", parent=body, fontSize=8, textColor=colors.grey)
    mono = ParagraphStyle("mono", parent=body, fontName="Courier", fontSize=8, leading=11)

    INDIGO = colors.HexColor("#4F46E5")
    LIGHT_ROW = colors.HexColor("#F5F5F5")

    run = report["run"]
    summary = report["summary"]

    story = []

    # ── Cover ────────────────────────────────────────────────────────────────
    # The client's own logo leads the cover when they have one, so the report
    # reads as theirs before the title does.
    if logo is not None:
        from app.services.logo_service import build_logo_flowable

        logo_flowable = build_logo_flowable(
            logo[0], logo[1], max_width_pt=60 * mm, max_height_pt=20 * mm
        )
        if logo_flowable is not None:
            story.append(logo_flowable)
            story.append(Spacer(1, 6 * mm))

    story.append(Paragraph("GEO Monitoring Report", h1))
    story.append(Paragraph(f"<b>Client:</b> {client_name}", body))
    story.append(Paragraph(f"<b>Run:</b> {run.get('display_id') or run['id']}", body))
    story.append(Paragraph(f"<b>Date:</b> {run['created_at'][:10]}", body))
    story.append(Paragraph(
        f"<b>Generated:</b> {report['generated_at'][:19].replace('T', ' ')} UTC", small
    ))
    story.append(Spacer(1, 6 * mm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.lightgrey))
    story.append(Spacer(1, 4 * mm))

    # ── Executive Summary ────────────────────────────────────────────────────
    story.append(Paragraph("Executive Summary", h2))
    citation_pct = round(summary["overall_citation_rate"] * 100, 1)
    story.append(Paragraph(
        f"Overall citation rate: <b>{citation_pct}%</b> across {summary['total_analyses']} AI responses "
        f"<font color='grey'>(hollow citations excluded)</font>.",
        body,
    ))
    quality = summary.get("citation_quality") or {}
    if quality.get("effective_total"):
        story.append(Paragraph(
            "Citation quality: "
            f"<b>{round(quality['recommended_pct'] * 100)}%</b> recommended · "
            f"<b>{round(quality['mentioned_pct'] * 100)}%</b> neutral · "
            f"<b>{round(quality['negative_pct'] * 100)}%</b> negative.",
            body,
        ))
    hollow = summary.get("hollow_citation_count", 0)
    if hollow:
        story.append(Paragraph(
            f"<font color='grey'>{hollow} hollow citation(s) excluded from the rate above.</font>",
            small,
        ))

    # Responses where every web search failed and the model answered from
    # training data. Stated on the face of the report, not buried: a reader has
    # to be able to see that part of the run did not measure the live web. The
    # alternative (silently excluding them) is how the 2026-07-31 Whip Around
    # report came to show a confident 44% built partly on recollection.
    ungrounded = summary.get("ungrounded_count", 0)
    if ungrounded:
        by_platform = summary.get("ungrounded_by_platform") or {}
        detail = ", ".join(
            f"{name.capitalize()}: {count}"
            for name, count in sorted(by_platform.items(), key=lambda kv: -kv[1])
        )
        story.append(Spacer(1, 2 * mm))
        story.append(Paragraph(
            f"<b>Coverage note.</b> {ungrounded} response(s) could not be answered "
            f"from live web search and were excluded from every figure above "
            f"({detail}). The rates shown reflect the remaining "
            f"{summary['total_analyses']} responses.",
            small,
        ))

    # Responses that reached the live web and then ran out of search budget.
    # Deliberately worded to say they ARE included: the client asked for these
    # counted in the rate and flagged, not excluded (2026-08-01), because a
    # response citing twenty sources before running out is still a live-web
    # answer. The point of printing it is that a reader can judge how much of
    # the run was finished without lookup instead of having to assume none.
    partial = summary.get("partial_count", 0)
    if partial:
        by_platform = summary.get("partial_by_platform") or {}
        detail = ", ".join(
            f"{name.capitalize()}: {count}"
            for name, count in sorted(by_platform.items(), key=lambda kv: -kv[1])
        )
        story.append(Spacer(1, 2 * mm))
        story.append(Paragraph(
            f"<b>Search depth.</b> {partial} response(s) used the full web search "
            f"allowance available per answer ({detail}). These cited live sources "
            f"and are included in every figure above; the later part of each was "
            f"written without further lookup.",
            small,
        ))
    story.append(Spacer(1, 4 * mm))

    # ── Platform Breakdown ────────────────────────────────────────────────────
    if report["platform_stats"]:
        story.append(Paragraph("Platform Breakdown", h2))
        data = [["Platform", "Model", "Cited", "Total", "Citation Rate"]]
        for ps in report["platform_stats"]:
            data.append([
                ps["platform"].capitalize(),
                ps["model_used"],
                str(ps["cited_count"]),
                str(ps["total_responses"]),
                f"{ps['citation_rate'] * 100:.1f}%",
            ])
        tbl = Table(data, colWidths=[35 * mm, 55 * mm, 20 * mm, 20 * mm, 30 * mm])
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), INDIGO),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_ROW]),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.lightgrey),
            ("ALIGN", (2, 0), (-1, -1), "CENTER"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(tbl)
        story.append(Spacer(1, 4 * mm))

    # ── Competitor Share of Voice ─────────────────────────────────────────────
    if report["competitor_stats"]:
        story.append(Paragraph("Competitor Share of Voice", h2))
        data = [["Competitor", "Cited", "Share of Voice"]]
        for cs in report["competitor_stats"]:
            data.append([cs["brand"], str(cs["cited_count"]), f"{cs['share_of_voice'] * 100:.1f}%"])
        tbl = Table(data, colWidths=[80 * mm, 30 * mm, 40 * mm])
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), INDIGO),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_ROW]),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.lightgrey),
            ("ALIGN", (1, 0), (-1, -1), "CENTER"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(tbl)
        story.append(Spacer(1, 4 * mm))

    # ── Recommendations ───────────────────────────────────────────────────────
    if report.get("recommendations"):
        story.append(Paragraph("Recommendations", h2))
        PRIORITY_COLOR = {"high": colors.HexColor("#DC2626"), "medium": colors.HexColor("#D97706"), "low": colors.grey}
        for rec in report["recommendations"]:
            story.append(Paragraph(
                f"<b>{rec['title']}</b>  "
                f"<font color='grey'>[{rec['type'].replace('_', ' ').title()} · {rec['status']}]</font>",
                h3,
            ))
            if rec.get("target_query"):
                story.append(Paragraph(f"Query: <i>{rec['target_query']}</i>", small))
            content = rec.get("content", {})
            for key, value in content.items():
                label = key.replace("_", " ").capitalize()
                if isinstance(value, list):
                    story.append(Paragraph(f"<b>{label}:</b>", body))
                    for item in value[:10]:
                        story.append(Paragraph(f"• {item}", body))
                elif isinstance(value, str):
                    text = value[:500] + ("…" if len(value) > 500 else "")
                    story.append(Paragraph(f"<b>{label}:</b> {text}", body))
            story.append(Spacer(1, 3 * mm))
        story.append(Spacer(1, 2 * mm))

    # ── Prompt-Level Analysis ─────────────────────────────────────────────────
    story.append(Paragraph("Prompt-Level Analysis", h2))

    SENTIMENT_COLOR = {
        "positive": colors.HexColor("#16A34A"),
        "negative": colors.HexColor("#DC2626"),
        "neutral": colors.grey,
        "not_cited": colors.lightgrey,
    }

    for p in report["prompts"]:
        # Prompt header
        category = p["category"].capitalize()
        prompt_text = p["prompt_text"]
        if len(prompt_text) > 200:
            prompt_text = prompt_text[:200] + "…"
        # An ungrounded result is not evidence the client was absent from the
        # web, so it must not appear in "Cited by" either way.
        grounded_results = [
            r for r in p["results"] if r.get("grounding_status") != "ungrounded"
        ]
        cited_platforms = [r["platform"] for r in grounded_results if r.get("client_cited")]
        story.append(Paragraph(
            f"<b>[{category}]</b> {prompt_text}", body,
        ))
        story.append(Paragraph(
            f"Cited by: <b>{', '.join(cited_platforms) if cited_platforms else 'None'}</b>",
            small,
        ))

        # Per-platform table. An ungrounded row keeps its place (the reader
        # should see the platform was asked) but reports no verdict, because it
        # never consulted the web and its answer came out of training data.
        tbl_data = [["Platform", "Cited", "Prominence", "Sentiment", "Opportunity"]]
        for r in p["results"]:
            if r.get("grounding_status") == "ungrounded":
                tbl_data.append([
                    r["platform"].capitalize(),
                    "-", "no live search", "-", "excluded",
                ])
                continue
            tbl_data.append([
                r["platform"].capitalize(),
                "✓" if r.get("client_cited") else "✗",
                (r.get("client_prominence") or "—").replace("_", " "),
                (r.get("client_sentiment") or "—").replace("_", " "),
                _opportunity_cell(r),
            ])
        mini_tbl = Table(tbl_data, colWidths=[32 * mm, 14 * mm, 30 * mm, 24 * mm, 24 * mm])
        mini_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E0E7FF")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_ROW]),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.lightgrey),
            ("ALIGN", (1, 0), (1, -1), "CENTER"),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        story.append(mini_tbl)

        # Per-platform detail: characterization, reasoning, gaps, competitors
        for r in p["results"]:
            details = []
            if r.get("client_characterization"):
                details.append(f"<i>&ldquo;{r['client_characterization'][:300]}&rdquo;</i>")
            if r.get("reasoning"):
                details.append(f"Reasoning: {r['reasoning'][:300]}")
            if r.get("competitors_cited"):
                names = ", ".join(c.get("brand", "") for c in r["competitors_cited"][:5])
                details.append(f"Competitors cited: {names}")
            if r.get("content_gaps"):
                gaps = "; ".join(str(g) for g in r["content_gaps"][:3])
                details.append(f"Gaps: {gaps}")
            if details:
                story.append(Paragraph(
                    f"<b>{r['platform'].capitalize()}:</b> " + " | ".join(details),
                    small,
                ))

            # Raw response (truncated). Ungrounded text is still shown — it is
            # the evidence for why the row was excluded — but never without the
            # label, so a reader can never mistake a remembered answer for a
            # live-web one.
            if r.get("grounding_status") == "ungrounded":
                story.append(Paragraph(
                    f"<b>{r['platform'].capitalize()}:</b> web search failed on every "
                    f"attempt, so this answer came from the model's training data. "
                    f"Excluded from all figures in this report; shown for reference only.",
                    small,
                ))
            if r.get("raw_response"):
                resp_text = r["raw_response"][:600].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                if len(r["raw_response"]) > 600:
                    resp_text += "…"
                story.append(Paragraph(resp_text, mono))

        story.append(Spacer(1, 4 * mm))

    doc.build(story)
    return buf.getvalue()
