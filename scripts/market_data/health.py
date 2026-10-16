"""Feed-level health observations for metrics and failover decisions."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from .contracts import parse_timestamp, iso_utc


@dataclass
class FeedHealth:
    provider: str
    market: str
    heartbeat_timeout_seconds: float
    last_heartbeat_at: Optional[str] = None
    last_event_at: Optional[str] = None
    last_success_at: Optional[str] = None
    disconnect_count: int = 0
    recovery_count: int = 0
    sequence_gap_count: int = 0
    parse_error_count: int = 0
    last_error_code: Optional[str] = None
    _was_healthy: bool = field(default=False, repr=False)
    _last_sequence: Optional[int] = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.heartbeat_timeout_seconds <= 0:
            raise ValueError("heartbeat_timeout_seconds must be positive")

    def observe_heartbeat(self, received_at: Any = None) -> None:
        parsed = parse_timestamp(received_at) if received_at is not None else datetime.now(timezone.utc)
        if parsed is None:
            self.record_error("invalid_heartbeat_time", parse_error=True)
            return
        self.last_heartbeat_at = iso_utc(parsed)
        self.last_success_at = iso_utc(parsed)
        self.last_error_code = None
        if not self._was_healthy:
            if self.disconnect_count:
                self.recovery_count += 1
            self._was_healthy = True

    def observe_event(self, event_time: Any, received_at: Any = None, sequence: Any = None) -> None:
        received = parse_timestamp(received_at) if received_at is not None else datetime.now(timezone.utc)
        event = parse_timestamp(event_time)
        if received is None:
            self.record_error("invalid_receive_time", parse_error=True)
            return
        if event is not None:
            self.last_event_at = iso_utc(event)
        self.last_success_at = iso_utc(received)
        # A feed may have opaque non-numeric sequence identifiers; retain health
        # and skip gap math rather than declaring a false gap.
        try:
            current = int(sequence) if sequence is not None else None
        except (TypeError, ValueError):
            current = None
        if current is not None:
            if self._last_sequence is not None and current > self._last_sequence + 1:
                self.sequence_gap_count += current - self._last_sequence - 1
            self._last_sequence = current
        self.observe_heartbeat(received)

    def record_error(self, code: str, *, parse_error: bool = False, disconnected: bool = False) -> None:
        self.last_error_code = str(code)
        if parse_error:
            self.parse_error_count += 1
        if disconnected:
            self.disconnect_count += 1
            self._was_healthy = False

    def status(self, now: Any = None, *, market_open: bool = True) -> str:
        """Return closed/fresh/stale/unavailable; caller supplies calendar state."""
        if not market_open:
            return "closed"
        current = parse_timestamp(now) if now is not None else datetime.now(timezone.utc)
        heartbeat = parse_timestamp(self.last_heartbeat_at)
        if current is None or heartbeat is None:
            return "unavailable"
        age = (current - heartbeat).total_seconds()
        if age < 0:
            return "unavailable"
        return "fresh" if age <= self.heartbeat_timeout_seconds else "stale"

    def metrics(self, now: Any = None, *, market_open: bool = True) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "market": self.market,
            "status": self.status(now, market_open=market_open),
            "heartbeat_timeout_seconds": self.heartbeat_timeout_seconds,
            "last_heartbeat_at": self.last_heartbeat_at,
            "last_event_at": self.last_event_at,
            "last_success_at": self.last_success_at,
            "disconnect_count": self.disconnect_count,
            "recovery_count": self.recovery_count,
            "sequence_gap_count": self.sequence_gap_count,
            "parse_error_count": self.parse_error_count,
            "last_error_code": self.last_error_code,
        }
