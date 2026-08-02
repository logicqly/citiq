"""
LLM-based citation analyzer.

Uses the per-client configured platform + model (defaults to gpt-4o-mini on OpenAI).
On JSON parse / validation failure, retries once with corrective context.
Logs estimated cost on every call.
Persists results to the analyses table.
"""
import asyncio
import json

import structlog
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.prompt_template import build_prompt, build_retry_prompt
from app.analysis.schemas import AnalysisResult
from app.analysis.scoring import bucket_for_score, clamp_score
from app.config import settings
from app.models.analysis import (
    Analysis,
    CitationOpportunity,
    CitationType,
    Prominence,
    Sentiment,
)
from app.models.response import Response
from app.services.llm_pricing import estimate_cost, sum_tokens
from app.services.platform_rate_limiter import acquire_platform_token

logger = structlog.get_logger()

_TEMPERATURE = 0


class AnalysisParseError(Exception):
    """Raised when the LLM output cannot be parsed after all retries.

    Carries the estimated spend of the failed attempt(s) when the provider
    reported usage before the failure (an unparseable completion was still a
    billed completion), so the pipeline can record it on the run instead of
    silently dropping it. A timeout reports no usage — cost stays None and the
    attempt is only counted.

    ``kind`` types the failure for the run call log — "timeout" (no response
    within the ceiling), "parse" (completion arrived but was not valid JSON)
    or "validation" (JSON parsed but failed the schema) — so the exact drop
    reason is queryable per record instead of buried in this message.
    ``raw_snippet`` preserves the head of the unparseable completion for
    forensics (what did the model actually say?).
    """

    def __init__(
        self,
        message: str,
        *,
        cost_usd: float | None = None,
        tokens_used: int | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        kind: str = "parse",
        raw_snippet: str | None = None,
    ) -> None:
        super().__init__(message)
        self.cost_usd = cost_usd
        self.tokens_used = tokens_used
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.kind = kind
        self.raw_snippet = raw_snippet


