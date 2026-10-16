"""Local-only, fixed-target REA capture for the AI Safety HOT research panel.

The REA browser scenario has no network-interception/origin allowlist. This
service therefore requires an explicit launch opt-in, permits loopback callers
only, fixes both URLs in code, performs no clicks/forms/searches, and stores
only a sanitized graph/request summary (never raw Evidence or feed text).
"""
from __future__ import annotations

import base64
import ipaddress
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def _user_data_dir() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")).expanduser()
    candidate = (base / "fusion-office" / "rea").resolve()
    root = ROOT.resolve()
    if candidate == root or root in candidate.parents:
        candidate = (Path.home() / ".local" / "share" / "fusion-office" / "rea").resolve()
    if candidate == root or root in candidate.parents:
        raise RuntimeError("REA report directory must be outside the static web root")
    return candidate


DATA_DIR = _user_data_dir()
REPORT_PATH = DATA_DIR / "aisafetyhot-latest.json"
SITE_ORIGIN = "https://aisafetyhot.com"
TARGETS = {
    "overview": "https://aisafetyhot.com/zh",
    "hot": "https://aisafetyhot.com/zh/hot",
}
TIMEOUT_SECONDS = 45
MAX_CAPTURE_JSON_BYTES = 20 * 1024 * 1024
MAX_MONITOR_BODY_BYTES = 512 * 1024
SLUG_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$")

_WARNING = (
    "REA 无网络请求拦截/域名白名单。扫描仅导航到固定的总览与热点页，不点击、"
    "不搜索、不提交表单；网页脚本仍可能自行请求资源或发送遥测（例如 Cloudflare RUM）。"
)


class InspectorError(RuntimeError):
    """Safe, user-facing REA inspector failure."""


