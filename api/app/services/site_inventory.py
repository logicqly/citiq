"""Reads a client's own website so the recommendation engine can see what
already exists there.

Client requirement (2026-07-25 call, point 8): the engine must check what is
already implemented before recommending, so it never hands back work the
client has already done. The database cannot answer that — a recommendation
marked "implemented" records what was approved, not what is actually live — so
this reads the site directly and reports three things the recommendation
prompt needs:

  - which pages exist, and what each one is about (title, description, H1/H2s)
  - what schema.org markup is already on them (JSON-LD @type values)
  - whether an llms.txt exists, and what is in it

Operating rules, all deliberate:
  - Politeness first. These are client-owned sites, but the crawler must not
    look like an attack: robots.txt is honored, concurrency is small, requests
    are spaced, and the whole crawl is capped in both pages and wall time.
  - Never fatal. Any failure yields a snapshot with ``error`` set and whatever
    was gathered; generation proceeds and says "no live site data" honestly
    rather than silently treating an unreachable site as an empty one.
  - Cached by TTL. Back-to-back runs for one client reuse a recent snapshot
    instead of re-crawling.

Parsing is deliberately regex/stdlib-based rather than pulling in a DOM
library: the fields needed are a handful of head-level tags plus the JSON-LD
blocks, and being lenient about malformed HTML matters more here than being
correct about deeply nested structure.
"""
import asyncio
import json
import re
import time
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import settings
from app.models.site_snapshot import SiteSnapshot

logger = structlog.get_logger()

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_DESC_RE = re.compile(
    r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']', re.I | re.S
)
_HEADING_RE = re.compile(r"<h([12])[^>]*>(.*?)</h\1>", re.I | re.S)
_JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.I | re.S
)
_TAG_RE = re.compile(r"<[^>]+>")
_SITEMAP_LOC_RE = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.I | re.S)
_HREF_RE = re.compile(r'<a[^>]+href=["\']([^"\']+)["\']', re.I)


def _clean_text(raw: str, limit: int = 300) -> str:
    """Strip tags/entities/whitespace out of an HTML fragment."""
    text = _TAG_RE.sub(" ", raw)
    text = (
        text.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&nbsp;", " ")
    )
    return " ".join(text.split())[:limit]


def _schema_types(html: str) -> list[str]:
    """schema.org @type values declared in JSON-LD blocks on the page.

    Handles the three shapes real sites use: a single object, an array of
    objects, and an @graph wrapper. A malformed block is skipped rather than
    failing the page — plenty of production sites ship invalid JSON-LD.
    """
    found: list[str] = []
    for block in _JSONLD_RE.findall(html):
        try:
            data = json.loads(block.strip())
        except (ValueError, TypeError):
            continue
        nodes = data if isinstance(data, list) else [data]
        expanded: list = []
        for node in nodes:
            if isinstance(node, dict) and isinstance(node.get("@graph"), list):
                expanded.extend(node["@graph"])
            else:
                expanded.append(node)
        for node in expanded:
            if not isinstance(node, dict):
                continue
            value = node.get("@type")
            if isinstance(value, str):
                found.append(value)
            elif isinstance(value, list):
                found.extend(v for v in value if isinstance(v, str))
    # Dedupe, order preserved.
    seen: set[str] = set()
    return [t for t in found if not (t in seen or seen.add(t))]


def _parse_page(url: str, html: str) -> dict:
    title_match = _TITLE_RE.search(html)
    desc_match = _DESC_RE.search(html)
    headings = [_clean_text(text, 160) for _level, text in _HEADING_RE.findall(html)]
    return {
        "url": url,
        "title": _clean_text(title_match.group(1)) if title_match else None,
        "description": _clean_text(desc_match.group(1)) if desc_match else None,
        # A handful of headings is enough to say what a page covers.
        "headings": [h for h in headings if h][:8],
        "schema_types": _schema_types(html),
    }


def _normalize_root(website: str) -> str:
    """A bare domain from the client record becomes a fetchable https root."""
    url = website.strip()
    if not url:
        return ""
    if not urlparse(url).scheme:
        url = f"https://{url}"
    return url.rstrip("/")


def _same_host(url: str, root: str) -> bool:
    """Keep the crawl on the client's own host (no outbound wandering)."""
    try:
        host = urlparse(url).netloc.lower()
        root_host = urlparse(root).netloc.lower()
    except ValueError:
        return False
    if not host or not root_host:
        return False
    return host == root_host or host == f"www.{root_host}" or f"www.{host}" == root_host


