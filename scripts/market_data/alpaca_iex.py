"""Optional Alpaca IEX latest-quote adapter for private, single-user use.

This adapter is read-only market-data access only. It never registers accounts,
creates orders, logs credentials, or sends credentials anywhere except the
provider's documented authentication headers. All requests use Scrapling's
ordinary Fetcher through the shared Scrapling transport.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
import re
import threading
import time
from typing import Any, Callable, Mapping, Optional, Sequence
from urllib.parse import urlencode

from .normalizer import normalize_quote
from .providers import ProviderPolicy

ALPACA_SNAPSHOTS_URL = "https://data.alpaca.markets/v2/stocks/snapshots"
PROVIDER_ID = "alpaca-market-data"
FEED_ID = "iex"
_ALLOWED_SCOPES = frozenset({"local_personal"})
_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,19}$")


@dataclass(frozen=True)
class HTTPResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes
    json_data: Any = None


class ScraplingRESTTransport:
    """Use Scrapling's ordinary Fetcher only; never enable stealth or proxies."""

    def get(self, url: str, headers: Mapping[str, str], timeout: float) -> HTTPResponse:
        try:
            from scrapling.fetchers import Fetcher
        except ImportError as exc:
            raise RuntimeError("Install modules/newsfeed/requirements.txt to enable Scrapling") from exc
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
        body = bytes(response.body)
        try:
            payload = response.json()
        except Exception:
            payload = None
        return HTTPResponse(
            status=int(response.status),
            headers={str(key): str(value) for key, value in response.headers.items()},
            body=body,
            json_data=payload,
        )


@dataclass(frozen=True)
class AlpacaIEXConfig:
    api_key: str = field(default="", repr=False)
    api_secret: str = field(default="", repr=False)
    provider_enabled: bool = False
    authorized_scopes: frozenset[str] = frozenset()
    freshness_threshold_seconds: Optional[float] = None
    timeout_seconds: float = 8.0
    min_request_interval_seconds: float = 0.35

    @classmethod
    def from_environment(
        cls,
        *,
        provider_enabled: bool = False,
        authorized_scopes: Sequence[str] = (),
        freshness_threshold_seconds: Optional[float] = None,
        timeout_seconds: float = 8.0,
        min_request_interval_seconds: float = 0.35,
    ) -> "AlpacaIEXConfig":
        """Read credentials only from explicit local environment variables.

        Credentials alone never enable requests. A caller must separately set
        provider_enabled=True and authorize the exact local_personal scope.
        """
        return cls(
            api_key=os.environ.get("ALPACA_API_KEY", "").strip(),
            api_secret=os.environ.get("ALPACA_API_SECRET", "").strip(),
            provider_enabled=provider_enabled,
            authorized_scopes=frozenset(authorized_scopes),
            freshness_threshold_seconds=freshness_threshold_seconds,
            timeout_seconds=timeout_seconds,
            min_request_interval_seconds=min_request_interval_seconds,
        )

    @property
    def credentials_present(self) -> bool:
        return bool(self.api_key and self.api_secret)

    @property
    def enabled_for_personal_local(self) -> bool:
        return (
            self.provider_enabled
            and self.credentials_present
            and self.freshness_threshold_seconds is not None
            and self.authorized_scopes == _ALLOWED_SCOPES
        )


@dataclass(frozen=True)
class AlpacaIEXResult:
    status: str  # ok | partial | disabled | denied | error | no_data
    records: Mapping[str, dict[str, Any]]
    error: Optional[Mapping[str, Any]] = None
    received_at: Optional[str] = None


