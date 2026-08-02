from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _to_asyncpg(v: str) -> str:
    """Convert any sync Postgres URL scheme to the asyncpg driver scheme."""
    if not isinstance(v, str) or not v:
        return v
    if v.startswith("postgresql+asyncpg://"):
        return v
    if v.startswith("postgresql://"):
        return v.replace("postgresql://", "postgresql+asyncpg://", 1)
    if v.startswith("postgres://"):
        return v.replace("postgres://", "postgresql+asyncpg://", 1)
    return v


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── Service identity ──────────────────────────────────────────────────────
    # Controls which DB engines are created and which routes are mounted.
    # Values: "combined" (default, local dev) | "admin" | "client" | "worker"
    service_role: str = "combined"

    # ── Database connections ──────────────────────────────────────────────────
    # DATABASE_URL  — superuser, used for Alembic migrations (admin-api only)
    # DATABASE_URL_ADMIN — citiq_admin role, BYPASSRLS (admin-api + worker)
    # DATABASE_URL_APP   — citiq_app role, subject to RLS (client-api only)
    #
    # In combined/local mode, all three can be the same URL.
    # In production, each service only receives the credential it needs.
    database_url: str = "postgresql+asyncpg://citiq:citiq_dev@localhost:5432/citiq"
    database_url_admin: str = ""  # Falls back to database_url if empty
    database_url_app: str = ""    # Falls back to database_url if empty

    # API Keys — never logged, never serialized
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    perplexity_api_key: str = ""
    gemini_api_key: str = ""

    # App config
    log_level: str = "INFO"
    # Orchestration: max simultaneous in-flight API calls per platform.
    # 25 stays within all configured per-minute rate limits for 50 prompts.
    max_concurrent_per_platform: int = 25
    # Analysis: max simultaneous gpt-4o-mini calls for citation analysis.
    # 20 concurrent × ~3 s avg = ~30 s for 200 responses (within OpenAI 500/min).
    analysis_max_concurrent: int = 20
    # Analysis: max output tokens per citation-analysis call. Must be generous
    # enough for reasoning ("thinking") models — they spend part of this budget
    # on internal reasoning before emitting the JSON, so a low cap makes them
    # return an empty completion and the analysis fails. Configurable so ops can
    # raise it for heavier thinking models without a redeploy.
    analysis_max_tokens: int = 4096
    # Hard per-call ceiling for any single upstream LLM request (monitoring OR
    # generation). One hung/slow call must not stall an entire run, so the call
    # is abandoned and counted as failed once this elapses.
    platform_call_timeout_seconds: float = 90.0
    # Ceiling for a single citation-analysis attempt. Decoupled from the
    # monitoring ceiling per the 2026-07-25 client agreement: 120s per attempt,
    # generous because a failed attempt is retried up to analysis_retry_passes
    # times — the safety net for the one response that fails on almost every run.
    analysis_call_timeout_seconds: float = 120.0
    # Ceiling for WEB-GROUNDED monitoring calls. Grounded OpenAI/Anthropic calls
    # run a multi-round server-side search loop (and Perplexity sonar is always
    # web-grounded), so they are the SLOW platforms, not the fast ones — a large
    # share of the "dropped calls in every run" were grounded calls hitting the
    # plain 90s ceiling. The effective timeout for a grounded call is
    # max(platform_call_timeout_seconds, this value). Ungrounded calls and
    # citation-analysis calls (single-shot JSON, never grounded) keep the plain
    # ceiling above.
    platform_call_timeout_grounded_seconds: float = 240.0
    # ── Dropped-call retries ──────────────────────────────────────────────────
    # A monitoring call that times out or errors is no longer silently dropped:
    # after the first wave finishes, the failed (prompt × platform) pairs are
    # re-run in up to this many extra passes. Retrying AFTER the wave (rather
    # than inline) lets transient rate-limit/load pressure subside first and
    # never extends the per-call timeout. 0 disables retries. Note the adapters
    # additionally retry 429/5xx per call (app.platforms.retry) — these passes
    # cover what that cannot: timeouts and exhausted in-call retries.
    monitoring_retry_passes: int = 2
    # Delay before retry pass N is N × this value (10s, then 20s). Kept modest:
    # the per-platform rate limiter already paces individual calls.
    monitoring_retry_backoff_seconds: float = 10.0
    # Same idea for the citation-analysis phase: responses whose analysis call
    # failed (timeout / unparseable twice) get this many extra attempts before
    # being counted as analysis drops. This attacks the "386 stored but only
    # 361 analyzed" gap. Raised to 10 per the 2026-07-25 client agreement
    # ("120s timeout per attempt, up to 10 retries in a loop" — a safety net,
    # not an expected path; only failing responses are retried). 0 disables.
    analysis_retry_passes: int = 10
    # Minimum fraction of monitoring responses that must be successfully analyzed
    # for a run to count as "completed". Below this the run is marked failed, so a
    # badly under-analyzed run never ships a misleading citation rate as if real.
    analysis_min_coverage: float = 0.9

    # ── Per-platform rate limits (requests / minute) ──────────────────────────
    # Paces every upstream call (monitoring AND analysis) so a run cannot burst
    # past a provider's per-minute cap. These defaults are deliberately
    # conservative — set the real per-tier ceilings via env to pace closer to the
    # provider limit without a redeploy, e.g. PLATFORM_RATE_LIMIT_PERPLEXITY=100.
    # A value <= 0 disables limiting for that platform.
    platform_rate_limit_openai: int = 500
    platform_rate_limit_anthropic: int = 500
    platform_rate_limit_perplexity: int = 50
    platform_rate_limit_gemini: int = 60
    # Longest a single call will wait for a rate-limit slot before proceeding
    # anyway (fail-open). Generous on purpose: a compliant 100-prompt run's
    # slowest call waits only ~1-2 windows; this only guards against a
    # misconfigured (e.g. accidentally tiny) limit hanging the run forever.
    platform_rate_limit_max_wait_seconds: float = 300.0

    @property
    def platform_rate_limits(self) -> dict[str, int]:
        """Per-platform requests-per-minute ceilings, keyed by platform value."""
        return {
            "openai": self.platform_rate_limit_openai,
            "anthropic": self.platform_rate_limit_anthropic,
            "perplexity": self.platform_rate_limit_perplexity,
            "gemini": self.platform_rate_limit_gemini,
        }

    # ── Run call log (per-attempt diagnostics) ────────────────────────────────
    # Raw HTTP exchanges (redacted request/response) are persisted for FAILED
    # attempts by default. Set true to also keep them for successful calls —
    # heavy; intended for short debugging windows, not steady state.
    run_log_capture_all: bool = False
    # Truncation cap for stored request/response bodies (bytes of text kept).
    run_log_body_max_bytes: int = 65536
    # Days to keep the heavy run_call_exchanges rows before the daily purge
    # deletes them. The light run_calls rows (typed outcome per attempt) are
    # kept regardless — they are the long-term drop statistics. <= 0 disables
    # the purge entirely.
    run_log_exchange_retention_days: int = 30

    # ── Platform model cache refresh ──────────────────────────────────────────
    # Maximum age of the platform_model_cache rows before the live model lists
    # are re-fetched from the provider APIs. Checked at startup AND by a
    # periodic in-process refresh loop, so a deprecated/retired model is
    # detected within this window instead of only on a manual refresh click.
    # <= 0 disables both the TTL check and the refresh loop (cache then only
    # updates via POST /admin/platforms/refresh-models).
    model_cache_ttl_hours: float = 24.0

    # Admin auth
    jwt_secret_key: str = "change-me-in-production"
    jwt_access_token_expire_minutes: int = 60
    jwt_refresh_token_expire_days: int = 7
    redis_url: str = "redis://localhost:6379"

    # ── Audit API (/v1) auth ──────────────────────────────────────────────────
    # Per-environment API keys for the public /v1 automation surface, sent as
    # `X-API-Key: <key>`. Comma-separated list; each entry is either a bare key
    # or `label:key` (the label is logged on a successful auth, never the key).
    #
    # Multiple keys are valid at once, which is the rotation path: add the new
    # key alongside the old, migrate callers, then drop the old key — no code
    # change and no rebuild, just an update to the AUDIT_API_KEYS secret. The
    # value is read fresh from the environment on every request (see
    # app.api.v1.dependencies), so a secret update takes effect immediately.
    #
    # Empty value disables /v1 auth (all /v1 requests are rejected with 401
    # until at least one key is configured — fail closed).
    #
    #   AUDIT_API_KEYS="primary:k_live_abc123,rotating:k_live_def456"
    audit_api_keys: str = ""

    # ── CORS origins ──────────────────────────────────────────────────────────
    # Each service only allows its own frontend. In combined mode both are allowed.
    admin_frontend_url: str = "http://localhost:5174"
    client_frontend_url: str = "http://localhost:5173"
    # Extra CORS origins — comma-separated list of additional allowed origins.
    # Use this when running multiple deployments (e.g. staging + production).
    # Example: EXTRA_CORS_ORIGINS=https://staging.example.com,https://preview.example.com
    extra_cors_origins: str = ""

    @property
    def extra_cors_origins_list(self) -> list[str]:
        if not self.extra_cors_origins.strip():
            return []
        return [o.strip() for o in self.extra_cors_origins.split(",") if o.strip()]

    # ── Scheduler ─────────────────────────────────────────────────────────────
    # Set SCHEDULER_ENABLED=false to disable without redeployment.
    # In the split architecture, only the worker service has this true.
    scheduler_enabled: bool = True

    # ── Web grounding ─────────────────────────────────────────────────────────
    # When enabled, the OpenAI / Anthropic / Gemini adapters attach the provider's
    # web-search / grounding tool so they answer from the live web (like the real
    # consumer apps) instead of from frozen training data. Perplexity is always
    # web-grounded via its `sonar` model and ignores these flags.
    # Global config (not per-client) — toggle without a redeploy.
    web_grounding_enabled: bool = True            # master switch
    web_grounding_openai: bool = True
    web_grounding_anthropic: bool = True
    web_grounding_gemini: bool = True
    # Upper bound on web searches per call, to cap added cost/latency. Raised
    # from 5 on 2026-07-31: a Whip Around run showed Claude exhausting the cap
    # on multi-part comparison queries ("best X or Y for Z"), receiving
    # max_uses_exceeded, and finishing the answer from training data — which
    # then shipped in a client report as though it were a live-web answer.
    web_search_max_uses: int = 12
    # When a call is supposed to be grounded but comes back citing NOTHING, is
    # that an error? Yes, by default. Before this existed the answer was
    # accepted silently and the model's training-data recollection was recorded
    # as a monitoring result: the citation rate then measured what the model
    # remembered, not what the live web says. Turning this off restores the old
    # permissive behaviour and should only ever be temporary.
    web_grounding_require_sources: bool = True
    # A response that cites nothing is retried this many times before the run
    # gives up and records it as ungrounded. Transient search-backend failures
    # are the common case and usually clear on the next attempt.
    web_grounding_retry_attempts: int = 2

    # ── Monitoring call framing ───────────────────────────────────────────────
    # A short system prompt telling every platform it is answering a real
    # buyer's question. Without it the adapters sent a bare user turn, and the
    # more agentic models read a product-shaped prompt ("Fleet inspection app
    # for an oil and gas company") as a BUILD request: the same Whip Around run
    # has Claude replying "let me build out a working application" and asking
    # clarifying questions instead of naming vendors. Those responses cite
    # nobody, so they depress the citation rate for a reason that has nothing
    # to do with the client's visibility.
    #
    # Applied identically to all four platforms, deliberately: they are being
    # compared to each other, so they must be asked the same way. It does make
    # runs from before this change not strictly comparable with runs after it.
    platform_system_prompt_enabled: bool = True
    # Anthropic is the only adapter that ever sent an output cap, and 2048 was
    # not enough for a grounded answer that also has to run several searches.
    # Sized to match what the uncapped adapters actually return.
    anthropic_max_output_tokens: int = 8192

    # ── Generation Engine ─────────────────────────────────────────────────────
    # Master on/off switch for the whole generation engine (ops toggle, checked
    # in the orchestrator). The generation MODEL is not set here — each generator
    # resolves it per-client via platform_model_config / the recommendation
    # config resolver (defaults live in model_registry.DEFAULT_RECOMMENDATION_*).
    generation_enabled: bool = True
    generation_temperature: float = 0.3
    generation_max_concurrent: int = 3
    generation_content_brief_enabled: bool = True
    generation_schema_enabled: bool = True
    generation_llms_txt_enabled: bool = True
    generation_authority_building_enabled: bool = True
    generation_dedup_days: int = 7
    generation_llms_txt_dedup_days: int = 14

    # ── Recommendation engine (2026-07-25 redesign) ───────────────────────────
    # "single_call": ONE LLM call receives the top-scoring responses together
    # and decides for itself what to produce (points 5-7 of the client call) —
    # it sees the whole run at once, so it can merge fifteen queries failing on
    # the same gap into one strong brief instead of fifteen weak ones.
    # "legacy": the previous per-analysis fan-out (one call per response for
    # briefs + schema, plus one llms.txt and one authority call per run). Kept
    # as a rollback path for one release; remove once single_call has proven
    # itself in production.
    recommendation_mode: str = "single_call"
    # Hard ceiling on recommendations persisted from one call, as a guard
    # against a runaway completion. The stage now targets one brief per distinct
    # gap across every service line rather than a consolidated ten, so this sits
    # far above any realistic run — it exists to catch a model that has started
    # looping, not to shape the output.
    recommendation_max_items: int = 150
    # Chars of each response included in the prompt. 0 means NO LIMIT, which is
    # the intended setting.
    #
    # This was 2000, and that was a genuine defect: responses run 3-6k chars, so
    # the stage was reasoning about roughly a third of each answer, cut from the
    # front — and in ranked "best X for Y" answers the brand and competitor
    # mentions sit mid-to-late, exactly what got dropped. A non-zero value here
    # reintroduces that bug and should only ever be a temporary cost control.
    recommendation_response_max_chars: int = 0
    # Input-token budget for the assembled single call. Sized for a large-context
    # recommendation model (Gemini 3.1 Pro and similar). This is a ceiling, not a
    # target: the real limit applied at call time is the smaller of this and what
    # the configured model's own context window allows once output is reserved
    # (see model_registry.usable_input_tokens), so pointing a client at a 128k
    # model cannot blow the context.
    #
    # Sized so a full run fits WHOLE: ~400 gap responses at the 3-6k chars they
    # actually run to, plus per-response metadata, lands near 750k estimated
    # tokens. Anything lower silently reintroduces truncation on the largest
    # clients, which is the bug this redesign exists to fix.
    recommendation_input_token_budget: int = 900000
    # Max output tokens for the single call — it emits every recommendation for
    # the run in one completion, and "one brief per gap" makes that a long
    # completion, so this is much larger than a per-rec call.
    recommendation_max_output_tokens: int = 32000
    # The single call legitimately runs for minutes (large input, large output);
    # it gets its own ceiling rather than the 90s per-call default.
    recommendation_call_timeout_seconds: float = 600.0

    # ── Live site inventory (recommendation input) ────────────────────────────
    # Before recommending, the engine reads the client's own website to see
    # what content and schema already exist, so it does not re-recommend work
    # that is already implemented (point 8 of the client call). Failure is
    # never fatal: generation proceeds with an explicit "no live site data"
    # note in the prompt.
    site_inventory_enabled: bool = True
    # Pages fetched per snapshot, beyond robots.txt / sitemap.xml / llms.txt.
    site_inventory_max_pages: int = 30
    # Reuse a snapshot younger than this instead of re-crawling — back-to-back
    # runs for one client should not hit the site twice.
    site_inventory_ttl_hours: float = 24.0
    # Per-request and whole-crawl ceilings. The crawl is a prompt input, not
    # the product: it must never hold a run open.
    site_inventory_request_timeout_seconds: float = 10.0
    site_inventory_total_timeout_seconds: float = 90.0
    # Politeness: simultaneous requests to one host, and the delay between
    # them. Deliberately conservative — these are client-owned sites and the
    # engine must not look like an attack.
    site_inventory_max_concurrent: int = 4
    site_inventory_request_delay_seconds: float = 0.2
    # Bytes of HTML read per page before truncation (schema/JSON-LD and head
    # metadata live near the top; full-page text is not needed).
    site_inventory_page_max_bytes: int = 400000
    site_inventory_user_agent: str = (
        "CitiqBot/1.0 (+https://citiq.ai; GEO audit for the site owner)"
    )

    @field_validator(
        "openai_api_key",
        "anthropic_api_key",
        "perplexity_api_key",
        "gemini_api_key",
        mode="before",
    )
    @classmethod
    def clean_api_keys(cls, v: str) -> str:
        # Railway env vars sometimes carry trailing newlines or surrounding quotes
        # from copy-paste. Strip them so the raw value reaches the SDK intact.
        if isinstance(v, str):
            return v.strip().strip('"').strip("'")
        return v

    @field_validator("database_url", mode="before")
    @classmethod
    def ensure_async_driver(cls, v: str) -> str:
        return _to_asyncpg(v)

    @field_validator("database_url_admin", mode="before")
    @classmethod
    def ensure_async_driver_admin(cls, v: str) -> str:
        return _to_asyncpg(v)

    @field_validator("database_url_app", mode="before")
    @classmethod
    def ensure_async_driver_app(cls, v: str) -> str:
        return _to_asyncpg(v)

    @property
    def effective_database_url_admin(self) -> str:
        """The URL to use for the admin engine (falls back to superuser URL)."""
        return self.database_url_admin or self.database_url

    @property
    def effective_database_url_app(self) -> str:
        """The URL to use for the client engine (falls back to superuser URL)."""
        return self.database_url_app or self.database_url


settings = Settings()
