"""Narrow read-only U.S. Treasury Fiscal Data API adapter."""
from __future__ import annotations

import datetime as dt
import threading
from typing import Any, Callable
from urllib.parse import urlencode

from .official_api_runtime import DEFAULT_BUDGET, DEFAULT_TRANSPORT, PersistentRequestBudget, SourceError, utc_iso, utc_now

HOST = "api.fiscaldata.treasury.gov"
BASE = f"https://{HOST}/services/api/fiscal_service"
API_DOCS_URL = "https://fiscaldata.treasury.gov/api-documentation/"
ABOUT_URL = "https://fiscaldata.treasury.gov/about-us/"
ROBOTS_URL = "https://api.fiscaldata.treasury.gov/robots.txt"

DEBT_URL = "https://fiscaldata.treasury.gov/datasets/debt-to-the-penny/"
DTS_URL = "https://fiscaldata.treasury.gov/datasets/daily-treasury-statement/"
MTS_URL = "https://fiscaldata.treasury.gov/datasets/monthly-treasury-statement/"
YIELD_CURVE_URL = "https://home.treasury.gov/policy-issues/financing-the-government/interest-rate-statistics"

_DEBT_FIELDS = ("record_date", "debt_held_public_amt", "intragov_hold_amt", "tot_pub_debt_out_amt")
_DTS_FIELDS = ("record_date", "account_type", "close_today_bal", "open_today_bal", "open_month_bal")
_TGA_CLOSING_ROW = "Treasury General Account (TGA) Closing Balance"


def _records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("data")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise SourceError("invalid_record_shape")
    return rows


