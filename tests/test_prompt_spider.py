import json
import unittest
from pathlib import Path

from modules.news_governance.normalizer import normalize_news
from modules.news_governance.prompt_spider import to_spider_event


def normalized_record(*, scopes=("local_personal",), content_type="announcement", host="example.invalid", status="stale"):
    accession = content_type == "filing"
    return normalize_news({
        "title": "Synthetic earnings update",
        "provider": "fixture-provider",
        "publisher": "Synthetic issuer",
        "market": "US",
        "source_url": f"https://{ 'www.sec.gov' if accession else host }/Archives/edgar/data/1/0000000001/mock.htm",
        "published_at": "2026-10-16T14:00:00Z",
        "event_ts": "2026-10-16T14:00:00Z",
        "received_at": "2026-10-16T14:01:00Z",
        "status": status,
        "content_type": content_type,
        "related_instruments": [{
            "instrument_id": "XNAS:MOCK",
            "symbol": "MOCK",
            "market": "US",
            "exchange": "XNAS",
            "method": "official-code",
            "confidence": 1.0,
        }],
        "policy": {"authorized_scopes": list(scopes)},
    })


class PromptSpiderProjectionTests(unittest.TestCase):
    def test_demo_fixture_matches_spider_card_contract(self):
        fixture_path = Path(__file__).resolve().parent / "fixtures/spider_finance_event_demo.json"
        event = json.loads(fixture_path.read_text(encoding="utf-8"))
        required = {
            "kind", "headline", "summary", "classification", "route_reason",
            "symbol", "symbols", "provider", "publisher", "market", "event_ts",
            "received_at", "freshness", "source_url", "provider_url", "domain", "data_mode",
        }
        self.assertTrue(required.issubset(event))
        self.assertEqual(event["domain"], "finance")
        self.assertEqual(event["data_mode"], "DEMO")

    def test_finance_news_maps_to_spider_shape_without_inference(self):
        record = normalized_record()
        event = to_spider_event(
            record,
            intended_scope="local_personal",
            data_mode="DEMO",
            classification="financial_update",
            route_reason="explicit fixture rule",
        )
        self.assertEqual(event["kind"], "announcement")
        self.assertEqual(event["headline"], "Synthetic earnings update")
        self.assertIsNone(event["summary"])
        self.assertEqual(event["classification"], "financial_update")
        self.assertEqual(event["route_reason"], "explicit fixture rule")
        self.assertEqual(event["symbol"], "MOCK")
        self.assertEqual(event["symbols"], ["MOCK"])
        self.assertEqual(event["provider"], "fixture-provider")
        self.assertEqual(event["publisher"], "Synthetic issuer")
        self.assertEqual(event["event_ts"], "2026-10-16T14:00:00.000Z")
        self.assertEqual(event["received_at"], "2026-10-16T14:01:00.000Z")
        self.assertEqual(event["freshness"], "stale")
        self.assertEqual(event["domain"], "finance")
        self.assertEqual(event["data_mode"], "DEMO")

    def test_local_personal_record_never_projects_to_public(self):
        record = normalized_record(scopes=("local_personal",))
        self.assertIsNone(to_spider_event(
            record, intended_scope="public", data_mode="DEMO"))

    def test_live_is_explicit_and_freshness_does_not_change_mode(self):
        record = normalized_record(status="stale")
        event = to_spider_event(record, intended_scope="local_personal", data_mode="LIVE")
        self.assertEqual(event["data_mode"], "LIVE")
        self.assertEqual(event["freshness"], "stale")

    def test_sec_filing_stays_separate_from_media_news(self):
        record = normalized_record(content_type="filing")
        event = to_spider_event(record, intended_scope="local_personal", data_mode="DEMO")
        self.assertEqual(event["kind"], "filing")
        self.assertEqual(event["headline"], "Synthetic earnings update")

    def test_non_finance_domain_is_not_projected(self):
        record = normalized_record()
        record["domain"] = "other"
        self.assertIsNone(to_spider_event(record, intended_scope="local_personal", data_mode="DEMO"))

    def test_invalid_mode_or_scope_fails_closed(self):
        record = normalized_record()
        with self.assertRaises(ValueError):
            to_spider_event(record, intended_scope="local_personal", data_mode="AUTOMATIC")
        with self.assertRaises(ValueError):
            to_spider_event(record, intended_scope="unreviewed", data_mode="DEMO")


if __name__ == "__main__":
    unittest.main()