class _Crawler:
    def __init__(self, root: str, client: httpx.AsyncClient) -> None:
        self._root = root
        self._client = client
        self._robots: RobotFileParser | None = None
        self._sem = asyncio.Semaphore(max(1, settings.site_inventory_max_concurrent))

    async def _get(self, url: str) -> httpx.Response | None:
        """One polite GET. Returns None on any transport/HTTP failure."""
        async with self._sem:
            if settings.site_inventory_request_delay_seconds > 0:
                await asyncio.sleep(settings.site_inventory_request_delay_seconds)
            try:
                resp = await self._client.get(url)
            except (httpx.HTTPError, ValueError) as exc:
                logger.debug("site_fetch_failed", url=url, error=str(exc)[:200])
                return None
        if resp.status_code >= 400:
            logger.debug("site_fetch_status", url=url, status=resp.status_code)
            return None
        return resp

    async def load_robots(self) -> None:
        """Honor robots.txt. A missing or unreadable file means no rules, which
        is the standard interpretation — not a reason to refuse the crawl."""
        resp = await self._get(urljoin(self._root + "/", "robots.txt"))
        if resp is None:
            return
        parser = RobotFileParser()
        try:
            parser.parse(resp.text.splitlines())
        except Exception:  # a malformed robots.txt must not stop the crawl
            return
        self._robots = parser

    def allowed(self, url: str) -> bool:
        if self._robots is None:
            return True
        try:
            return self._robots.can_fetch(settings.site_inventory_user_agent, url)
        except Exception:
            return True

    async def llms_txt(self) -> str | None:
        resp = await self._get(urljoin(self._root + "/", "llms.txt"))
        if resp is None:
            return None
        return resp.text[:20000]

    async def discover_urls(self, limit: int) -> list[str]:
        """Candidate page URLs: sitemap first, homepage links as the fallback.

        Sitemaps are preferred because they are the site's own statement of
        what matters; link discovery only runs when there is no usable sitemap.
        """
        urls: list[str] = []
        seen: set[str] = set()

        def _add(candidate: str) -> None:
            candidate = candidate.split("#")[0].rstrip("/")
            if (
                candidate
                and candidate not in seen
                and _same_host(candidate, self._root)
                and self.allowed(candidate)
            ):
                seen.add(candidate)
                urls.append(candidate)

        _add(self._root)

        sitemap = await self._get(urljoin(self._root + "/", "sitemap.xml"))
        if sitemap is not None:
            locs = _SITEMAP_LOC_RE.findall(sitemap.text)
            # A sitemap index points at more sitemaps; follow a few.
            if locs and locs[0].endswith(".xml"):
                for child_url in locs[:5]:
                    child = await self._get(child_url)
                    if child is not None:
                        for loc in _SITEMAP_LOC_RE.findall(child.text):
                            _add(loc)
                    if len(urls) >= limit:
                        break
            else:
                for loc in locs:
                    _add(loc)

        if len(urls) < limit:
            home = await self._get(self._root)
            if home is not None:
                for href in _HREF_RE.findall(home.text):
                    _add(urljoin(self._root + "/", href))
                    if len(urls) >= limit:
                        break

        return urls[:limit]

    async def fetch_page(self, url: str) -> dict | None:
        resp = await self._get(url)
        if resp is None:
            return None
        content_type = resp.headers.get("content-type", "")
        if "html" not in content_type.lower():
            return None
        return _parse_page(url, resp.text[: settings.site_inventory_page_max_bytes])


async def _crawl(root: str) -> tuple[list[dict], str | None, str | None]:
    """(pages, llms_txt_content, error) for one site."""
    limits = httpx.Limits(
        max_connections=max(1, settings.site_inventory_max_concurrent),
        max_keepalive_connections=max(1, settings.site_inventory_max_concurrent),
    )
    async with httpx.AsyncClient(
        timeout=settings.site_inventory_request_timeout_seconds,
        follow_redirects=True,
        headers={"User-Agent": settings.site_inventory_user_agent},
        limits=limits,
    ) as http:
        crawler = _Crawler(root, http)
        await crawler.load_robots()
        llms_txt = await crawler.llms_txt()
        urls = await crawler.discover_urls(settings.site_inventory_max_pages)
        if not urls:
            return [], llms_txt, "no reachable pages found"
        results = await asyncio.gather(
            *[crawler.fetch_page(u) for u in urls], return_exceptions=True
        )
        pages = [r for r in results if isinstance(r, dict)]
        error = None if pages else "every page fetch failed"
        return pages, llms_txt, error