def _browser_executable() -> str | None:
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _safe_environment(home: str) -> dict[str, str]:
    """Pass only runtime necessities and network proxy settings to the child."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": home,
        "TMPDIR": home,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "XDG_CACHE_HOME": str(Path(home) / ".cache"),
    }
    # The sandbox may require its configured outbound proxy. Do not forward the
    # rest of the parent environment (which may contain API keys or credentials).
    for key in (
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
        "http_proxy", "https_proxy", "all_proxy", "no_proxy",
        "SSL_CERT_FILE", "NODE_EXTRA_CA_CERTS",
    ):
        value = os.environ.get(key)
        if value:
            env[key] = value
    return env


def _rea_version(rea_path: str | None) -> str | None:
    if not rea_path:
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="fusion-rea-version-") as home:
            proc = subprocess.run(
                [rea_path, "--version"], capture_output=True, text=True,
                timeout=6, check=False, env=_safe_environment(home), cwd=str(ROOT),
            )
        if proc.returncode == 0:
            return ((proc.stdout or proc.stderr).strip().splitlines() or [None])[0]
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def get_status() -> dict[str, Any]:
    rea_path = shutil.which("rea")
    browser = _browser_executable()
    return {
        "ok": True,
        "enabled": os.environ.get("REA_ENABLE_INSPECTOR") == "1",
        "available": bool(rea_path and browser),
        "reaVersion": _rea_version(rea_path),
        "browserAvailable": bool(browser),
        "targetUrls": list(TARGETS.values()),
        "warning": _WARNING,
        "dataPolicy": "仅写入静态网站根目录之外的用户数据目录；不保存原始 Evidence、文章标题或正文。",
    }


def is_loopback_client(address: str) -> bool:
    try:
        return ipaddress.ip_address(address).is_loopback
    except (ValueError, TypeError):
        return False


def is_local_same_origin_request(address: str, origin: str | None, host: str | None,
                                 sec_fetch_site: str | None) -> bool:
    if not is_loopback_client(address):
        return False
    fetch_site = (sec_fetch_site or "").strip().lower()
    if fetch_site and fetch_site not in ("same-origin", "none"):
        return False
    if origin:
        parsed = urllib.parse.urlsplit(origin.strip())
        if parsed.scheme not in ("http", "https") or not parsed.netloc or not host:
            return False
        if parsed.netloc.casefold() != host.strip().casefold():
            return False
    return True


def _scenario(url: str, browser: str) -> dict[str, Any]:
    # `url` is always selected from TARGETS inside run_scan; never from HTTP input.
    return {
        "browser": {"mode": "launch", "executable_path": browser},
        "start_url": {"url": url},
        "actions": [{"step_id": "page-settle", "action": "wait_for_timeout", "duration_ms": 1800}],
        "capture": {
            "at_end": ["url"],
            "events": ["network"],
            "network": {
                "request_body": False,
                "response_body": True,
                "header_values": False,
            },
        },
    }


def _events(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    normalized = evidence.get("normalized_result") or evidence.get("normalizedResult") or {}
    event_box = normalized.get("events", {}) if isinstance(normalized, dict) else {}
    items = event_box.get("items", []) if isinstance(event_box, dict) else []
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _raw_url(event: dict[str, Any]) -> str:
    value = event.get("url")
    if isinstance(value, dict):
        return str(value.get("url") or "")
    return str(value or "")


def _safe_path(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    path = parsed.path or "/"
    pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    safe_query = []
    for key, value in pairs:
        if key == "lang" and value in ("zh", "en"):
            safe_query.append((key, value))
        elif key:
            safe_query.append((key, "[redacted]"))
    return path + (("?" + urllib.parse.urlencode(safe_query)) if safe_query else "")


def _request_summary(page_key: str, evidence: dict[str, Any]) -> dict[str, Any]:
    items = _events(evidence)
    responses: dict[str, int] = {}
    for item in items:
        if item.get("kind") == "response" and item.get("transaction_id") is not None:
            try:
                responses[str(item["transaction_id"])] = int(item.get("status") or 0)
            except (TypeError, ValueError):
                pass
    first_party = []
    external_count = 0
    non_get_count = 0
    for item in items:
        if item.get("kind") != "request":
            continue
        url = _raw_url(item)
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in ("http", "https") or parsed.netloc.lower() != "aisafetyhot.com":
            external_count += 1
            continue
        method = str(item.get("method") or "GET").upper()
        if method != "GET":
            non_get_count += 1
        first_party.append({
            "method": method,
            "path": _safe_path(url),
            "status": responses.get(str(item.get("transaction_id"))),
            "resourceType": str(item.get("resource_type") or "other")[:32],
        })
        if len(first_party) >= 100:
            break
    return {
        "page": page_key,
        "url": TARGETS[page_key],
        "firstPartyRequests": first_party,
        "firstPartyRequestCount": len(first_party),
        "firstPartyNonGetCount": non_get_count,
        "offOriginRequestCount": external_count,
    }


def _monitor_data(evidence: dict[str, Any]) -> dict[str, Any] | None:
    items = _events(evidence)
    request_by_tx = {
        str(item.get("transaction_id")): item
        for item in items
        if item.get("kind") == "request" and item.get("transaction_id") is not None
    }
    for item in items:
        if item.get("kind") != "network-content" or item.get("phase") != "response":
            continue
        req = request_by_tx.get(str(item.get("transaction_id")))
        if not req:
            continue
        parsed = urllib.parse.urlsplit(_raw_url(req))
        if parsed.netloc.lower() != "aisafetyhot.com" or parsed.path != "/api/site/monitor":
            continue
        body = item.get("body")
        if not isinstance(body, dict) or body.get("state") != "captured" or body.get("encoding") != "base64":
            continue
        content = body.get("content")
        if not isinstance(content, str) or len(content) > MAX_MONITOR_BODY_BYTES * 2:
            continue
        try:
            raw = base64.b64decode(content, validate=True)
            if len(raw) > MAX_MONITOR_BODY_BYTES:
                continue
            decoded = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(decoded, dict) and isinstance(decoded.get("topics"), list) and isinstance(decoded.get("pairs"), list):
            return decoded
    return None


def _int_or_none(value: Any) -> int | None:
    try:
        number = int(value)
        return number if number >= 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _sanitize_graph(data: dict[str, Any]) -> dict[str, Any]:
    topics = []
    seen: set[str] = set()
    for item in data.get("topics", [])[:200]:
        if not isinstance(item, dict):
            continue
        slug = item.get("slug")
        name = item.get("name")
        if not isinstance(slug, str) or not SLUG_RE.fullmatch(slug) or slug in seen:
            continue
        if not isinstance(name, str) or not name.strip():
            continue
        group = item.get("group") if item.get("group") in ("company", "field", "genre") else "other"
        topics.append({
            "slug": slug,
            "name": name.strip()[:100],
            "group": group,
            "total": _int_or_none(item.get("total")) or 0,
        })
        seen.add(slug)
    edges = []
    for item in data.get("pairs", [])[:1000]:
        if not isinstance(item, list) or len(item) < 3:
            continue
        left, right, weight = item[0], item[1], _int_or_none(item[2])
        if isinstance(left, str) and isinstance(right, str) and left in seen and right in seen and weight is not None and weight > 0:
            edges.append({"source": left, "target": right, "weight": weight})
    stats_in = data.get("stats") if isinstance(data.get("stats"), dict) else {}
    feed = data.get("feed") if isinstance(data.get("feed"), dict) else {}
    feed_items = feed.get("items") if isinstance(feed.get("items"), list) else []
    return {
        "generatedAt": str(data.get("generatedAt") or "")[:64],
        "stats": {
            "sources": _int_or_none(stats_in.get("sources")),
            "items": _int_or_none(stats_in.get("items")),
            "today": _int_or_none(stats_in.get("today")),
        },
        "topics": topics,
        "edges": edges,
        "feedCount": len(feed_items),
        "hasNextCursor": bool(feed.get("nextCursor")),
    }


def _run_capture(rea_path: str, browser: str, page_key: str) -> dict[str, Any]:
    scenario = _scenario(TARGETS[page_key], browser)
    try:
        with tempfile.TemporaryDirectory(prefix="fusion-rea-") as home:
            proc = subprocess.run(
                [rea_path, "capture-browser-scenario", json.dumps(scenario, separators=(",", ":"), ensure_ascii=False), "--format", "json"],
                capture_output=True,
                text=True,
                timeout=TIMEOUT_SECONDS,
                check=False,
                env=_safe_environment(home),
                cwd=str(ROOT),
            )
    except subprocess.TimeoutExpired as exc:
        raise InspectorError("REA 页面捕获超时；没有保留原始浏览器 Evidence。") from exc
    except OSError as exc:
        raise InspectorError("无法启动已安装的 REA CLI。") from exc
    if proc.returncode != 0:
        raise InspectorError(f"REA 捕获失败（退出码 {proc.returncode}）。")
    if len(proc.stdout.encode("utf-8", errors="replace")) > MAX_CAPTURE_JSON_BYTES:
        raise InspectorError("REA 返回的捕获包超过本地大小上限，已丢弃。")
    try:
        evidence = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise InspectorError("REA 返回内容不是有效 JSON，未保存捕获包。") from exc
    if not isinstance(evidence, dict):
        raise InspectorError("REA 返回结构无效，未保存捕获包。")
    return evidence


def _save_report(report: dict[str, Any]) -> None:
    DATA_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(DATA_DIR, 0o700)
    except OSError:
        pass
    tmp_path: Path | None = None
    try:
        fd, name = tempfile.mkstemp(prefix=".rea-report-", suffix=".tmp", dir=DATA_DIR)
        tmp_path = Path(name)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(tmp_path, 0o600)
        except OSError:
            pass
        os.replace(tmp_path, REPORT_PATH)
    finally:
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def run_scan() -> dict[str, Any]:
    if os.environ.get("REA_ENABLE_INSPECTOR") != "1":
        raise InspectorError("REA 面板未启用；请使用 scripts/run.sh --with-rea-inspector 显式启动。")
    rea_path = shutil.which("rea")
    browser = _browser_executable()
    if not rea_path:
        raise InspectorError("当前环境未找到 rea CLI。")
    if not browser:
        raise InspectorError("未找到 Chromium；未自动安装浏览器。")

    captures: dict[str, dict[str, Any]] = {}
    for page_key in ("overview", "hot"):
        captures[page_key] = _run_capture(rea_path, browser, page_key)

    graph_data = _monitor_data(captures["overview"]) or _monitor_data(captures["hot"])
    if graph_data is None:
        raise InspectorError("捕获完成，但未取得 /api/site/monitor 的有效 JSON 响应；旧的本机快照未覆盖。")

    graph = _sanitize_graph(graph_data)
    if not graph["topics"]:
        raise InspectorError("monitor 响应中没有可用话题节点；旧的本机快照未覆盖。")
    report = {
        "ok": True,
        "schemaVersion": 1,
        "source": "REA browser scenario",
        "reaVersion": _rea_version(rea_path),
        "capturedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "targetPages": [_request_summary(key, captures[key]) for key in ("overview", "hot")],
        "graph": graph,
        "warning": _WARNING,
        "persistence": "仅本机用户数据目录（静态网站根目录之外，报告权限 0600）；不包含原始 Evidence 或 feed 文本。",
    }
    _save_report(report)
    return report


def get_report() -> dict[str, Any]:
    try:
        with REPORT_PATH.open("r", encoding="utf-8") as handle:
            report = json.load(handle)
        if isinstance(report, dict) and report.get("schemaVersion") == 1:
            return {"ok": True, "hasReport": True, "report": report}
    except FileNotFoundError:
        pass
    except (OSError, json.JSONDecodeError):
        return {"ok": False, "hasReport": False, "report": None, "error": "无法读取本机 REA 快照。"}
    return {"ok": True, "hasReport": False, "report": None}
