import json
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from scripts.market_data.alpaca_iex import (
    AlpacaIEXConfig,
    AlpacaIEXPersonalProvider,
    HTTPResponse,
    ScraplingRESTTransport,
)
from scripts.market_data.normalizer import public_quote

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / "tests/fixtures/alpaca_iex_latest_quote.json").read_text(encoding="utf-8"))
NOW = datetime(2026, 10, 16, 14, 30, 2, tzinfo=timezone.utc)


class MockTransport:
    def __init__(self, *, status=200, fixture=FIXTURE):
        self.status = status
        self.fixture = fixture
        self.calls = []

    def get(self, url, headers, timeout):
        self.calls.append((url, dict(headers), timeout))
        return HTTPResponse(self.status, {"content-type": "application/json"},
                            json.dumps(self.fixture).encode(), self.fixture)


def enabled_config(scopes=("local_personal",), threshold=30):
    return AlpacaIEXConfig(
        api_key="fixture-key-redacted",
        api_secret="fixture-secret-redacted",
        provider_enabled=True,
        authorized_scopes=frozenset(scopes),
        freshness_threshold_seconds=threshold,
        min_request_interval_seconds=0,
    )


class AlpacaIEXProviderTests(unittest.TestCase):
    def test_missing_environment_credentials_disables_without_transport_call(self):
        transport = MockTransport()
        with patch.dict(os.environ, {}, clear=True):
            config = AlpacaIEXConfig.from_environment(
                provider_enabled=True, authorized_scopes=("local_personal",),
                freshness_threshold_seconds=30,
            )
        provider = AlpacaIEXPersonalProvider(config, transport=transport, clock=lambda: NOW)
        result = provider.fetch(["AAPL"])
        self.assertEqual(result.status, "disabled")
        self.assertEqual(result.error["code"], "credentials_missing")
        self.assertEqual(transport.calls, [])

    def test_default_config_is_disabled_even_when_credentials_exist(self):
        transport = MockTransport()
        config = enabled_config()
        config = AlpacaIEXConfig(api_key=config.api_key, api_secret=config.api_secret)
        provider = AlpacaIEXPersonalProvider(config, transport=transport, clock=lambda: NOW)
        result = provider.fetch(["AAPL"])
        self.assertEqual(result.status, "disabled")
        self.assertFalse(provider.policy.provider_enabled)
        self.assertEqual(transport.calls, [])

    def test_public_scope_and_scope_expansion_are_rejected_without_transport(self):
        transport = MockTransport()
        public_only = AlpacaIEXPersonalProvider(
            enabled_config(scopes=("public",)), transport=transport, clock=lambda: NOW)
        self.assertEqual(public_only.fetch(["AAPL"], scope="public").status, "denied")
        expanded = AlpacaIEXPersonalProvider(
            enabled_config(scopes=("local_personal", "public")), transport=transport, clock=lambda: NOW)
        self.assertEqual(expanded.fetch(["AAPL"]).status, "denied")
        self.assertEqual(transport.calls, [])

    def test_iex_quote_normalizes_event_and_receive_times_and_keeps_scope_private(self):
        transport = MockTransport()
        provider = AlpacaIEXPersonalProvider(
            enabled_config(), transport=transport, clock=lambda: NOW)
        result = provider.fetch(["aapl"])
        self.assertEqual(result.status, "ok")
        self.assertEqual(len(transport.calls), 1)
        url, headers, timeout = transport.calls[0]
        parsed = urlparse(url)
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "data.alpaca.markets")
        self.assertEqual(parsed.path, "/v2/stocks/snapshots")
        self.assertEqual(parse_qs(parsed.query)["feed"], ["iex"])
        self.assertEqual(parse_qs(parsed.query)["symbols"], ["AAPL"])
        self.assertIn("APCA-API-KEY-ID", headers)
        self.assertIn("APCA-API-SECRET-KEY", headers)
        self.assertNotIn("fixture-secret-redacted", url)

        record = result.records["AAPL"]
        self.assertEqual(record["domain"], "finance")
        self.assertEqual(record["provider"], "Alpaca Market Data")
        self.assertEqual(record["feed"], "iex")
        self.assertEqual(record["publisher"], "Investors Exchange (IEX)")
        self.assertEqual(record["event_ts"], "2026-10-16T14:30:00.456Z")
        self.assertEqual(record["quote"]["trade_time"], "2026-10-16T14:30:00.456789123Z")
        self.assertEqual(record["quote"]["quote_time"], "2026-10-16T14:30:00.123456789Z")
        self.assertEqual(record["received_at"], "2026-10-16T14:30:02.000Z")
        self.assertEqual(record["status"], "fresh")
        self.assertEqual(record["quote"]["price"], 231.11)
        self.assertEqual(record["quote"]["bid"], 231.10)
        self.assertEqual(record["quote"]["ask"], 231.12)
        self.assertEqual(record["quote"]["coverage"], "single_venue_iex_not_consolidated")
        self.assertEqual(record["policy"]["authorized_scopes"], ["local_personal"])
        self.assertIsNone(public_quote(record))
        self.assertNotIn("fixture-secret-redacted", repr(provider.config))

    def test_stale_quote_is_explicit_and_does_not_become_current(self):
        transport = MockTransport()
        provider = AlpacaIEXPersonalProvider(
            enabled_config(threshold=1), transport=transport, clock=lambda: NOW)
        result = provider.fetch(["AAPL"])
        self.assertEqual(result.records["AAPL"]["status"], "stale")

    def test_disabled_provider_and_missing_freshness_threshold_make_zero_requests(self):
        transport = MockTransport()
        config = enabled_config(threshold=None)
        provider = AlpacaIEXPersonalProvider(config, transport=transport, clock=lambda: NOW)
        result = provider.fetch(["AAPL"])
        self.assertEqual(result.status, "disabled")
        self.assertEqual(result.error["code"], "freshness_threshold_required")
        self.assertEqual(transport.calls, [])

        disabled = AlpacaIEXConfig(
            api_key="fixture-key-redacted", api_secret="fixture-secret-redacted",
            provider_enabled=False, authorized_scopes=frozenset({"local_personal"}),
            freshness_threshold_seconds=30,
        )
        result = AlpacaIEXPersonalProvider(disabled, transport=transport, clock=lambda: NOW).fetch(["AAPL"])
        self.assertEqual(result.error["code"], "provider_disabled")
        self.assertEqual(transport.calls, [])

    def test_provider_rejects_invalid_symbol_and_failed_http_without_exposing_body(self):
        transport = MockTransport()
        provider = AlpacaIEXPersonalProvider(enabled_config(), transport=transport, clock=lambda: NOW)
        self.assertEqual(provider.fetch(["AAPL/../../secret"]).error["code"], "invalid_symbol")
        self.assertEqual(transport.calls, [])

        failed = AlpacaIEXPersonalProvider(
            enabled_config(), transport=MockTransport(status=401), clock=lambda: NOW)
        result = failed.fetch(["AAPL"])
        self.assertEqual(result.status, "error")
        self.assertEqual(result.error["http_status"], 401)
        self.assertNotIn("fixture-secret-redacted", repr(result))

    def test_scrapling_transport_uses_only_plain_fetcher_options(self):
        class Response:
            status = 200
            headers = {"content-type": "application/json"}
            body = json.dumps(FIXTURE).encode()

            @staticmethod
            def json():
                return FIXTURE

        with patch("scrapling.fetchers.Fetcher.get", return_value=Response) as get:
            response = ScraplingRESTTransport().get(
                "https://data.alpaca.markets/v2/stocks/snapshots?symbols=AAPL&feed=iex",
                {"Accept": "application/json"}, 8,
            )
        self.assertEqual(response.status, 200)
        self.assertEqual(response.json_data, FIXTURE)
        kwargs = get.call_args.kwargs
        self.assertEqual(kwargs["retries"], 0)
        self.assertFalse(kwargs["follow_redirects"])
        self.assertFalse(kwargs["stealthy_headers"])
        self.assertIsNone(kwargs["impersonate"])
        self.assertIsNone(kwargs["proxy"])
        self.assertIsNone(kwargs["proxy_rotator"])
        self.assertTrue(kwargs["verify"])


if __name__ == "__main__":
    unittest.main()
