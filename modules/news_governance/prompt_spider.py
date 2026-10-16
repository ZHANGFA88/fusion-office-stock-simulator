"""Finance-only projection to the Prompt Spider UI event shape."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Optional

from .normalizer import AUTHORIZED_SCOPES, public_news

DATA_MODES = frozenset({"DEMO", "LIVE"})


def to_spider_event(
    record: Mapping[str, Any],
    *,
    intended_scope: str,
    data_mode: str,
    classification: Optional[str] = None,
    route_reason: Optional[str] = None,
    summary: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Project an authorized normalized finance-news record for visualization.

    `data_mode` is explicit and never inferred from freshness. Classification,
    route reason, and summary are only passed through when a caller supplies them;
    this module does not invent facts or summaries.
    """
    if data_mode not in DATA_MODES:
        raise ValueError("data_mode must be DEMO or LIVE")
    if intended_scope not in AUTHORIZED_SCOPES:
        raise ValueError("intended_scope must be an exact contract scope")
    if record.get("record_type") != "news" or record.get("domain") != "finance":
        return None
    visible = public_news(record, intended_scope=intended_scope)
    if visible is None:
        return None
    if data_mode == "LIVE" and not (visible.get("provider") and visible.get("source_url")):
        return None

    news = visible.get("news")
    if not isinstance(news, Mapping):
        return None
    content_type = str(news.get("content_type") or "news").strip().casefold().replace(" ", "_")
    if content_type in {"sec_filing", "filing", "company_filing"}:
        kind = "filing"
    elif "announcement" in content_type:
        kind = "announcement"
    else:
        kind = "news"

    symbols: list[str] = []
    related = visible.get("related_instruments")
    if isinstance(related, list):
        for item in related:
            if not isinstance(item, Mapping) or float(item.get("confidence") or 0) < 0.9:
                continue
            symbol = item.get("symbol")
            if isinstance(symbol, str) and symbol.strip() and symbol.strip() not in symbols:
                symbols.append(symbol.strip())
    symbol = visible.get("symbol")
    if len(symbols) == 1:
        selected_symbol = symbols[0]
    elif not symbols and isinstance(symbol, str) and symbol.strip():
        selected_symbol = symbol.strip()
        symbols.append(selected_symbol)
    else:
        selected_symbol = None

    return {
        "kind": kind,
        "headline": str(news.get("title") or ""),
        "summary": summary,
        "classification": classification,
        "route_reason": route_reason,
        "symbol": selected_symbol,
        "symbols": symbols,
        "provider": visible.get("provider"),
        "publisher": visible.get("publisher"),
        "market": visible.get("market", "UNKNOWN"),
        "event_ts": visible.get("event_ts"),
        "received_at": visible.get("received_at"),
        "freshness": visible.get("status", "unavailable"),
        "source_url": visible.get("source_url"),
        "provider_url": visible.get("provider_url"),
        "domain": "finance",
        "data_mode": data_mode,
    }
