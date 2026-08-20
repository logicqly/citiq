"""
Charts for the PDF run report.

Every chart is a self-contained reportlab Drawing: it carries its own card rule,
eyebrow, title and plot, so build_pdf can drop one into the story without having
to know its internals.

Design rules applied here (they are what keep the set looking like one system):

  Form follows the data's job. Magnitude -> bars in a single hue. Change over
  time -> one line. Polarity on an ordered scale (how the brand is described) ->
  a diverging stacked bar centred on neutral. "You against the field" ->
  emphasis: the client in the accent hue, everyone else in the de-emphasis grey.

  The brand palette, unchanged: one accent on an ink scale. Citiq orange carries
  every measured value; the neutral midpoint and context marks are grey; the
  negative pole is deep ink. Nothing else gets a colour.

  Red is deliberately absent as the negative pole. Beside the brand orange it
  measures ΔE 11.9 for normal vision, under the 15 floor, so a reader with full
  colour vision cannot reliably tell the two apart; orange against ink separates
  on lightness, which survives every colour-vision deficiency and grayscale
  print as well.

  Text never wears the data colour. Values, labels and legends are ink; identity
  comes from the coloured mark beside them.

  Labels are selective. A number rides the end of a bar and the last point of the
  line; nothing else is labelled, because a value on every mark goes unread. The
  tables that follow each chart are the full-precision view.

A chart with nothing worth drawing returns None rather than an empty frame, so a
run with (say) no competitors simply has no competitor chart.
"""
import re

from reportlab.graphics.shapes import Circle, Drawing, Line, PolyLine, Rect, String
from reportlab.lib import colors
from reportlab.pdfbase.pdfmetrics import stringWidth

# -- Palette ------------------------------------------------------------------
# Print surface is white. Orange and deep ink are the diverging poles; grey is
# the neutral midpoint and the de-emphasis mark, which is meant to read as grey.
SURFACE = colors.white
INK = colors.HexColor("#0B0B0B")          # primary text
INK_2 = colors.HexColor("#52514E")        # secondary text
INK_MUTED = colors.HexColor("#898781")    # axis labels, de-emphasis marks
GRID = colors.HexColor("#E1E0D9")         # hairline gridlines
RULE = colors.HexColor("#C3C2B7")         # baselines, card rules

SERIES = colors.HexColor("#F06922")       # Citiq orange: magnitude + the client
SERIES_WASH = colors.HexColor("#FEF0E9")  # the accent at ~10% over white
NEGATIVE = colors.HexColor("#2A2A2A")     # deep ink: the opposite pole
NEUTRAL = colors.HexColor("#898781")      # diverging midpoint / context

FONT = "Helvetica"
FONT_BOLD = "Helvetica-Bold"

# Mark specs, in points (the screen specs scaled for print).
BAR_RADIUS = 2.0        # rounded data-end
LINE_WIDTH = 1.2
HAIRLINE = 0.4
GAP = 1.5               # surface gap between touching fills
DOT_R = 2.2
CARD_PAD = 13.0


# -- Primitives ---------------------------------------------------------------

def _text(d, x, y, text, size=8, font=FONT, color=INK_2, anchor="start"):
    d.add(String(x, y, pdf_safe(text), fontName=font, fontSize=size,
                 fillColor=color, textAnchor=anchor))


# Characters the base-14 PDF fonts cannot draw, and the closest thing they can.
# reportlab renders anything unmappable as a "not defined" box, so an AI answer
# quoting a brand in Japanese would otherwise print as a row of black squares in
# a document going to a client.
_TRANSLITERATE = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"',
    "–": "-", "—": "-", "‐": "-", "‑": "-", "−": "-",
    "…": "...", " ": " ", "•": "-", "·": "-",
    "→": "->", "←": "<-", "✓": "yes", "✗": "no",
    "­": "", "﻿": "",
}
_TRANSLATION = {ord(k): v for k, v in _TRANSLITERATE.items()}


