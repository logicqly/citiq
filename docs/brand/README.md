# Origo brand assets

Canonical brand assets for Origo (origolabs.ai), extracted from the 2026 brand
kit. The full kit is in [Origo-Brand-Kit.pdf](Origo-Brand-Kit.pdf); this file
summarizes the parts engineers need without opening the PDF.

## Assets

| File | What it is |
|---|---|
| `origo-mark.svg` | The mark: a concentric O that brings a source into focus, with a citation marker at its edge. White fill, 500x500 viewBox. |
| `origo-lockup.svg` | The wordmark: the mark as an enlarged first letter followed by "rigo" (Inter 600, tight tracking). White fill. |
| `Origo-Brand-Kit.pdf` | The full 13-section brand kit (logo rules, color, type, components, voice). |

The original delivery also contained `origo-o.svg` and `origo-wordmark.svg`;
those were byte-identical duplicates of the mark and lockup and were not kept.

## Where the brand lives in this repo

- Admin app mark component: `admin-frontend/src/components/ui/mark.tsx`
  (`OrigoMark`, `currentColor` fill — recolors with the surrounding text).
- Client app mark component: `web/src/components/ui.tsx` (`OrigoMark`, same).
- Favicons: `admin-frontend/public/favicon.svg` and `web/public/favicon.svg`,
  both the white mark on a pure-black rounded tile.

## Logo rules (from the kit)

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

Taglines: "Be the source." / "Engineered from origin."

Concrete, plain, confident. Speak to the reader as "you". Sentence case
everywhere. Lead with the claim. One strong number beats five weak ones.
Never: em dashes, "it's not X it's Y", "imagine...", power words (unlock,
supercharge, seamless), emoji.
