"""Deterministic, evidence-bearing security association for news records."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping, Optional, Protocol, Sequence

MARKETS = frozenset({"CN", "HK", "US", "UNKNOWN"})


class SecurityMatcher(Protocol):
    def match(self, *, title: str, market: str) -> Sequence[Mapping[str, Any]]: ...


def normalize_related_instruments(values: Any) -> list[dict[str, Any]]:
    """Validate explicit or inferred associations without upgrading confidence."""
    if values is None:
        return []
    if isinstance(values, Mapping):
        values = [values]
    if not isinstance(values, (list, tuple)):
        return []
    result: list[dict[str, Any]] = []
    for value in values:
        if not isinstance(value, Mapping):
            continue
        instrument_id = str(value.get("instrument_id") or "").strip()
        symbol = str(value.get("symbol") or "").strip() or None
        market_raw = str(value.get("market") or "UNKNOWN").strip().upper()
        market = market_raw if market_raw in MARKETS else "UNKNOWN"
        exchange = str(value.get("exchange") or "").strip().upper() or None
        method = str(value.get("method") or "").strip()
        try:
            confidence = float(value.get("confidence"))
        except (TypeError, ValueError):
            continue
        if not instrument_id or not method or not 0.0 <= confidence <= 1.0:
            continue
        row = {
            "instrument_id": instrument_id,
            "symbol": symbol,
            "market": market,
            "exchange": exchange,
            "method": method,
            "confidence": confidence,
        }
        result.append(row)
    return result


@dataclass(frozen=True)
class Security:
    instrument_id: str
    symbol: str
    market: str
    exchange: str
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.instrument_id.strip() or not self.symbol.strip() or not self.exchange.strip():
            raise ValueError("instrument_id, symbol and exchange are required")
        if self.market.upper() not in MARKETS - {"UNKNOWN"}:
            raise ValueError("market must be CN, HK or US")

    def as_match(self, *, method: str, confidence: float) -> dict[str, Any]:
        return {
            "instrument_id": self.instrument_id,
            "symbol": self.symbol,
            "market": self.market.upper(),
            "exchange": self.exchange.upper(),
            "method": method,
            "confidence": confidence,
        }


class StaticSecurityMatcher:
    """Small local/mock directory; exact mentions only, no fuzzy/model matching.

    Stable identity is market + exchange + instrument_id. If the market is
    unknown or several listings share a symbol, all matches are retained with
    low confidence so downstream displays can label them as possible matches.
    """

    def __init__(self, securities: Sequence[Security] = ()) -> None:
        self.securities = tuple(securities)

    def match(self, *, title: str, market: str) -> tuple[dict[str, Any], ...]:
        normalized_market = str(market or "UNKNOWN").upper()
        hits: list[Security] = []
        for security in self.securities:
            tokens = (security.symbol, *security.aliases)
            if any(_contains_token(title, token) for token in tokens if token):
                hits.append(security)
        if not hits:
            return ()

        market_hits = [row for row in hits if row.market.upper() == normalized_market]
        if normalized_market != "UNKNOWN" and market_hits:
            selected = market_hits
            if len(selected) == 1:
                return (selected[0].as_match(method="exact-market-symbol", confidence=0.75),)
            return tuple(row.as_match(method="ambiguous-market-symbol", confidence=0.4) for row in selected)

        if len(hits) == 1 and normalized_market == hits[0].market.upper():
            return (hits[0].as_match(method="exact-market-symbol", confidence=0.75),)
        return tuple(row.as_match(method="ambiguous-symbol-collision", confidence=0.35) for row in hits)


def _contains_token(text: str, token: str) -> bool:
    token = token.strip()
    if not token:
        return False
    pattern = rf"(?<![A-Za-z0-9])\$?{re.escape(token)}(?![A-Za-z0-9])"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None
