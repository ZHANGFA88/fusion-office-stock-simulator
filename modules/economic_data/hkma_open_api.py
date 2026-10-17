"""Narrow read-only adapter for HKMA Open API market statistics and releases."""
from __future__ import annotations

import datetime as dt
import threading
from typing import Any, Callable
from urllib.parse import urlencode, urlsplit

from .official_api_runtime import DEFAULT_BUDGET, DEFAULT_TRANSPORT, PersistentRequestBudget, SourceError, utc_iso, utc_now

HOST = "api.hkma.gov.hk"
BASE = f"https://{HOST}/public"
DOCS_URL = "https://apidocs.hkma.gov.hk/documentation/"
OVERVIEW_URL = "https://apidocs.hkma.gov.hk/abouthkmasapi/"
TERMS_URL = "https://www.hkma.gov.hk/eng/other-information/terms-and-conditions-of-use/"
ROBOTS_URL = "https://apidocs.hkma.gov.hk/robots.txt"

_CHANNELS = {
    "interbank_liquidity": {
        "title": "Daily Figures: Interbank Liquidity",
        "path": "/market-data-and-statistics/daily-monetary-statistics/daily-figures-interbank-liquidity",
        "fields": ("end_of_date", "opening_balance", "closing_balance", "hibor_overnight"),
        "units": {"opening_balance": "HKD million", "closing_balance": "HKD million", "hibor_overnight": "% per annum"},
        "frequency": "daily",
        "event_field": "end_of_date",
    },
    "monetary_base": {
        "title": "Daily Figures: Monetary Base",
        "path": "/market-data-and-statistics/daily-monetary-statistics/daily-figures-monetary-base",
        "fields": ("end_of_date", "aggr_balance_af_disc_win", "mb_bf_disc_win_total"),
        "units": {"aggr_balance_af_disc_win": "HKD million", "mb_bf_disc_win_total": "HKD million"},
        "frequency": "daily",
        "event_field": "end_of_date",
    },
    "press_releases": {
        "title": "HKMA press-release metadata",
        "path": "/press-releases",
        "fields": ("date", "title", "link"),
        "units": {},
        "frequency": "as released",
        "event_field": "date",
    },
}


def _records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    result = payload.get("result", payload)
    if not isinstance(result, dict) or result.get("success") is False:
        raise SourceError("source_reported_error")
    rows = result.get("records")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise SourceError("invalid_record_shape")
    return rows


