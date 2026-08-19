"""
Client brand logo — validation, storage helpers and PDF rendering.

An admin uploads a PNG or SVG per client (admin_clients). The bytes are stored
on the clients row (see the 0037 migration for why not object storage) and read
back in three places:

  - the admin console preview (GET /admin/clients/{id}/logo)
  - the client-facing GEO Monitor header (GET /client/dashboard/logo)
  - the cover of that client's generated run reports (report_service.build_pdf)

Only PNG and SVG are accepted, and the format is decided by sniffing the bytes,
never by trusting the upload's declared content type or file extension.
"""
import io
import re

MAX_LOGO_BYTES = 512 * 1024  # 512 KB — ample for a logo, small enough to inline in a row.

PNG_MIME = "image/png"
SVG_MIME = "image/svg+xml"
ALLOWED_MIMES = (PNG_MIME, SVG_MIME)

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# Active content an SVG can carry. An <img> tag never executes any of it, but the
# logo is also reachable as a top-level URL, so anything scriptable is rejected at
# the door rather than relied on being rendered harmlessly.
_SVG_FORBIDDEN = (
    re.compile(rb"<\s*script", re.IGNORECASE),
    re.compile(rb"<\s*foreignObject", re.IGNORECASE),
    re.compile(rb"\son\w+\s*=", re.IGNORECASE),      # onload=, onclick=, ...
    re.compile(rb"javascript\s*:", re.IGNORECASE),
)


class LogoError(ValueError):
    """Upload rejected — the message is safe to return to the admin."""


def sniff_logo_mime(data: bytes) -> str | None:
    """The logo's real MIME type from its bytes, or None if it is neither PNG nor SVG."""
    if data.startswith(_PNG_MAGIC):
        return PNG_MIME
    # SVG is text: an <svg> root, optionally behind an XML declaration, a BOM,
    # a doctype or comments.
    head = data[:1024].lstrip(b"\xef\xbb\xbf").lstrip()
    if head.startswith(b"<?xml") or head.startswith(b"<!DOCTYPE") or head.startswith(b"<"):
        if re.search(rb"<\s*svg[\s>]", data[:4096], re.IGNORECASE):
            return SVG_MIME
    return None


def validate_logo(data: bytes) -> str:
    """Validate uploaded logo bytes, returning the MIME type to store.

    Raises LogoError with an admin-facing message when the upload is not a
    usable PNG or SVG.
    """
    if not data:
        raise LogoError("The uploaded file is empty")
    if len(data) > MAX_LOGO_BYTES:
        # No actual size in the message: callers stop reading one byte past the
        # limit, so the length here is the cap, not the file's real size.
        raise LogoError(f"Logo is larger than the {MAX_LOGO_BYTES // 1024} KB maximum")

    mime = sniff_logo_mime(data)
    if mime is None:
        raise LogoError("Logo must be a PNG or an SVG file")

    if mime == SVG_MIME:
        for pattern in _SVG_FORBIDDEN:
            if pattern.search(data):
                raise LogoError(
                    "SVG contains scripting or embedded HTML, which is not allowed in a logo"
                )
    else:
        # A file can start with the PNG magic and still be truncated or corrupt;
        # decoding it here means a broken logo is caught on upload rather than
        # when a report is generated weeks later.
        try:
            from PIL import Image

            Image.open(io.BytesIO(data)).verify()
        except LogoError:
            raise
        except Exception:
            raise LogoError("PNG file could not be read; it may be corrupt")

    return mime


def logo_response_headers(mime: str, updated_at) -> dict[str, str]:
    """Headers for a logo byte response.

    The logo is private per tenant, so it is cached by the browser only
    (`private`), and revalidated on every use — a re-upload has to show up in the
    header without a hard refresh. SVG is served with scripting locked off and
    sniffing disabled, so a direct hit on the URL cannot run anything.
    """
    headers = {
        "Cache-Control": "private, no-cache, max-age=0, must-revalidate",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
        "Content-Disposition": "inline",
    }
    if updated_at is not None:
        headers["ETag"] = f'"{int(updated_at.timestamp())}"'
    return headers


def build_logo_flowable(data: bytes, mime: str, max_width_pt: float, max_height_pt: float):
    """A reportlab flowable drawing the logo, scaled to fit the given box.

    Returns None when the logo cannot be rendered (an SVG with no converter
    installed, or bytes that fail to decode) so a report always builds — a run
    report without its logo is still a report.
    """
    try:
        if mime == SVG_MIME:
            return _svg_flowable(data, max_width_pt, max_height_pt)
        return _raster_flowable(data, max_width_pt, max_height_pt)
    except Exception:
        return None


def _raster_flowable(data: bytes, max_width_pt: float, max_height_pt: float):
    from reportlab.lib.utils import ImageReader
    from reportlab.platypus import Image

    reader = ImageReader(io.BytesIO(data))
    src_w, src_h = reader.getSize()
    if not src_w or not src_h:
        return None
    scale = min(max_width_pt / src_w, max_height_pt / src_h)
    return Image(io.BytesIO(data), width=src_w * scale, height=src_h * scale, mask="auto")


def _svg_flowable(data: bytes, max_width_pt: float, max_height_pt: float):
    # svglib is an optional dependency: without it SVG logos simply do not print,
    # rather than the whole report failing to build.
    from svglib.svglib import svg2rlg

    drawing = svg2rlg(io.BytesIO(data))
    if drawing is None or not drawing.width or not drawing.height:
        return None
    scale = min(max_width_pt / drawing.width, max_height_pt / drawing.height)
    drawing.scale(scale, scale)
    drawing.width *= scale
    drawing.height *= scale
    return drawing


async def fetch_client_logo(session, client_id):
    """The stored logo for a client as (data, mime, filename, updated_at), or None.

    Selects the four columns directly rather than loading a Client: logo_data is
    deferred on the ORM model, so a plain entity load would either miss it or
    fire a lazy load the async session cannot service.
    """
    from sqlalchemy import select

    from app.models.client import Client

    row = (
        await session.execute(
            select(
                Client.logo_data,
                Client.logo_mime,
                Client.logo_filename,
                Client.logo_updated_at,
            ).where(Client.id == client_id)
        )
    ).one_or_none()

    if row is None or row.logo_data is None or row.logo_mime is None:
        return None
    return bytes(row.logo_data), row.logo_mime, row.logo_filename, row.logo_updated_at
