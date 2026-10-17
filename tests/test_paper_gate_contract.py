import datetime
import unittest
from scripts.market_data.paper_gate import validate_ai_cycle_snapshot, validate_provenance, validate_quote_batch

def _iso(moment):
    return moment.astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def _record(symbol="AAPL", *, now=None, data_mode="LIVE", status="fresh"):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return {
        "id": "quote-record-" + symbol,
        "source_id": "source-" + symbol,
        "provider": "test-provider",
        "feed": "test-feed",
        "symbol": symbol,
        "price": 100.0,
        "data_mode": data_mode,
        "status": status,
        "event_ts": _iso(now - datetime.timedelta(seconds=2)),
        "received_at": _iso(now - datetime.timedelta(seconds=1)),
        "policy": {
            "authorized_scopes": ["local_personal"],
            "policy_reference": "reviewed-test-policy",
        },
    }


def _provenance(now, source_id="source-1"):
    return {
        "id": "record-" + source_id,
        "source_id": source_id,
        "provider": "test-provider",
        "feed": "test-feed",
        "data_mode": "LIVE",
        "status": "fresh",
        "event_ts": _iso(now - datetime.timedelta(seconds=3)),
        "received_at": _iso(now - datetime.timedelta(seconds=1)),
        "policy": {
            "authorized_scopes": ["local_personal"],
            "policy_reference": "reviewed-test-policy",
        },
    }


def _complete_cycle_snapshot(now, symbols):
    quotes = [_record(symbol, now=now) for symbol in symbols]
    analyses = {
        symbol: {
            "ok": True,
            "indicators": {"ok": True, "close": 100.0},
            "provenance": _provenance(now, "analysis-" + symbol),
        }
        for symbol in symbols
    }
    accounts = [
        {"market": market, "cash": 10000.0, "equity": 10000.0,
         "valuation_complete": True}
        for market in ("CN", "HK", "US")
    ]
    return {
        "watchlist": {"ok": True, "items": [{"symbol": symbol} for symbol in symbols]},
        "picks": {"ok": True, "picks": []},
        "quotes": {"ok": True, "items": quotes},
        "news": {
            "ok": True,
            "data_mode": "LIVE",
            "coverage_complete": True,
            "covered_symbols": list(symbols),
            "provenance": _provenance(now, "news-feed"),
            "items": [],
        },
        "account": {
            "ok": True, "source": "local-paper-ledger", "ts": now.timestamp(),
            "accounts": accounts,
        },
        "positions": {
            "ok": True, "source": "local-paper-ledger", "ts": now.timestamp(),
            "positions": [],
        },
        "analyses": analyses,
    }


class QuoteEligibilityTests(unittest.TestCase):
    def test_complete_fresh_live_authorized_quote_is_eligible(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        quote = _record(now=now)
        self.assertEqual([], validate_provenance(
            quote, now=now, max_age_seconds=120, require_price=True,
            expected_symbol="AAPL",
        ))
        self.assertEqual([], validate_quote_batch(
            [quote], ["AAPL"], now=now, max_age_seconds=120,
        ))

    def test_demo_missing_provenance_authority_and_staleness_fail_closed(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        cases = [
            ({"data_mode": "DEMO"}, "demo_data"),
            ({"source_id": None, "id": None}, "source_id_missing"),
            ({"policy": {"authorized_scopes": [], "policy_reference": "x"}},
             "paper_scope_not_authorized"),
            ({"status": "stale"}, "not_fresh"),
            ({"feed": None}, "feed_missing"),
        ]
        for changes, expected in cases:
            with self.subTest(expected=expected):
                row = _record(now=now)
                row.update(changes)
                self.assertIn(expected, validate_provenance(
                    row, now=now, max_age_seconds=120, require_price=True,
                ))

        expired = _record(now=now)
        expired["event_ts"] = _iso(now - datetime.timedelta(seconds=500))
        self.assertIn("event_timestamp_expired", validate_provenance(
            expired, now=now, max_age_seconds=120, require_price=True,
        ))

    def test_partial_or_duplicate_quote_coverage_is_rejected(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        aapl = _record("AAPL", now=now)
        msft = _record("MSFT", now=now)
        self.assertIn("partial_quote_coverage", validate_quote_batch(
            [aapl], ["AAPL", "MSFT"], now=now, max_age_seconds=120,
        ))
        self.assertIn("duplicate_quote:AAPL", validate_quote_batch(
            [aapl, aapl, msft], ["AAPL", "MSFT"], now=now,
            max_age_seconds=120,
        ))

    def test_entire_ai_cycle_requires_complete_all_inputs(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        symbols = ["AAPL", "MSFT"]
        snapshot = _complete_cycle_snapshot(now, symbols)
        self.assertEqual([], validate_ai_cycle_snapshot(
            snapshot, symbols, now=now, quote_max_age_seconds=120,
        ))

        partial = _complete_cycle_snapshot(now, symbols)
        partial["quotes"]["items"].pop()
        partial["news"]["data_mode"] = "DEMO"
        del partial["analyses"]["MSFT"]
        errors = validate_ai_cycle_snapshot(
            partial, symbols, now=now, quote_max_age_seconds=120,
        )
        self.assertIn("partial_quote_coverage", errors)
        self.assertIn("news_not_live", errors)
        self.assertIn("partial_analysis_coverage", errors)

    def test_partial_news_coverage_and_incomplete_account_block_cycle(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        symbols = ["AAPL"]
        snapshot = _complete_cycle_snapshot(now, symbols)
        snapshot["news"]["covered_symbols"] = []
        snapshot["account"]["accounts"][0]["valuation_complete"] = False
        errors = validate_ai_cycle_snapshot(
            snapshot, symbols, now=now, quote_max_age_seconds=120,
        )
        self.assertIn("partial_news_coverage", errors)
        self.assertIn("account_valuation_incomplete:CN", errors)


if __name__ == "__main__":
    unittest.main()
