"""Normalize provider-mapped quote dictionaries to DATA-CONTRACT.md."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Mapping, Optional
from urllib.parse import urlparse

from .contracts import Freshness, MarketState, parse_timestamp, iso_utc

SCHEMA_VERSION = "1.0"
VALID_STATUSES = {"fresh", "delayed", "stale", "unavailable", "closed", "error"}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _string(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _source_url(value: Any) -> Optional[str]:
    url = _string(value)
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme not in ("https", "http") or not parsed.netloc:
        raise ValueError("source_url must be an absolute http(s) URL")
    return url


def _as_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def normalize_quote(
    source: Mapping[str, Any],
    *,
    max_age_seconds: float,
    policy: Optional[Mapping[str, Any]] = None,
    market_state: MarketState | str = MarketState.UNKNOWN,
    received_at: Any = None,
    delayed: bool = False,
) -> dict[str, Any]:
    """Map an already-fetched provider row to the shared quote envelope.

    This function performs no I/O, does not assert any data rights, and never
    changes an unknown/naive provider timestamp into an assumed timezone.
    ``source`` is a mapped adapter row, not a vendor-specific raw response.
    """
    if max_age_seconds < 0:
        raise ValueError("max_age_seconds must be non-negative")
    provider = _string(source.get("provider"))
    symbol = _string(source.get("symbol"))
    currency = (_string(source.get("currency")) or "").upper()
    if not provider or not symbol or len(currency) != 3 or not currency.isalpha():
        raise ValueError("provider, symbol, and ISO-like 3-letter currency are required")

    market = (_string(source.get("market")) or "UNKNOWN").upper()
    if market not in {"CN", "HK", "US"}:
        market = "UNKNOWN"
    instrument_id = _string(source.get("instrument_id"))
    event_raw = source.get("event_ts")
    # Keep the original source value, even if it cannot safely be normalized.
    source_time_raw = source.get("source_time", event_raw)
    event = parse_timestamp(event_raw)
    received = parse_timestamp(received_at if received_at is not None else source.get("received_at"))
    if received is None:
        received = _utc_now()
    if received.tzinfo is None:  # defensive; parse_timestamp returns None for naive values
        raise ValueError("received_at must have a timezone")
    received_text = iso_utc(received)
    event_text = iso_utc(event)
    state_text = market_state.value if isinstance(market_state, MarketState) else str(market_state)
    if state_text not in {item.value for item in MarketState}:
        state_text = MarketState.UNKNOWN.value

    incoming_status = _string(source.get("status"))
    error_value = source.get("error")
    if state_text == MarketState.CLOSED.value:
        status = "closed"
    elif error_value:
        status = "error"
    elif delayed:
        status = "delayed"
    elif incoming_status in VALID_STATUSES:
        status = incoming_status
    else:
        freshness = Freshness.UNKNOWN
        if event is not None:
            age = (received - event).total_seconds()
            freshness = Freshness.UNKNOWN if age < 0 else (
                Freshness.FRESH if age <= max_age_seconds else Freshness.STALE
            )
        status = {
            Freshness.FRESH: "fresh",
            Freshness.STALE: "stale",
            Freshness.UNKNOWN: "unavailable",
        }[freshness]

    quote = {
        "data_level": _string(source.get("data_level")) or "unknown",
        "price": _as_float(source.get("price")),
        "bid": _as_float(source.get("bid")),
        "ask": _as_float(source.get("ask")),
        "last_size": _as_float(source.get("last_size")),
        "volume": _as_float(source.get("volume")),
        "sequence": _string(source.get("sequence")),
        "session": _string(source.get("session")) or "unknown",
        "phase": _string(source.get("phase")) or "unknown",
        "is_delayed": bool(delayed),
        "source_time": source_time_raw,
    }
    dedup_material = "|".join((provider, instrument_id or symbol, event_text or "unknown", quote["sequence"] or ""))
    dedup_key = _string(source.get("dedup_key")) or dedup_material
    record_id = _string(source.get("id")) or hashlib.sha256(dedup_material.encode("utf-8")).hexdigest()[:24]

    policy_value = dict(policy or {})
    scopes = policy_value.get("authorized_scopes", ())
    if not isinstance(scopes, (list, tuple, set, frozenset)):
        raise ValueError("policy.authorized_scopes must be a list of exact scopes")
    allowed_scopes = {"local_personal", "internal", "public", "redistribution"}
    submitted_scopes = {str(scope) for scope in scopes}
    if submitted_scopes - allowed_scopes:
        raise ValueError("policy.authorized_scopes contains an unsupported scope")
    canonical_scopes = sorted(submitted_scopes)
    canonical_policy = {
        "authorized_scopes": canonical_scopes,
        "cache_until": _normalize_optional_ts(policy_value.get("cache_until")),
        "attribution_required": bool(policy_value.get("attribution_required", True)),
        "policy_reference": _string(policy_value.get("policy_reference")),
    }

    related = source.get("related_instruments") or []
    if not isinstance(related, list):
        raise ValueError("related_instruments must be a list")
    result = {
        "schema_version": SCHEMA_VERSION,
        "record_type": "quote",
        "domain": "finance",
        "id": record_id,
        "provider": provider,
        "feed": _string(source.get("feed")),
        "publisher": _string(source.get("publisher")),
        "market": market,
        "instrument_id": instrument_id,
        "symbol": symbol,
        "currency": currency,
        "event_ts": event_text,
        "received_at": received_text,
        "status": status,
        "data_mode": (_string(source.get("data_mode")) or "").upper() or None,
        "source_url": _source_url(source.get("source_url")),
        "dedup_key": dedup_key,
        "source_id": _string(source.get("source_id")),
        "related_instruments": related,
        "policy": canonical_policy,
        "quote": quote,
    }
    if error_value:
        if isinstance(error_value, Mapping):
            result["error"] = {
                "code": _string(error_value.get("code")) or "provider_error",
                "message": _string(error_value.get("message")) or "Provider reported an error",
                "retryable": bool(error_value.get("retryable", False)),
                "retry_after_seconds": _as_float(error_value.get("retry_after_seconds")),
            }
        else:
            result["error"] = {
                "code": "provider_error",
                "message": str(error_value),
                "retryable": False,
                "retry_after_seconds": None,
            }
    if source.get("raw") is not None:
        # Raw provider payload is explicitly excluded from public serialization.
        result["raw"] = dict(source["raw"])
    return result


def _normalize_optional_ts(value: Any) -> Optional[str]:
    if value is None:
        return None
    parsed = parse_timestamp(value)
    return iso_utc(parsed) if parsed else None


def validate_quote_record(record: Mapping[str, Any]) -> list[str]:
    """Return schema violations without throwing, for adapters and fixture tests."""
    problems: list[str] = []
    required = ("schema_version", "record_type", "domain", "id", "provider", "market", "received_at", "status", "data_mode", "policy")
    for field in required:
        if field not in record or record[field] in (None, ""):
            problems.append(f"missing:{field}")
    if record.get("record_type") != "quote":
        problems.append("invalid:record_type")
    if record.get("domain") != "finance":
        problems.append("invalid:domain")
    if record.get("status") not in VALID_STATUSES:
        problems.append("invalid:status")
    if record.get("data_mode") not in {"LIVE", "DEMO"}:
        problems.append("invalid:data_mode")
    quote = record.get("quote")
    if not isinstance(quote, Mapping):
        problems.append("missing:quote")
    elif "price" in quote and quote["price"] is not None and _as_float(quote["price"]) is None:
        problems.append("invalid:quote.price")
    policy = record.get("policy")
    if not isinstance(policy, Mapping):
        problems.append("missing:policy")
    else:
        scopes = policy.get("authorized_scopes")
        if not isinstance(scopes, list) or any(scope not in {"local_personal", "internal", "public", "redistribution"} for scope in scopes):
            problems.append("invalid:policy.authorized_scopes")
        if "attribution_required" not in policy or not isinstance(policy["attribution_required"], bool):
            problems.append("invalid:policy.attribution_required")
    if record.get("received_at") and parse_timestamp(record["received_at"]) is None:
        problems.append("invalid:received_at")
    return problems


def may_emit(record: Mapping[str, Any], *, scope: str) -> bool:
    """Authorization gate for local-personal, internal, or public output."""
    policy = record.get("policy")
    if not isinstance(policy, Mapping):
        return False
    scopes = policy.get("authorized_scopes")
    return isinstance(scopes, (list, tuple, set, frozenset)) and scope in scopes


def quote_for_scope(record: Mapping[str, Any], *, scope: str) -> Optional[dict[str, Any]]:
    """Return a safe projection only when policy permits this exact scope."""
    if not may_emit(record, scope=scope):
        return None
    return {key: value for key, value in record.items() if key != "raw"}


def local_quote(record: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    """Projection for a reviewed, private single-user/local use scope."""
    return quote_for_scope(record, scope="local_personal")


def public_quote(record: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    """Projection for external/public use; requires redistribution permission."""
    return quote_for_scope(record, scope="public")
