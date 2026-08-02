# Origo Engine — Project Conventions

## UI: icons and symbols (MANDATORY)

Never put unicode symbol glyphs in user-visible frontend text — no arrows (→ ← ↑ ↓ › ▶ ▼), close marks (✕ ×), checks (✓), ellipsis (…), em/en dashes (— –), middle dots (·), bullets (•), or similar. They make the UI look machine-generated.

Instead:

- **Icons**: use `@mui/icons-material`, **Rounded** variant, default-path imports, sized via the `style` prop to match surrounding text. Both `admin-frontend` and `web` have MUI installed.
  ```tsx
  import ArrowForwardRoundedIcon from "@mui/icons-material/ArrowForwardRounded";
  <Link className="inline-flex items-center gap-0.5 ...">View <ArrowForwardRoundedIcon style={{ fontSize: 13 }} /></Link>
  ```
  Common picks: ArrowForward/ArrowBack (nav links), ChevronLeft/ChevronRight (pagination, "View ›"), PlayArrowRounded (start/run), CloseRounded (cancel/close), TrendingUp/TrendingDown (trends), ReplayRounded (retry), FileDownloadRounded (downloads), KeyboardArrowDown/Right (expanders), CheckRounded (success).
- **Empty values**: plain ASCII hyphen `"-"`.
- **Ellipsis**: ASCII `"..."` (e.g. `"Loading..."`).
- **Prose**: rewrite with commas, colons, semicolons, or parentheses instead of em dashes or middle-dot separators (e.g. "Citation rate (last 7 runs)", not "Citation rate · last 7 runs").
- Icon-only buttons need an `aria-label`.

## UI: cursor on clickables

Every clickable element must show `cursor: pointer`. Buttons get it globally: `admin-frontend/src/index.css` has a base-layer rule (`button:not(:disabled), [role="button"]:not(:disabled) { cursor: pointer }`) because Tailwind v4's preflight no longer sets it; `web` is on Tailwind v3 whose preflight still does. Don't add `cursor-pointer` to plain buttons — it's redundant. Do add it to non-button clickables (rows, cards, divs with `onClick`); modal backdrops are the exception.