class TreasuryFiscalData:
    def __init__(self, *, transport=DEFAULT_TRANSPORT, budget: PersistentRequestBudget = DEFAULT_BUDGET,
                 clock: Callable[[], dt.datetime] = utc_now):
        self.transport = transport
        self.budget = budget
        self.clock = clock
        self._lock = threading.Lock()
        self._cache: dict[str, dict[str, Any]] = {}

    def _debt_api_url(self) -> str:
        params = {
            "fields": ",".join(_DEBT_FIELDS),
            "sort": "-record_date",
            "page[number]": "1",
            "page[size]": "1",
        }
        return f"{BASE}/v2/accounting/od/debt_to_penny?{urlencode(params)}"

    def _dts_api_url(self) -> str:
        params = {
            "fields": ",".join(_DTS_FIELDS),
            "filter": f"account_type:eq:{_TGA_CLOSING_ROW}",
            "sort": "-record_date",
            "page[number]": "1",
            "page[size]": "1",
        }
        return f"{BASE}/v1/accounting/dts/operating_cash_balance?{urlencode(params)}"

    def _fetch(self, key: str) -> dict[str, Any]:
        cached = self._cache.get(key)
        interval = 21600 if key == "debt_to_penny" else 86400
        cap = 4 if key == "debt_to_penny" else 1
        allowed, gate_status = self.budget.reserve(
            f"treasury:{key}", min_interval_seconds=interval, max_per_utc_day=cap)
        if not allowed:
            return {"status": "cached" if cached else gate_status,
                    "row": cached["row"] if cached else None,
                    "received_at": cached["received_at"] if cached else None}
        url = self._debt_api_url() if key == "debt_to_penny" else self._dts_api_url()
        try:
            payload, _headers = self.transport.get_json(
                url, allowed_host=HOST, timeout_seconds=12, max_body_bytes=500_000)
            rows = _records(payload)
            if key == "dts_operating_cash_balance":
                rows = [row for row in rows if row.get("account_type") == _TGA_CLOSING_ROW]
            if not rows:
                state = {"status": "empty", "row": None, "received_at": utc_iso(self.clock())}
            else:
                expected = _DEBT_FIELDS if key == "debt_to_penny" else _DTS_FIELDS
                row = {field: rows[0].get(field) for field in expected}
                if not isinstance(row.get("record_date"), str) or not row["record_date"].strip():
                    raise SourceError("missing_record_date")
                state = {"status": "fresh", "row": row, "received_at": utc_iso(self.clock())}
            self._cache[key] = state
            return state
        except SourceError as exc:
            return {"status": "rate_limited_remote" if exc.http_status == 429 else exc.code,
                    "row": cached["row"] if cached else None,
                    "received_at": cached["received_at"] if cached else None}
        except Exception:
            return {"status": "error", "row": cached["row"] if cached else None,
                    "received_at": cached["received_at"] if cached else None}

    def payload(self) -> dict[str, Any]:
        with self._lock:
            checked_at = utc_iso(self.clock())
            debt = self._fetch("debt_to_penny")
            dts = self._fetch("dts_operating_cash_balance")
        datasets: list[dict[str, Any]] = []
        if debt["row"] is not None:
            datasets.append({
                "dataset_id": "debt_to_penny",
                "publisher": "U.S. Department of the Treasury, Bureau of the Fiscal Service",
                "dataset_title": "Debt to the Penny",
                "source_url": DEBT_URL,
                "api_url": self._debt_api_url(),
                "api_documentation_url": API_DOCS_URL,
                "event_time": debt["row"]["record_date"],
                "event_time_kind": "record_date as supplied by the Treasury dataset",
                "frequency": "daily; dataset updates with prior business-day data",
                "units_by_field": {
                    "debt_held_public_amt": "USD (source currency amount; unchanged)",
                    "intragov_hold_amt": "USD (source currency amount; unchanged)",
                    "tot_pub_debt_out_amt": "USD (source currency amount; unchanged)",
                },
                "source_record": debt["row"],
                "received_at": debt["received_at"],
                "status": debt["status"],
                "revision_status": "not separately supplied by this endpoint",
                "covered_symbols": [],
            })
        if dts["row"] is not None:
            datasets.append({
                "dataset_id": "dts_operating_cash_balance",
                "publisher": "U.S. Department of the Treasury, Bureau of the Fiscal Service",
                "dataset_title": "Daily Treasury Statement — Operating Cash Balance",
                "source_url": DTS_URL,
                "api_url": self._dts_api_url(),
                "api_documentation_url": API_DOCS_URL,
                "event_time": dts["row"]["record_date"],
                "event_time_kind": "record_date is the publication date per the field dictionary",
                "frequency": "daily",
                "units_by_field": {
                    "close_today_bal": "USD million (figures rounded to nearest million)",
                    "open_today_bal": "USD million (figures rounded to nearest million)",
                    "open_month_bal": "USD million (figures rounded to nearest million)",
                },
                "row_filter": {"account_type": _TGA_CLOSING_ROW},
                "source_record": dts["row"],
                "received_at": dts["received_at"],
                "status": dts["status"],
                "revision_status": "not separately supplied by this endpoint",
                "source_note": "Treasury's dataset notes that close_today_bal is null from 2022-04-18 onward and that the TGA closing-balance row is represented in the opening-balance columns; raw fields are preserved without substituting or calculating values.",
                "covered_symbols": [],
            })
        statuses = {"debt_to_penny": debt["status"], "dts_operating_cash_balance": dts["status"]}
        available = bool(datasets)
        return {
            "ok": True,
            "schema_version": "1.0",
            "publisher": "U.S. Department of the Treasury, Bureau of the Fiscal Service",
            "data_mode": "LIVE" if available else "UNAVAILABLE",
            "demo": False,
            "status": "fresh" if any(value == "fresh" for value in statuses.values()) else ("cached" if available else "unavailable"),
            "checked_at": checked_at,
            "dataset_status": statuses,
            "datasets": datasets,
            "not_integrated": [{
                "dataset_id": "monthly_treasury_statement",
                "publisher": "U.S. Department of the Treasury, Bureau of the Fiscal Service",
                "source_url": MTS_URL,
                "api_documentation_url": API_DOCS_URL,
                "status": "official_link_only",
                "reason": "The official MTS Table 1 contains a changing parent/child classification hierarchy and subtotal rows; no single series/classification mapping is selected, so it is not queried or aggregated.",
            }, {
                "dataset_id": "daily_treasury_par_yield_curve_xml",
                "publisher": "U.S. Department of the Treasury",
                "source_url": YIELD_CURVE_URL,
                "status": "official_link_only",
                "reason": "The public GET/XML documentation and robots rules were reviewed, but the reviewed Treasury policy pages did not provide a dataset-specific reuse licence. The product adapter therefore does not query, cache, or display the feed; it remains link-only until reuse terms are explicit.",
            }],
            "coverage_complete": False,
            "covered_symbols": [],
            "source_links": {"api_documentation": API_DOCS_URL, "reuse_terms": ABOUT_URL,
                             "robots": ROBOTS_URL, "debt_to_penny": DEBT_URL,
                             "daily_treasury_statement": DTS_URL, "monthly_treasury_statement": MTS_URL,
                             "daily_par_yield_curve": YIELD_CURVE_URL},
            "limits": {"on_demand_only": True, "debt_to_penny_min_interval_seconds": 21600,
                       "debt_to_penny_max_requests_per_utc_day": 4,
                       "dts_min_interval_seconds": 86400, "dts_max_requests_per_utc_day": 1,
                       "page_size": 1, "automatic_retry": False, "background_refresh": False},
            "notice": "All values and source field names are preserved. The API does not provide a separate revision timestamp for these selected rows; no currency conversion, aggregation, or security mapping is performed.",
        }


DEFAULT_TREASURY = TreasuryFiscalData()


def treasury_fiscal_data_payload() -> dict[str, Any]:
    return DEFAULT_TREASURY.payload()
