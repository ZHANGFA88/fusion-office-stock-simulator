"""SEC EDGAR company submissions adapter using Scrapling's ordinary Fetcher.

Network access is off by default. Before enabling, configure an operator-approved
contact email for the SEC User-Agent, review the official API/fair-access policy,
and confirm the exact intended local use. This adapter only requests the official
submissions JSON endpoint; it never fetches company articles, uses stealth,
proxy rotation, redirects, or bypasses access controls.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import os
import re
import threading
import time
from typing import Any, Callable, Mapping, Optional
from urllib.parse import urlparse

from .scrapling_feed import FeedHealth, FeedResult, HTTPResponse, ScraplingTransport

SEC_API_BASE = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_FAIR_ACCESS_URL = "https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data"
SEC_API_DOCS_URL = "https://www.sec.gov/search-filings/edgar-application-programming-interfaces"
_SEC_BUDGET_LOCK = threading.Lock()
_SEC_NEXT_ALLOWED_AT = 0.0


@dataclass(frozen=True)
class SECSubmissionsConfig:
    cik: str
    contact_email: str = ""
    enabled: bool = False
    terms_reviewed: bool = False
    robots_allowed: bool = False
    # Personal use means local/private display only. External redistribution remains off.
    local_personal_authorized: bool = False
    min_poll_seconds: float = 60.0
    max_stale_seconds: int = 900
    timeout_seconds: float = 8.0
    max_body_bytes: int = 8_000_000

    @classmethod
    def from_environment(cls, cik: str, **overrides: Any) -> "SECSubmissionsConfig":
        """Read only SEC_CONTACT_EMAIL; absence always disables collection."""
        contact_email = os.environ.get("SEC_CONTACT_EMAIL", "").strip()
        requested_enabled = bool(overrides.pop("enabled", False))
        enabled = requested_enabled and bool(contact_email)
        return cls(cik=cik, contact_email=contact_email, enabled=enabled, **overrides)


class SECSubmissionsProvider:
    """Read one issuer's filing history; no HTTP call occurs until explicitly enabled."""

    def __init__(
        self,
        config: SECSubmissionsConfig,
        transport: Optional[Any] = None,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self.transport = transport or ScraplingTransport()
        self.clock = clock
        self.monotonic = monotonic
        self.cik = _normalize_cik(config.cik)
        self.source_id = f"sec-edgar:{self.cik}" if self.cik else "sec-edgar:invalid"
        self.endpoint = SEC_API_BASE.format(cik=self.cik) if self.cik else ""
        self._last_attempt: Optional[float] = None
        self._last_success: Optional[datetime] = None
        self._last_records: tuple[dict[str, Any], ...] = ()
        self._etag: Optional[str] = None
        self._last_modified: Optional[str] = None
        self._retry_until: Optional[float] = None

    def read(self) -> FeedResult:
        now = _iso_utc(self.clock())
        cfg = self.config
        if not self.cik:
            return FeedResult((), FeedHealth(self.source_id, "denied", now,
                                             error_code="invalid_cik", error_detail="CIK must be 1-10 digits"))
        if not cfg.enabled:
            return FeedResult((), FeedHealth(self.source_id, "denied", now,
                                             error_code="disabled", error_detail="SEC source is disabled"))
        email = cfg.contact_email.strip()
        if not _valid_email(email):
            return FeedResult((), FeedHealth(self.source_id, "denied", now,
                                             error_code="contact_email_required",
                                             error_detail="configure local SEC_CONTACT_EMAIL; no request was sent"))
        if not cfg.terms_reviewed or not cfg.robots_allowed:
            return FeedResult((), FeedHealth(self.source_id, "denied", now,
                                             error_code="policy_review_required",
                                             error_detail="review SEC API terms and endpoint access policy before enabling"))
        if not cfg.local_personal_authorized:
            return FeedResult((), FeedHealth(self.source_id, "denied", now,
                                             error_code="local_personal_scope_not_authorized",
                                             error_detail="explicitly approve the local_personal scope after reviewing the intended use"))

        minimum_interval = max(0.2, float(cfg.min_poll_seconds))  # stay well below SEC's 10 req/s ceiling
        tick = self.monotonic()
        if self._retry_until is not None and tick < self._retry_until:
            return FeedResult(self._cached_records(now), FeedHealth(
                self.source_id, "rate_limited_local", now, self._last_success_iso(),
                self._is_stale(now), retry_after_seconds=self._retry_until - tick,
                error_code="retry_after",
            ))
        if self._last_attempt is not None and tick - self._last_attempt < minimum_interval:
            remaining = max(0.0, minimum_interval - (tick - self._last_attempt))
            return FeedResult(self._cached_records(now), FeedHealth(
                self.source_id, "rate_limited_local", now, self._last_success_iso(),
                self._is_stale(now), retry_after_seconds=remaining, error_code="poll_interval",
            ))
        self._last_attempt = tick

        headers = {
            "User-Agent": f"FusionOffice-News/1.0 ({email})",
            "Accept": "application/json",
        }
        if self._etag:
            headers["If-None-Match"] = self._etag
        if self._last_modified:
            headers["If-Modified-Since"] = self._last_modified
        try:
            _wait_for_global_sec_slot()
            # ScraplingTransport uses Fetcher.get with stealth_headers=False,
            # impersonate=None, no proxy, retries=0 and redirects disabled.
            response = self.transport.get(self.endpoint, headers, cfg.timeout_seconds)
        except Exception as exc:
            return self._failure(now, "transport_error", str(exc))

        response_headers = {str(k).lower(): str(v) for k, v in response.headers.items()}
        if response.status == 304:
            self._remember_success(now)
            self._update_cache_validators(response_headers)
            records = _copy_with_status(self._last_records, "fresh")
            return FeedResult(records, FeedHealth(self.source_id, "not_modified", now,
                                                   self._last_success_iso(), False, 304,
                                                   etag=self._etag, last_modified=self._last_modified))
        if 300 <= response.status < 400:
            return self._failure(now, "redirect_blocked", "redirect responses are not followed",
                                 status=response.status, headers=response_headers)
        if response.status == 429:
            return self._failure(now, "rate_limited", "SEC returned HTTP 429",
                                 status=429, headers=response_headers)
        if not 200 <= response.status < 300:
            return self._failure(now, "http_error", f"SEC returned HTTP {response.status}",
                                 status=response.status, headers=response_headers)
        if len(response.body) > cfg.max_body_bytes:
            return self._failure(now, "payload_too_large", "SEC response exceeds configured body limit",
                                 status=response.status, headers=response_headers)

        try:
            payload = response.json_data
            if payload is None:
                payload = json.loads(response.body.decode("utf-8-sig"))
            records = tuple(self._normalize(payload, now))
        except (ValueError, TypeError, KeyError) as exc:
            return self._failure(now, "parse_error", str(exc), status=response.status,
                                 headers=response_headers)

        self._last_records = records
        self._remember_success(now)
        self._update_cache_validators(response_headers)
        return FeedResult(records, FeedHealth(self.source_id, "ok", now,
                                               self._last_success_iso(), False, response.status,
                                               etag=self._etag, last_modified=self._last_modified))

    def _normalize(self, payload: Any, received_at: str) -> list[dict[str, Any]]:
        if not isinstance(payload, Mapping):
            raise ValueError("SEC submissions JSON must be an object")
        company_name = str(payload.get("name") or "SEC registrant").strip()
        tickers = payload.get("tickers") if isinstance(payload.get("tickers"), list) else []
        tickers = [str(value).strip() for value in tickers if value]
        recent = payload.get("filings", {}).get("recent") if isinstance(payload.get("filings"), Mapping) else None
        if not isinstance(recent, Mapping):
            raise ValueError("SEC response has no filings.recent object")

        # SEC submissions JSON is column-oriented: each property holds a list.
        accession_numbers = recent.get("accessionNumber")
        if not isinstance(accession_numbers, list):
            raise ValueError("SEC response has no accessionNumber array")
        rows: list[dict[str, Any]] = []
        for index, accession in enumerate(accession_numbers):
            accession = str(accession or "").strip()
            if not accession:
                continue
            form = _at(recent, "form", index)
            filed = _at(recent, "filingDate", index)
            accepted = _at(recent, "acceptanceDateTime", index)
            description = _at(recent, "primaryDocDescription", index)
            primary_document = _at(recent, "primaryDocument", index)
            published_raw = accepted or filed
            accession_path = accession.replace("-", "")
            doc = str(primary_document or "").strip()
            if doc and (".." in doc.split("/") or urlparse(doc).scheme):
                doc = ""
            filing_path = f"https://www.sec.gov/Archives/edgar/data/{int(self.cik)}/{accession_path}/"
            source_url = filing_path + doc if doc else filing_path
            title_parts = [company_name, str(form or "Filing")]
            if description:
                title_parts.append(str(description).strip())
            title = " — ".join(part for part in title_parts if part)
            published_at = _parse_utc(published_raw)
            related = [{"instrument_id": None, "symbol": symbol,
                        "method": "SEC-CIK-ticker-map", "confidence": 0.9}
                       for symbol in tickers]
            stable_id = hashlib.sha256(f"{self.cik}\n{accession}".encode()).hexdigest()[:24]
            rows.append({
                "schema_version": "1.0",
                "record_type": "news",
                "domain": "finance",
                "id": stable_id,
                "provider": "SEC EDGAR Submissions API",
                "publisher": company_name,
                "market": "US",
                "instrument_id": None,
                "symbol": tickers[0] if len(tickers) == 1 else None,
                "currency": None,
                "event_ts": published_at,
                "published_at": published_at,
                "published_at_raw": str(published_raw) if published_raw else None,
                "received_at": received_at,
                "status": "fresh",
                "source_url": source_url,
                "canonical_url": source_url,
                "source_id": accession,
                "dedup_key": f"id:sec-edgar:{self.cik}:{accession}",
                "related_instruments": related,
                "policy": {
                    "authorized_scopes": ["local_personal"] if self.config.local_personal_authorized else [],
                    "cache_until": None,
                    "attribution_required": True,
                    "policy_reference": SEC_FAIR_ACCESS_URL,
                },
                "news": {
                    "title": title,
                    "language": None,
                    "content_type": "SEC Filing",
                    "is_update": False,
                    "withdrawn_at": None,
                    "cluster_id": f"id:sec-edgar:{self.cik}:{accession}",
                    "form": form,
                },
            })
        return rows

    def _update_cache_validators(self, headers: Mapping[str, str]) -> None:
        if headers.get("etag"):
            self._etag = headers["etag"]
        if headers.get("last-modified"):
            self._last_modified = headers["last-modified"]

    def _remember_success(self, now: str) -> None:
        self._last_success = datetime.fromisoformat(now.replace("Z", "+00:00"))

    def _last_success_iso(self) -> Optional[str]:
        return self._last_success.isoformat(timespec="seconds").replace("+00:00", "Z") if self._last_success else None

    def _is_stale(self, now: str) -> Optional[bool]:
        if self._last_success is None:
            return None
        current = datetime.fromisoformat(now.replace("Z", "+00:00"))
        return (current - self._last_success).total_seconds() > self.config.max_stale_seconds

    def _cached_records(self, now: str) -> tuple[dict[str, Any], ...]:
        age_status = "stale" if self._is_stale(now) else "delayed"
        return _copy_with_status(self._last_records, age_status)

    def _failure(self, now: str, code: str, detail: str, *, status: Optional[int] = None,
                 headers: Optional[Mapping[str, str]] = None) -> FeedResult:
        headers = headers or {}
        retry_after = _retry_after(headers.get("retry-after"), now)
        if status == 429 and retry_after is not None and self._last_attempt is not None:
            self._retry_until = self._last_attempt + retry_after
        stale = self._is_stale(now)
        records = self._cached_records(now)
        return FeedResult(records, FeedHealth(
            source_id=self.source_id, status="failed", checked_at=now,
            last_success_at=self._last_success_iso(), is_stale=stale, http_status=status,
            retry_after_seconds=retry_after, etag=headers.get("etag"),
            last_modified=headers.get("last-modified"), error_code=code,
            error_detail=detail[:240],
        ))


def _normalize_cik(value: str) -> Optional[str]:
    digits = str(value or "").strip()
    if not digits.isdigit() or not 1 <= len(digits) <= 10:
        return None
    return digits.zfill(10)


def _valid_email(value: str) -> bool:
    # Deliberately validate only basic header-safe address syntax; never source from user profile implicitly.
    return bool(re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", value))


def _at(columns: Mapping[str, Any], name: str, index: int) -> Optional[str]:
    values = columns.get(name)
    if not isinstance(values, list) or index >= len(values) or values[index] is None:
        return None
    value = str(values[index]).strip()
    return value or None


def _parse_utc(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _copy_with_status(records: tuple[dict[str, Any], ...], status: str) -> tuple[dict[str, Any], ...]:
    result = []
    for record in records:
        copied = dict(record)
        copied["status"] = status
        result.append(copied)
    return tuple(result)


def _retry_after(value: Optional[str], now: str) -> Optional[float]:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            target = parsedate_to_datetime(value)
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            current = datetime.fromisoformat(now.replace("Z", "+00:00"))
            return max(0.0, (target.astimezone(timezone.utc) - current).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


def _wait_for_global_sec_slot() -> None:
    """Serialize requests and cap this process at five requests per second."""
    global _SEC_NEXT_ALLOWED_AT
    with _SEC_BUDGET_LOCK:
        now = time.monotonic()
        wait = max(0.0, _SEC_NEXT_ALLOWED_AT - now)
        if wait:
            time.sleep(wait)
        _SEC_NEXT_ALLOWED_AT = max(now, _SEC_NEXT_ALLOWED_AT) + 0.2
