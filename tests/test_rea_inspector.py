from __future__ import annotations

import base64
import http.client
import importlib.util
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from modules.rea_inspector import service as rea_service  # noqa: E402

_spec = importlib.util.spec_from_file_location("rea_test_serve", ROOT / "scripts" / "serve.py")
serve = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(serve)


def _capture(graph: dict | None = None, include_requests: bool = True) -> dict:
    events = []
    if include_requests:
        url = "https://aisafetyhot.com/api/site/monitor?lang=zh"
        payload = graph or {
            "topics": [
                {"slug": "alpha", "name": "Alpha", "group": "field", "total": 9},
                {"slug": "openai", "name": "OpenAI", "group": "company", "total": 4},
            ],
            "pairs": [["alpha", "openai", 3]],
            "stats": {"sources": 7, "items": 11, "today": 2},
            "generatedAt": "2026-10-17T00:00:00Z",
            "feed": {"items": [{"title": "must not persist", "summary": "private article body"}], "nextCursor": "secret-cursor"},
        }
        raw = json.dumps(payload).encode("utf-8")
        events.extend([
            {"kind": "request", "transaction_id": "monitor-1", "method": "GET",
             "url": {"url": url}, "resource_type": "fetch"},
            {"kind": "response", "transaction_id": "monitor-1", "status": 200,
             "url": {"url": url}},
            {"kind": "network-content", "transaction_id": "monitor-1", "phase": "response",
             "body": {"state": "captured", "encoding": "base64",
                      "content": base64.b64encode(raw).decode("ascii")}},
            {"kind": "request", "transaction_id": "rum-1", "method": "POST",
             "url": {"url": "https://aisafetyhot.com/cdn-cgi/rum"}, "resource_type": "fetch"},
            {"kind": "request", "transaction_id": "external-1", "method": "GET",
             "url": {"url": "https://assets.example.invalid/script.js"}, "resource_type": "script"},
        ])
    return {"normalized_result": {"events": {"items": events}}}


