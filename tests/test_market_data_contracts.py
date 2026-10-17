import json
import unittest
from pathlib import Path

from scripts.market_data import (
    FeedHealth,
    ProviderPolicy,
    ProviderRegistry,
    Freshness,
    MarketState,
    local_quote,
    normalize_quote,
    public_quote,
    validate_quote_record,
)
from scripts.market_data.contracts import classify_freshness, parse_timestamp


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / "tests/fixtures/quote.json").read_text(encoding="utf-8"))


class QuoteNormalizerTests(unittest.TestCase):
    def test_fixture_keeps_event_source_and_received_times_distinct(self):
        row = normalize_quote(FIXTURE, max_age_seconds=5)
        self.assertEqual(row["event_ts"], "2026-10-16T07:00:00.000Z")
        self.assertEqual(row["received_at"], "2026-10-16T07:00:01.000Z")
        self.assertEqual(row["quote"]["source_time"], "2026-10-16T15:00:00+08:00")
        self.assertEqual(row["status"], "fresh")
        self.assertEqual(row["market"], "CN")
        self.assertEqual(row["currency"], "CNY")
        self.assertEqual(row["data_mode"], "DEMO")
        self.assertEqual(row["feed"], "fixture-snapshot")
        self.assertEqual(row["source_id"], "quote-1001")
        self.assertEqual(validate_quote_record(row), [])

    def test_missing_or_naive_event_timestamp_is_not_fabricated(self):
        row = normalize_quote({**FIXTURE, "event_ts": "2026-10-16 15:00:00",
                               "source_time": "2026-10-16 15:00:00"},
                              max_age_seconds=5)
        self.assertIsNone(row["event_ts"])
        self.assertEqual(row["quote"]["source_time"], "2026-10-16 15:00:00")
        self.assertEqual(row["status"], "unavailable")

    def test_missing_data_mode_is_not_inferred_as_live(self):
        row = normalize_quote({key: value for key, value in FIXTURE.items()
                               if key != "data_mode"}, max_age_seconds=5)
        self.assertIsNone(row["data_mode"])
        self.assertIn("invalid:data_mode", validate_quote_record(row))

    def test_freshness_threshold_is_explicit_and_future_timestamp_is_unknown(self):
        self.assertEqual(classify_freshness("2026-10-16T07:00:00Z", "2026-10-16T07:00:03Z",
                                            max_age_seconds=3), Freshness.FRESH)
        self.assertEqual(classify_freshness("2026-10-16T07:00:00Z", "2026-10-16T07:00:04Z",
                                            max_age_seconds=3), Freshness.STALE)
        self.assertEqual(classify_freshness("2026-10-16T07:00:02Z", "2026-10-16T07:00:01Z",
                                            max_age_seconds=3), Freshness.UNKNOWN)
        self.assertIsNone(parse_timestamp("2026-10-16 15:00:00"))

    def test_closed_and_delayed_are_explicit_statuses(self):
        closed = normalize_quote(FIXTURE, max_age_seconds=0,
                                 market_state=MarketState.CLOSED)
        delayed = normalize_quote(FIXTURE, max_age_seconds=0, delayed=True)
        self.assertEqual(closed["status"], "closed")
        self.assertEqual(delayed["status"], "delayed")
        self.assertTrue(delayed["quote"]["is_delayed"])

    def test_provider_error_is_explicit_and_keeps_retry_guidance(self):
        row = normalize_quote({**FIXTURE, "error": {
            "code": "rate_limited", "message": "fixture limit", "retryable": True,
            "retry_after_seconds": 12,
        }}, max_age_seconds=5)
        self.assertEqual(row["status"], "error")
        self.assertEqual(row["error"]["code"], "rate_limited")
        self.assertTrue(row["error"]["retryable"])
        self.assertEqual(row["error"]["retry_after_seconds"], 12.0)

    def test_policy_is_not_inferred_and_public_projection_drops_raw(self):
        defaulted = normalize_quote(FIXTURE, max_age_seconds=5)
        self.assertEqual(defaulted["policy"]["authorized_scopes"], [])
        self.assertIsNone(public_quote(defaulted))

        allowed = normalize_quote(FIXTURE, max_age_seconds=5, policy={
            "authorized_scopes": ["local_personal"],
            "policy_reference": "reviewed-local-use",
        })
        shown = local_quote(allowed)
        self.assertIsNotNone(shown)
        self.assertNotIn("raw", shown)
        self.assertEqual(allowed["policy"]["authorized_scopes"], ["local_personal"])
        self.assertIsNone(public_quote(allowed))

        public_allowed = normalize_quote(FIXTURE, max_age_seconds=5, policy={
            "authorized_scopes": ["public"],
        })
        self.assertIsNotNone(public_quote(public_allowed))
        self.assertIsNone(local_quote(public_allowed))
        with self.assertRaises(ValueError):
            normalize_quote(FIXTURE, max_age_seconds=5,
                            policy={"authorized_scopes": ["local-personal"]})

    def test_invalid_currency_and_source_url_are_rejected(self):
        with self.assertRaises(ValueError):
            normalize_quote({**FIXTURE, "currency": "CN"}, max_age_seconds=5)
        with self.assertRaises(ValueError):
            normalize_quote({**FIXTURE, "source_url": "javascript:alert(1)"}, max_age_seconds=5)