class HKMAOpenAPI:
    def __init__(self, *, transport=DEFAULT_TRANSPORT, budget: PersistentRequestBudget = DEFAULT_BUDGET,
                 clock: Callable[[], dt.datetime] = utc_now):
        self.transport = transport
        self.budget = budget
        self.clock = clock
        self._lock = threading.Lock()
        self._cache: dict[str, dict[str, Any]] = {}

    def _url(self, key: str) -> str:
        spec = _CHANNELS[key]
        params: dict[str, str] = {
            "lang": "en",
            "fields": ",".join(spec["fields"]),
            "pagesize": "10" if key == "press_releases" else "1",
            "offset": "0",
            "sortby": spec["event_field"],
            "sortorder": "desc",
        }
        return BASE + spec["path"] + "?" + urlencode(params)

    def _fetch(self, key: str) -> dict[str, Any]:
        spec = _CHANNELS[key]
        cached = self._cache.get(key)
        allowed, gate_status = self.budget.reserve(
            f"hkma:{key}", min_interval_seconds=7200, max_per_utc_day=12)
        if not allowed:
            return {"status": "cached" if cached else gate_status,
                    "rows": cached["rows"] if cached else [],
                    "received_at": cached["received_at"] if cached else None}
        try:
            payload, _headers = self.transport.get_json(
                self._url(key), allowed_host=HOST, timeout_seconds=12, max_body_bytes=750_000)
            rows = _records(payload)
            selected: list[dict[str, Any]] = []
            for row in rows:
                if key == "press_releases":
                    date = row.get("date")
                    title = row.get("title")
                    link = row.get("link")
                    if not all(isinstance(x, str) and x.strip() for x in (date, title, link)):
                        continue
                    parsed = urlsplit(link)
                    if parsed.scheme != "https" or parsed.hostname not in {"hkma.gov.hk", "www.hkma.gov.hk"}:
                        continue
                    selected.append({"date": date, "title": title, "link": link})
                else:
                    selected.append({field: row.get(field) for field in spec["fields"] if field in row})
            received_at = utc_iso(self.clock())
            result = {"status": "fresh" if selected else "empty", "rows": selected,
                      "received_at": received_at}
            self._cache[key] = result
            return result
        except SourceError as exc:
            return {"status": "rate_limited_remote" if exc.http_status == 429 else exc.code,
                    "rows": cached["rows"] if cached else [],
                    "received_at": cached["received_at"] if cached else None}
        except Exception:
            return {"status": "error", "rows": cached["rows"] if cached else [],
                    "received_at": cached["received_at"] if cached else None}

    def payload(self) -> dict[str, Any]:
        with self._lock:
            checked_at = utc_iso(self.clock())
            channels = {key: self._fetch(key) for key in _CHANNELS}
        statistics: list[dict[str, Any]] = []
        for key in ("interbank_liquidity", "monetary_base"):
            spec = _CHANNELS[key]
            state = channels[key]
            for row in state["rows"][:1]:
                statistics.append({
                    "source_id": f"hkma-{key}",
                    "publisher": "Hong Kong Monetary Authority",
                    "source_title": spec["title"],
                    "source_url": BASE + spec["path"],
                    "api_documentation_url": DOCS_URL,
                    "event_time": row.get(spec["event_field"]),
                    "event_time_kind": "source end_of_date (date only)",
                    "received_at": state["received_at"],
                    "frequency": spec["frequency"],
                    "units_by_field": spec["units"],
                    "source_record": row,
                    "revision_status": "not separately supplied by this endpoint",
                    "status": state["status"],
                    "covered_symbols": [],
                })
        announcements: list[dict[str, Any]] = []
        press_state = channels["press_releases"]
        for row in press_state["rows"][:10]:
            announcements.append({
                "publisher": "Hong Kong Monetary Authority",
                "source_title": "HKMA press release",
                "event_time": row["date"],
                "event_time_kind": "source date (date only)",
                "received_at": press_state["received_at"],
                "title": row["title"],
                "source_url": row["link"],
                "api_documentation_url": DOCS_URL,
                "frequency": "as released",
                "status": press_state["status"],
                "summary": None,
                "covered_symbols": [],
            })
        statuses = {key: value["status"] for key, value in channels.items()}
        available = bool(statistics or announcements)
        return {
            "ok": True,
            "schema_version": "1.0",
            "publisher": "Hong Kong Monetary Authority",
            "data_mode": "LIVE" if available else "UNAVAILABLE",
            "demo": False,
            "status": "fresh" if any(v == "fresh" for v in statuses.values()) else ("cached" if available else "unavailable"),
            "checked_at": checked_at,
            "channels": statuses,
            "statistics": statistics,
            "announcements": announcements,
            "coverage_complete": False,
            "covered_symbols": [],
            "source_links": {
                "open_api_overview": OVERVIEW_URL,
                "documentation": DOCS_URL,
                "terms": TERMS_URL,
                "robots": ROBOTS_URL,
            },
            "limits": {
                "on_demand_only": True,
                "minimum_interval_seconds_per_endpoint": 7200,
                "maximum_requests_per_endpoint_per_utc_day": 12,
                "automatic_retry": False,
                "background_refresh": False,
                "press_release_fields_only": ["date", "title", "link"],
            },
            "notice": "Official source values are displayed without summary, market inference, security mapping, or currency conversion. The API does not supply a separate release timestamp or revision status for these records.",
        }


DEFAULT_HKMA = HKMAOpenAPI()


def hkma_open_api_payload() -> dict[str, Any]:
    return DEFAULT_HKMA.payload()
