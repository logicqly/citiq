# M2 Audit API — deploy & operations runbook

Milestone 2 hardens the `/v1` Audit API from the M1 staging wrapper into a
production service. This is **additive** to M1 — the six endpoints and their M1
outputs are unchanged; M2 adds per-env key auth, new output fields, prospect
enforcement, and a consistent error envelope.

Anything under "You must do" requires Railway/secret access that the codebase
cannot perform on its own.

---

## What changed (code)

| Area | Change |
|---|---|
| Auth | `AUDIT_API_KEY` (single static token) → `AUDIT_API_KEYS` (per-env, multi-key, `label:key`, rotatable). Read fresh from the env on every request. |
| Scores | `GET /v1/audits/{id}/results` → `scores.citation_rate_by_category` `{discovery, criteria, shortlist, fit, social_proof, comparison}` (the 6 admin-managed categories; also the accepted `PUT …/prompts` category tokens). |
| Recommendations | New `authority_building` bucket; every recommendation now carries `effort ∈ S \| M \| L`. `review_status` stays `pending_qc`. |
| Data | `clients.record_type` enforced (CHECK `prospect\|client`) + indexed; admin list gains a `record_type` filter. |
| Errors | Every `/v1` response uses `{"error":{"code","message","details"}}` with correct status codes (401/404/409/422/500). Non-`/v1` routes unchanged. |
| DB | Migration `0020_m2_audit_hardening` (enum value, `recommendations.effort`, record_type CHECK + index). |

## Migration `0020`

Additive and idempotent (`IF NOT EXISTS` / guarded `ADD CONSTRAINT`). The enum
value is added in an autocommit block (PostgreSQL forbids `ALTER TYPE ... ADD
VALUE` inside a transaction on older versions). Re-running is safe.

```bash
# from api/
alembic upgrade head        # applies 0019 → 0020
```

`recommendations.effort` backfills existing (M1) rows to `'M'`, so every
recommendation — new or historical — returns a valid `effort`.

## Auth: `AUDIT_API_KEYS`

- Format: comma-separated. Each entry is a bare `key` or `label:key`. The label
  (`[A-Za-z0-9_.-]+`) is logged on success as `key_label`; the key is never
  logged.
- **Fail-closed**: empty/unset ⇒ all `/v1` requests get `401`.
- Generate a strong key, e.g. `python -c "import secrets; print(secrets.token_urlsafe(32))"`.

```
AUDIT_API_KEYS=primary:k_live_<random>
```

### Rotation (no code change, no rebuild)

The value is read from the environment on every request and multiple keys are
valid at once, so rotation is zero-downtime:

1. **Add** the new key alongside the old:
   `AUDIT_API_KEYS=old:k_live_OLD,new:k_live_NEW`
2. Migrate every caller to `k_live_NEW`.
3. **Remove** the old key: `AUDIT_API_KEYS=new:k_live_NEW`.

Each step is just an update to the secret. (On Railway a variable change restarts
the service; there is no code deploy or image rebuild.)

---

## Promote staging → production — You must do

1. **Set the secret in each environment** (staging and production) *before*
   deploying the code, so there is no unauthenticated window:
   - `AUDIT_API_KEYS` = a strong, environment-specific key (do **not** reuse the
     old M1 `AUDIT_API_KEY` value; different keys per env).
   - Remove the now-unused `AUDIT_API_KEY` variable.
2. **Deploy the branch** to staging; run `alembic upgrade head`.
3. **Smoke-test staging** (see below).
4. **Deploy to production**; run `alembic upgrade head` against the prod DB.
5. **Smoke-test production** with the prod key.

### Smoke test (per environment)

```bash
BASE=https://<env-host>            # staging or prod
KEY=<the AUDIT_API_KEYS value, key part only>

# 401 fail-closed
curl -s -o /dev/null -w '%{http_code}\n' $BASE/v1/clients            # → 401
# authed lifecycle
curl -s -H "X-API-Key: $KEY" -X POST $BASE/v1/clients \
     -H 'content-type: application/json' -d '{"name":"Smoke Co","record_type":"prospect"}'
# … load KB, load prompts, POST audit, poll GET /v1/audits/{id},
#   then GET /v1/audits/{id}/results and confirm the new fields:
#   scores.citation_rate_by_category, recommendations[].effort,
#   any recommendations[].bucket == "authority_building".
```

## Definition-of-done checklist

- [ ] `AUDIT_API_KEYS` set in staging **and** production; legacy `AUDIT_API_KEY` removed.
- [ ] `alembic upgrade head` applied in both environments.
- [ ] Results return `citation_rate_by_category`, `authority_building` (where applicable), and `effort` on every recommendation; all M1 outputs unchanged.
- [ ] `record_type` enforced/filterable at the data layer.
- [ ] Consistent `/v1` error envelope + status codes; ENGINE_TIMEOUT → partial results intact.
- [ ] Critical-path unit tests + full-lifecycle e2e passing; platform APIs mocked.
- [ ] Existing admin/JWT routes untouched and passing.

## Out of scope for M2 (do not build)

run-config body; DeepSeek adapter; `runs_per_prompt` multi-run sampling;
run-over-run movement; numeric position/rank; skeptical sentiment value;
`GET /v1/clients/{id}/audits`; webhooks; prospect auto-expire (archive is manual
via `PATCH /admin/clients/{id}/status → archived`).
