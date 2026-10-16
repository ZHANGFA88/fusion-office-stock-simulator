"""Provider-neutral news normalization for the shared v1.0 data contract.

No network access, provider SDK, article body, or authorization inference belongs
in this module. Source-time uncertainty is preserved rather than guessed.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import re
from typing import Any, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .securities import normalize_related_instruments

SCHEMA_VERSION = "1.0"
MARKETS = frozenset({"CN", "HK", "US", "UNKNOWN"})
STATUSES = frozenset({"fresh", "delayed", "stale", "unavailable", "closed", "error"})
_TRACKING_PARAMS = frozenset({"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source"})
AUTHORIZED_SCOPES = frozenset({"local_personal", "internal", "public", "redistribution"})


class NewsNormalizationError(ValueError):
    """Invalid required news input; callers should retain this as source health."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _value(obj: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(obj, Mapping) and name in obj:
            value = obj.get(name)
        else:
            value = getattr(obj, name, None)
        if value is not None:
            return value
    return default


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_datetime(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            return parsedate_to_datetime(text)
        except (TypeError, ValueError, OverflowError):
            return None


def _localize_unambiguous(value: datetime, zone_name: str) -> tuple[Optional[datetime], Optional[str]]:
    """Attach an explicitly configured source zone only when the wall time is unique."""
    try:
        zone = ZoneInfo(zone_name)
    except (ZoneInfoNotFoundError, ValueError):
        return None, "invalid_source_timezone"
    first = value.replace(tzinfo=zone, fold=0)
    second = value.replace(tzinfo=zone, fold=1)
    first_utc = first.astimezone(timezone.utc)
    second_utc = second.astimezone(timezone.utc)
    if first_utc.astimezone(zone).replace(tzinfo=None) != value:
        return None, "nonexistent_source_time"
    if first.utcoffset() != second.utcoffset():
        return None, "ambiguous_source_time"
    return first, None


def parse_source_time(value: Any, *, source_timezone: Optional[str] = None) -> tuple[Optional[str], Optional[str]]:
    """Return UTC ISO-8601 with Z plus a diagnostic; naive timestamps stay unknown.

    `source_timezone` is only appropriate when a source's documented timestamp
    zone has been reviewed. Ambiguous/nonexistent DST wall times remain unknown.
    The original value is never rewritten; callers preserve it in `source_time_raw`.
    """
    parsed = _parse_datetime(value)
    if parsed is None:
        return None, "missing_source_time" if value in (None, "") else "invalid_source_time"
    if parsed.tzinfo is None:
        if not source_timezone:
            return None, "timezone_missing"
        parsed, error = _localize_unambiguous(parsed, source_timezone)
        if parsed is None:
            return None, error
    return _utc_iso(parsed), None


def _canonical_url(source_url: str) -> str:
    if not isinstance(source_url, str) or not source_url.strip():
        raise NewsNormalizationError("missing_source_url", "news requires an original source URL")
    original = source_url.strip()
    parts = urlsplit(original)
    if parts.scheme.lower() not in {"https", "http"} or not parts.hostname:
        raise NewsNormalizationError("invalid_source_url", "source URL must be an absolute HTTP(S) URL")
    if parts.username or parts.password:
        raise NewsNormalizationError("invalid_source_url", "source URL must not contain credentials")
    try:
        port = parts.port
    except ValueError as exc:
        raise NewsNormalizationError("invalid_source_url", "source URL has an invalid port") from exc
    host = parts.hostname.lower().rstrip(".")
    netloc = host
    if port and not ((parts.scheme.lower() == "https" and port == 443) or
                     (parts.scheme.lower() == "http" and port == 80)):
        netloc = f"{host}:{port}"
    query = [(key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True)
             if key.casefold() not in _TRACKING_PARAMS and not key.casefold().startswith("utm_")]
    query.sort(key=lambda pair: (pair[0], pair[1]))
    path = parts.path or "/"
    return urlunsplit((parts.scheme.lower(), netloc, path, urlencode(query, doseq=True), ""))


def normalize_title(title: str) -> str:
    """Conservative Unicode-normalized title key for last-resort clustering only."""
    import unicodedata
    text = unicodedata.normalize("NFKC", title).casefold()
    return re.sub(r"[^\w]+", "", text, flags=re.UNICODE)


def _hash_id(value: str, *, prefix: str = "news-") -> str:
    return prefix + hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _policy(item: Any, override: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    raw = override if override is not None else _value(item, "policy", default={})
    raw = raw if isinstance(raw, Mapping) else {}
    if "authorized_scopes" in raw:
        scopes_value = raw.get("authorized_scopes")
    elif override is None:
        scopes_value = _value(item, "authorized_scopes", default=())
    else:
        scopes_value = ()
    if scopes_value is None:
        scopes_value = ()
    if not isinstance(scopes_value, (list, tuple)):
        raise NewsNormalizationError("invalid_authorized_scopes", "authorized_scopes must be a list or tuple")
    scopes = list(scopes_value)
    if any(not isinstance(scope, str) or scope not in AUTHORIZED_SCOPES for scope in scopes):
        raise NewsNormalizationError("invalid_authorized_scopes", "authorized_scopes contains an unsupported scope")
    reference = raw.get("policy_reference")
    reference = str(reference).strip() if reference else None
    cache_until, _ = parse_source_time(raw.get("cache_until"))
    return {
        "authorized_scopes": scopes,
        "cache_until": cache_until,
        "attribution_required": raw.get("attribution_required") is not False,
        "policy_reference": reference,
    }


def _news_status(item: Any, published_at: Optional[str], received_at: str,
                 max_age_seconds: Optional[float]) -> str:
    explicit = _value(item, "status")
    if isinstance(explicit, str) and explicit in STATUSES:
        return str(explicit)
    if _value(item, "is_delayed", "delayed", default=False) is True:
        return "delayed"
    if max_age_seconds is None or published_at is None:
        return "unavailable"  # valid item; recency cannot be established without a feed SLA/time
    if max_age_seconds < 0:
        raise NewsNormalizationError("invalid_freshness_window", "max_age_seconds must be non-negative")
    pub = _parse_datetime(published_at)
    received = _parse_datetime(received_at)
    if pub is None or received is None or pub.tzinfo is None or received.tzinfo is None:
        return "unavailable"
    age = (received.astimezone(timezone.utc) - pub.astimezone(timezone.utc)).total_seconds()
    if age < 0:
        return "unavailable"  # source clock skew/future timestamp; do not call it fresh
    return "fresh" if age <= max_age_seconds else "stale"


def normalize_news(
    item: Any,
    *,
    max_age_seconds: Optional[float] = None,
    source_timezone: Optional[str] = None,
    policy: Optional[Mapping[str, Any]] = None,
    security_matcher: Any = None,
    include_raw: bool = False,
) -> dict[str, Any]:
    """Normalize one feed/API item into the shared v1.0 news envelope.

    `published_at` is derived only from an offset-aware source value or a naive
    value localized under an explicitly reviewed source timezone. `received_at`
    is always the collector's separate, timezone-aware clock. Descriptions and
    article bodies are never copied into the record.
    """
    title_value = _value(item, "title")
    title = str(title_value).strip() if title_value is not None else ""
    if not title:
        raise NewsNormalizationError("missing_title", "news title is required")
    provider_value = _value(item, "provider", "source_id")
    publisher_value = _value(item, "publisher")
    provider = str(provider_value).strip() if provider_value else ""
    publisher = str(publisher_value).strip() if publisher_value else ""
    if not provider or not publisher:
        raise NewsNormalizationError("missing_attribution", "provider and original publisher are required")

    market_value = str(_value(item, "market", default="UNKNOWN")).strip().upper()
    market = market_value if market_value in MARKETS else "UNKNOWN"
    currency_value = _value(item, "currency")
    currency = str(currency_value).strip().upper() if currency_value else None
    if currency and (len(currency) != 3 or not currency.isalpha()):
        raise NewsNormalizationError("invalid_currency", "currency must be a three-letter code")

    url_value = _value(item, "source_url", "url")
    if not isinstance(url_value, str) or not url_value.strip():
        raise NewsNormalizationError("missing_source_url", "news requires the original item link")
    source_url = url_value.strip()
    canonical_url = _canonical_url(source_url)
    source_host = (urlsplit(source_url).hostname or "").lower().rstrip(".")

    raw_source_time = _value(item, "source_time_raw", "source_timestamp", "published_at")
    published_input = _value(item, "published_at")
    published_at, publication_error = parse_source_time(published_input, source_timezone=source_timezone)
    if published_at is None and raw_source_time is not None and raw_source_time != published_input:
        published_at, raw_error = parse_source_time(raw_source_time, source_timezone=source_timezone)
        publication_error = raw_error
    elif published_input is None and raw_source_time is not None:
        published_at, publication_error = parse_source_time(raw_source_time, source_timezone=source_timezone)
    if raw_source_time is not None and not isinstance(raw_source_time, str):
        raw_source_time = raw_source_time.isoformat() if isinstance(raw_source_time, datetime) else str(raw_source_time)

    received_input = _value(item, "received_at")
    received_at, received_error = parse_source_time(received_input)
    received_dt = _parse_datetime(received_input)
    if received_at is None or received_dt is None or received_dt.tzinfo is None:
        raise NewsNormalizationError("invalid_received_at", "received_at must include a timezone")
    if received_error:
        raise NewsNormalizationError("invalid_received_at", "received_at must include a timezone")

    explicit_event = _value(item, "event_ts", "event_time")
    if explicit_event is None:
        event_ts = published_at
        event_error = None
    else:
        event_ts, event_error = parse_source_time(explicit_event, source_timezone=source_timezone)

    source_record_id_value = _value(item, "source_record_id", "raw_id", "guid", "record_id", "id")
    source_record_id = str(source_record_id_value).strip() if source_record_id_value not in (None, "") else None
    source_feed_id = _value(item, "source_feed_id")
    if source_feed_id is None and hasattr(item, "source_id") and not isinstance(item, Mapping):
        # NewsItem.source_id denotes the registered feed; raw_id denotes its item.
        source_feed_id = getattr(item, "source_id", None)
    language = _value(item, "language")
    extra = _value(item, "news", default={})
    if isinstance(extra, Mapping):
        language = language or extra.get("language")
    content_type_value = _value(item, "content_type") or (extra.get("content_type") if isinstance(extra, Mapping) else None)
    content_type = str(content_type_value).strip() if content_type_value else "news"
    is_filing = content_type.casefold().replace(" ", "_") in {"filing", "sec_filing", "company_filing"}
    is_sec_filing = is_filing and (source_host == "sec.gov" or source_host.endswith(".sec.gov"))
    if is_sec_filing:
        content_type = "SEC Filing"
    is_update = _value(item, "is_update", default=False)
    withdrawn_input = _value(item, "withdrawn_at")
    withdrawn_at, _ = parse_source_time(withdrawn_input, source_timezone=source_timezone)
    filing_metadata = None
    if is_sec_filing:
        accession_value = _value(item, "accession_number", "accession", default=source_record_id)
        form_value = _value(item, "form", "form_type")
        filing_metadata = {
            "form": str(form_value).strip() if form_value else None,
            "accession_number": str(accession_value).strip() if accession_value else None,
            "filing_url": source_url,
        }

    explicit_refs = _value(item, "related_instruments", default=()) or ()
    refs = normalize_related_instruments(explicit_refs)
    root_instrument_id = _value(item, "instrument_id", "security_id")
    root_symbol = _value(item, "symbol")
    root_exchange = _value(item, "exchange")
    if root_instrument_id:
        refs.extend(normalize_related_instruments([{
            "instrument_id": root_instrument_id,
            "symbol": root_symbol,
            "market": market,
            "exchange": root_exchange,
            "method": "source_explicit_id",
            "confidence": 1.0,
        }]))
    if security_matcher is not None:
        refs.extend(normalize_related_instruments(
            security_matcher.match(title=title, market=market)
        ))
    by_identity: dict[tuple[str, str, str], dict[str, Any]] = {}
    for ref in refs:
        identity = (ref["market"], ref.get("exchange") or "", ref["instrument_id"])
        previous = by_identity.get(identity)
        if previous is None or ref["confidence"] > previous["confidence"]:
            by_identity[identity] = ref
    related = sorted(by_identity.values(), key=lambda row: (row["market"], row.get("exchange") or "", row["instrument_id"]))

    effective_policy = _policy(item, policy)
    status = _news_status(item, published_at, received_at, max_age_seconds)
    title_key = normalize_title(title)
    if source_record_id:
        dedup_key = f"source:{provider}:{source_record_id}"
    elif canonical_url:
        dedup_key = f"url:{canonical_url}"
    else:
        dedup_key = f"title:{hashlib.sha256(title_key.encode()).hexdigest()[:24]}"
    revision_material = "|".join((provider, source_record_id or "", publisher, canonical_url,
                                   title_key, published_at or ""))
    normalized_id = _hash_id(revision_material)

    certain = [ref for ref in related if ref["confidence"] >= 0.9]
    instrument_id = str(root_instrument_id) if root_instrument_id else (certain[0]["instrument_id"] if len(certain) == 1 else None)
    symbol = str(root_symbol) if root_symbol else (certain[0].get("symbol") if len(certain) == 1 else None)
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "record_type": "news",
        "domain": "finance",
        "id": normalized_id,
        "provider": provider,
        "publisher": publisher,
        "market": market,
        "instrument_id": instrument_id,
        "symbol": symbol,
        "currency": currency,
        "event_ts": event_ts,
        "published_at": published_at,
        "received_at": received_at,
        "status": status,
        "source_url": source_url,
        "canonical_url": canonical_url,
        "source_id": source_record_id,
        "dedup_key": dedup_key,
        "related_instruments": related,
        "policy": effective_policy,
        "news": {
            "title": title,
            "language": str(language) if language else None,
            "content_type": content_type,
            "filing": filing_metadata,
            "is_update": is_update is True,
            "withdrawn_at": withdrawn_at,
            "cluster_id": dedup_key,
            "source_feed_id": str(source_feed_id) if source_feed_id else None,
            "source_time_raw": raw_source_time,
            "source_time_error": publication_error,
            "event_time_error": event_error,
        },
    }
    if include_raw:
        # Strict allow-list: never copy article body, description, or arbitrary payload.
        record["raw"] = {
            "source_record_id": source_record_id,
            "source_time_raw": raw_source_time,
        }
    return record


def public_news(record: Mapping[str, Any], *, intended_scope: str = "public") -> Optional[dict[str, Any]]:
    """Return a safe projection only when the exact intended use is authorized."""
    if intended_scope not in AUTHORIZED_SCOPES:
        raise ValueError("intended_scope must be an exact contract scope")
    policy = record.get("policy") if isinstance(record.get("policy"), Mapping) else {}
    scopes = policy.get("authorized_scopes", ())
    if not isinstance(scopes, (list, tuple)) or intended_scope not in scopes:
        return None
    result = deepcopy(dict(record))
    result.pop("raw", None)
    return result
