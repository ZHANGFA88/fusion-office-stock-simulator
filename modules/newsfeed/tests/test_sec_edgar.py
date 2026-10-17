from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from modules.newsfeed.scrapling_feed import HTTPResponse
from modules.newsfeed.sec_edgar import SEC_FAIR_ACCESS_URL, SECSubmissionsConfig, SECSubmissionsProvider

FIXTURE = Path(__file__).parent / "fixtures" / "sec_submissions.json"


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, headers, timeout):
        self.calls.append((url, dict(headers), timeout))
        if not self.responses:
            raise AssertionError("unexpected outbound request")
        response = self.responses.pop(0)
        return response


class FixedClock:
    def __init__(self):
        self.value = datetime(2026, 10, 16, 14, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        from datetime import timedelta
        self.value += timedelta(seconds=seconds)


class SECSubmissionsTests(unittest.TestCase):
    def setUp(self):
        self.payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.clock = FixedClock()

    def enabled_config(self, **overrides):
        values = dict(cik="1", contact_email="operator@example.test", enabled=True,
                      terms_reviewed=True, robots_allowed=True, local_personal_authorized=True,
                      min_poll_seconds=60)
        values.update(overrides)
        return SECSubmissionsConfig(**values)

    def test_missing_contact_email_fails_closed_without_network(self):
        transport = FakeTransport()
        provider = SECSubmissionsProvider(self.enabled_config(contact_email=""), transport,
                                          clock=self.clock, monotonic=lambda: 1000.0)
        result = provider.read()
        self.assertEqual(result.health.status, "denied")
        self.assertEqual(result.health.error_code, "contact_email_required")
        self.assertEqual(transport.calls, [])

    def test_contact_email_is_read_only_from_explicit_local_environment(self):
        with patch.dict(os.environ, {"SEC_CONTACT_EMAIL": "operator@example.test"}, clear=True):
            config = SECSubmissionsConfig.from_environment(
                "1", enabled=True, terms_reviewed=True, robots_allowed=True,
                local_personal_authorized=True,
            )
        self.assertEqual(config.contact_email, "operator@example.test")
        self.assertTrue(config.enabled)
        self.assertFalse(SECSubmissionsConfig(cik="1").enabled)

    def test_missing_local_contact_email_forces_environment_config_disabled(self):
        with patch.dict(os.environ, {}, clear=True):
            config = SECSubmissionsConfig.from_environment(
                "1", enabled=True, terms_reviewed=True, robots_allowed=True,
                local_personal_authorized=True,
            )
        self.assertEqual(config.contact_email, "")
        self.assertFalse(config.enabled)
        transport = FakeTransport()
        result = SECSubmissionsProvider(config, transport, clock=self.clock,
                                        monotonic=lambda: 1000.0).read()
        self.assertEqual(result.health.error_code, "disabled")
        self.assertEqual(transport.calls, [])

    def test_policy_review_is_required_before_fetch(self):
        transport = FakeTransport()
        provider = SECSubmissionsProvider(self.enabled_config(terms_reviewed=False), transport,
                                          clock=self.clock, monotonic=lambda: 1000.0)
        result = provider.read()
        self.assertEqual(result.health.error_code, "policy_review_required")
        self.assertEqual(transport.calls, [])

    def test_local_personal_scope_defaults_off(self):
        transport = FakeTransport()
        config = self.enabled_config(local_personal_authorized=False)
        provider = SECSubmissionsProvider(config, transport,
                                          clock=self.clock, monotonic=lambda: 1000.0)
        result = provider.read()
        self.assertEqual(result.health.error_code, "local_personal_scope_not_authorized")
        self.assertEqual(transport.calls, [])

    def test_fixture_normalizes_filing_records_and_uses_explicit_user_agent(self):
        transport = FakeTransport(HTTPResponse(200, {"ETag": '"fixture-v1"'},
                                               json.dumps(self.payload).encode()))
        provider = SECSubmissionsProvider(self.enabled_config(), transport,
                                          clock=self.clock, monotonic=lambda: 1000.0)
        result = provider.read()
        self.assertEqual(result.health.status, "ok")
        self.assertEqual(len(result.records), 2)
        record = result.records[0]
        self.assertEqual(record["schema_version"], "1.0")
        self.assertEqual(record["record_type"], "news")
        self.assertEqual(record["domain"], "finance")
        self.assertEqual(record["provider"], "SEC EDGAR Submissions API")
        self.assertEqual(record["publisher"], "Fixture Issuer Inc.")
        self.assertEqual(record["market"], "US")
        self.assertEqual(record["event_ts"], "2026-10-16T13:14:15Z")
        self.assertEqual(record["news"]["form"], "8-K")
        self.assertEqual(record["filing_date"], "2026-10-16")
        self.assertEqual(record["acceptance_datetime"], "2026-10-16T13:14:15.000Z")
        self.assertEqual(record["primary_doc_description"], "Current report")
        self.assertFalse(record["is_xbrl"])
        self.assertFalse(record["is_inline_xbrl"])
        self.assertEqual(record["received_at"], "2026-10-16T14:00:00Z")
        self.assertEqual(record["news"]["content_type"], "SEC Filing")
        self.assertEqual(record["source_url"], "https://www.sec.gov/Archives/edgar/data/1/000000000126000002/")
        self.assertNotIn("primaryDocument", str(record))
        self.assertNotIn("fixture.htm", record["source_url"])
        self.assertEqual(record["policy"]["authorized_scopes"], ["local_personal"])
        self.assertEqual(record["policy"]["policy_reference"], SEC_FAIR_ACCESS_URL)
        url, headers, _ = transport.calls[0]
        self.assertEqual(url, "https://data.sec.gov/submissions/CIK0000000001.json")
        self.assertEqual(headers["User-Agent"], "Fusion Office/1.0 (contact operator@example.test)")

    def test_latest_record_limit_is_applied_and_does_not_fetch_documents(self):
        transport = FakeTransport(HTTPResponse(200, {}, json.dumps(self.payload).encode()))
        provider = SECSubmissionsProvider(self.enabled_config(max_records=1), transport,
                                          clock=self.clock, monotonic=lambda: 1000.0)
        result = provider.read()
        self.assertEqual(1, len(result.records))
        self.assertEqual("8-K", result.records[0]["news"]["form"])
        self.assertTrue(result.records[0]["source_url"].endswith("/000000000126000002/"))
        self.assertEqual(1, len(transport.calls))

    def test_low_frequency_limit_and_conditional_request(self):
        transport = FakeTransport(
            HTTPResponse(200, {"ETag": '"v1"'}, json.dumps(self.payload).encode()),
            HTTPResponse(304, {"ETag": '"v1"'}, b""),
        )
        ticks = iter([10.0, 10.1, 71.0])
        provider = SECSubmissionsProvider(self.enabled_config(), transport,
                                          clock=self.clock, monotonic=lambda: next(ticks))
        first = provider.read()
        limited = provider.read()
        self.assertEqual(first.health.status, "ok")
        self.assertEqual(limited.health.status, "rate_limited_local")
        self.assertEqual(len(transport.calls), 1)
        self.clock.advance(61)
        not_modified = provider.read()
        self.assertEqual(not_modified.health.status, "not_modified")
        self.assertEqual(len(not_modified.records), 2)
        self.assertEqual(transport.calls[1][1]["If-None-Match"], '"v1"')

    def test_sec_429_retry_after_blocks_followup_request(self):
        transport = FakeTransport(
            HTTPResponse(429, {"Retry-After": "120"}, b""),
            HTTPResponse(200, {}, json.dumps(self.payload).encode()),
        )
        ticks = iter([0.0, 5.0, 121.0])
        provider = SECSubmissionsProvider(self.enabled_config(min_poll_seconds=0), transport,
                                          clock=self.clock, monotonic=lambda: next(ticks))
        limited = provider.read()
        waiting = provider.read()
        recovered = provider.read()
        self.assertEqual(limited.health.error_code, "rate_limited")
        self.assertEqual(limited.health.retry_after_seconds, 120.0)
        self.assertEqual(waiting.health.status, "rate_limited_local")
        self.assertEqual(waiting.health.error_code, "retry_after")
        self.assertEqual(recovered.health.status, "ok")
        self.assertEqual(len(transport.calls), 2)

    def test_outage_returns_explicitly_delayed_cached_records_not_current_data(self):
        transport = FakeTransport(
            HTTPResponse(200, {}, json.dumps(self.payload).encode()),
            HTTPResponse(503, {}, b""),
        )
        ticks = iter([1.0, 62.0])
        provider = SECSubmissionsProvider(self.enabled_config(max_stale_seconds=30), transport,
                                          clock=self.clock, monotonic=lambda: next(ticks))
        first = provider.read()
        self.clock.advance(40)
        failed = provider.read()
        self.assertEqual(first.records[0]["status"], "fresh")
        self.assertEqual(failed.health.status, "failed")
        self.assertTrue(failed.health.is_stale)
        self.assertEqual(failed.records[0]["status"], "stale")
        self.assertEqual(failed.records[0]["received_at"], first.records[0]["received_at"])

    def test_invalid_cik_never_calls_network(self):
        transport = FakeTransport()
        provider = SECSubmissionsProvider(self.enabled_config(cik="not-a-cik"), transport,
                                          clock=self.clock, monotonic=lambda: 1000.0)
        result = provider.read()
        self.assertEqual(result.health.error_code, "invalid_cik")
        self.assertEqual(transport.calls, [])


if __name__ == "__main__":
    unittest.main()
