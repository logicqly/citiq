"""
Unit tests for client brand logos — validation, and the logo on a report cover.
No DB: validate_logo and build_pdf are pure functions over bytes.
"""
import io

import pytest

from app.services.logo_service import (
    MAX_LOGO_BYTES,
    PNG_MIME,
    SVG_MIME,
    LogoError,
    build_logo_flowable,
    sniff_logo_mime,
    validate_logo,
)


def _png_bytes(size=(64, 32)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGBA", size, (79, 70, 229, 255)).save(buf, format="PNG")
    return buf.getvalue()


SVG = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<svg xmlns="http://www.w3.org/2000/svg" width="120" height="40" viewBox="0 0 120 40">'
    b'<rect width="120" height="40" fill="#4F46E5"/></svg>'
)


# -- Sniffing: the bytes decide the format, not the upload's claims ------------

def test_sniffs_png_and_svg():
    assert sniff_logo_mime(_png_bytes()) == PNG_MIME
    assert sniff_logo_mime(SVG) == SVG_MIME
    assert sniff_logo_mime(b'<svg viewBox="0 0 1 1"></svg>') == SVG_MIME


def test_rejects_other_formats():
    # A JPEG and a plain text file are both refused, whatever they are named.
    assert sniff_logo_mime(b"\xff\xd8\xff\xe0" + b"\x00" * 32) is None
    with pytest.raises(LogoError, match="PNG or an SVG"):
        validate_logo(b"just some text, not an image at all")


def test_rejects_empty_and_oversized():
    with pytest.raises(LogoError, match="empty"):
        validate_logo(b"")
    with pytest.raises(LogoError, match="maximum"):
        validate_logo(_png_bytes()[:8] + b"\x00" * MAX_LOGO_BYTES)


def test_rejects_corrupt_png():
    # Right magic bytes, truncated body -- caught on upload, not weeks later
    # when a report tries to draw it.
    with pytest.raises(LogoError, match="corrupt"):
        validate_logo(_png_bytes()[:40])


# -- SVG is served as an image, so nothing scriptable is stored ---------------

@pytest.mark.parametrize("payload", [
    b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><a href="javascript:alert(1)">x</a></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><foreignObject><b>x</b></foreignObject></svg>',
])
def test_rejects_active_svg_content(payload):
    with pytest.raises(LogoError, match="scripting"):
        validate_logo(payload)


def test_accepts_plain_png_and_svg():
    assert validate_logo(_png_bytes()) == PNG_MIME
    assert validate_logo(SVG) == SVG_MIME


# -- Flowables: scaled into the cover's box, never failing the report ---------

def test_png_flowable_fits_the_box_and_keeps_its_aspect_ratio():
    flowable = build_logo_flowable(_png_bytes((200, 50)), PNG_MIME, 100.0, 100.0)
    assert flowable is not None
    assert flowable.drawWidth == pytest.approx(100.0)
    assert flowable.drawHeight == pytest.approx(25.0)


def test_svg_flowable_fits_the_box():
    flowable = build_logo_flowable(SVG, SVG_MIME, 60.0, 60.0)
    assert flowable is not None
    assert flowable.width <= 60.0 and flowable.height <= 60.0


def test_unrenderable_logo_is_skipped_not_raised():
    assert build_logo_flowable(b"not an image", PNG_MIME, 100.0, 100.0) is None
    assert build_logo_flowable(b"<svg>", SVG_MIME, 100.0, 100.0) is None


# -- The report itself -------------------------------------------------------

def _minimal_report() -> dict:
    return {
        "generated_at": "2026-08-19T00:00:00+00:00",
        "run": {
            "id": "11111111-1111-1111-1111-111111111111",
            "display_id": "RUN-1",
            "status": "completed",
            "created_at": "2026-08-19T00:00:00+00:00",
            "total_prompts": 0,
            "completed_prompts": 0,
        },
        "summary": {
            "total_analyses": 0,
            "overall_citation_rate": 0.0,
            "hollow_citation_count": 0,
            "citation_quality": {},
            "ungrounded_count": 0,
            "ungrounded_by_platform": {},
            "partial_count": 0,
            "partial_by_platform": {},
        },
        "platform_stats": [],
        "competitor_stats": [],
        "recommendations": [],
        "prompts": [],
    }


@pytest.mark.parametrize("logo", [
    None,
    (_png_bytes(), PNG_MIME),
    (SVG, SVG_MIME),
])
def test_report_builds_with_and_without_a_logo(logo):
    from app.services.report_service import build_pdf

    pdf = build_pdf(_minimal_report(), client_name="Acme", logo=logo)
    assert pdf.startswith(b"%PDF")


def test_report_still_builds_when_the_logo_cannot_be_drawn():
    from app.services.report_service import build_pdf

    pdf = build_pdf(_minimal_report(), client_name="Acme", logo=(b"garbage", PNG_MIME))
    assert pdf.startswith(b"%PDF")
