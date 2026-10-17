from __future__ import annotations

from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import tempfile
import unittest

from modules.economic_data.bls_v1 import (
    BLS_API_URL,
    BLS_DAILY_LIMIT,
    BLS_DISCLAIMER,
    BLS_FAQ_URL,
    BLS_TERMS_URL,
    BLS_USER_AGENT,
    BLSLaborStatisticsProvider,
    BLSHTTPResponse,
)


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, body, headers, timeout):
        self.calls.append((url, body, dict(headers), timeout))
        if not self.responses:
            raise AssertionError("unexpected outbound request")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeClock:
    def __init__(self):
        self.value = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


class FakeTick:
    def __init__(self):
        self.value = 10.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


def success_response(*, include_all=True):
    rows = [
        {"seriesID": "LNS14000000", "data": [
            {"year": "2026", "period": "M09", "periodName": "September", "value": "4.2", "footnotes": [{}]},
            {"year": "2026", "period": "M13", "periodName": "Annual", "value": "4.0", "footnotes": [{}]},
            {"year": "2025", "period": "M12", "periodName": "December", "value": "4.1", "footnotes": [{}]},
        ]},
        {"seriesID": "LNS11300000", "data": [
            {"year": "2026", "period": "M09", "periodName": "September", "value": "61.8", "footnotes": [{}]},
        ]},
        {"seriesID": "CES0000000001", "data": [
            {"year": "2026", "period": "M09", "periodName": "September", "value": "159044", "footnotes": [{"code": "P", "text": "preliminary"}]},
        ]},
    ]
    if not include_all:
        rows = rows[:2]
    return BLSHTTPResponse(200, json.dumps({"status": "REQUEST_SUCCEEDED", "Results": {"series": rows}}).encode())


class BLSV1Tests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.tick = FakeTick()

    def provider(self, transport, **kwargs):
        return BLSLaborStatisticsProvider(transport, clock=self.clock, monotonic=self.tick,
                                          **kwargs)

    def test_reads_only_three_fixed_series_and_returns_provenance_disclaimer(self):
        transport = FakeTransport(success_response())
        payload = self.provider(transport).payload()
        self.assertTrue(payload["ok"])
        self.assertEqual("LIVE", payload["data_mode"])
        self.assertEqual("fresh", payload["status"])
        self.assertEqual("three_fixed_us_labor_series_only", payload["coverage_scope"])
        self.assertEqual([], payload["covered_symbols"])
        self.assertEqual(BLS_DISCLAIMER, payload["notice"])
        self.assertEqual(BLS_FAQ_URL, payload["provider_url"])
        self.assertEqual(BLS_TERMS_URL, payload["terms_url"])
        self.assertEqual(3, len(payload["items"]))
        unemployment = next(item for item in payload["items"] if item["series_id"] == "LNS14000000")
        self.assertEqual("4.2", unemployment["value"])
        self.assertEqual("2026-M09", unemployment["period"])
        employment = next(item for item in payload["items"] if item["series_id"] == "CES0000000001")
        self.assertEqual([{"code": "P", "text": "preliminary"}], employment["footnotes"])
        self.assertIsNone(employment["symbol"])
        self.assertEqual([], employment["symbols"])
        url, body, headers, timeout = transport.calls[0]
        self.assertEqual(BLS_API_URL, url)
        request = json.loads(body)
        self.assertEqual(["LNS14000000", "LNS11300000", "CES0000000001"], request["seriesid"])
        self.assertEqual("2025", request["startyear"])
        self.assertEqual("2026", request["endyear"])
        self.assertNotIn("registrationkey", request)
        self.assertEqual(BLS_USER_AGENT, headers["User-Agent"])
        self.assertEqual("application/json", headers["Content-Type"])
        self.assertEqual(10.0, timeout)

    def test_local_poll_interval_and_daily_cap_prevent_extra_requests(self):
        transport = FakeTransport(success_response(), success_response(), success_response())
        provider = self.provider(transport, max_requests_per_day=2, min_poll_seconds=3600)
        first = provider.payload()
        limited = provider.payload()
        self.assertEqual("fresh", first["status"])
        self.assertEqual("rate_limited_local", limited["status"])
        self.assertEqual("poll_interval", limited["error_code"])
        self.assertTrue(all(item["cached"] for item in limited["items"]))
        self.assertEqual(1, len(transport.calls))
        self.tick.advance(3601)
        second = provider.payload()
        self.assertEqual(2, len(transport.calls))
        self.assertEqual(2, second["requests_today"])
        self.tick.advance(3601)
        daily_limited = provider.payload()
        self.assertEqual("daily_limit", daily_limited["error_code"])
        self.assertEqual(2, daily_limited["requests_today"])
        self.assertEqual(BLS_DAILY_LIMIT, daily_limited["official_daily_limit"])
        self.assertEqual(2, len(transport.calls))

    def test_partial_response_is_explicit_and_does_not_invent_missing_series(self):
        transport = FakeTransport(success_response(include_all=False))
        payload = self.provider(transport).payload()
        self.assertTrue(payload["ok"])
        self.assertEqual("partial", payload["status"])
        self.assertEqual(2, len(payload["items"]))
        self.assertNotIn("CES0000000001", [item["series_id"] for item in payload["items"]])

    def test_http_failure_is_counted_and_never_retried(self):
        transport = FakeTransport(BLSHTTPResponse(429, b""), success_response())
        provider = self.provider(transport)
        failed = provider.payload()
        self.assertFalse(failed["ok"])
        self.assertEqual("http_error", failed["error_code"])
        self.assertEqual(1, failed["requests_today"])
        limited = provider.payload()
        self.assertEqual("rate_limited_local", limited["status"])
        self.assertEqual(1, len(transport.calls))

    def test_transport_failure_returns_safe_error_without_body_or_retry(self):
        transport = FakeTransport(ConnectionError("private transport details"), success_response())
        payload = self.provider(transport).payload()
        self.assertEqual("transport_error", payload["error_code"])
        self.assertNotIn("private transport details", str(payload))
        self.assertEqual(1, len(transport.calls))

    def test_daily_cap_cannot_exceed_official_v1_limit(self):
        with self.assertRaises(ValueError):
            self.provider(FakeTransport(), max_requests_per_day=BLS_DAILY_LIMIT + 1)

    def test_persisted_daily_budget_survives_provider_recreation(self):
        with tempfile.TemporaryDirectory(prefix="bls-budget-test-") as temp_dir:
            state_path = Path(temp_dir) / "bls-v1-usage.json"
            first_transport = FakeTransport(success_response())
            first = BLSLaborStatisticsProvider(
                first_transport, clock=self.clock, monotonic=self.tick,
                max_requests_per_day=1, min_poll_seconds=0, state_path=state_path,
            ).payload()
            self.assertEqual("fresh", first["status"])
            self.tick.advance(1)
            second_transport = FakeTransport(success_response())
            second = BLSLaborStatisticsProvider(
                second_transport, clock=self.clock, monotonic=self.tick,
                max_requests_per_day=1, min_poll_seconds=0, state_path=state_path,
            ).payload()
            self.assertEqual("rate_limited_local", second["status"])
            self.assertEqual("daily_limit", second["error_code"])
            self.assertEqual(1, second["requests_today"])
            self.assertEqual([], second_transport.calls)


if __name__ == "__main__":
    unittest.main()
