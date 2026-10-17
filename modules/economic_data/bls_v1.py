"""On-demand, read-only BLS Public Data API v1 labor statistics.

No network access occurs on import. The provider requests only three fixed
monthly series, performs one HTTPS POST per refresh, does not follow redirects
or retry, caches in memory, caps usage below BLS API v1's published daily limit,
and never emits stock symbols or market quotes.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import http.client
import fcntl
import json
import os
from pathlib import Path
import re
import ssl
import tempfile
import threading
import time
from typing import Any, Callable, Mapping, Optional, Protocol
from urllib.parse import urlparse

BLS_API_URL = "https://api.bls.gov/publicAPI/v1/timeseries/data/"
BLS_API_HOST = "api.bls.gov"
BLS_API_PATH = "/publicAPI/v1/timeseries/data/"
BLS_FAQ_URL = "https://www.bls.gov/developers/api_faqs.htm"
BLS_TERMS_URL = "https://www.bls.gov/developers/termsOfService.htm"
BLS_DAILY_LIMIT = 25
BLS_CONFIGURED_DAILY_CAP = 20
BLS_MIN_POLL_SECONDS = 3600.0
BLS_MAX_RESPONSE_BYTES = 2_000_000
BLS_TIMEOUT_SECONDS = 10.0
BLS_USER_AGENT = "FusionOffice/1.0 (local read-only BLS statistics client)"
BLS_DISCLAIMER = (
    "BLS.gov cannot vouch for the data or analyses derived from these data "
    "after the data have been retrieved from BLS.gov."
)


@dataclass(frozen=True)
class SeriesSpec:
    series_id: str
    label_zh: str
    label_en: str
    unit_zh: str
    unit_en: str
    seasonal_adjustment: str


SERIES: tuple[SeriesSpec, ...] = (
    SeriesSpec("LNS14000000", "美国失业率", "U.S. unemployment rate", "%", "%", "Seasonally adjusted"),
    SeriesSpec("LNS11300000", "美国劳动参与率", "U.S. labor force participation rate", "%", "%", "Seasonally adjusted"),
    SeriesSpec("CES0000000001", "美国非农就业人数", "U.S. total nonfarm employment", "千人", "thousands of jobs", "Seasonally adjusted"),
)
_SERIES_BY_ID = {item.series_id: item for item in SERIES}


@dataclass(frozen=True)
class BLSHTTPResponse:
    status: int
    body: bytes


class BLSHTTPTransport(Protocol):
    def post(self, url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> BLSHTTPResponse: ...


class DirectTLSHTTPTransport:
    """Direct HTTPS POST to the single fixed BLS API host; redirects are not followed."""

    def post(self, url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> BLSHTTPResponse:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != BLS_API_HOST or parsed.path != BLS_API_PATH:
            raise ValueError("BLS endpoint is not the fixed official API URL")
        connection = http.client.HTTPSConnection(
            BLS_API_HOST, timeout=timeout, context=ssl.create_default_context()
        )
        try:
            connection.request("POST", BLS_API_PATH, body=body, headers=dict(headers))
            response = connection.getresponse()
            payload = response.read(BLS_MAX_RESPONSE_BYTES + 1)
            return BLSHTTPResponse(int(response.status), payload)
        finally:
            connection.close()


class BLSLaborStatisticsProvider:
    """Private read-only labor-statistics endpoint with conservative query limits."""

    def __init__(
        self,
        transport: Optional[BLSHTTPTransport] = None,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        monotonic: Callable[[], float] = time.monotonic,
        max_requests_per_day: int = BLS_CONFIGURED_DAILY_CAP,
        min_poll_seconds: float = BLS_MIN_POLL_SECONDS,
        max_response_bytes: int = BLS_MAX_RESPONSE_BYTES,
        timeout_seconds: float = BLS_TIMEOUT_SECONDS,
        state_path: Optional[str | Path] = None,
    ) -> None:
        if not 1 <= int(max_requests_per_day) <= BLS_DAILY_LIMIT:
            raise ValueError("configured BLS daily cap must be between 1 and the official v1 limit")
        if min_poll_seconds < 0 or max_response_bytes < 1:
            raise ValueError("invalid BLS request limits")
        self.transport = transport or DirectTLSHTTPTransport()
        self.clock = clock
        self.monotonic = monotonic
        self.max_requests_per_day = int(max_requests_per_day)
        self.min_poll_seconds = float(min_poll_seconds)
        self.max_response_bytes = int(max_response_bytes)
        self.timeout_seconds = float(timeout_seconds)
        self.state_path = Path(state_path).expanduser() if state_path is not None else None
        self._lock = threading.Lock()
        self._request_day: Optional[str] = None
        self._requests_today = 0
        self._last_attempt_tick: Optional[float] = None
        self._last_success_at: Optional[str] = None
        self._last_success_tick: Optional[float] = None
        self._items: tuple[dict[str, Any], ...] = ()

    def payload(self) -> dict[str, Any]:
        # Serialize refreshes so simultaneous page requests cannot exceed the budget.
        with self._lock:
            now = self._now()
            now_tick = self.monotonic()
            request_day = now[:10]
            if request_day != self._request_day:
                self._request_day = request_day
                if self.state_path is None:
                    self._requests_today = 0

            if self._last_attempt_tick is not None:
                elapsed = now_tick - self._last_attempt_tick
                if elapsed < self.min_poll_seconds:
                    return self._response(now, "rate_limited_local", "poll_interval", cached=True,
                                          retry_after_seconds=max(0.0, self.min_poll_seconds - elapsed))
            reserved, budget_error = self._reserve_request_slot(request_day)
            if not reserved:
                status = "unavailable" if budget_error.startswith("budget_state_") else "rate_limited_local"
                return self._response(now, status, budget_error, cached=True)

            self._last_attempt_tick = now_tick
            try:
                year = int(now[:4])
                request_body = json.dumps({
                    "seriesid": [series.series_id for series in SERIES],
                    "startyear": str(year - 1),
                    "endyear": str(year),
                }, separators=(",", ":")).encode("utf-8")
                response = self.transport.post(
                    BLS_API_URL,
                    request_body,
                    {"User-Agent": BLS_USER_AGENT, "Accept": "application/json",
                     "Content-Type": "application/json"},
                    self.timeout_seconds,
                )
                if response.status != 200:
                    return self._response(now, "unavailable", "http_error", http_status=response.status)
                if len(response.body) > self.max_response_bytes:
                    return self._response(now, "unavailable", "payload_too_large", http_status=response.status)
                parsed = json.loads(response.body.decode("utf-8-sig"))
                if not isinstance(parsed, Mapping) or parsed.get("status") != "REQUEST_SUCCEEDED":
                    return self._response(now, "unavailable", "api_error", http_status=response.status)
                items = self._normalize(parsed.get("Results"), now)
                if not items:
                    return self._response(now, "unavailable", "no_series_data", http_status=response.status)
                self._items = tuple(items)
                self._last_success_at = now
                self._last_success_tick = now_tick
                status = "fresh" if len(items) == len(SERIES) else "partial"
                return self._response(now, status, None, http_status=response.status, cached=False)
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
                return self._response(now, "unavailable", "invalid_response")
            except Exception:
                # Keep details out of the client response; no retry is attempted.
                return self._response(now, "unavailable", "transport_error")

    def _reserve_request_slot(self, request_day: str) -> tuple[bool, str]:
        """Atomically persist the daily request budget before any network call."""
        if self.state_path is None:
            if self._requests_today >= self.max_requests_per_day:
                return False, "daily_limit"
            self._requests_today += 1
            return True, ""

        path = self.state_path
        lock_path = path.with_suffix(path.suffix + ".lock")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(lock_path, "a+", encoding="utf-8") as guard:
                fcntl.flock(guard.fileno(), fcntl.LOCK_EX)
                count = 0
                if path.exists():
                    try:
                        saved = json.loads(path.read_text(encoding="utf-8"))
                    except (OSError, ValueError, TypeError):
                        self._requests_today = self.max_requests_per_day
                        return False, "budget_state_invalid"
                    if not isinstance(saved, Mapping) or not isinstance(saved.get("date"), str) or not isinstance(saved.get("count"), int):
                        self._requests_today = self.max_requests_per_day
                        return False, "budget_state_invalid"
                    if saved["date"] == request_day:
                        count = max(0, saved["count"])
                self._requests_today = count
                if count >= self.max_requests_per_day:
                    return False, "daily_limit"
                new_count = count + 1
                fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as stream:
                        json.dump({"date": request_day, "count": new_count}, stream, separators=(",", ":"))
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temp_name, path)
                finally:
                    try:
                        os.unlink(temp_name)
                    except FileNotFoundError:
                        pass
                self._requests_today = new_count
                return True, ""
        except OSError:
            self._requests_today = self.max_requests_per_day
            return False, "budget_state_unavailable"

    @staticmethod
    def _normalize(results: Any, retrieved_at: str) -> list[dict[str, Any]]:
        series_rows = results.get("series") if isinstance(results, Mapping) else None
        if not isinstance(series_rows, list):
            return []
        rows_by_id = {str(row.get("seriesID") or ""): row for row in series_rows if isinstance(row, Mapping)}
        items: list[dict[str, Any]] = []
        for spec in SERIES:
            raw_rows = rows_by_id.get(spec.series_id, {}).get("data")
            if not isinstance(raw_rows, list):
                continue
            valid_rows: list[tuple[int, int, Mapping[str, Any]]] = []
            for row in raw_rows:
                if not isinstance(row, Mapping):
                    continue
                year = str(row.get("year") or "")
                period = str(row.get("period") or "")
                if not year.isdigit() or not re.fullmatch(r"M(?:0[1-9]|1[0-2])", period):
                    continue  # Exclude annual M13 and non-monthly periods.
                try:
                    numeric = Decimal(str(row.get("value", "")))
                    if not numeric.is_finite():
                        continue
                except (InvalidOperation, ValueError):
                    continue
                valid_rows.append((int(year), int(period[1:]), row))
            if not valid_rows:
                continue
            _, _, latest = max(valid_rows, key=lambda item: (item[0], item[1]))
            footnotes = []
            for note in latest.get("footnotes") or []:
                if isinstance(note, Mapping) and (note.get("text") or note.get("code")):
                    footnotes.append({"code": str(note.get("code") or ""),
                                      "text": str(note.get("text") or "")})
            year = str(latest["year"])
            period = str(latest["period"])
            items.append({
                "series_id": spec.series_id,
                "label_zh": spec.label_zh,
                "label_en": spec.label_en,
                "value": str(latest["value"]),
                "unit_zh": spec.unit_zh,
                "unit_en": spec.unit_en,
                "period": f"{year}-{period}",
                "period_name": str(latest.get("periodName") or period),
                "seasonal_adjustment": spec.seasonal_adjustment,
                "footnotes": footnotes,
                "provider": "U.S. Bureau of Labor Statistics Public Data API v1",
                "publisher": "U.S. Bureau of Labor Statistics",
                "source_url": BLS_API_URL,
                "provider_url": BLS_FAQ_URL,
                "terms_url": BLS_TERMS_URL,
                "retrieved_at": retrieved_at,
                "symbol": None,
                "symbols": [],
            })
        return items

    def _response(
        self,
        now: str,
        status: str,
        error_code: Optional[str],
        *,
        http_status: Optional[int] = None,
        cached: bool = False,
        retry_after_seconds: Optional[float] = None,
    ) -> dict[str, Any]:
        age_seconds = None
        if self._last_success_tick is not None:
            age_seconds = max(0.0, self.monotonic() - self._last_success_tick)
        if age_seconds is None:
            freshness = "unavailable"
        elif age_seconds <= self.min_poll_seconds:
            freshness = "fresh"
        elif age_seconds <= 86400:
            freshness = "delayed"
        else:
            freshness = "stale"
        items = []
        for item in self._items:
            copied = dict(item)
            copied["freshness"] = freshness
            copied["cached"] = bool(cached)
            items.append(copied)
        return {
            "ok": bool(items),
            "schema_version": "1.0",
            "domain": "finance",
            "data_mode": "LIVE" if items else "UNAVAILABLE",
            "demo": False,
            "scope": "official_us_labor_statistics",
            "coverage_scope": "three_fixed_us_labor_series_only",
            "coverage_complete": False,
            "covered_symbols": [],
            "provider": "U.S. Bureau of Labor Statistics Public Data API v1",
            "publisher": "U.S. Bureau of Labor Statistics",
            "source_url": BLS_API_URL,
            "provider_url": BLS_FAQ_URL,
            "terms_url": BLS_TERMS_URL,
            "status": status,
            "error_code": error_code,
            "http_status": http_status,
            "generated_at": now,
            "retrieved_at": self._last_success_at,
            "access_date": now[:10],
            "cache_age_seconds": age_seconds,
            "retry_after_seconds": retry_after_seconds,
            "requests_today": self._requests_today,
            "configured_daily_cap": self.max_requests_per_day,
            "official_daily_limit": BLS_DAILY_LIMIT,
            "min_poll_seconds": self.min_poll_seconds,
            "notice": BLS_DISCLAIMER,
            "display_note": (
                "Read-only official labor statistics; observations are monthly, may be revised, "
                "and are not real-time securities prices or investment advice."
            ),
            "items": items,
        }

    def _now(self) -> str:
        value = self.clock()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        value = value.astimezone(timezone.utc)
        return value.isoformat(timespec="seconds").replace("+00:00", "Z")


_default_provider = BLSLaborStatisticsProvider(
    state_path=Path.home() / ".local" / "share" / "fusion-office" / "bls-v1-usage.json"
)


def bls_labor_statistics_payload() -> dict[str, Any]:
    """Return the current read-only payload; network work begins only on first explicit call."""
    return _default_provider.payload()