class ProviderRegistryTests(unittest.TestCase):
    class Stub:
        def __init__(self, provider_id, priority, policy):
            self.provider_id = provider_id
            self.priority = priority
            self.policy = policy

        def fetch(self, symbols):
            raise AssertionError("tests use provider metadata only; no network")

    def test_eligibility_requires_market_enabled_provider_and_exact_scope(self):
        registry = ProviderRegistry()
        allowed = self.Stub("personal-local", 5, ProviderPolicy(
            permitted_markets=frozenset({"CN"}),
            authorized_scopes=frozenset({"local_personal"}),
            provider_enabled=True,
            policy_reference="local-terms-reviewed",
        ))
        blocked = self.Stub("unreviewed", 1, ProviderPolicy(
            permitted_markets=frozenset({"CN"}),
            authorized_scopes=frozenset({"local_personal", "public"}),
            provider_enabled=False,
        ))
        registry.register(allowed)
        registry.register(blocked)
        self.assertEqual([p.provider_id for p in registry.eligible(
            market="CN", scope="local_personal")], ["personal-local"])
        self.assertEqual(registry.eligible(market="US", scope="local_personal"), ())

    def test_public_fallback_requires_redistribution_permission(self):
        registry = ProviderRegistry()
        registry.register(self.Stub("personal-only", 0, ProviderPolicy(
            permitted_markets=frozenset({"US"}),
            authorized_scopes=frozenset({"local_personal"}),
            provider_enabled=True,
        )))
        self.assertEqual(registry.eligible(market="US", scope="public"), ())
        self.assertEqual(len(registry.eligible(market="US", scope="local_personal")), 1)


class FeedHealthTests(unittest.TestCase):
    def test_heartbeat_not_quote_activity_controls_feed_health(self):
        health = FeedHealth("fixture", "CN", heartbeat_timeout_seconds=10)
        health.observe_heartbeat("2026-10-16T07:00:00Z")
        self.assertEqual(health.status("2026-10-16T07:00:09Z"), "fresh")
        self.assertEqual(health.status("2026-10-16T07:00:11Z"), "stale")
        self.assertEqual(health.status("2026-10-16T07:00:11Z", market_open=False), "closed")

    def test_sequence_gaps_and_disconnect_recovery_are_observable(self):
        health = FeedHealth("fixture", "CN", heartbeat_timeout_seconds=10)
        health.observe_event("2026-10-16T07:00:00Z", "2026-10-16T07:00:01Z", 100)
        health.observe_event("2026-10-16T07:00:02Z", "2026-10-16T07:00:03Z", 103)
        health.record_error("socket_closed", disconnected=True)
        health.observe_heartbeat("2026-10-16T07:00:05Z")
        metrics = health.metrics("2026-10-16T07:00:06Z")
        self.assertEqual(metrics["sequence_gap_count"], 2)
        self.assertEqual(metrics["disconnect_count"], 1)
        self.assertEqual(metrics["recovery_count"], 1)
        self.assertEqual(metrics["status"], "fresh")

    def test_opaque_sequence_does_not_create_false_gap(self):
        health = FeedHealth("fixture", "CN", heartbeat_timeout_seconds=10)
        health.observe_event(None, "2026-10-16T07:00:01Z", "seq-A")
        health.observe_event(None, "2026-10-16T07:00:02Z", "seq-C")
        self.assertEqual(health.sequence_gap_count, 0)


if __name__ == "__main__":
    unittest.main()
