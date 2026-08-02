"""
Generation orchestrator — coordinates all generators after a run's analysis is complete.

Called by the pipeline after analyze_responses completes. Failure is non-fatal:
if generation errors, the run still completes.
"""
import asyncio
import json
import time
import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.models.analysis import Analysis
from app.models.client import Client
from app.models.client_knowledge_base import ClientKnowledgeBase
from app.models.prompt import Prompt
from app.models.recommendation import RecommendationHistory
from app.models.response import Response
from app.models.run import GenerationStatus, Run
from app.models.run_call import RunCallOutcome, RunCallPhase
from app.services.call_capture import capture_calls, drain_exchanges
from app.services.call_log import http_status_of, record_call

logger = structlog.get_logger()


# Summary keys that count as "recommendations were actually produced".
_PRODUCED_KEYS = (
    "content_briefs",
    "schema_recs",
    "llms_txt_recs",
    "authority_building_recs",
)


def _generation_outcome(exc: BaseException) -> RunCallOutcome:
    """Type a generation failure for the run call log."""
    if isinstance(exc, TimeoutError):
        return RunCallOutcome.timeout
    if isinstance(exc, json.JSONDecodeError):
        return RunCallOutcome.parse_error
    return RunCallOutcome.http_error


async def generate_recommendations(
    run_id: uuid.UUID,
    client_id: uuid.UUID,
    session_factory: async_sessionmaker,
) -> dict:
    """
    Top-level entry point called after analysis completes.

    Returns summary: {content_briefs, schema_recs, llms_txt_recs, skipped, errors}
    """
    log = logger.bind(run_id=str(run_id), client_id=str(client_id))
    log.info("generation_start")

    summary = {
        "content_briefs": 0,
        "schema_recs": 0,
        "llms_txt_recs": 0,
        "authority_building_recs": 0,
        "skipped": 0,
        "errors": 0,
    }

    if not settings.generation_enabled:
        log.info("generation_disabled_by_config")
        async with session_factory() as db:
            async with db.begin():
                run = (await db.execute(select(Run).where(Run.id == run_id))).scalar_one()
                run.generation_status = GenerationStatus.skipped
        summary["skipped"] = 1
        return summary

    # ── Mark generation as running ────────────────────────────────────────────
    async with session_factory() as db:
        async with db.begin():
            run = (await db.execute(select(Run).where(Run.id == run_id))).scalar_one()
            run.generation_status = GenerationStatus.running

    try:
        # ── Load all analyses for this run (with eager-loaded response + prompt) ─
        async with session_factory() as db:
            rows = (
                await db.execute(
                    select(Analysis, Response, Prompt)
                    .join(Response, Analysis.response_id == Response.id)
                    .join(Prompt, Response.prompt_id == Prompt.id)
                    .where(Response.run_id == run_id)
                )
            ).all()

        if not rows:
            log.info("generation_no_analyses")
            async with session_factory() as db:
                async with db.begin():
                    run = (await db.execute(select(Run).where(Run.id == run_id))).scalar_one()
                    run.generation_status = GenerationStatus.skipped
            return summary

        # ── Load client + knowledge base ──────────────────────────────────────
        async with session_factory() as db:
            client = (await db.execute(select(Client).where(Client.id == client_id))).scalar_one()
            kb = (
                await db.execute(
                    select(ClientKnowledgeBase).where(ClientKnowledgeBase.client_id == client_id)
                )
            ).scalar_one_or_none()

        # ── Refuse to generate without knowledge-base content ─────────────────
        # An empty KB used to flow straight into the prompts as literal
        # "Not provided"/"{}" placeholders and the LLM generated plausible
        # generic recommendations anyway — a silent failure that shipped weeks
        # of generic briefs. A blank KB row is auto-created with every client,
        # so this checks content, not row existence. Skipping (not failing)
        # keeps the run's own status intact and shows up in the UI as
        # generation_status = skipped.
        from app.generation.kb_context import kb_has_content
        if not kb_has_content(kb):
            log.warning(
                "generation_skipped_empty_knowledge_base",
                reason="knowledge base has no content; refusing to generate generic recommendations",
            )
            async with session_factory() as db:
                async with db.begin():
                    run = (await db.execute(select(Run).where(Run.id == run_id))).scalar_one()
                    run.generation_status = GenerationStatus.skipped
            summary["skipped"] = 1
            return summary

        # ── Single-call engine (the 2026-07-25 redesign) ──────────────────────
        # One call sees the whole run at once and decides for itself what to
        # produce, having been shown what already exists on the client's site.
        # The per-analysis fan-out below is the rollback path, kept for one
        # release behind RECOMMENDATION_MODE=legacy.
        if settings.recommendation_mode == "single_call":
            from app.generation.single_call import (
                generate_recommendations_single_call,
            )
            summary = await generate_recommendations_single_call(
                run_id, client_id, session_factory
            )
            produced = sum(summary[k] for k in _PRODUCED_KEYS)
            status = (
                GenerationStatus.failed
                if summary["errors"] and not produced
                else GenerationStatus.completed
            )
            async with session_factory() as db:
                async with db.begin():
                    run = (
                        await db.execute(select(Run).where(Run.id == run_id))
                    ).scalar_one()
                    run.generation_status = status
            log.info("generation_complete", mode="single_call", **summary)
            return summary

        # ── Fan-out content briefs + schema recs per analysis (legacy mode) ───
        sem = asyncio.Semaphore(settings.generation_max_concurrent)

        # Resolved once for the call log: the provider/model failures were sent
        # to (successful recs carry their own generation_model).
        from app.platforms.model_registry import get_recommendation_config_for_client
        rec_platform, rec_model, _ = get_recommendation_config_for_client(
            client.platform_model_config
        )

        async def _log_gen_call(
            outcome: RunCallOutcome,
            *,
            rec_type: str,
            prompt_id=None,
            response_id=None,
            error: BaseException | None = None,
            latency_ms: int | None = None,
            model: str | None = None,
            tokens_used: int | None = None,
            input_tokens: int | None = None,
            output_tokens: int | None = None,
            cost_usd: float | None = None,
        ) -> None:
            # Raw HTTP exchanges persist for failures (and for everything when
            # RUN_LOG_CAPTURE_ALL); drained either way so the next generator
            # in this task never inherits stale traffic.
            exchanges = drain_exchanges()
            if outcome == RunCallOutcome.success and not settings.run_log_capture_all:
                exchanges = None
            await record_call(
                session_factory,
                exchanges=exchanges,
                run_id=run_id,
                client_id=client_id,
                phase=RunCallPhase.generation.value,
                outcome=outcome.value,
                rec_type=rec_type,
                prompt_id=prompt_id,
                response_id=response_id,
                platform=rec_platform,
                model=model or rec_model,
                error_type=type(error).__name__ if error else None,
                error_detail=str(error)[:500] if error else None,
                http_status=http_status_of(error) if error else None,
                latency_ms=latency_ms,
                tokens_used=tokens_used,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
            )

        async def _generate_for_analysis(
            analysis: Analysis,
            response: Response,
            prompt: Prompt,
        ) -> tuple[int, int, int]:
            cb = sr = 0
            async with sem:
                async with session_factory() as db:
                    async with db.begin():
                        # Reload fresh ORM objects in this session
                        fresh_analysis = (
                            await db.execute(select(Analysis).where(Analysis.id == analysis.id))
                        ).scalar_one()
                        fresh_response = (
                            await db.execute(select(Response).where(Response.id == response.id))
                        ).scalar_one()
                        # Attach response to analysis for relationship access
                        fresh_analysis.response = fresh_response

                        platform = fresh_response.platform.value

                        if settings.generation_content_brief_enabled:
                            from app.generation.content_brief_generator import (
                                generate_content_brief,
                            )
                            started = time.monotonic()
                            with capture_calls():
                                try:
                                    rec = await generate_content_brief(
                                        session=db,
                                        analysis=fresh_analysis,
                                        client=client,
                                        kb=kb,
                                        prompt_text=prompt.text,
                                        raw_response=fresh_response.raw_response,
                                        platform=platform,
                                        client_model_config=client.platform_model_config,
                                    )
                                    if rec:
                                        await db.flush()
                                        await _add_history(db, rec, old_status=None, actor="system")
                                        cb = 1
                                        await _log_gen_call(
                                            RunCallOutcome.success,
                                            rec_type="content_brief",
                                            prompt_id=prompt.id,
                                            response_id=response.id,
                                            latency_ms=int((time.monotonic() - started) * 1000),
                                            model=rec.generation_model,
                                            tokens_used=rec.generation_tokens,
                                            input_tokens=rec.generation_input_tokens,
                                            output_tokens=rec.generation_output_tokens,
                                            cost_usd=rec.generation_cost_usd,
                                        )
                                except Exception as exc:
                                    log.error(
                                        "content_brief_generation_failed",
                                        analysis_id=str(analysis.id),
                                        error=str(exc),
                                    )
                                    await _log_gen_call(
                                        _generation_outcome(exc),
                                        rec_type="content_brief",
                                        prompt_id=prompt.id,
                                        response_id=response.id,
                                        error=exc,
                                        latency_ms=int((time.monotonic() - started) * 1000),
                                    )

                        if settings.generation_schema_enabled:
                            from app.generation.schema_generator import (
                                generate_schema_recommendation,
                            )
                            started = time.monotonic()
                            with capture_calls():
                                try:
                                    rec = await generate_schema_recommendation(
                                        session=db,
                                        analysis=fresh_analysis,
                                        client=client,
                                        kb=kb,
                                        prompt_text=prompt.text,
                                        platform=platform,
                                    )
                                    if rec:
                                        await db.flush()
                                        await _add_history(db, rec, old_status=None, actor="system")
                                        sr = 1
                                        await _log_gen_call(
                                            RunCallOutcome.success,
                                            rec_type="schema",
                                            prompt_id=prompt.id,
                                            response_id=response.id,
                                            latency_ms=int((time.monotonic() - started) * 1000),
                                            model=rec.generation_model,
                                            tokens_used=rec.generation_tokens,
                                            input_tokens=rec.generation_input_tokens,
                                            output_tokens=rec.generation_output_tokens,
                                            cost_usd=rec.generation_cost_usd,
                                        )
                                except Exception as exc:
                                    log.error(
                                        "schema_generation_failed",
                                        analysis_id=str(analysis.id),
                                        error=str(exc),
                                    )
                                    await _log_gen_call(
                                        _generation_outcome(exc),
                                        rec_type="schema",
                                        prompt_id=prompt.id,
                                        response_id=response.id,
                                        error=exc,
                                        latency_ms=int((time.monotonic() - started) * 1000),
                                    )
            return cb, sr, 0

        tasks = [_generate_for_analysis(a, r, p) for a, r, p in rows]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for r in results:
            if isinstance(r, BaseException):
                summary["errors"] += 1
                log.error("analysis_generation_task_failed", error=str(r))
            else:
                cb, sr, _ = r
                summary["content_briefs"] += cb
                summary["schema_recs"] += sr

        # ── llms.txt: once per run ────────────────────────────────────────────
        if settings.generation_llms_txt_enabled:
            async with session_factory() as db:
                async with db.begin():
                    analyses_only = [a for a, _, _ in rows]
                    # Reload with response relationship
                    fresh_analyses = []
                    for a in analyses_only:
                        fa = (await db.execute(select(Analysis).where(Analysis.id == a.id))).scalar_one()
                        fr = (await db.execute(select(Response).where(Response.id == a.response_id))).scalar_one()
                        fa.response = fr
                        fresh_analyses.append(fa)

                    from app.generation.llms_txt_generator import generate_llms_txt_recommendation
                    started = time.monotonic()
                    with capture_calls():
                        try:
                            rec = await generate_llms_txt_recommendation(
                                session=db,
                                run_id=run_id,
                                client=client,
                                kb=kb,
                                analyses=fresh_analyses,
                            )
                            if rec:
                                await db.flush()
                                await _add_history(db, rec, old_status=None, actor="system")
                                summary["llms_txt_recs"] = 1
                                await _log_gen_call(
                                    RunCallOutcome.success,
                                    rec_type="llms_txt",
                                    latency_ms=int((time.monotonic() - started) * 1000),
                                    model=rec.generation_model,
                                    tokens_used=rec.generation_tokens,
                                    input_tokens=rec.generation_input_tokens,
                                    output_tokens=rec.generation_output_tokens,
                                    cost_usd=rec.generation_cost_usd,
                                )
                        except Exception as exc:
                            log.error("llms_txt_generation_failed", error=str(exc))
                            summary["errors"] += 1
                            await _log_gen_call(
                                _generation_outcome(exc),
                                rec_type="llms_txt",
                                error=exc,
                                latency_ms=int((time.monotonic() - started) * 1000),
                            )

        # ── authority building: once per run ──────────────────────────────────
        if settings.generation_authority_building_enabled:
            async with session_factory() as db:
                async with db.begin():
                    analyses_only = [a for a, _, _ in rows]
                    # Reload with response relationship
                    fresh_analyses = []
                    for a in analyses_only:
                        fa = (await db.execute(select(Analysis).where(Analysis.id == a.id))).scalar_one()
                        fr = (await db.execute(select(Response).where(Response.id == a.response_id))).scalar_one()
                        fa.response = fr
                        fresh_analyses.append(fa)

                    from app.generation.authority_building_generator import (
                        generate_authority_building_recommendation,
                    )
                    started = time.monotonic()
                    with capture_calls():
                        try:
                            rec = await generate_authority_building_recommendation(
                                session=db,
                                run_id=run_id,
                                client=client,
                                kb=kb,
                                analyses=fresh_analyses,
                            )
                            if rec:
                                await db.flush()
                                await _add_history(db, rec, old_status=None, actor="system")
                                summary["authority_building_recs"] = 1
                                await _log_gen_call(
                                    RunCallOutcome.success,
                                    rec_type="authority_building",
                                    latency_ms=int((time.monotonic() - started) * 1000),
                                    model=rec.generation_model,
                                    tokens_used=rec.generation_tokens,
                                    input_tokens=rec.generation_input_tokens,
                                    output_tokens=rec.generation_output_tokens,
                                    cost_usd=rec.generation_cost_usd,
                                )
                        except Exception as exc:
                            log.error("authority_building_generation_failed", error=str(exc))
                            summary["errors"] += 1
                            await _log_gen_call(
                                _generation_outcome(exc),
                                rec_type="authority_building",
                                error=exc,
                                latency_ms=int((time.monotonic() - started) * 1000),
                            )

        # ── Mark generation as completed ──────────────────────────────────────
        async with session_factory() as db:
            async with db.begin():
                run = (await db.execute(select(Run).where(Run.id == run_id))).scalar_one()
                run.generation_status = GenerationStatus.completed

        log.info("generation_complete", **summary)

    except Exception as exc:
        log.error("generation_fatal_error", error=str(exc))
        summary["errors"] += 1
        async with session_factory() as db:
            async with db.begin():
                run = (await db.execute(select(Run).where(Run.id == run_id))).scalar_one()
                run.generation_status = GenerationStatus.failed

    return summary


async def _add_history(db: AsyncSession, rec, old_status, actor: str, notes: str | None = None) -> None:
    """Add initial history entry for a newly created recommendation."""
    entry = RecommendationHistory(
        recommendation_id=rec.id,
        client_id=rec.client_id,
        old_status=old_status,
        new_status=rec.status.value,
        actor=actor,
        notes=notes,
    )
    db.add(entry)
