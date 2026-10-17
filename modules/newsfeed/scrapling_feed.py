"""Policy-gated RSS/Atom/JSON/HTML ingestion using Scrapling's ordinary HTTP Fetcher.

No live sources are configured here. A feed/page must be explicitly registered
only after its robots policy and intended-use terms have been reviewed. The
reader requests only that exact registered URL: it does not crawl links, fetch
article pages, follow redirects, load browsers, impersonate, use proxies, or
retry around access controls. RSS/Atom XML is parsed with ElementTree because
Scrapling officially parses HTML only; Scrapling performs the HTTP request.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import html
from html.entities import html5 as HTML5_NAMED_ENTITIES
import json
import re
import time
from typing import Any, Callable, Iterable, Mapping, Optional, Protocol
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
import xml.etree.ElementTree as ET

SCHEMA_VERSION = "1.0"
USER_AGENT = "FusionOffice-NewsFeed/0.1 (ordinary feed/API client)"
ACCEPT_HEADER = "application/rss+xml, application/atom+xml, application/json, application/xml, text/xml;q=0.9, text/html;q=0.8"
FORMATS = frozenset({"rss", "atom", "json", "html"})
TRACKING_PARAMS = frozenset({"fbclid", "gclid", "mc_cid", "mc_eid"})


class FeedPolicyError(ValueError):
    """Raised when a source has not been explicitly approved for this use."""


class FeedParseError(ValueError):
    """Raised for malformed or unsupported feed/API payloads."""


@dataclass(frozen=True)
class FeedSource:
    source_id: str
    feed_url: str
    provider: str
    publisher: str
    market: str = "UNKNOWN"  # CN | HK | US | UNKNOWN
    allowed_hosts: tuple[str, ...] = ()
    feed_format: str = "rss"  # rss | atom | json | html
    terms_url: str = ""
    terms_reviewed: bool = False
    robots_allowed: bool = False
    rights_scope: str = ""  # cite the reviewed rights/use scope; never infer it
    authorized_scopes: tuple[str, ...] = ()  # local_personal | internal | public | redistribution
    attribution_required: bool = True
    cache_until: Optional[str] = None
    enabled: bool = False
    min_poll_seconds: float = 60.0
    max_items: int = 100
    max_body_bytes: int = 2_000_000
    max_stale_seconds: int = 900
    language: Optional[str] = None
    include_summary: bool = False
    allow_html_named_entities: bool = False
    # JSON mapping: items_path and fields {title,url,published_at,id,symbol,instrument_id}
    json_items_path: str = "items"
    json_fields: Optional[Mapping[str, str]] = None
    # HTML selector mapping: item,title,url,published_at,id,symbol,instrument_id.
    # Values for title/time should use ::text; URL/ID attributes use ::attr(href/id).
    html_selectors: Optional[Mapping[str, str]] = None

    def validate(self) -> None:
        if not self.source_id.strip() or not self.provider.strip() or not self.publisher.strip():
            raise FeedPolicyError("source_id, provider and publisher are required")
        if not self.enabled:
            raise FeedPolicyError("source is disabled")
        if not self.terms_reviewed or not self.terms_url.strip() or not self.rights_scope.strip():
            raise FeedPolicyError("terms and intended-use rights must be reviewed explicitly")
        if not self.robots_allowed:
            raise FeedPolicyError("robots policy has not been confirmed for this endpoint")
        if not self.authorized_scopes:
            raise FeedPolicyError("at least one exact authorized scope must be approved")
        if set(self.authorized_scopes) - {"local_personal", "internal", "public", "redistribution"}:
            raise FeedPolicyError("authorized_scopes contains an unsupported scope")
        if self.feed_format not in FORMATS:
            raise FeedPolicyError("only registered RSS, Atom, JSON API or HTML listing endpoints are supported")
        parsed = urlparse(self.feed_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise FeedPolicyError("endpoint must be HTTPS and contain no embedded credentials")
        allowed = {host.strip().lower().rstrip(".") for host in self.allowed_hosts if host.strip()}
        if not allowed or parsed.hostname.lower().rstrip(".") not in allowed:
            raise FeedPolicyError("endpoint host is not in the exact host allow-list")
        if self.market not in {"CN", "HK", "US", "UNKNOWN"}:
            raise FeedPolicyError("market must be CN, HK, US or UNKNOWN")
        if self.min_poll_seconds < 0 or self.max_items < 1 or self.max_body_bytes < 1:
            raise FeedPolicyError("invalid polling or payload limits")
        if self.allow_html_named_entities and self.feed_format not in {"rss", "atom"}:
            raise FeedPolicyError("HTML named-entity compatibility applies only to XML feeds")
        if self.feed_format == "html" and not (self.html_selectors or {}).get("item"):
            raise FeedPolicyError("approved HTML list sources require an explicit item selector")


@dataclass(frozen=True)
class HTTPResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes
    json_data: Any = None
    document: Any = None  # Scrapling Response for explicit HTML CSS selection


class HTTPTransport(Protocol):
    def get(self, url: str, headers: Mapping[str, str], timeout: float) -> HTTPResponse: ...


class ScraplingTransport:
    """Scrapling Fetcher, with its anti-detection and routing features disabled."""

    def get(self, url: str, headers: Mapping[str, str], timeout: float) -> HTTPResponse:
        try:
            from scrapling.fetchers import Fetcher
        except ImportError as exc:
            raise RuntimeError('Install modules/newsfeed/requirements.txt to enable Scrapling') from exc

        response = Fetcher.get(
            url,
            headers=dict(headers),
            timeout=timeout,
            retries=0,
            follow_redirects=False,
            stealthy_headers=False,
            impersonate=None,
            proxy=None,
            proxies=None,
            proxy_rotator=None,
            verify=True,
        )
        response_headers = {str(k): str(v) for k, v in response.headers.items()}
        content_type = _casefold_headers(response_headers).get("content-type", "").lower()
        json_data = None
        document = None
        if "json" in content_type:
            try:
                json_data = response.json()
            except Exception as exc:
                raise FeedParseError(f"Scrapling JSON parsing failed: {exc}") from exc
        elif "html" in content_type or "xhtml" in content_type:
            document = response
        return HTTPResponse(
            status=int(response.status),
            headers=response_headers,
            body=bytes(response.body),
            json_data=json_data,
            document=document,
        )


@dataclass(frozen=True)
class FeedHealth:
    source_id: str
    status: str  # ok, not_modified, rate_limited_local, denied, failed
    checked_at: Optional[str] = None
    last_success_at: Optional[str] = None
    is_stale: Optional[bool] = None
    http_status: Optional[int] = None
    retry_after_seconds: Optional[float] = None
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    error_code: Optional[str] = None
    error_detail: Optional[str] = None
    recovered_from: Optional[str] = None


@dataclass(frozen=True)
class FeedResult:
    records: tuple[dict[str, Any], ...]
    health: FeedHealth


class ScraplingNewsProvider:
    """Approved-source NewsProvider implementation; no enabled sources by default."""

    def __init__(
        self,
        sources: Iterable[FeedSource] = (),
        transport: Optional[HTTPTransport] = None,
        *,
        timeout_seconds: float = 8.0,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.sources = {source.source_id: source for source in sources}
        self.transport = transport or ScraplingTransport()
        self.timeout_seconds = timeout_seconds
        self.clock = clock
        self.monotonic = monotonic
        self._conditional_headers: dict[str, dict[str, str]] = {}
        self._last_success: dict[str, datetime] = {}
        self._last_attempt: dict[str, float] = {}
        self._last_records: dict[str, tuple[dict[str, Any], ...]] = {}
        self._retry_until: dict[str, float] = {}

    def read(self, source_id: str) -> FeedResult:
        source = self.sources.get(source_id)
        if source is None:
            now = self._now()
            return FeedResult((), FeedHealth(source_id, "denied", now, error_code="unknown_source",
                                             error_detail="source is not registered"))
        now = self._now()
        try:
            source.validate()
        except FeedPolicyError as exc:
            return FeedResult((), FeedHealth(source_id, "denied", now, error_code="policy_denied",
                                             error_detail=str(exc)))

        tick = self.monotonic()
        retry_until = self._retry_until.get(source_id)
        if retry_until is not None and tick < retry_until:
            remaining = retry_until - tick
            return FeedResult(self._cached_records(source, now), FeedHealth(
                source_id, "rate_limited_local", now, self._last_success_iso(source_id),
                self._is_stale(source, now), retry_after_seconds=remaining,
                error_code="retry_after",
            ))
        last_attempt = self._last_attempt.get(source_id)
        if last_attempt is not None and tick - last_attempt < source.min_poll_seconds:
            elapsed = tick - last_attempt
            return FeedResult(self._cached_records(source, now), FeedHealth(
                source_id, "rate_limited_local", now, self._last_success_iso(source_id),
                self._is_stale(source, now), retry_after_seconds=max(0.0, source.min_poll_seconds - elapsed),
                error_code="poll_interval",
            ))
        self._last_attempt[source_id] = tick

        headers = {"User-Agent": USER_AGENT, "Accept": ACCEPT_HEADER}
        headers.update(self._conditional_headers.get(source_id, {}))
        try:
            response = self.transport.get(source.feed_url, headers, self.timeout_seconds)
        except Exception as exc:
            return self._failure(source, now, "transport_error", exc)

        response_headers = _casefold_headers(response.headers)
        etag = response_headers.get("etag")
        last_modified = response_headers.get("last-modified")
        if response.status == 304:
            self._remember_success(source_id, now)
            cached = []
            for record in self._last_records.get(source_id, ()):
                item = dict(record)
                item["status"] = "fresh"
                cached.append(item)
            return FeedResult(tuple(cached), FeedHealth(source_id, "not_modified", now,
                                                       self._last_success_iso(source_id), False, 304,
                                                       etag=etag, last_modified=last_modified))
        if 300 <= response.status < 400:
            return self._failure(source, now, "redirect_blocked", RuntimeError("redirects are disabled"),
                                 status=response.status, headers=response_headers)
        if response.status == 429:
            return self._failure(source, now, "rate_limited", RuntimeError("source returned HTTP 429"),
                                 status=429, headers=response_headers)
        if not 200 <= response.status < 300:
            return self._failure(source, now, "http_error", RuntimeError(f"source returned HTTP {response.status}"),
                                 status=response.status, headers=response_headers)
        if len(response.body) > source.max_body_bytes:
            return self._failure(source, now, "payload_too_large", RuntimeError("payload exceeds configured limit"),
                                 status=response.status, headers=response_headers)

        try:
            items = parse_payload(response, source)
            records = tuple(_to_contract_record(source, item, now) for item in items)
        except FeedParseError as exc:
            return self._failure(source, now, "parse_error", exc, status=response.status,
                                 headers=response_headers)

        self._remember_success(source_id, now)
        self._last_records[source_id] = records
        validators = {}
        if etag:
            validators["If-None-Match"] = etag
        if last_modified:
            validators["If-Modified-Since"] = last_modified
        self._conditional_headers[source_id] = validators
        return FeedResult(records, FeedHealth(source_id, "ok", now, self._last_success_iso(source_id),
                                              False, response.status, etag=etag, last_modified=last_modified))

    def read_with_fallback(self, source_ids: Iterable[str]) -> FeedResult:
        """Try only explicitly supplied registered feeds; never invent a fallback."""
        last: Optional[FeedResult] = None
        for source_id in source_ids:
            result = self.read(source_id)
            if result.health.status in {"ok", "not_modified"}:
                if last and result.health.status == "ok":
                    health = FeedHealth(**{**result.health.__dict__, "recovered_from": last.health.source_id})
                    return FeedResult(result.records, health)
                return result
            last = result
        if last:
            return last
        now = self._now()
        return FeedResult((), FeedHealth("", "failed", now, error_code="no_sources",
                                         error_detail="no approved fallback sources configured"))

    def _failure(self, source: FeedSource, now: str, code: str, exc: Exception, *,
                 status: Optional[int] = None, headers: Optional[Mapping[str, str]] = None) -> FeedResult:
        headers = _casefold_headers(headers or {})
        last_success = self._last_success_iso(source.source_id)
        retry_after = _retry_after(headers, now)
        if status == 429 and retry_after is not None:
            attempted_at = self._last_attempt.get(source.source_id)
            if attempted_at is None:
                attempted_at = self.monotonic()
            self._retry_until[source.source_id] = attempted_at + retry_after
        health = FeedHealth(
            source_id=source.source_id, status="failed", checked_at=now, last_success_at=last_success,
            is_stale=self._is_stale(source, now), http_status=status,
            retry_after_seconds=retry_after, etag=headers.get("etag"),
            last_modified=headers.get("last-modified"), error_code=code, error_detail=str(exc)[:240],
        )
        return FeedResult(self._cached_records(source, now), health)

    def _now(self) -> str:
        current = self.clock()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return current.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    def _remember_success(self, source_id: str, now: str) -> None:
        self._last_success[source_id] = datetime.fromisoformat(now.replace("Z", "+00:00"))

    def _last_success_iso(self, source_id: str) -> Optional[str]:
        last = self._last_success.get(source_id)
        return last.isoformat(timespec="seconds").replace("+00:00", "Z") if last else None

    def _is_stale(self, source: FeedSource, now_iso: str) -> Optional[bool]:
        last = self._last_success.get(source.source_id)
        if last is None:
            return None
        now = datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
        return (now - last).total_seconds() > source.max_stale_seconds

    def _cached_records(self, source: FeedSource, now_iso: str) -> tuple[dict[str, Any], ...]:
        cached = self._last_records.get(source.source_id, ())
        if not cached:
            return ()
        status = "stale" if self._is_stale(source, now_iso) else "delayed"
        result = []
        for record in cached:
            item = dict(record)
            item["status"] = status
            result.append(item)
        return tuple(result)


def parse_payload(response: HTTPResponse, source: FeedSource) -> list[dict[str, Any]]:
    if source.feed_format in {"rss", "atom"}:
        return parse_xml_feed(response.body, source)
    if source.feed_format == "json":
        payload = response.json_data
        if payload is None:
            try:
                payload = json.loads(response.body.decode("utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise FeedParseError(f"invalid JSON response: {exc}") from exc
        return parse_json_items(payload, source)
    if source.feed_format == "html":
        if response.document is None:
            raise FeedParseError("HTML endpoint requires Scrapling's parsed Response")
        return parse_html_listing(response.document, source)
    raise FeedParseError("unsupported feed format")


def parse_xml_feed(body: bytes, source: FeedSource) -> list[dict[str, Any]]:
    """Parse RSS/Atom XML after Scrapling has fetched the exact approved feed URL."""
    if not body:
        raise FeedParseError("empty feed body")
    if re.search(br"<!\s*(?:DOCTYPE|ENTITY)\b", body, flags=re.IGNORECASE):
        raise FeedParseError("DTD/entity declarations are not accepted")
    if source.allow_html_named_entities:
        body = _normalize_html_named_entities(body)
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise FeedParseError(f"invalid XML: {exc}") from exc
    root_name = _local_name(root.tag).lower()
    if source.feed_format == "rss":
        entries = [node for node in root.iter() if _local_name(node.tag).lower() == "item"]
        if root_name not in {"rss", "rdf"} and not entries:
            raise FeedParseError("expected RSS channel/item structure")
    else:
        entries = [node for node in root.iter() if _local_name(node.tag).lower() == "entry"]
        if root_name != "feed":
            raise FeedParseError("expected Atom feed structure")

    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in entries:
        title = _child_text(entry, "title")
        url = _entry_link(entry, source.feed_url)
        if not title or not url:
            continue
        raw_time = (_child_text(entry, "pubdate") or _child_text(entry, "published") or
                    _child_text(entry, "updated") or _child_text(entry, "date"))
        raw_id = _child_text(entry, "guid") or _child_text(entry, "id") or ""
        item = {"title": title, "url": url, "published_at": raw_time or None, "id": raw_id or None}
        item.update(_optional_fields(entry, source))
        key = _dedup_key(source.source_id, item)
        if key not in seen:
            seen.add(key)
            items.append(item)
        if len(items) >= source.max_items:
            break
    return items


def _normalize_html_named_entities(body: bytes) -> bytes:
    """Decode only known HTML5 named character references; never resolve DTDs."""
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise FeedParseError("HTML-entity XML feed is not valid UTF-8") from exc

    xml_predefined = {"amp", "lt", "gt", "quot", "apos"}

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in xml_predefined:
            return match.group(0)
        return HTML5_NAMED_ENTITIES.get(name + ";", match.group(0))

    return re.sub(r"&([A-Za-z][A-Za-z0-9]+);", replace, text).encode("utf-8")


def parse_json_items(payload: Any, source: FeedSource) -> list[dict[str, Any]]:
    fields = {"title": "title", "url": "url", "published_at": "published_at", "id": "id",
              "symbol": "symbol", "instrument_id": "instrument_id"}
    fields.update(dict(source.json_fields or {}))
    rows = _path_get(payload, source.json_items_path)
    if not isinstance(rows, list):
        raise FeedParseError("configured JSON items_path did not resolve to a list")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        item = {name: _path_get(row, path) if path else None for name, path in fields.items()}
        item = {key: (str(value).strip() if value is not None else None) for key, value in item.items()}
        if not item.get("title") or not item.get("url"):
            continue
        item["url"] = urljoin(source.feed_url, item["url"])
        if urlparse(item["url"]).scheme not in {"http", "https"}:
            continue
        key = _dedup_key(source.source_id, item)
        if key not in seen:
            seen.add(key)
            items.append(item)
        if len(items) >= source.max_items:
            break
    return items


def parse_html_listing(document: Any, source: FeedSource) -> list[dict[str, Any]]:
    """Parse one pre-approved listing page using Scrapling CSS selectors only."""
    selectors = dict(source.html_selectors or {})
    try:
        cards = document.css(selectors["item"])
    except Exception as exc:
        raise FeedParseError(f"invalid HTML item selector: {exc}") from exc
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for card in cards:
        item = {}
        for field_name in ("title", "url", "published_at", "id", "symbol", "instrument_id"):
            selector = selectors.get(field_name)
            if selector:
                try:
                    value = card.css(selector).get()
                except Exception as exc:
                    raise FeedParseError(f"invalid HTML {field_name} selector: {exc}") from exc
                item[field_name] = _clean_html_text(value) if value is not None else None
        if not item.get("title") or not item.get("url"):
            continue
        item["url"] = urljoin(source.feed_url, item["url"])
        if urlparse(item["url"]).scheme not in {"http", "https"}:
            continue
        key = _dedup_key(source.source_id, item)
        if key not in seen:
            seen.add(key)
            items.append(item)
        if len(items) >= source.max_items:
            break
    return items


def _to_contract_record(source: FeedSource, item: Mapping[str, Any], received_at: str) -> dict[str, Any]:
    title = str(item.get("title") or "").strip()
    canonical_url = _canonical_url(str(item.get("url") or ""))
    source_item_id = str(item.get("id") or "").strip() or None
    published_raw = str(item.get("published_at") or "").strip() or None
    published_at = _parse_timestamp(published_raw) if published_raw else None
    dedup_key = _dedup_key(source.source_id, item)
    related = []
    if item.get("instrument_id") or item.get("symbol"):
        related.append({
            "instrument_id": item.get("instrument_id") or None,
            "symbol": item.get("symbol") or None,
            "method": "provider-field",
            "confidence": 1.0 if item.get("instrument_id") else 0.85,
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "record_type": "news",
        "domain": "finance",
        "id": _stable_record_id(source.source_id, source_item_id, canonical_url, title),
        "provider": source.provider,
        "publisher": source.publisher,
        "summary": item.get("summary") or None,
        "market": source.market,
        "instrument_id": item.get("instrument_id") or None,
        "symbol": item.get("symbol") or None,
        "currency": None,
        "event_ts": published_at,
        "published_at": published_at,
        "published_at_raw": published_raw,
        "received_at": received_at,
        "status": "fresh",
        "source_url": canonical_url,
        "canonical_url": canonical_url,
        "source_id": source_item_id,
        "feed_source_id": source.source_id,
        "dedup_key": dedup_key,
        "related_instruments": related,
        "policy": {
            "authorized_scopes": list(source.authorized_scopes),
            "cache_until": source.cache_until,
            "attribution_required": source.attribution_required,
            "policy_reference": source.terms_url or None,
            "rights_scope": source.rights_scope or None,
        },
        "news": {
            "title": title,
            "language": source.language,
            "content_type": "news",
            "is_update": False,
            "withdrawn_at": None,
            "cluster_id": dedup_key,
        },
    }


def _optional_fields(entry: ET.Element, source: FeedSource) -> dict[str, Any]:
    # Never retain description/content unless the feed is explicitly approved to
    # expose its RSS summary; only the short description field is considered.
    fields = {}
    for name in ("symbol", "instrument_id"):
        value = _child_text(entry, name)
        if value:
            fields[name] = value
    if source.include_summary:
        raw = _child_text(entry, "description")
        if raw:
            raw = re.sub(r"(?is)<(script|style)\b[^>]*>.*?</\1\s*>", " ", raw)
            summary = html.unescape(re.sub(r"(?is)<[^>]*>", " ", raw))
            summary = " ".join(summary.split())
            if summary:
                fields["summary"] = summary[:700].rstrip()
    return fields


def _entry_link(entry: ET.Element, feed_url: str) -> Optional[str]:
    links = [node for node in list(entry) if _local_name(node.tag).lower() == "link"]
    if not links:
        return None
    chosen = next((node for node in links if node.attrib.get("rel", "alternate") == "alternate"), links[0])
    raw = chosen.attrib.get("href") or (chosen.text or "").strip()
    if not raw:
        return None
    absolute = urljoin(feed_url, raw.strip())
    if urlparse(absolute).scheme not in {"http", "https"} or not urlparse(absolute).hostname:
        return None
    return absolute


def _child_text(parent: ET.Element, name: str) -> str:
    for child in list(parent):
        if _local_name(child.tag).lower() == name.lower():
            value = "".join(child.itertext()).strip()
            if value:
                return " ".join(value.split())
    return ""


def _path_get(data: Any, dotted_path: str) -> Any:
    current = data
    if not dotted_path:
        return current
    for part in dotted_path.split("."):
        if isinstance(current, Mapping):
            current = current.get(part)
        else:
            return None
    return current


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].split(":")[-1]


def _clean_html_text(value: Any) -> str:
    text = str(value).strip()
    if text.startswith('"') and text.endswith('"'):
        text = text[1:-1]
    return " ".join(text.split())


def _parse_timestamp(raw: str) -> Optional[str]:
    try:
        try:
            parsed = parsedate_to_datetime(raw)
        except (TypeError, ValueError, OverflowError):
            parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        return None  # retain raw value; never guess its timezone
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _canonical_url(url: str) -> str:
    parsed = urlparse(url)
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
             if not (k.lower().startswith("utm_") or k.lower() in TRACKING_PARAMS)]
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/",
                       parsed.params, urlencode(query, doseq=True), ""))


def _dedup_key(source_id: str, item: Mapping[str, Any]) -> str:
    raw_id = str(item.get("id") or "").strip()
    if raw_id:
        return f"id:{source_id}:{raw_id}"
    url = _canonical_url(str(item.get("url") or ""))
    if url:
        return f"url:{url}"
    title = re.sub(r"\W+", "", str(item.get("title") or "").casefold(), flags=re.UNICODE)
    return f"title:{hashlib.sha256(title.encode('utf-8')).hexdigest()[:20]}" if title else f"source:{source_id}:unknown"


def _stable_record_id(source_id: str, raw_id: Optional[str], canonical_url: str, title: str) -> str:
    seed = f"{source_id}\n{raw_id or ''}\n{canonical_url}\n{title.casefold()}".encode("utf-8")
    return hashlib.sha256(seed).hexdigest()[:24]


def _casefold_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {str(k).lower(): str(v) for k, v in headers.items()}


def _retry_after(headers: Mapping[str, str], now_iso: str) -> Optional[float]:
    value = headers.get("retry-after")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            target = parsedate_to_datetime(value)
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            now = datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
            return max(0.0, (target.astimezone(timezone.utc) - now).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None
