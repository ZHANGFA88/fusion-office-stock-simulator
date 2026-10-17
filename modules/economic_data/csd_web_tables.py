"""Read-only, allowlisted Hong Kong C&SD Web Table API adapter."""
from __future__ import annotations

import datetime as dt
import re
import threading
from typing import Any, Callable
from urllib.parse import urlencode

from .official_api_runtime import DEFAULT_BUDGET, DEFAULT_TRANSPORT, PersistentRequestBudget, SourceError, utc_iso, utc_now

HOST = "www.censtatd.gov.hk"
API_BASE = f"https://{HOST}/api/get.php"
GUIDE_URL = "https://www.censtatd.gov.hk/en/API_Guide.html"
API_LIST_URL = "https://www.censtatd.gov.hk/en/API.html"
TERMS_URL = "https://www.censtatd.gov.hk/en/page_31.html"
ROBOTS_URL = "https://www.censtatd.gov.hk/robots.txt"

_TABLES = {
    "510-60001": {
        "title": "Consumer Price Indices",
        "url": "https://www.censtatd.gov.hk/en/web_table.html?id=510-60001",
        "expected_freq": "M",
    },
    "210-06101": {
        "title": "Statistics on labour force, employment, unemployment and underemployment",
        "url": "https://www.censtatd.gov.hk/en/web_table.html?id=210-06101",
        "expected_freq": "M3M",
    },
}


def _source_labels(row: dict[str, Any]) -> list[str]:
    return [str(value).strip() for key, value in row.items()
            if key.casefold().endswith("desc") and isinstance(value, (str, int, float)) and str(value).strip()]


def _period_rank(value: Any) -> int:
    digits = re.sub(r"\D", "", str(value or ""))
    return int(digits) if digits else -1


def _cpi_series(row: dict[str, Any]) -> tuple[str, str, str] | None:
    labels = _source_labels(row)
    joined = " | ".join(labels).casefold()
    if not ("composite" in joined and ("consumer price index" in joined or "composite cpi" in joined)):
        return None
    if any(re.search(r"year[ -]on[ -]year|year over year|\byoy\b", label, re.I) for label in labels):
        return ("cpi_composite_yoy", "Composite CPI — year-on-year change", "%")
    # Only accept a separate source descriptor for the measure; do not mistake
    # the words "Consumer Price Index" in a series title for the index measure.
    for label in labels:
        candidate = label.casefold().strip()
        if (candidate in {"index", "composite cpi index", "composite consumer price index index"}
                or ("index" in candidate and "consumer price index" not in candidate
                    and "%" not in candidate and "change" not in candidate)):
            return ("cpi_composite_index", "Composite CPI — index", "index (Oct 2019–Sep 2020=100)")
    return None


def _labour_series(row: dict[str, Any]) -> tuple[str, str, str] | None:
    labels = _source_labels(row)
    metric = " ".join(labels).casefold()
    if "unemployment rate (seasonally adjusted)" not in metric:
        return None
    sex = ""
    for key, value in row.items():
        if key.casefold() in {"sexdesc", "sex_desc"} and isinstance(value, str):
            sex = value.strip().casefold()
    if sex not in {"both sexes", "both sex", "all sexes"}:
        return None
    return ("unemployment_rate_seasonally_adjusted", "Unemployment rate (seasonally adjusted), both sexes", "%")


