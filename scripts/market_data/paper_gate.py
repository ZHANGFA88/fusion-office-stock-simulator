"""Fail-closed data gates for simulated PAPER decisions.

This module is deliberately pure: it performs no I/O, provider calls, or ledger
writes. A record is eligible only when its provenance, authorization, mode,
freshness, and requested coverage can all be verified.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from .contracts import parse_timestamp

PAPER_SCOPE = "local_personal"
SUPPORTED_DATA_MODES = {"LIVE", "DEMO"}


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _current_time(now: Any = None) -> datetime | None:
    if now is None:
        return datetime.now(timezone.utc)
    return parse_timestamp(now)


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def quote_price(record: Mapping[str, Any]) -> float | None:
    """Read a price from either the canonical quote envelope or a flat row."""
    quote = record.get("quote")
    raw = quote.get("price") if isinstance(quote, Mapping) else record.get("price")
    value = _finite_number(raw)
    return value if value is not None and value > 0 else None


def validate_provenance(
    record: Any,
    *,
    now: Any = None,
    max_age_seconds: float | None = None,
    require_price: bool = False,
    expected_symbol: str | None = None,
) -> list[str]:
    """Return fail-closed reasons for a quote/news/analysis provenance record.

    `max_age_seconds` is an explicit caller policy. When omitted, the source
    must still assert `status=fresh`; timestamps are checked for order and future
    skew, while the provider's documented freshness classifier remains the
    authority for non-quote inputs such as news.
    """
    problems: list[str] = []
    if not isinstance(record, Mapping):
        return ["record_missing"]

    mode = _text(record.get("data_mode")).upper()
    if mode == "DEMO":
        problems.append("demo_data")
    elif mode != "LIVE":
        problems.append("data_mode_missing_or_invalid")

    if not _text(record.get("provider")):
        problems.append("provider_missing")
    if not _text(record.get("feed")):
        problems.append("feed_missing")
    if not (_text(record.get("source_id")) or _text(record.get("id"))):
        problems.append("source_id_missing")

    status = _text(record.get("status") or record.get("freshness")).lower()
    if status != "fresh" or record.get("stale") is True:
        problems.append("not_fresh")

    policy = record.get("policy")
    if not isinstance(policy, Mapping):
        problems.append("policy_missing")
    else:
        scopes = policy.get("authorized_scopes")
        if not isinstance(scopes, (list, tuple, set, frozenset)):
            problems.append("authorization_missing")
        elif PAPER_SCOPE not in scopes:
            problems.append("paper_scope_not_authorized")
        if not _text(policy.get("policy_reference")):
            problems.append("policy_reference_missing")

    event_value = record.get("event_ts")
    received_value = record.get("received_at")
    event_time = parse_timestamp(event_value)
    received_time = parse_timestamp(received_value)
    current = _current_time(now)
    if event_time is None:
        problems.append("event_timestamp_missing_or_invalid")
    if received_time is None:
        problems.append("received_timestamp_missing_or_invalid")
    if current is None:
        problems.append("decision_clock_invalid")
    if event_time and received_time:
        if event_time > received_time:
            problems.append("timestamp_order_invalid")
    if current and event_time and event_time > current:
        problems.append("event_timestamp_in_future")
    if current and received_time and received_time > current:
        problems.append("received_timestamp_in_future")
    if max_age_seconds is not None:
        if isinstance(max_age_seconds, bool) or max_age_seconds < 0:
            problems.append("freshness_policy_invalid")
        elif current and event_time and 0 <= (current - event_time).total_seconds() > max_age_seconds:
            problems.append("event_timestamp_expired")
        elif current and event_time and (current - event_time).total_seconds() < 0:
            pass  # The future-time reason above is more specific.
        if current and received_time and 0 <= (current - received_time).total_seconds() > max_age_seconds:
            problems.append("received_timestamp_expired")

    if expected_symbol is not None and _text(record.get("symbol")).upper() != expected_symbol.upper():
        problems.append("symbol_mismatch")
    if require_price and quote_price(record) is None:
        problems.append("price_missing_or_invalid")
    return list(dict.fromkeys(problems))


def validate_quote_batch(
    records: Any,
    expected_symbols: Sequence[str],
    *,
    now: Any = None,
    max_age_seconds: float,
) -> list[str]:
    """Require one eligible quote for every requested symbol, and no extras."""
    expected = [str(symbol).strip().upper() for symbol in expected_symbols]
    problems: list[str] = []
    if not expected or any(not symbol for symbol in expected):
        problems.append("expected_symbols_missing")
    if len(set(expected)) != len(expected):
        problems.append("expected_symbols_duplicated")
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes, bytearray)):
        return problems + ["quote_batch_missing"]

    by_symbol: dict[str, list[Mapping[str, Any]]] = {}
    for row in records:
        if not isinstance(row, Mapping):
            problems.append("quote_record_invalid")
            continue
        symbol = _text(row.get("symbol")).upper()
        if not symbol:
            problems.append("quote_symbol_missing")
            continue
        by_symbol.setdefault(symbol, []).append(row)

    wanted = set(expected)
    actual = set(by_symbol)
    if wanted - actual:
        problems.append("partial_quote_coverage")
    if actual - wanted:
        problems.append("unexpected_quote_coverage")
    for symbol, rows in by_symbol.items():
        if len(rows) != 1:
            problems.append("duplicate_quote:" + symbol)
            continue
        problems.extend(
            f"{symbol}:{reason}" for reason in validate_provenance(
                rows[0], now=now, max_age_seconds=max_age_seconds,
                require_price=True, expected_symbol=symbol,
            )
        )
    return list(dict.fromkeys(problems))


def _snapshot_time_error(envelope: Any, *, now: datetime, max_age_seconds: float) -> str | None:
    if not isinstance(envelope, Mapping):
        return "snapshot_missing"
    timestamp = parse_timestamp(envelope.get("ts") or envelope.get("received_at"))
    if timestamp is None:
        return "snapshot_timestamp_missing_or_invalid"
    age = (now - timestamp).total_seconds()
    if age < 0:
        return "snapshot_timestamp_in_future"
    if age > max_age_seconds:
        return "snapshot_expired"
    return None


def validate_ai_cycle_snapshot(
    snapshot: Mapping[str, Any],
    expected_symbols: Sequence[str],
    *,
    now: Any = None,
    quote_max_age_seconds: float,
) -> list[str]:
    """Validate all required inputs before an AI cycle may submit any PAPER order.

    The gate is intentionally stricter than display/UI validation. Required
    inputs are the full candidate universe, complete fresh quotes, one complete
    analysis per candidate, a LIVE/authorized news feed with full universe
    coverage, and current local-ledger/account snapshots.
    """
    current = _current_time(now)
    if current is None:
        return ["decision_clock_invalid"]
    if not isinstance(snapshot, Mapping):
        return ["cycle_snapshot_missing"]
    symbols = [str(symbol).strip().upper() for symbol in expected_symbols]
    if not symbols or any(not symbol for symbol in symbols):
        return ["expected_symbols_missing"]

    problems: list[str] = []
    for key in ("watchlist", "picks", "quotes", "news", "account", "positions"):
        envelope = snapshot.get(key)
        if not isinstance(envelope, Mapping) or envelope.get("ok") is not True:
            problems.append("input_unavailable:" + key)

    watch = snapshot.get("watchlist")
    picks = snapshot.get("picks")
    if isinstance(watch, Mapping) and not isinstance(watch.get("items"), list):
        problems.append("watchlist_coverage_invalid")
    if isinstance(picks, Mapping) and not isinstance(picks.get("picks"), list):
        problems.append("picks_coverage_invalid")

    quotes = snapshot.get("quotes")
    if isinstance(quotes, Mapping):
        problems.extend(validate_quote_batch(
            quotes.get("items"), symbols, now=current,
            max_age_seconds=quote_max_age_seconds,
        ))

    news = snapshot.get("news")
    if isinstance(news, Mapping) and news.get("ok") is True:
        if _text(news.get("data_mode")).upper() != "LIVE" or news.get("demo") is True:
            problems.append("news_not_live")
        if news.get("coverage_complete") is not True:
            problems.append("news_coverage_not_complete")
        covered = news.get("covered_symbols")
        if not isinstance(covered, list) or not set(symbols).issubset(
            {str(value).strip().upper() for value in covered}
        ):
            problems.append("partial_news_coverage")
        provenance = news.get("provenance")
        problems.extend("news:" + reason for reason in validate_provenance(
            provenance, now=current, max_age_seconds=None,
        ))
        items = news.get("items")
        if not isinstance(items, list):
            problems.append("news_items_invalid")
        else:
            for index, item in enumerate(items):
                problems.extend(
                    f"news_item_{index}:{reason}" for reason in validate_provenance(
                        item, now=current, max_age_seconds=None,
                    )
                )

    analyses = snapshot.get("analyses")
    if not isinstance(analyses, Mapping):
        problems.append("analyses_missing")
    else:
        analysis_symbols = {str(value).strip().upper() for value in analyses}
        if analysis_symbols != set(symbols):
            problems.append("partial_analysis_coverage")
        for symbol in symbols:
            analysis = analyses.get(symbol) or analyses.get(symbol.lower())
            if not isinstance(analysis, Mapping) or analysis.get("ok") is not True:
                problems.append("analysis_unavailable:" + symbol)
                continue
            indicators = analysis.get("indicators")
            if not isinstance(indicators, Mapping) or indicators.get("ok") is not True:
                problems.append("indicators_unavailable:" + symbol)
            provenance = analysis.get("provenance")
            problems.extend(f"analysis_{symbol}:{reason}" for reason in validate_provenance(
                provenance, now=current, max_age_seconds=None,
            ))

    for key in ("account", "positions"):
        envelope = snapshot.get(key)
        if not isinstance(envelope, Mapping) or envelope.get("ok") is not True:
            continue
        if envelope.get("source") != "local-paper-ledger":
            problems.append("ledger_source_invalid:" + key)
        time_error = _snapshot_time_error(
            envelope, now=current, max_age_seconds=quote_max_age_seconds,
        )
        if time_error:
            problems.append(key + ":" + time_error)

    account = snapshot.get("account")
    if isinstance(account, Mapping) and account.get("ok") is True:
        account_rows = account.get("accounts")
        if not isinstance(account_rows, list):
            problems.append("account_rows_invalid")
        else:
            by_market = {str(row.get("market", "")).upper(): row
                         for row in account_rows if isinstance(row, Mapping)}
            for market in ("CN", "HK", "US"):
                row = by_market.get(market)
                if row is None:
                    problems.append("account_missing:" + market)
                    continue
                if row.get("valuation_complete") is not True:
                    problems.append("account_valuation_incomplete:" + market)
                if _finite_number(row.get("cash")) is None or _finite_number(row.get("equity")) is None:
                    problems.append("account_values_invalid:" + market)

    positions = snapshot.get("positions")
    if isinstance(positions, Mapping) and positions.get("ok") is True:
        if not isinstance(positions.get("positions"), list):
            problems.append("positions_rows_invalid")

    return list(dict.fromkeys(problems))