def pdf_safe(text) -> str:
    """Text reduced to what the report's fonts can actually draw.

    Typographic characters become their ASCII equivalents; anything still
    unencodable (CJK, emoji, other scripts) is dropped rather than printed as a
    box, and the gap it leaves is collapsed. Lossy by design: the alternative is
    embedding a Unicode font in every PDF, and a dropped glyph reads better than
    a black square in a client's report.
    """
    out = str(text).translate(_TRANSLATION)
    if out.isascii():
        return out
    # cp1252 is what the base-14 fonts encode; ignore drops the rest.
    out = out.encode("cp1252", "ignore").decode("cp1252")
    # Close the gap a dropped run of characters leaves behind, without touching
    # line breaks.
    return re.sub(r"[ 	]{2,}", " ", out)


def tracked(text: str) -> str:
    """Letter-spaced caps for eyebrow labels, for canvas and Drawing strings.

    The brand sets labels in tracked uppercase; reportlab's paragraph styles have
    no letter-spacing, so the spacing is put in the string itself.
    """
    return " ".join(text.upper())


def tracked_xml(text: str) -> str:
    """tracked() for a Paragraph.

    A Paragraph collapses runs of whitespace, which would close the gap between
    words and print "DATAQUALITY", so the spacing is non-breaking.
    """
    return "&nbsp;".join(text.upper()).replace("&nbsp; &nbsp;", "&nbsp;&nbsp;&nbsp;")


def _bar(d, x, y, width, height, color, round_end="right"):
    """A bar with a rounded data-end and a square baseline end.

    Drawn as a fully rounded rect with the baseline end squared off by a second
    rect, because a rounded corner belongs on the end the data reaches, not on
    the end every bar shares.
    """
    if width <= 0.1:
        return
    if width <= BAR_RADIUS * 2 or round_end == "none":
        d.add(Rect(x, y, width, height, fillColor=color, strokeColor=None))
        return
    d.add(Rect(x, y, width, height, rx=BAR_RADIUS, ry=BAR_RADIUS,
               fillColor=color, strokeColor=None))
    # Square off the baseline end.
    if round_end == "right":
        d.add(Rect(x, y, BAR_RADIUS, height, fillColor=color, strokeColor=None))
    else:
        d.add(Rect(x + width - BAR_RADIUS, y, BAR_RADIUS, height,
                   fillColor=color, strokeColor=None))


def _card(width, height, eyebrow=None, title=None, subtitle=None):
    """A hairline card with its eyebrow/title block already drawn.

    Returns (drawing, plot_top) where plot_top is the y of the first free line
    below the heading block.
    """
    d = Drawing(width, height)
    d.add(Rect(0, 0, width, height, rx=6, ry=6,
               fillColor=SURFACE, strokeColor=GRID, strokeWidth=HAIRLINE))
    y = height - CARD_PAD
    if eyebrow:
        y -= 7
        _text(d, CARD_PAD, y, tracked(eyebrow), size=6.2, font=FONT_BOLD, color=INK_MUTED)
        y -= 12
    if title:
        _text(d, CARD_PAD, y, title, size=11, font=FONT_BOLD, color=INK)
        y -= 12
    if subtitle:
        _text(d, CARD_PAD, y, subtitle, size=7.5, color=INK_MUTED)
        y -= 10
    return d, y


def _legend(d, x, y, entries):
    """Swatch + label pairs. Identity never rests on colour alone."""
    for label, color in entries:
        d.add(Rect(x, y, 6, 6, rx=1.5, ry=1.5, fillColor=color, strokeColor=None))
        _text(d, x + 9, y + 0.6, label, size=7, color=INK_2)
        x += 9 + stringWidth(label, FONT, 7) + 14


def _pct(value: float) -> str:
    """A share as a percentage, without a decimal point that says nothing."""
    pct = value * 100
    return f"{pct:.0f}%" if pct >= 10 or pct == 0 else f"{pct:.1f}%"