class AlpacaIEXPersonalProvider:
    """Fetch IEX-only latest quotes through Scrapling's normal REST Fetcher."""

    provider_id = PROVIDER_ID
    priority = 50
    feed = FEED_ID
    coverage = "single_venue_iex_not_consolidated"

    def __init__(
        self,
        config: AlpacaIEXConfig,
        transport: Any = None,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if config.freshness_threshold_seconds is not None and config.freshness_threshold_seconds < 0:
            raise ValueError("freshness_threshold_seconds must be non-negative")
        if config.timeout_seconds <= 0 or config.min_request_interval_seconds < 0:
            raise ValueError("invalid timeout or request interval")
        self.config = config
        self.transport = transport or ScraplingRESTTransport()
        self.clock = clock
        self.monotonic = monotonic
        self.sleep = sleep
        self._last_request_at: Optional[float] = None
        self._rate_lock = threading.Lock()

    @property
    def policy(self) -> ProviderPolicy:
        return ProviderPolicy(
            permitted_markets=frozenset({"US"}),
            authorized_scopes=self.config.authorized_scopes,
            provider_enabled=self.config.enabled_for_personal_local,
            policy_reference="Alpaca IEX individual API; exact private local scope only",
        )

    def fetch(self, symbols: Sequence[str], *, scope: str = "local_personal") -> AlpacaIEXResult:
        """Read latest IEX quotes; no order/trading endpoint is present in this class."""
        if not self.config.provider_enabled:
            return self._result("disabled", error_code="provider_disabled")
        if not self.config.credentials_present:
            return self._result("disabled", error_code="credentials_missing")
        if self.config.freshness_threshold_seconds is None:
            return self._result("disabled", error_code="freshness_threshold_required")
        if scope != "local_personal" or scope not in self.config.authorized_scopes:
            return self._result("denied", error_code="scope_not_authorized")
        if self.config.authorized_scopes != _ALLOWED_SCOPES:
            # This personal-only adapter must never be widened into public use.
            return self._result("denied", error_code="unsupported_authorized_scope")

        normalized: list[str] = []
        for raw in symbols:
            symbol = str(raw or "").strip().upper()
            if not _SYMBOL_RE.fullmatch(symbol):
                return self._result("error", error_code="invalid_symbol")
            if symbol not in normalized:
                normalized.append(symbol)
        if not normalized:
            return self._result("no_data", error_code="no_symbols")

        query = urlencode({"symbols": ",".join(normalized), "feed": FEED_ID})
        url = f"{ALPACA_SNAPSHOTS_URL}?{query}"
        headers = {
            "APCA-API-KEY-ID": self.config.api_key,
            "APCA-API-SECRET-KEY": self.config.api_secret,
            "Accept": "application/json",
            "User-Agent": "FusionOffice-AlpacaIEX/0.1 (private read-only market data)",
        }
        try:
            self._wait_for_request_slot()
            response = self.transport.get(url, headers, self.config.timeout_seconds)
        except Exception as exc:
            # Do not return exception strings: some HTTP clients include headers.
            return self._result("error", error_code="transport_error", error_type=type(exc).__name__)

        if not 200 <= int(response.status) < 300:
            return self._result("error", error_code="http_error", http_status=int(response.status))
        try:
            payload = self._payload(response)
        except (ValueError, TypeError, UnicodeDecodeError):
            return self._result("error", error_code="invalid_json")
        if not isinstance(payload, Mapping):
            return self._result("error", error_code="invalid_payload")
        received = self.clock()
        if received.tzinfo is None:
            return self._result("error", error_code="clock_timezone_missing")
        received_text = received.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        records: dict[str, dict[str, Any]] = {}
        for symbol in normalized:
            snapshot = payload.get(symbol)
            if not isinstance(snapshot, Mapping):
                continue
            quote = snapshot.get("latestQuote")
            trade = snapshot.get("latestTrade")
            quote = quote if isinstance(quote, Mapping) else {}
            trade = trade if isinstance(trade, Mapping) else {}
            quote_time = quote.get("t")
            trade_time = trade.get("t")
            # The simulated mark price comes from latestTrade, so freshness is
            # measured against that event; latestQuote keeps its own timestamp.
            event_time = trade_time or quote_time
            if event_time is None:
                continue
            mapped = {
                "provider": "Alpaca Market Data",
                "publisher": "Investors Exchange (IEX)",
                "feed": FEED_ID,
                "source_id": f"alpaca-iex:{symbol}",
                "source_url": "https://docs.alpaca.markets/us/reference/stocksnapshots-1",
                "market": "US",
                "instrument_id": f"US:{symbol}",
                "symbol": symbol,
                "currency": "USD",
                "event_ts": event_time,
                "source_time": event_time,
                "received_at": received_text,
                "price": trade.get("p"),
                "last_size": trade.get("s"),
                "bid": quote.get("bp"),
                "ask": quote.get("ap"),
                "bid_size": quote.get("bs"),
                "ask_size": quote.get("as"),
                "bid_exchange": quote.get("bx"),
                "ask_exchange": quote.get("ax"),
                "trade_time": trade_time,
                "quote_time": quote_time,
                "data_level": "iex-snapshot-single-venue",
                "session": "unknown",
                "phase": "unknown",
            }
            try:
                record = normalize_quote(
                    mapped,
                    max_age_seconds=self.config.freshness_threshold_seconds,
                    policy={
                        "authorized_scopes": ["local_personal"],
                        "attribution_required": True,
                        "policy_reference": "Alpaca personal API terms reviewed by operator; private local use only",
                    },
                    received_at=received_text,
                )
            except (TypeError, ValueError):
                continue
            # Preserve fields useful for quote-side simulation and make IEX's
            # single-venue coverage visible, never label it consolidated SIP.
            record["quote"].update({
                "trade_time": trade_time,
                "quote_time": quote_time,
                "last_trade_size": _as_number(trade.get("s")),
                "last_trade_exchange": _safe_text(trade.get("x")),
                "bid_size": _as_number(quote.get("bs")),
                "ask_size": _as_number(quote.get("as")),
                "bid_exchange": _safe_text(quote.get("bx")),
                "ask_exchange": _safe_text(quote.get("ax")),
                "coverage": self.coverage,
            })
            records[symbol] = record

        if not records:
            return AlpacaIEXResult("no_data", {}, received_at=received_text)
        status = "ok" if len(records) == len(normalized) else "partial"
        return AlpacaIEXResult(status, records, received_at=received_text)

    def _wait_for_request_slot(self) -> None:
        interval = self.config.min_request_interval_seconds
        if interval <= 0:
            return
        with self._rate_lock:
            now = self.monotonic()
            if self._last_request_at is not None:
                wait = interval - (now - self._last_request_at)
                if wait > 0:
                    self.sleep(wait)
                    now = self.monotonic()
            self._last_request_at = now

    @staticmethod
    def _payload(response: HTTPResponse) -> Any:
        if response.json_data is not None:
            return response.json_data
        return json.loads(response.body.decode("utf-8-sig"))

    @staticmethod
    def _result(status: str, *, error_code: Optional[str] = None,
                error_type: Optional[str] = None, http_status: Optional[int] = None) -> AlpacaIEXResult:
        error: Optional[dict[str, Any]] = None
        if error_code:
            error = {"code": error_code, "retryable": error_code in {"transport_error", "http_error"}}
            if error_type:
                error["error_type"] = error_type
            if http_status is not None:
                error["http_status"] = http_status
        return AlpacaIEXResult(status, {}, error=error)


def _safe_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_number(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number == number and abs(number) != float("inf") else None
