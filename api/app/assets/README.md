# Runtime assets

Files the API needs while serving requests, as opposed to design sources.

`docs/brand/` is the design source of truth for everything here. These are
copies, because the API images are built with `api/` as the Docker build context
(see `Dockerfile.admin-api`) and nothing outside it exists at runtime. Reading
the original straight out of `docs/brand/` would work on a developer's machine
and silently render nothing in production.

| File | Copied from | Used by |
|---|---|---|
| `citiq-logo.svg` | `docs/brand/citiq-ful-logo.svg` | the cover of every generated run report (`report_service`) |

When a brand asset changes, update `docs/brand/` first and copy it here in the
same commit. `test_report_pdf.py` asserts this copy still parses and renders, so
a corrupt or truncated copy fails the suite rather than a client's report.