class ResponseAnalyzer:
    def __init__(self, client_model_config: dict | None = None) -> None:
        from app.platforms.model_registry import get_analysis_config_for_client
        self._platform, self._model, self._custom_prompt = get_analysis_config_for_client(client_model_config)

    @property
    def platform(self) -> str:
        """The ANALYSIS provider (not the monitored engine) — for the call log."""
        return self._platform

    @property
    def model(self) -> str:
        return self._model

    async def analyze_and_persist(
        self,
        response: Response,
        client_brand: str,
        competitor_names: list[str],
        prompt_text: str,
        db: AsyncSession,
    ) -> Analysis:
        """
        Analyze a platform response for brand citations and persist the result.

        Args:
            response: the Response ORM object to analyze
            client_brand: name of the client's brand
            competitor_names: list of known competitor names
            prompt_text: the original prompt that generated this response
            db: async DB session (caller manages commit)
        """
        log = logger.bind(
            response_id=str(response.id),
            client_id=str(response.client_id),
            platform=response.platform.value,
        )

        result, cost_usd, tokens_used, input_tokens, output_tokens = (
            await self._call_with_retry(
                prompt_text=prompt_text,
                raw_response=response.raw_response,
                client_brand=client_brand,
                competitor_names=competitor_names,
                log=log,
            )
        )

        analysis = _to_orm(
            result,
            response,
            cost_usd=cost_usd,
            tokens_used=tokens_used,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        db.add(analysis)
        log.info(
            "analysis_persisted",
            opportunity_score=analysis.opportunity_score,
            citation_opportunity=analysis.citation_opportunity.value,
        )
        return analysis

    async def _call_with_retry(
        self,
        prompt_text: str,
        raw_response: str,
        client_brand: str,
        competitor_names: list[str],
        log,
    ) -> tuple[AnalysisResult, float | None, int | None, int | None, int | None]:
        """Call the LLM. On parse failure, retry once with corrective context.

        Returns (result, total estimated cost, total tokens, total input
        tokens, total output tokens) across attempts — all persisted on the
        Analysis row so run spend and per-phase token figures are complete
        (R5), with the input/output split the phase breakdown reports.
        """
        messages = [
            {
                "role": "user",
                "content": build_prompt(
                    original_prompt=prompt_text,
                    raw_response=raw_response,
                    client_brand=client_brand,
                    competitor_names=competitor_names,
                    custom_template=self._custom_prompt,
                ),
            }
        ]

        raw_text, input_tokens, output_tokens = await self._call_llm(messages, log)
        cost = estimate_cost(self._platform, self._model, input_tokens, output_tokens)
        tokens = sum_tokens(input_tokens, output_tokens)
        log.info(
            "analyzer_llm_call",
            model=self._model,
            attempt=1,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=round(cost, 6) if cost else None,
        )

        first_err_msg: str | None = None
        try:
            return _parse(raw_text), cost, tokens, input_tokens, output_tokens
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            # Capture before Python deletes the except-clause variable on exit
            first_err_msg = str(exc)[:300]
            log.warning("analysis_parse_failed_attempt_1", error=first_err_msg[:200])

        # ── Retry once with corrective context ────────────────────────────────
        # Build a NEW list — don't mutate in place so captured references stay clean
        retry_messages = messages + [
            {"role": "assistant", "content": raw_text},
            {
                "role": "user",
                "content": build_retry_prompt(
                    previous_response=raw_text[:500],
                    parse_error=first_err_msg or "unknown parse error",
                ),
            },
        ]

        try:
            raw_text2, input_tokens2, output_tokens2 = await self._call_llm(retry_messages, log)
        except AnalysisParseError as exc:
            # The retry call itself failed (timeout) — attempt 1's completion
            # was still billed; carry its cost out with the error.
            exc.cost_usd = cost
            exc.tokens_used = tokens
            exc.input_tokens = input_tokens
            exc.output_tokens = output_tokens
            raise
        cost2 = estimate_cost(self._platform, self._model, input_tokens2, output_tokens2)
        tokens2 = sum_tokens(input_tokens2, output_tokens2)
        log.info(
            "analyzer_llm_call",
            model=self._model,
            attempt=2,
            input_tokens=input_tokens2,
            output_tokens=output_tokens2,
            cost_usd=round(cost2, 6) if cost2 else None,
        )

        # Both attempts spent tokens — bill the sum even if attempt 1 failed.
        total_tokens = sum_tokens(tokens, tokens2)
        total_input = sum_tokens(input_tokens, input_tokens2)
        total_output = sum_tokens(output_tokens, output_tokens2)
        total_cost = (cost or 0.0) + (cost2 or 0.0) if (cost or cost2) else None
        try:
            return _parse(raw_text2), total_cost, total_tokens, total_input, total_output
        except (json.JSONDecodeError, ValidationError, ValueError) as second_err:
            log.error("analysis_parse_failed_attempt_2", error=str(second_err)[:200])
            # Both completions were billed by the provider even though neither
            # parsed — hand the known spend to the caller for run accounting.
            # kind + raw_snippet type the drop for the run call log: bad shape
            # vs bad JSON, plus what the model actually returned.
            raise AnalysisParseError(
                f"LLM output unparseable after 2 attempts: {second_err}",
                cost_usd=total_cost,
                tokens_used=total_tokens,
                input_tokens=total_input,
                output_tokens=total_output,
                kind="validation" if isinstance(second_err, ValidationError) else "parse",
                raw_snippet=raw_text2[:2000],
            ) from second_err

    async def _call_llm(
        self, messages: list[dict], log
    ) -> tuple[str, int | None, int | None]:
        # Pace analysis calls through the same per-platform limiter the monitoring
        # phase uses. The analysis fan-out previously bypassed it entirely, so a
        # large audit could burst every response at the analysis concurrency with
        # no pacing and trip the provider's per-minute cap. (Token acquisition is
        # outside the timeout below — waiting for a slot isn't a slow call.)
        await acquire_platform_token(self._platform)
        # Bound the call so one hung analysis request can't stall the run.
        # Analysis has its own ceiling (120s by default), decoupled from the
        # monitoring timeout: each attempt gets generous headroom because the
        # retry passes — not a longer wait — are the recovery mechanism.
        try:
            return await asyncio.wait_for(
                self._invoke_llm(messages),
                timeout=settings.analysis_call_timeout_seconds,
            )
        except TimeoutError as exc:
            log.error(
                "analyzer_llm_timeout",
                platform=self._platform,
                timeout_s=settings.analysis_call_timeout_seconds,
            )
            raise AnalysisParseError(
                f"analysis call timed out after {settings.analysis_call_timeout_seconds:g}s",
                kind="timeout",
            ) from exc

    async def _invoke_llm(
        self, messages: list[dict]
    ) -> tuple[str, int | None, int | None]:
        # max_tokens is intentionally sourced from settings, not hardcoded: a low
        # cap starves reasoning ("thinking") models — they spend the budget on
        # internal reasoning and return an empty completion, failing the analysis.
        max_tokens = settings.analysis_max_tokens
        # Instrumented transport: analysis calls feed the run call log's HTTP
        # capture when a capture context is armed (no-op otherwise).
        from app.services.call_capture import instrumented_httpx_client
        if self._platform == "anthropic":
            client = AsyncAnthropic(
                api_key=settings.anthropic_api_key,
                http_client=instrumented_httpx_client(),
            )
            resp = await client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                messages=messages,
            )
            content = resp.content[0].text if resp.content else ""
            input_tokens = resp.usage.input_tokens if resp.usage else None
            output_tokens = resp.usage.output_tokens if resp.usage else None
        elif self._platform == "gemini":
            from app.platforms.llm_client import gemini_chat
            content, input_tokens, output_tokens = await gemini_chat(
                self._model, messages, json_mode=True, max_tokens=max_tokens
            )
        elif self._platform == "perplexity":
            from app.platforms.llm_client import perplexity_chat
            content, input_tokens, output_tokens = await perplexity_chat(
                self._model, messages, temperature=_TEMPERATURE, max_tokens=max_tokens
            )
        else:
            from app.platforms.model_registry import (
                model_supports_json_object_mode,
                model_supports_temperature,
            )
            client = AsyncOpenAI(
                api_key=settings.openai_api_key,
                http_client=instrumented_httpx_client(),
            )
            kwargs: dict = {"model": self._model, "messages": messages}
            if model_supports_temperature(self._model):
                kwargs["temperature"] = _TEMPERATURE
            if model_supports_json_object_mode(self._model):
                kwargs["response_format"] = {"type": "json_object"}
            resp = await client.chat.completions.create(**kwargs)
            content = resp.choices[0].message.content or ""
            input_tokens = resp.usage.prompt_tokens if resp.usage else None
            output_tokens = resp.usage.completion_tokens if resp.usage else None
        return content, input_tokens, output_tokens


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse(raw_text: str) -> AnalysisResult:
    """Parse and validate LLM output, tolerating the two ways it deviates.

    Both were measured on the 2026-07-31 Whip Around run, where twelve analyses
    failed to parse and cost sixteen extra LLM calls to recover. Every failure
    was one of these, and neither is worth a retry:

      1. A valid object followed by a stray closing brace:
             {"client_cited": true, ...}\\n}
         Ten of the twelve. `raw_decode` reads the first complete value and
         ignores whatever trails it, which is exactly the right behaviour when
         a model overshoots its own closing token.

      2. The object wrapped in a single-element list: [{...}]. Two of twelve.

    Anything past those still raises, so a genuinely broken completion is still
    retried rather than silently coerced into a wrong analysis.
    """
    text = raw_text.strip()
    if text.startswith("```"):
        # Strip ```json ... ``` fences
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]) if len(lines) > 2 else text

    # raw_decode instead of loads: stops at the end of the first complete JSON
    # value rather than demanding the whole string be consumed.
    data, _end = json.JSONDecoder().raw_decode(text.lstrip())

    if isinstance(data, list):
        # A one-item list is the model wrapping its answer; anything else is a
        # real shape error and should fail validation below.
        if len(data) == 1 and isinstance(data[0], dict):
            data = data[0]

    return AnalysisResult.model_validate(data)


