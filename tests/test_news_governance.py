from __future__ import annotations

import json
from pathlib import Path
import unittest

from modules.news_governance import (
    InMemoryDeduplicator,
    NewsNormalizationError,
    Security,
    StaticSecurityMatcher,
    normalize_news,
    parse_source_time,
    public_news,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / "tests/fixtures/news_governance/sec_submission_mock.json").read_text(encoding="utf-8"))


class NewsNormalizationTests(unittest.TestCase):
    def test_sec_fixture_utc_offsets_publisher_provider_and_original_link(self):
        record = normalize_news(FIXTURE, max_age_seconds=60)
        required = {"schema_version", "record_type", "domain", "id", "provider",
                    "market", "received_at", "status", "policy"}
        self.assertTrue(required.issubset(record))
        self.assertEqual(record["record_type"], "news")
        self.assertEqual(record["domain"], "finance")
        self.assertEqual(record["provider"], "sec-edgar-submissions-mock")
        self.assertEqual(record["publisher"], "U.S. Securities and Exchange Commission")
        self.assertEqual(record["market"], "US")
        self.assertEqual(record["published_at"], "2026-10-16T16:30:00.000Z")
        self.assertEqual(record["event_ts"], "2026-10-16T16:30:00.000Z")
        self.assertEqual(record["received_at"], "2026-10-16T16:30:04.000Z")
        self.assertEqual(record["status"], "fresh")
        self.assertEqual(record["news"]["content_type"], "SEC Filing")
        self.assertEqual(record["news"]["filing"], {
            "form": "10-Q",
            "accession_number": "0000123456-26-000001",
            "filing_url": FIXTURE["source_url"],
        })
        self.assertEqual(record["source_url"], FIXTURE["source_url"])
        self.assertEqual(record["canonical_url"], "https://www.sec.gov/Archives/edgar/data/123456/000012345626000001/mock.htm")
        self.assertEqual(record["news"]["source_time_raw"], "2026-10-16T12:30:00-04:00")
        self.assertNotEqual(record["published_at"], record["received_at"])
        self.assertEqual(record["policy"]["authorized_scopes"], [])

    def test_content_is_excluded_and_raw_is_opt_in_allowlisted(self):
        ordinary = normalize_news(FIXTURE)
        self.assertNotIn("raw", ordinary)
        self.assertNotIn("description", json.dumps(ordinary))
        debug = normalize_news(FIXTURE, include_raw=True)
        self.assertNotIn("description", json.dumps(debug))
        self.assertEqual(debug["raw"]["source_record_id"], FIXTURE["source_record_id"])

    def test_naive_and_ambiguous_source_times_are_not_fabricated(self):
        item = {**FIXTURE, "published_at": "2026-10-16 12:30:00", "source_time_raw": "2026-10-16 12:30:00"}
        record = normalize_news(item, max_age_seconds=60)
        self.assertIsNone(record["published_at"])
        self.assertEqual(record["news"]["source_time_error"], "timezone_missing")
        self.assertEqual(record["news"]["source_time_raw"], "2026-10-16 12:30:00")
        self.assertEqual(record["status"], "unavailable")

        ambiguous, error = parse_source_time("2026-11-01 01:30:00", source_timezone="America/New_York")
        self.assertIsNone(ambiguous)
        self.assertEqual(error, "ambiguous_source_time")
        nonexistent, error = parse_source_time("2026-03-08 02:30:00", source_timezone="America/New_York")
        self.assertIsNone(nonexistent)
        self.assertEqual(error, "nonexistent_source_time")

    def test_documented_timezone_can_be_used_only_when_unambiguous(self):
        value, error = parse_source_time("2026-01-15 10:00:00", source_timezone="America/New_York")
        self.assertIsNone(error)
        self.assertEqual(value, "2026-01-15T15:00:00.000Z")

    def test_freshness_requires_source_threshold_and_handles_stale_future_and_delay(self):
        unknown = normalize_news(FIXTURE)
        stale = normalize_news(FIXTURE, max_age_seconds=1)
        future = normalize_news({**FIXTURE, "published_at": "2026-10-16T16:31:00Z", "source_time_raw": "2026-10-16T16:31:00Z"}, max_age_seconds=60)
        delayed = normalize_news({**FIXTURE, "is_delayed": True}, max_age_seconds=60)
        self.assertEqual(unknown["status"], "unavailable")
        self.assertEqual(stale["status"], "stale")
        self.assertEqual(future["status"], "unavailable")
        self.assertEqual(delayed["status"], "delayed")

    def test_invalid_required_fields_and_currency_fail_closed(self):
        with self.assertRaises(NewsNormalizationError):
            normalize_news({**FIXTURE, "title": ""})
        with self.assertRaises(NewsNormalizationError):
            normalize_news({**FIXTURE, "received_at": "2026-10-16 16:30:04"})
        with self.assertRaises(NewsNormalizationError):
            normalize_news({**FIXTURE, "currency": "US"})
        with self.assertRaises(NewsNormalizationError):
            normalize_news({**FIXTURE, "source_url": "javascript:alert(1)"})

    def test_scopes_default_empty_and_are_never_inferred_from_legacy_booleans(self):
        blocked = normalize_news(FIXTURE)
        self.assertEqual(blocked["policy"]["authorized_scopes"], [])
        self.assertIsNone(public_news(blocked, intended_scope="local_personal"))
        legacy_only = normalize_news(FIXTURE, policy={
            "display_authorized": True, "redistribution_authorized": True,
            "scope": "public", "policy_reference": "legacy-not-authoritative",
        })
        self.assertEqual(legacy_only["policy"]["authorized_scopes"], [])
        self.assertIsNone(public_news(legacy_only, intended_scope="public"))

        local_policy = {"authorized_scopes": ["local_personal"], "policy_reference": "fixture-policy"}
        local = normalize_news(FIXTURE, policy=local_policy, include_raw=True)
        self.assertEqual(local["policy"]["authorized_scopes"], ["local_personal"])
        self.assertNotIn("display_authorized", local["policy"])
        self.assertNotIn("redistribution_authorized", local["policy"])
        self.assertIsNotNone(public_news(local, intended_scope="local_personal"))
        self.assertIsNone(public_news(local, intended_scope="public"))
        self.assertIsNone(public_news(local, intended_scope="redistribution"))
        self.assertNotIn("raw", public_news(local, intended_scope="local_personal"))
        public = normalize_news(FIXTURE, policy={"authorized_scopes": ["public"],
                                                  "policy_reference": "fixture-public"})
        self.assertIsNotNone(public_news(public, intended_scope="public"))
        self.assertIsNone(public_news(public, intended_scope="redistribution"))
        with self.assertRaises(NewsNormalizationError):
            normalize_news(FIXTURE, policy={"authorized_scopes": ["local_personal", "unknown"]})
        with self.assertRaises(ValueError):
            public_news(public, intended_scope="all")