def _nice_top(value: float) -> float:
    """A clean axis ceiling at or above `value` (0.2, 0.4, ... 1.0)."""
    for step in (0.2, 0.4, 0.6, 0.8, 1.0):
        if value <= step:
            return step
    return 1.0


def _truncate(text: str, font: str, size: float, max_width: float) -> str:
    """Shorten to fit, so a long brand never overruns its gutter."""
    if stringWidth(text, font, size) <= max_width:
        return text
    while text and stringWidth(text + "...", font, size) > max_width:
        text = text[:-1]
    return text + "..."


# -- KPI strip ----------------------------------------------------------------

def kpi_strip(width, tiles, hero_index=0):
    """The headline numbers, one per cell, divided by hairlines.

    `tiles` is a list of (label, value, note). The hero is set larger because a
    report should lead with exactly one number.
    """
    if not tiles:
        return None
    height = 70.0
    d = Drawing(width, height)
    d.add(Rect(0, 0, width, height, rx=6, ry=6,
               fillColor=SURFACE, strokeColor=GRID, strokeWidth=HAIRLINE))

    cell = width / len(tiles)
    for i, (label, value, note) in enumerate(tiles):
        x = i * cell + CARD_PAD
        if i:
            d.add(Line(i * cell, 10, i * cell, height - 10,
                       strokeColor=GRID, strokeWidth=HAIRLINE))
        _text(d, x, height - 19, tracked(label), size=6.2, font=FONT_BOLD, color=INK_MUTED)
        size = 22 if i == hero_index else 17
        _text(d, x, height - 21 - size, str(value), size=size, font=FONT_BOLD, color=INK)
        if note:
            _text(d, x, 13, _truncate(str(note), FONT, 7, cell - CARD_PAD * 2),
                  size=7, color=INK_MUTED)
    return d


# -- Citation rate over recent runs -------------------------------------------