async def _recent_snapshot(
    client_id: uuid.UUID, session_factory: async_sessionmaker
) -> SiteSnapshot | None:
    """The newest snapshot inside the TTL, or None."""
    ttl_hours = settings.site_inventory_ttl_hours
    if ttl_hours <= 0:
        return None
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=ttl_hours)
    async with session_factory() as db:
        return (
            await db.execute(
                select(SiteSnapshot)
                .where(
                    SiteSnapshot.client_id == client_id,
                    SiteSnapshot.fetched_at >= cutoff,
                )
                .order_by(SiteSnapshot.fetched_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()


async def get_site_snapshot(
    client_id: uuid.UUID,
    website: str | None,
    session_factory: async_sessionmaker,
) -> SiteSnapshot | None:
    """The client's current site inventory: a cached snapshot, or a fresh crawl.

    Returns None only when there is nothing to crawl (no website on record) or
    the feature is switched off. A failed crawl still returns a snapshot, with
    ``error`` set — the caller says so in the prompt rather than pretending the
    site is empty.
    """
    if not settings.site_inventory_enabled:
        return None
    root = _normalize_root(website or "")
    if not root:
        return None

    cached = await _recent_snapshot(client_id, session_factory)
    if cached is not None:
        logger.info(
            "site_snapshot_reused",
            client_id=str(client_id),
            pages=cached.page_count,
            fetched_at=cached.fetched_at.isoformat(),
        )
        return cached

    log = logger.bind(client_id=str(client_id), root=root)
    log.info("site_crawl_start", max_pages=settings.site_inventory_max_pages)
    started = time.monotonic()
    try:
        pages, llms_txt, error = await asyncio.wait_for(
            _crawl(root), timeout=settings.site_inventory_total_timeout_seconds
        )
    except TimeoutError:
        pages, llms_txt, error = [], None, (
            f"crawl exceeded {settings.site_inventory_total_timeout_seconds:g}s"
        )
    except Exception as exc:
        pages, llms_txt, error = [], None, f"{type(exc).__name__}: {str(exc)[:200]}"
    duration_ms = int((time.monotonic() - started) * 1000)

    snapshot = SiteSnapshot(
        client_id=client_id,
        root_url=root,
        fetched_at=datetime.now(UTC).replace(tzinfo=None),
        pages=pages,
        page_count=len(pages),
        llms_txt_present=bool(llms_txt),
        llms_txt_content=llms_txt,
        error=error,
        duration_ms=duration_ms,
    )
    try:
        async with session_factory() as db:
            async with db.begin():
                db.add(snapshot)
                await db.flush()
                db.expunge(snapshot)
    except Exception as exc:
        # An unstorable snapshot is still usable for this run.
        log.warning("site_snapshot_persist_failed", error=str(exc)[:200])

    log.info(
        "site_crawl_done", pages=len(pages), llms_txt=bool(llms_txt),
        duration_ms=duration_ms, error=error,
    )
    return snapshot


def render_for_prompt(snapshot: SiteSnapshot | None, max_chars: int = 12000) -> str:
    """The snapshot as prompt text: what exists on the site today.

    Says plainly when there is no data, so the model treats "unknown" as
    unknown instead of inferring that nothing is implemented.
    """
    if snapshot is None:
        return (
            "No live site data was collected for this client "
            "(no website on record, or site inspection is disabled). "
            "Do not assume anything about what is already implemented."
        )
    if snapshot.error and not snapshot.pages:
        return (
            f"The client website ({snapshot.root_url}) could not be read: "
            f"{snapshot.error}. Do not assume anything about what is already "
            "implemented."
        )

    lines = [f"Live site inventory for {snapshot.root_url} "
             f"({snapshot.page_count} pages read):"]
    if snapshot.error:
        lines.append(f"NOTE: the crawl was partial ({snapshot.error}).")

    all_schema: set[str] = set()
    for page in snapshot.pages or []:
        types = page.get("schema_types") or []
        all_schema.update(types)
        title = page.get("title") or "(untitled)"
        entry = f"- {page.get('url')} | {title}"
        if types:
            entry += f" | schema: {', '.join(types[:6])}"
        headings = page.get("headings") or []
        if headings:
            entry += f" | sections: {'; '.join(headings[:4])}"
        lines.append(entry)

    lines.append(
        "Schema types already present sitewide: "
        + (", ".join(sorted(all_schema)) if all_schema else "none detected")
    )
    if snapshot.llms_txt_present:
        content = (snapshot.llms_txt_content or "")[:2000]
        lines.append(f"llms.txt EXISTS. Current content:\n{content}")
    else:
        lines.append("llms.txt: not present on the site.")

    return "\n".join(lines)[:max_chars]