class NewsDeduplicationTests(unittest.TestCase):
    def test_duplicate_source_id_updates_are_kept_as_versions(self):
        first = normalize_news(FIXTURE, max_age_seconds=60)
        revision = normalize_news({**FIXTURE, "title": "Mock issuer files amended quarterly report",
                                   "source_time_raw": "2026-10-16T12:35:00-04:00",
                                   "published_at": "2026-10-16T12:35:00-04:00",
                                   "received_at": "2026-10-16T16:35:01Z"}, max_age_seconds=60)
        store = InMemoryDeduplicator()
        result_a = store.add(first)
        result_b = store.add(revision)
        self.assertEqual(result_a.disposition, "new")
        self.assertEqual(result_b.disposition, "update")
        self.assertTrue(result_b.record["news"]["is_update"])
        self.assertEqual(len(store.records(result_b.cluster_id)), 2)
        self.assertEqual(len(result_b.provenance), 2)
        duplicate = store.add(first)
        self.assertEqual(duplicate.disposition, "duplicate")

    def test_url_and_title_matches_cluster_without_losing_attribution(self):
        first = normalize_news(FIXTURE)
        same_url_other_provider = normalize_news({**FIXTURE,
            "provider": "mock-wire-b", "publisher": "Mock Wire B", "source_record_id": "wire-9"})
        similar_title_other_url = normalize_news({**FIXTURE,
            "provider": "mock-wire-c", "publisher": "Mock Wire C", "source_record_id": "wire-10",
            "source_url": "https://example.invalid/secondary", "canonical_url": "https://example.invalid/secondary"})
        store = InMemoryDeduplicator()
        one = store.add(first)
        two = store.add(same_url_other_provider)
        three = store.add(similar_title_other_url)
        self.assertEqual(two.disposition, "canonical_match")
        self.assertEqual(three.disposition, "near_duplicate")
        members = store.records(one.cluster_id)
        self.assertEqual(len(members), 3)
        self.assertEqual({row["publisher"] for row in members}, {
            "U.S. Securities and Exchange Commission", "Mock Wire B", "Mock Wire C"})
        self.assertEqual(len({row["source_url"] for row in members}), 2)

    def test_url_canonicalization_is_conservative(self):
        record = normalize_news({**FIXTURE, "source_url": "HTTPS://EXAMPLE.invalid:443/story?b=2&utm_source=x&a=1#top"})
        self.assertEqual(record["source_url"], "HTTPS://EXAMPLE.invalid:443/story?b=2&utm_source=x&a=1#top")
        self.assertEqual(record["canonical_url"], "https://example.invalid/story?a=1&b=2")