class CSDWebTables:
    def __init__(self, *, transport=DEFAULT_TRANSPORT, budget: PersistentRequestBudget = DEFAULT_BUDGET,
                 clock: Callable[[], dt.datetime] = utc_now):
        self.transport = transport
        self.budget = budget
        self.clock = clock
        self._lock = threading.Lock()
        self._cache: dict[str, dict[str, Any]] = {}

    def _url(self, table_id: str) -> str:
        return API_BASE + "?" + urlencode({"id": table_id, "lang": "en", "full_series": "1"})

    def _fetch_table(self, table_id: str) -> dict[str, Any]:
        cached = self._cache.get(table_id)
        allowed, gate_status = self.budget.reserve(
            f"csd:{table_id}", min_interval_seconds=86400, max_per_utc_day=1)
        if not allowed:
            return {"status": "cached" if cached else gate_status,
                    "rows": cached["rows"] if cached else [],
                    "received_at": cached["received_at"] if cached else None,
                    "source_header": cached.get("source_header") if cached else None}
        try:
            payload, _headers = self.transport.get_json(
                self._url(table_id), allowed_host=HOST, timeout_seconds=20, max_body_bytes=4_500_000)
            header = payload.get("header")
            rows = payload.get("dataSet")
            if not isinstance(header, dict) or not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
                raise SourceError("invalid_table_shape")
            source_text = str(header.get("source") or "")
            if "Census and Statistics Department" not in source_text:
                raise SourceError("source_not_verified")
            selected: list[dict[str, Any]] = []
            for row in rows:
                if str(row.get("freq") or "").upper() != _TABLES[table_id]["expected_freq"]:
                    continue
                if table_id == "510-60001":
                    classification = _cpi_series(row)
                else:
                    classification = _labour_series(row)
                if classification is None or row.get("period") in (None, ""):
                    continue
                series_id, series_label, unit = classification
                selected.append({
                    "series_id": series_id,
                    "series_label": series_label,
                    "unit": unit,
                    "period": row.get("period"),
                    "frequency_code": row.get("freq"),
                    "value": row.get("figure"),
                    "source_marker": row.get("sd_value"),
                    "source_labels": {key: value for key, value in row.items()
                                      if key.casefold().endswith("desc") or key in {"sv", "freq", "period", "figure", "sd_value"}},
                    "source_record": row,
                })
            latest: dict[str, dict[str, Any]] = {}
            for row in selected:
                prior = latest.get(row["series_id"])
                if prior is None or _period_rank(row["period"]) > _period_rank(prior["period"]):
                    latest[row["series_id"]] = row
            received_at = utc_iso(self.clock())
            state = {"status": "fresh" if latest else "no_whitelisted_series_match",
                     "rows": list(latest.values()), "received_at": received_at,
                     "source_header": {key: value for key, value in header.items()
                                       if key in {"source", "title", "table", "frequency", "started", "finished"}}}
            self._cache[table_id] = state
            return state
        except SourceError as exc:
            return {"status": "rate_limited_remote" if exc.http_status == 429 else exc.code,
                    "rows": cached["rows"] if cached else [],
                    "received_at": cached["received_at"] if cached else None,
                    "source_header": cached.get("source_header") if cached else None}
        except Exception:
            return {"status": "error", "rows": cached["rows"] if cached else [],
                    "received_at": cached["received_at"] if cached else None,
                    "source_header": cached.get("source_header") if cached else None}

    def payload(self) -> dict[str, Any]:
        with self._lock:
            checked_at = utc_iso(self.clock())
            tables = {table_id: self._fetch_table(table_id) for table_id in _TABLES}
        items: list[dict[str, Any]] = []
        statuses: dict[str, str] = {}
        for table_id, spec in _TABLES.items():
            state = tables[table_id]
            statuses[table_id] = state["status"]
            for row in state["rows"]:
                marker = row.get("source_marker")
                if isinstance(marker, str) and marker.strip().casefold() == "p":
                    publication_state = "provisional (source marker p)"
                elif marker not in (None, "", "null"):
                    publication_state = "source marker preserved; interpretation not mapped"
                else:
                    publication_state = "provisional/revised status not separately marked by this API row"
                items.append({
                    "publisher": "Census and Statistics Department, Hong Kong SAR",
                    "table_id": table_id,
                    "table_title": spec["title"],
                    "source_url": spec["url"],
                    "api_url": self._url(table_id),
                    "api_guide_url": GUIDE_URL,
                    "event_time": row["period"],
                    "event_time_kind": "source observation period/code",
                    "frequency_code": row["frequency_code"],
                    "frequency": "monthly" if row["frequency_code"] == "M" else "moving 3-month period",
                    "indicator_id": row["series_id"],
                    "indicator_label": row["series_label"],
                    "unit": row["unit"],
                    "value": row["value"],
                    "source_marker": marker,
                    "publication_state": publication_state,
                    "release_timestamp": None,
                    "release_timestamp_status": "not supplied by Web Table API; see official table page",
                    "received_at": state["received_at"],
                    "status": state["status"],
                    "source_labels": row["source_labels"],
                    "source_record": row["source_record"],
                    "covered_symbols": [],
                })
        available = bool(items)
        return {
            "ok": True,
            "schema_version": "1.0",
            "publisher": "Census and Statistics Department, Hong Kong SAR",
            "data_mode": "LIVE" if available else "UNAVAILABLE",
            "demo": False,
            "status": "fresh" if any(value == "fresh" for value in statuses.values()) else ("cached" if available else "unavailable"),
            "checked_at": checked_at,
            "table_status": statuses,
            "items": items,
            "coverage_complete": False,
            "covered_symbols": [],
            "source_links": {"api_guide": GUIDE_URL, "api_list": API_LIST_URL,
                             "terms": TERMS_URL, "robots": ROBOTS_URL},
            "limits": {"on_demand_only": True, "minimum_interval_seconds_per_table": 86400,
                       "maximum_requests_per_table_per_utc_day": 1, "automatic_retry": False,
                       "background_refresh": False, "max_response_bytes": 4500000},
            "notice": "Only whitelisted official C&SD series are returned. Values, observation periods and source markers are preserved; no timestamps, revisions or delays are inferred when the API does not provide them.",
        }


DEFAULT_CSD = CSDWebTables()


def csd_web_tables_payload() -> dict[str, Any]:
    return DEFAULT_CSD.payload()