def citation_trend(width, points):
    """One line: the citation rate across this client's recent runs.

    A single series, so no legend — the title names it — and only the last point
    carries a value label.
    """
    if not points or len(points) < 2:
        return None

    height = 152.0
    d, top = _card(
        width, height, eyebrow="Trend",
        title="Citation rate over recent runs",
        subtitle="Share of AI responses citing the brand, oldest run first. Hollow citations excluded.",
    )

    left, right, bottom = CARD_PAD + 26, width - CARD_PAD - 16, 26.0
    # Headroom above the top gridline: a point sitting on the ceiling still needs
    # room for its value label.
    plot_h = top - bottom - 16
    if plot_h < 30:
        return None
    top_rate = _nice_top(max(p["rate"] for p in points))

    # Gridlines and y ticks, drawn first so the data sits over them.
    for frac in (0.0, 0.5, 1.0):
        y = bottom + plot_h * frac
        d.add(Line(left, y, right, y, strokeColor=RULE if frac == 0 else GRID,
                   strokeWidth=HAIRLINE))
        _text(d, left - 5, y - 2.4, _pct(top_rate * frac), size=6.5,
              color=INK_MUTED, anchor="end")

    step = (right - left) / max(len(points) - 1, 1)
    coords = [
        (left + i * step, bottom + plot_h * (p["rate"] / top_rate if top_rate else 0))
        for i, p in enumerate(points)
    ]

    # Area wash under the line, then the line itself.
    area = []
    for x, y in coords:
        area.extend([x, y])
    d.add(PolyLine(
        [coords[0][0], bottom] + area + [coords[-1][0], bottom],
        strokeColor=None, fillColor=SERIES_WASH, strokeWidth=0,
    ))
    flat = []
    for x, y in coords:
        flat.extend([x, y])
    d.add(PolyLine(flat, strokeColor=SERIES, strokeWidth=LINE_WIDTH,
                   strokeLineJoin=1, strokeLineCap=1))

    # Markers carry a surface ring so they stay legible where they cross the line.
    for i, (x, y) in enumerate(coords):
        last = i == len(coords) - 1
        d.add(Circle(x, y, DOT_R + (0.9 if last else 0.4),
                     fillColor=SURFACE, strokeColor=None))
        d.add(Circle(x, y, DOT_R if last else DOT_R - 0.5,
                     fillColor=SERIES, strokeColor=None))

    # X labels: the ends always, the middle ones only where they will not collide.
    label_every = max(1, len(points) // 6)
    for i, (p, (x, _y)) in enumerate(zip(points, coords)):
        if i not in (0, len(points) - 1) and i % label_every:
            continue
        anchor = "start" if i == 0 else "end" if i == len(points) - 1 else "middle"
        _text(d, x, bottom - 11, _truncate(str(p["label"]), FONT, 6.5, step * 1.6),
              size=6.5, color=INK_MUTED, anchor=anchor)

    # The one direct label: where the client stands now. Carried to one decimal
    # so it reads as the same number as the citation rate printed beside the
    # chart, rather than a rounded near-miss of it.
    lx, ly = coords[-1]
    _text(d, lx, ly + 8, f"{points[-1]['rate'] * 100:.1f}%", size=8.5,
          font=FONT_BOLD, color=INK, anchor="end")
    return d


# -- Citation rate by platform ------------------------------------------------

def platform_rates(width, rows):
    """Magnitude by platform: one hue, value at each bar tip."""
    rows = sorted([r for r in rows if r.get("total")],
                  key=lambda r: r["rate"], reverse=True)
    if not rows:
        return None

    band, gutter = 22.0, 76.0
    height = 62 + band * len(rows) + 16
    d, top = _card(width, height, eyebrow="Coverage",
                   title="Citation rate by AI platform",
                   subtitle="Share of each platform's responses that cite the brand.")

    left = CARD_PAD + gutter
    right = width - CARD_PAD - 34
    span = right - left
    y = top - 12

    for row in rows:
        bar_h = 9.0
        _text(d, CARD_PAD, y + 1, _truncate(row["label"], FONT_BOLD, 8, gutter - 8),
              size=8, font=FONT_BOLD, color=INK)
        _text(d, CARD_PAD, y - 8.5, f"{row['cited']} of {row['total']}", size=6.5,
              color=INK_MUTED)
        # The unfilled track is a lighter step of the bar's own hue, so the
        # reader can see each bar against the same 100% extent.
        d.add(Rect(left, y - 3.5, span, bar_h, rx=BAR_RADIUS, ry=BAR_RADIUS,
                   fillColor=SERIES_WASH, strokeColor=None))
        _bar(d, left, y - 3.5, span * row["rate"], bar_h, SERIES)
        _text(d, left + span * row["rate"] + 5, y + 0.5, _pct(row["rate"]),
              size=8, font=FONT_BOLD, color=INK)
        y -= band

    d.add(Line(left, y + band - 7, left, top - 8, strokeColor=RULE, strokeWidth=HAIRLINE))
    return d


# -- How the brand is described (diverging) -----------------------------------

def sentiment_mix(width, rows):
    """A diverging stacked bar per platform, centred on the neutral mention.

    Negative descriptions run left of the centre line, recommendations run right,
    and neutral mentions straddle it. Polarity is the question, so the form is
    diverging rather than a plain stack: a reader sees which engines speak well
    of the brand without reading a number.
    """
    rows = [r for r in rows if (r["recommended"] + r["mentioned"] + r["negative"]) > 0]
    if not rows:
        return None

    band, gutter = 24.0, 76.0
    height = 74 + band * len(rows) + 14
    d, top = _card(width, height, eyebrow="Quality",
                   title="How the brand is described, by platform",
                   subtitle="Share of that platform's citations. Hollow citations are excluded.")

    _legend(d, CARD_PAD, top - 8, [
        ("Recommended", SERIES), ("Neutral mention", NEUTRAL), ("Negative", NEGATIVE),
    ])
    y = top - 26

    left = CARD_PAD + gutter
    right = width - CARD_PAD - 38
    centre = (left + right) / 2
    half = (right - left) / 2

    for row in rows:
        total = row["recommended"] + row["mentioned"] + row["negative"]
        rec = row["recommended"] / total
        neu = row["mentioned"] / total
        neg = row["negative"] / total
        bar_h = 10.0
        _text(d, CARD_PAD, y + 1.5, _truncate(row["label"], FONT_BOLD, 8, gutter - 8),
              size=8, font=FONT_BOLD, color=INK)
        _text(d, CARD_PAD, y - 8, f"{total} cited", size=6.5, color=INK_MUTED)

        neutral_half = (neu / 2) * half
        # Negative arm: runs left, its outer tip rounded.
        neg_w = neg * half
        if neg_w:
            _bar(d, centre - neutral_half - neg_w, y - 3, neg_w - GAP, bar_h,
                 NEGATIVE, round_end="left")
        # Neutral straddles the centre.
        if neu:
            _bar(d, centre - neutral_half, y - 3, neutral_half * 2, bar_h,
                 NEUTRAL, round_end="none")
        # Recommended arm: runs right, outer tip rounded.
        rec_w = rec * half
        if rec_w:
            _bar(d, centre + neutral_half + GAP, y - 3, rec_w - GAP, bar_h,
                 SERIES, round_end="right")
        # The number that matters, at the tip of the recommended arm.
        _text(d, right + 5, y + 0.5, _pct(rec), size=8, font=FONT_BOLD, color=INK)
        y -= band

    # The centre rule last, so it reads over the fills.
    d.add(Line(centre, y + band - 6, centre, top - 20, strokeColor=INK,
               strokeWidth=HAIRLINE))
    return d


# -- Share of voice -----------------------------------------------------------

def share_of_voice(width, client_label, client_share, competitors, limit=6):
    """The client against the field, by emphasis.

    The client is the accent hue and every competitor is the de-emphasis grey,
    because the reader's question is "where do I stand", not "which grey is
    which". Bars share one denominator (all analysed responses), so the lengths
    are directly comparable.
    """
    rows = [{"label": client_label, "share": client_share, "client": True}]
    rows += [
        {"label": c["brand"], "share": c["share_of_voice"], "client": False}
        for c in competitors[:limit]
    ]
    if len(rows) < 2:
        return None
    rows.sort(key=lambda r: r["share"], reverse=True)

    shown = min(limit, len(competitors))
    band, gutter = 19.0, 108.0
    height = 62 + band * len(rows) + 12
    d, top = _card(
        width, height, eyebrow="Benchmark",
        title="The brand against its competitors",
        subtitle=f"Share of all analysed responses citing each brand. Top {shown} competitors shown.",
    )

    left = CARD_PAD + gutter
    right = width - CARD_PAD - 34
    top_share = _nice_top(max(r["share"] for r in rows))
    span = right - left
    y = top - 12

    for row in rows:
        label_font = FONT_BOLD if row["client"] else FONT
        _text(d, CARD_PAD, y, _truncate(row["label"], label_font, 8, gutter - 8),
              size=8, font=label_font, color=INK if row["client"] else INK_2)
        width_pt = span * (row["share"] / top_share if top_share else 0)
        _bar(d, left, y - 2.5, width_pt, 8.0, SERIES if row["client"] else NEUTRAL)
        _text(d, left + width_pt + 5, y, _pct(row["share"]), size=7.5,
              font=label_font, color=INK if row["client"] else INK_2)
        y -= band

    d.add(Line(left, y + band - 6, left, top - 6, strokeColor=RULE, strokeWidth=HAIRLINE))
    return d
