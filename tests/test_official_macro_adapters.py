import datetime as dt
import tempfile
import unittest
from pathlib import Path

from modules.economic_data.csd_web_tables import CSDWebTables
from modules.economic_data.hkma_open_api import HKMAOpenAPI
from modules.economic_data.official_api_runtime import FixedHTTPSJSONTransport, PersistentRequestBudget, SourceError
from modules.economic_data.treasury_fiscal_data import TreasuryFiscalData


class FakeClock:
    def __init__(self):
        self.value = dt.datetime(2026, 10, 17, 7, 0, tzinfo=dt.timezone.utc)

    def __call__(self):
        return self.value


class FakeTransport:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get_json(self, url, *, allowed_host, timeout_seconds, max_body_bytes):
        self.calls.append((url, allowed_host, timeout_seconds, max_body_bytes))
        for key, payload in self.responses.items():
            if key in url:
                return payload, {"content-type": "application/json"}
        raise AssertionError("Unexpected mock endpoint: " + url)


class OfficialMacroAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.clock = FakeClock()
        self.budget_path = str(Path(self.temp.name) / "request-budget.json")
        self.budget = PersistentRequestBudget(self.budget_path, clock=self.clock)

    def tearDown(self):
        self.temp.cleanup()

    def test_budget_persists_interval_and_utc_daily_cap(self):
        first = PersistentRequestBudget(self.budget_path, clock=self.clock)
        self.assertEqual((True, "reserved"), first.reserve("provider:one", min_interval_seconds=60, max_per_utc_day=1))
        restarted = PersistentRequestBudget(self.budget_path, clock=self.clock)
        self.assertEqual((False, "local_interval"), restarted.reserve("provider:one", min_interval_seconds=60, max_per_utc_day=1))
        self.clock.value += dt.timedelta(seconds=61)
        self.assertEqual((False, "local_daily_cap"), restarted.reserve("provider:one", min_interval_seconds=60, max_per_utc_day=1))

    def test_transport_rejects_non_allowlisted_scheme_or_host_without_network(self):
        transport = FixedHTTPSJSONTransport()
        with self.assertRaises(SourceError) as bad_scheme:
            transport.get_json("http://api.hkma.gov.hk/data", allowed_host="api.hkma.gov.hk")
        self.assertEqual("endpoint_not_allowlisted", bad_scheme.exception.code)
        with self.assertRaises(SourceError) as bad_host:
            transport.get_json("https://example.invalid/data", allowed_host="api.hkma.gov.hk")
        self.assertEqual("endpoint_not_allowlisted", bad_host.exception.code)

    def test_hkma_selects_only_documented_fields_and_press_metadata(self):
        transport = FakeTransport({
            "/daily-figures-interbank-liquidity": {"result": {"success": True, "records": [{
                "end_of_date": "2026-10-16", "opening_balance": "MOCK_A", "closing_balance": "MOCK_B",
                "hibor_overnight": "MOCK_C", "unrequested_extra": "DROP_ME"}]}},
            "/daily-figures-monetary-base": {"result": {"success": True, "records": [{
                "end_of_date": "2026-10-16", "aggr_balance_af_disc_win": "MOCK_D",
                "mb_bf_disc_win_total": "MOCK_E", "unrequested_extra": "DROP_ME"}]}},
            "/press-releases": {"result": {"success": True, "records": [{
                "date": "2026-10-16", "title": "Mock release", "link": "https://www.hkma.gov.hk/eng/mock",
                "summary": "DROP_ME"}]}}
        })
        api = HKMAOpenAPI(transport=transport, budget=self.budget, clock=self.clock)
        payload = api.payload()
        self.assertEqual("LIVE", payload["data_mode"])
        self.assertEqual(2, len(payload["statistics"]))
        self.assertEqual({"end_of_date", "opening_balance", "closing_balance", "hibor_overnight"},
                         set(payload["statistics"][0]["source_record"]))
        self.assertNotIn("unrequested_extra", str(payload))
        self.assertNotIn("DROP_ME", str(payload))
        self.assertEqual("Mock release", payload["announcements"][0]["title"])
        self.assertIsNone(payload["announcements"][0]["summary"])
        self.assertEqual(3, len(transport.calls))
        api.payload()
        self.assertEqual(3, len(transport.calls), "a second page load must use the local cooldown")

    def test_csd_selects_only_fixed_series_and_preserves_source_marker(self):
        payload = {"header": {"source": "Census and Statistics Department", "title": "Mock table"},
                   "dataSet": [
                       {"freq": "M", "period": "202609", "figure": "MOCK_INDEX", "sd_value": "p",
                        "cpi_typeDesc": "Composite Consumer Price Index", "measureDesc": "Index"},
                       {"freq": "M", "period": "202609", "figure": "MOCK_YOY", "sd_value": "",
                        "cpi_typeDesc": "Composite Consumer Price Index", "measureDesc": "Year-on-year rate"},
                       {"freq": "A", "period": "2025", "figure": "DROP_ME", "sd_value": "",
                        "cpi_typeDesc": "Composite Consumer Price Index", "measureDesc": "Index"},
                       {"freq": "M3M", "period": "202608", "figure": "MOCK_LABOUR", "sd_value": "p",
                        "indicatorDesc": "Unemployment rate (seasonally adjusted)", "sexDesc": "Both sexes"},
                       {"freq": "M3M", "period": "202608", "figure": "DROP_ME", "sd_value": "",
                        "indicatorDesc": "Unemployment rate (seasonally adjusted)", "sexDesc": "Female"},
                   ]}
        transport = FakeTransport({"510-60001": payload, "210-06101": payload})
        api = CSDWebTables(transport=transport, budget=self.budget, clock=self.clock)
        result = api.payload()
        self.assertEqual("LIVE", result["data_mode"])
        self.assertEqual(3, len(result["items"]))
        self.assertEqual({"cpi_composite_index", "cpi_composite_yoy", "unemployment_rate_seasonally_adjusted"},
                         {x["indicator_id"] for x in result["items"]})
        labour = next(x for x in result["items"] if x["indicator_id"].startswith("unemployment"))
        self.assertEqual("p", labour["source_marker"])
        self.assertIn("provisional", labour["publication_state"])
        self.assertNotIn("DROP_ME", str(result))
        self.assertEqual(2, len(transport.calls))

    def test_treasury_uses_fixed_debt_and_tga_rows_and_keeps_mts_and_yield_curve_link_only(self):
        transport = FakeTransport({
            "debt_to_penny": {"data": [{"record_date": "2026-10-15", "debt_held_public_amt": "MOCK_1",
                                          "intragov_hold_amt": "MOCK_2", "tot_pub_debt_out_amt": "MOCK_3",
                                          "extra": "DROP_ME"}]},
            "operating_cash_balance": {"data": [{"record_date": "2026-10-15",
                                                   "account_type": "Treasury General Account (TGA) Closing Balance",
                                                   "close_today_bal": "null", "open_today_bal": "MOCK_4",
                                                   "open_month_bal": "MOCK_5", "extra": "DROP_ME"}]}
        })
        api = TreasuryFiscalData(transport=transport, budget=self.budget, clock=self.clock)
        result = api.payload()
        self.assertEqual("LIVE", result["data_mode"])
        self.assertEqual({"debt_to_penny", "dts_operating_cash_balance"}, {x["dataset_id"] for x in result["datasets"]})
        self.assertEqual(2, len(transport.calls))
        self.assertTrue(all("page%5Bsize%5D=1" in url for url, *_ in transport.calls))
        self.assertTrue(all("mts_table_1" not in url for url, *_ in transport.calls))
        curve = next(x for x in result["not_integrated"] if x["dataset_id"] == "daily_treasury_par_yield_curve_xml")
        self.assertEqual("official_link_only", curve["status"])
        self.assertIn("did not provide a dataset-specific reuse licence", curve["reason"])
        self.assertTrue(all("home.treasury.gov" not in url for url, *_ in transport.calls))
        dts = next(x for x in result["datasets"] if x["dataset_id"] == "dts_operating_cash_balance")
        self.assertEqual("null", dts["source_record"]["close_today_bal"])
        self.assertIn("official_link_only", result["not_integrated"][0]["status"])
        self.assertNotIn("DROP_ME", str(result))


if __name__ == "__main__":
    unittest.main()
