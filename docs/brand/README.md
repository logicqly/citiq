# Citiq brand assets

Design-system reference for Citiq (citiq.ai), a Logicqly product. This file
summarizes the parts engineers need: the tokens below are what both frontends
actually implement in their `index.css`.

## Mark

The Citiq mark is `citiq-mark.svg` in this folder: a 131.01 x 127.02 viewBox
holding three shapes (the open ring, the corner wedge, the base sweep). It is
the single source of truth; every surface below renders the same geometry.

| Where | File |
|---|---|
| Admin app component | `admin-frontend/src/components/ui/mark.tsx` (`CitiqMark`, `currentColor` fill) |
| Client app component | `web/src/components/ui.tsx` (`CitiqMark`, same) |
| Favicons | `admin-frontend/public/favicon.svg`, `web/public/favicon.svg` |
| Empty-state watermark | inline data-URI in `admin-frontend/src/index.css` and `web/src/index.css` |
| Design handoff sheets | `docs/design-handoff/*.html` (`CITIQ_PATHS`) |

In-app the mark inherits `currentColor` so it tracks the ink scale in both
themes; the favicons are the only place it carries the brand orange.

`Origo-Brand-Kit.pdf` in this folder is the *previous* product's brand kit. It
carries no Citiq licence and nothing renders from it; delete it once nothing
references it.

## Logo rules

- Monochrome only: render white on near-black, or invert to black on white.
- Never recolor, stretch, rotate, add effects, or resize the mark out of
  proportion with the word.
- Clear space around the mark equals the weight of its outer ring.

## Color

The page is pure black; surfaces step up in near-black increments. White is
the only accent; all hierarchy comes from the ink scale. Structure comes from
white hairlines at low alpha and one soft white radial glow (6-12% opacity).

| Token | Hex | | Token | Hex |
|---|---|---|---|---|
| Canvas | `#000000` | | White | `#FFFFFF` |
| Raised | `#070707` | | Ink 100 (primary text) | `#EDEDED` |
| Card | `#101010` | | Ink 200 | `#C4C4C4` |
| Hover | `#161616` | | Ink 300 (muted text) | `#9A9A9A` |
| Well | `#1E1E1E` | | Ink 400 (faint text) | `#6A6A6A` |
| | | | Ink 500 | `#4A4A4A` |
| | | | Ink 600 | `#2A2A2A` |

Hairline borders: white at alpha .06 (faint), .08, .11, .14, .22 (strong).

## Typography

- **Inter** 400/500/600/700 + italic: display, headings, body, UI. Body 17px,
  line-height 1.6. Headings tight, heavy, negative tracking.
- **Geist Mono** 400/500: eyebrows, labels, units. Uppercase, tracking 0.2em.
- Scale: H1 76/600, H2 50/600, H3 23/600, body 17/400, label 12 mono.

Both apps load these from Google Fonts in their `index.html`.

## Foundations

- Spacing: 8px base unit (8, 16, 24, 32, 48, 64; 104 between sections).
- Radius: 6 xs, 10 sm, 14 md, 20 lg, pill.
- Shadows near-black, soft and low; the white glow is the only light.
- Buttons: pill. Primary = white fill with black text; ghost = hairline
  border. Press scales to 0.97.

## Voice

The login taglines currently read "Citation visibility across AI answers" and
"Citiq by Logicqly, citiq.ai". These are plain descriptors, not final brand
copy; replace them alongside the mark. They live in
`web/src/auth/LoginPage.tsx` and `admin-frontend/src/auth/LoginPage.tsx`.

Concrete, plain, confident. Speak to the reader as "you". Sentence case
everywhere. Lead with the claim. One strong number beats five weak ones.
Never: em dashes, "it's not X it's Y", "imagine...", power words (unlock,
supercharge, seamless), emoji.
