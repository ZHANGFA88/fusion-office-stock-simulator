import unittest
from pathlib import Path

from modules.finance_observatory import get_observatory_payload, validate_response


class FinanceObservatoryContractTests(unittest.TestCase):
    def test_fixture_is_valid_finance_only_demo_with_separated_collections(self):
        payload = get_observatory_payload()
        self.assertEqual([], validate_response(payload))
        self.assertEqual("finance", payload["domain"])
        self.assertEqual("DEMO", payload["data_mode"])
        self.assertEqual({"quote"}, {item["kind"] for item in payload["quotes"]})
        self.assertEqual({"filing"}, {item["kind"] for item in payload["filings"]})
        self.assertEqual({"news"}, {item["kind"] for item in payload["news"]})
        self.assertEqual({"insight"}, {item["kind"] for item in payload["insights"]})
        self.assertTrue(all(item["data_mode"] == "DEMO" for item in payload["items"]))

    def test_filing_is_not_collapsed_into_media_news(self):
        payload = get_observatory_payload()
        filing = payload["filings"][0]
        self.assertEqual("filing", filing["kind"])
        self.assertTrue(filing["form"])
        self.assertTrue(filing["accession"])
        self.assertNotIn(filing, payload["news"])

    def test_live_mode_is_rejected_by_demo_service_gate(self):
        payload = get_observatory_payload()
        payload["data_mode"] = "LIVE"
        with self.assertRaisesRegex(ValueError, "DEMO"):
            from modules.finance_observatory.contract import demo_response
            demo_response(payload)

    def test_invalid_domain_or_item_mode_fails_closed(self):
        payload = get_observatory_payload()
        payload["domain"] = "other"
        self.assertTrue(any(problem == "invalid:domain" for problem in validate_response(payload)))
        payload = get_observatory_payload()
        payload["items"][0]["data_mode"] = "LIVE"
        self.assertIn("items[0].mode_mismatch", validate_response(payload))

    def test_response_is_independent_copy(self):
        payload = get_observatory_payload()
        payload["items"][0]["title"] = "mutated"
        self.assertNotEqual("mutated", get_observatory_payload()["items"][0]["title"])

    def test_settings_page_is_read_only_and_has_no_secret_controls(self):
        page = (Path(__file__).resolve().parents[1] / "app" / "settings.html").read_text(encoding="utf-8").lower()
        self.assertNotIn("<form", page)
        self.assertNotIn("type=\"password\"", page)
        self.assertNotIn("fetch(", page)
        self.assertNotIn("/api/", page)
        self.assertIn("does not implement password protection", page)


if __name__ == "__main__":
    unittest.main()