def _to_orm(
    result: AnalysisResult,
    response: Response,
    cost_usd: float | None = None,
    tokens_used: int | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> Analysis:
    """Map a validated AnalysisResult to an Analysis ORM object."""
    # Filter out competitors the LLM listed but then marked as "not_cited" —
    # they shouldn't be in the cited list at all.
    cited_competitors = [
        c.model_dump()
        for c in result.competitors_cited
        if c.prominence != "not_cited"
    ]
    client_cited, citation_type = _reconcile_citation(result)
    # Score is what the model now emits; the bucket is derived from it so every
    # existing consumer keeps reading the enum it always read. When only a
    # legacy bucket came back (a stale per-client custom prompt), the score
    # column stays NULL — ranking falls back to the bucket rather than storing
    # a number the model never produced.
    score = clamp_score(result.citation_opportunity_score)
    if score is not None:
        opportunity = bucket_for_score(score)
    else:
        opportunity = CitationOpportunity(result.citation_opportunity or "medium")
    return Analysis(
        client_id=response.client_id,
        response_id=response.id,
        cost_usd=cost_usd,
        tokens_used=tokens_used,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        client_cited=client_cited,
        client_prominence=Prominence(result.client_prominence),
        client_sentiment=Sentiment(result.client_sentiment),
        citation_type=citation_type,
        client_characterization=result.client_characterization,
        competitors_cited=cited_competitors,
        content_gaps=result.content_gaps,
        citation_opportunity=opportunity,
        opportunity_score=score,
        reasoning=result.reasoning,
    )


def _reconcile_citation(result: AnalysisResult) -> tuple[bool, CitationType]:
    """Derive a coherent (client_cited, citation_type) pair from the model output.

    Product rule: if the brand appears in the response in ANY form it counts as
    cited. The only "not cited" (blank) case is when the brand is absent.

    The model sometimes disagrees with itself, so we reconcile:
    - hollow: the name appears by definition, so it is ALWAYS cited — even when
      the model contradictorily also set client_cited=false (this was the cause
      of cited brands showing up blank).
    - brand absent (client_cited=false, not hollow): not_cited wins; ignore any
      substantive label the model may have returned.
    - cited but the model typed it not_cited: fall back to a neutral 'mentioned'.
    """
    if result.citation_type == "hollow":
        return True, CitationType.hollow
    if not result.client_cited:
        return False, CitationType.not_cited
    if result.citation_type == "not_cited":
        return True, CitationType.mentioned
    return True, CitationType(result.citation_type)