class ReaInspectorUnitTests(unittest.TestCase):
    def test_loopback_client_check(self):
        self.assertTrue(rea_service.is_loopback_client("127.0.0.1"))
        self.assertTrue(rea_service.is_loopback_client("::1"))
        self.assertFalse(rea_service.is_loopback_client("192.0.2.44"))
        self.assertFalse(rea_service.is_loopback_client("not-an-ip"))

    def test_local_same_origin_guard_rejects_cross_site_metadata(self):
        self.assertTrue(rea_service.is_local_same_origin_request(
            "127.0.0.1", "http://127.0.0.1:8772", "127.0.0.1:8772", "same-origin"))
        self.assertTrue(rea_service.is_local_same_origin_request("127.0.0.1", None, "localhost:8772", None))
        self.assertFalse(rea_service.is_local_same_origin_request(
            "127.0.0.1", "https://evil.example", "127.0.0.1:8772", "cross-site"))
        self.assertFalse(rea_service.is_local_same_origin_request(
            "192.0.2.44", None, "127.0.0.1:8772", "same-origin"))
        self.assertNotIn(rea_service.ROOT.resolve(), rea_service.DATA_DIR.resolve().parents)

    def test_monitor_body_is_extracted_and_feed_text_is_excluded(self):
        graph = rea_service._monitor_data(_capture())
        self.assertEqual(2, len(graph["topics"]))
        sanitized = rea_service._sanitize_graph(graph)
        encoded = json.dumps(sanitized)
        self.assertEqual(1, len(sanitized["edges"]))
        self.assertNotIn("must not persist", encoded)
        self.assertNotIn("private article body", encoded)
        self.assertNotIn("secret-cursor", encoded)

    def test_request_report_keeps_first_party_paths_but_redacts_values_and_external_origins(self):
        capture = _capture()
        capture["normalized_result"]["events"]["items"].append({
            "kind": "request", "transaction_id": "signed-image", "method": "GET",
            "url": {"url": "https://aisafetyhot.com/api/img-proxy?lang=zh&sig=secret-token"},
            "resource_type": "image",
        })
        summary = rea_service._request_summary("overview", capture)
        self.assertGreaterEqual(summary["firstPartyNonGetCount"], 1)
        self.assertGreaterEqual(summary["offOriginRequestCount"], 1)
        routes = [row["path"] for row in summary["firstPartyRequests"]]
        self.assertIn("/api/site/monitor?lang=zh", routes)
        self.assertIn("/api/img-proxy?lang=zh&sig=%5Bredacted%5D", routes)
        self.assertFalse(any("example.invalid" in route or "secret-token" in route for route in routes))

    def test_run_scan_is_opt_in_and_writes_only_normalized_local_report(self):
        with tempfile.TemporaryDirectory(prefix="rea-report-test-") as tmp:
            local = Path(tmp) / "rea"
            report_path = local / "aisafetyhot-latest.json"
            with patch.dict(os.environ, {"REA_ENABLE_INSPECTOR": "1"}, clear=True), \
                 patch.object(rea_service.shutil, "which", return_value="/usr/local/bin/rea"), \
                 patch.object(rea_service, "_browser_executable", return_value="/usr/bin/chromium"), \
                 patch.object(rea_service, "_rea_version", return_value="6.5.0"), \
                 patch.object(rea_service, "_run_capture", side_effect=lambda _rea, _browser, key: _capture() if key == "overview" else _capture(include_requests=False)), \
                 patch.object(rea_service, "DATA_DIR", local), \
                 patch.object(rea_service, "REPORT_PATH", report_path):
                report = rea_service.run_scan()
                saved = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual("6.5.0", report["reaVersion"])
            self.assertEqual(2, len(report["graph"]["topics"]))
            self.assertEqual(1, len(report["graph"]["edges"]))
            self.assertNotIn("private article body", json.dumps(saved))
            self.assertTrue(rea_service.get_report()["hasReport"] if report_path == rea_service.REPORT_PATH else report_path.exists())
            self.assertEqual(0o600, report_path.stat().st_mode & 0o777)

    def test_run_scan_fails_closed_without_explicit_opt_in(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(rea_service.subprocess, "run", side_effect=AssertionError("no subprocess while disabled")):
            with self.assertRaises(rea_service.InspectorError):
                rea_service.run_scan()


class ReaInspectorHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_status_api_is_available_locally_without_triggering_scan(self):
        with patch.object(rea_service, "get_status", return_value={"ok": True, "enabled": False}) as status:
            with urlopen(self.base + "/api/rea/status", timeout=3) as response:
                self.assertEqual(200, response.status)
                self.assertEqual({"ok": True, "enabled": False}, json.loads(response.read().decode("utf-8")))
        status.assert_called_once()

    def test_scan_route_rejects_user_supplied_body_before_invoking_runner(self):
        with patch.object(rea_service, "run_scan", side_effect=AssertionError("must not run with body")) as runner:
            connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
            connection.request("POST", "/api/rea/scan", body='{"url":"https://example.invalid"}', headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            self.assertEqual(400, response.status)
            payload = json.loads(response.read().decode("utf-8"))
            connection.close()
        self.assertFalse(payload["ok"])
        runner.assert_not_called()

    def test_scan_route_is_disabled_by_default_and_never_launches_rea(self):
        with patch.dict(os.environ, {"REA_ENABLE_INSPECTOR": "0"}), patch.object(rea_service, "_run_capture", side_effect=AssertionError("disabled scan must not launch REA")):
            connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
            connection.request("POST", "/api/rea/scan")
            response = connection.getresponse()
            self.assertEqual(503, response.status)
            payload = json.loads(response.read().decode("utf-8"))
            connection.close()
        self.assertFalse(payload["ok"])
        self.assertIn("未启用", payload["error"])

    def test_cross_origin_scan_request_is_denied_without_cors(self):
        with patch.object(rea_service, "run_scan", side_effect=AssertionError("cross-origin request must not scan")) as runner:
            connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
            connection.request("POST", "/api/rea/scan", headers={
                "Origin": "https://evil.example",
                "Sec-Fetch-Site": "cross-site",
            })
            response = connection.getresponse()
            self.assertEqual(403, response.status)
            self.assertIsNone(response.getheader("Access-Control-Allow-Origin"))
            json.loads(response.read().decode("utf-8"))
            connection.close()
        runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