class SecurityMatchingTests(unittest.TestCase):
    def test_exact_source_identifier_is_certain_and_stable(self):
        record = normalize_news(FIXTURE)
        related = record["related_instruments"]
        self.assertEqual(len(related), 1)
        self.assertEqual(related[0]["instrument_id"], "XNAS:MOCK")
        self.assertEqual(related[0]["method"], "source_explicit_id")
        self.assertEqual(related[0]["confidence"], 1.0)

    def test_market_collision_is_not_silently_resolved(self):
        matcher = StaticSecurityMatcher([
            Security("XNAS:ABC", "ABC", "US", "XNAS"),
            Security("XHKG:ABC", "ABC", "HK", "XHKG"),
        ])
        us = matcher.match(title="ABC announces a mock event", market="US")
        unknown = matcher.match(title="ABC announces a mock event", market="UNKNOWN")
        self.assertEqual(len(us), 1)
        self.assertEqual(us[0]["instrument_id"], "XNAS:ABC")
        self.assertLess(us[0]["confidence"], 1.0)  # title mention is not an official ID
        self.assertEqual(len(unknown), 2)
        self.assertTrue(all(row["confidence"] < 0.5 for row in unknown))

    def test_unbounded_symbol_substrings_do_not_match(self):
        matcher = StaticSecurityMatcher([Security("XNAS:ABC", "ABC", "US", "XNAS")])
        self.assertEqual(matcher.match(title="ABCD company news", market="US"), ())


class ScraplingFixtureParserTests(unittest.TestCase):
    def test_scrapling_selector_parses_local_fixture_only(self):
        """Use Scrapling's HTML parser on a local mock; never fetch a real URL."""
        try:
            from scrapling import Selector
        except ImportError:
            self.skipTest("Scrapling core parser is not installed in this interpreter")
        html = '''<article class="notice">
          <h1>Mock issuer announces a filing</h1>
          <a href="https://example.invalid/releases/fixture-1">Original</a>
          <time datetime="2026-10-16T12:30:00-04:00"></time>
        </article>'''
        document = Selector(html)
        parsed = {
            "provider": "mock-html-adapter",
            "publisher": "Mock Issuer",
            "market": "US",
            "title": document.css("article h1::text").get(),
            "source_url": document.css("article a::attr(href)").get(),
            "published_at": document.css("article time::attr(datetime)").get(),
            "source_time_raw": document.css("article time::attr(datetime)").get(),
            "received_at": "2026-10-16T16:30:04Z",
        }
        record = normalize_news(parsed, max_age_seconds=60)
        self.assertEqual(record["news"]["title"], "Mock issuer announces a filing")
        self.assertEqual(record["published_at"], "2026-10-16T16:30:00.000Z")
        self.assertEqual(record["source_url"], "https://example.invalid/releases/fixture-1")
        self.assertEqual(record["status"], "fresh")


if __name__ == "__main__":
    unittest.main()
