"""Provider-neutral market-data contracts.

No network access or provider-specific scraping belongs in this module. Provider
adapters should supply source timestamps and entitlement metadata explicitly.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


class Freshness(str, Enum):
    FRESH = "fresh"
    STALE = "stale"
    UNKNOWN = "unknown"


class MarketState(str, Enum):
    OPEN = "open"
    PRE_OPEN = "pre_open"
    AUCTION = "auction"
    HALTED = "halted"
    CLOSED = "closed"
    UNKNOWN = "unknown"


class BatchState(str, Enum):
    OK = "ok"
    PARTIAL = "partial"
    STALE = "stale"
    NO_DATA = "no_data"
    ERROR = "error"


def parse_timestamp(value: Any) -> Optional[datetime]:
    """Parse epoch seconds or an ISO-8601 timestamp; naive values stay unknown.

    We deliberately do not assume a timezone for provider timestamps that omit
    one. A naive timestamp must be normalized in the provider adapter where its
    documented timezone is known.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    return None


def iso_utc(value: Optional[datetime]) -> Optional[str]:
    if value is None or value.tzinfo is None:
        return None
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def classify_freshness(
    event_time: Any,
    received_at: Any,
    *,
    max_age_seconds: float,
) -> Freshness:
    """Classify quote freshness against an adapter/SLA-specific age budget.

    Callers must only use this during an applicable trading phase. Closed-market
    handling belongs to an exchange-calendar/market-state component, not an
    arbitrary timeout in this function.
    """
    if max_age_seconds < 0:
        raise ValueError("max_age_seconds must be non-negative")
    event = parse_timestamp(event_time)
    received = parse_timestamp(received_at)
    if event is None or received is None:
        return Freshness.UNKNOWN
    age = (received - event).total_seconds()
    # Future event times usually indicate clock skew or malformed source data.
    if age < 0:
        return Freshness.UNKNOWN
    return Freshness.FRESH if age <= max_age_seconds else Freshness.STALE


@dataclass(frozen=True)
class DataError:
    code: str
    message: str
    retryable: bool = False
    retry_after_seconds: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
