"""Shared fail-closed runtime for narrowly scoped, read-only official APIs.

This module never starts a worker, reads finance databases, follows redirects,
or retries a request. Its only persistent state is an atomic source-request
budget file in the OS temp directory; it contains endpoint names and UTC times.
"""
from __future__ import annotations

import datetime as dt
import http.client
import json
import os
import ssl
import tempfile
import threading
from typing import Any, Callable
from urllib.parse import urlsplit


class SourceError(Exception):
    def __init__(self, code: str, *, http_status: int | None = None):
        super().__init__(code)
        self.code = code
        self.http_status = http_status


class BudgetUnavailable(Exception):
    pass


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def utc_iso(value: dt.datetime | None = None) -> str:
    value = value or utc_now()
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class PersistentRequestBudget:
    """Persist a per-endpoint cooldown and UTC-day cap outside project databases."""

    def __init__(self, path: str | None = None, clock: Callable[[], dt.datetime] = utc_now):
        self.path = path or os.path.join(tempfile.gettempdir(), "fusion-office-official-source-gates-v1.json")
        self.clock = clock
        self._lock = threading.Lock()

    def _read(self) -> dict[str, Any]:
        if not os.path.exists(self.path):
            return {"schema": 1, "endpoints": {}}
        try:
            if os.path.getsize(self.path) > 1_000_000:
                raise BudgetUnavailable("budget_file_too_large")
            with open(self.path, "r", encoding="utf-8") as handle:
                state = json.load(handle)
            if not isinstance(state, dict) or state.get("schema") != 1 or not isinstance(state.get("endpoints"), dict):
                raise BudgetUnavailable("budget_file_invalid")
            return state
        except BudgetUnavailable:
            raise
        except Exception as exc:
            raise BudgetUnavailable("budget_file_unreadable") from exc

    def _write(self, state: dict[str, Any]) -> None:
        parent = os.path.dirname(self.path) or "."
        os.makedirs(parent, exist_ok=True)
        temporary = f"{self.path}.{os.getpid()}.{threading.get_ident()}.tmp"
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(state, handle, sort_keys=True, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def reserve(self, endpoint: str, *, min_interval_seconds: int, max_per_utc_day: int) -> tuple[bool, str]:
        now = self.clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=dt.timezone.utc)
        now = now.astimezone(dt.timezone.utc)
        today = now.date().isoformat()
        with self._lock:
            try:
                state = self._read()
                endpoints = state["endpoints"]
                previous = endpoints.get(endpoint, {})
                if not isinstance(previous, dict):
                    raise BudgetUnavailable("budget_record_invalid")
                last_at = previous.get("last_at")
                if last_at:
                    last = dt.datetime.fromisoformat(str(last_at).replace("Z", "+00:00"))
                    if last.tzinfo is None:
                        last = last.replace(tzinfo=dt.timezone.utc)
                    if (now - last.astimezone(dt.timezone.utc)).total_seconds() < min_interval_seconds:
                        return False, "local_interval"
                count = int(previous.get("count", 0)) if previous.get("day") == today else 0
                if count >= max_per_utc_day:
                    return False, "local_daily_cap"
                endpoints[endpoint] = {"day": today, "count": count + 1, "last_at": utc_iso(now)}
                state["endpoints"] = endpoints
                self._write(state)
                return True, "reserved"
            except BudgetUnavailable:
                return False, "budget_unavailable"
            except Exception:
                return False, "budget_unavailable"


class FixedHTTPSJSONTransport:
    """GET JSON from one exact HTTPS host; redirects and all other hosts are refused."""

    def get_json(self, url: str, *, allowed_host: str, timeout_seconds: int = 12,
                 max_body_bytes: int = 1_000_000) -> tuple[dict[str, Any], dict[str, str]]:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.hostname != allowed_host or parsed.port not in (None, 443)
                or parsed.username or parsed.password or parsed.fragment):
            raise SourceError("endpoint_not_allowlisted")
        request_path = parsed.path or "/"
        if parsed.query:
            request_path += "?" + parsed.query
        connection = http.client.HTTPSConnection(
            allowed_host, timeout=timeout_seconds, context=ssl.create_default_context())
        try:
            connection.request("GET", request_path, headers={
                "Accept": "application/json",
                "User-Agent": "FusionOfficePrivateReadOnly/1.0",
                "Connection": "close",
            })
            response = connection.getresponse()
            body = response.read(max_body_bytes + 1)
            headers = {name.lower(): value for name, value in response.getheaders()}
            if len(body) > max_body_bytes:
                raise SourceError("response_too_large", http_status=response.status)
            if 300 <= response.status < 400:
                raise SourceError("redirect_blocked", http_status=response.status)
            if response.status == 429:
                raise SourceError("remote_rate_limited", http_status=429)
            if response.status != 200:
                raise SourceError("upstream_http_error", http_status=response.status)
            content_type = headers.get("content-type", "").lower()
            if content_type and "json" not in content_type:
                raise SourceError("unexpected_content_type", http_status=response.status)
            try:
                payload = json.loads(body.decode("utf-8"))
            except Exception as exc:
                raise SourceError("invalid_json", http_status=response.status) from exc
            if not isinstance(payload, dict):
                raise SourceError("invalid_json_shape", http_status=response.status)
            return payload, headers
        except SourceError:
            raise
        except (OSError, http.client.HTTPException, ssl.SSLError, TimeoutError) as exc:
            raise SourceError("transport_error") from exc
        finally:
            connection.close()


DEFAULT_BUDGET = PersistentRequestBudget()
DEFAULT_TRANSPORT = FixedHTTPSJSONTransport()
