import importlib.util
import http.client
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
_spec = importlib.util.spec_from_file_location("integrated_serve", ROOT / "scripts" / "serve.py")
serve = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(serve)


class FinanceObservatoryEndpointTests(unittest.TestCase):
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

    def test_public_finance_observatory_endpoint_returns_demo_only(self):
        with urlopen(self.base + "/api/finance/observatory", timeout=3) as response:
            self.assertEqual(200, response.status)
            payload = json.loads(response.read().decode("utf-8"))
        self.assertEqual("finance", payload["domain"])
        self.assertEqual("DEMO", payload["data_mode"])
        self.assertTrue(payload["demo"])
        self.assertTrue(payload["items"])
        self.assertTrue(payload["quotes"])
        self.assertTrue(payload["filings"])
        self.assertTrue(payload["news"])
        self.assertTrue(all(item["data_mode"] == "DEMO" for item in payload["items"]))

    def test_typed_collections_keep_quotes_and_filings_separate(self):
        with urlopen(self.base + "/api/finance/observatory", timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
        self.assertEqual("quote", payload["quotes"][0]["kind"])
        self.assertEqual("filing", payload["filings"][0]["kind"])
        self.assertEqual("news", payload["news"][0]["kind"])

    def test_legacy_news_route_is_demo_only_and_never_calls_provider(self):
        with patch.object(serve.urllib.request, "urlopen", side_effect=AssertionError("outbound urllib must not be called")) as urllib_open, \
             patch("subprocess.run", side_effect=AssertionError("outbound subprocess must not be called")) as subprocess_run, \
             patch.object(serve, "fetch_news_global", side_effect=AssertionError("provider must not be called")) as global_provider, \
             patch.object(serve, "fetch_news_stock", side_effect=AssertionError("provider must not be called")) as stock_provider:
            connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
            connection.request("GET", "/api/finance/news?limit=14")
            response = connection.getresponse()
            self.assertEqual(200, response.status)
            payload = json.loads(response.read().decode("utf-8"))
            connection.close()
        self.assertEqual("DEMO", payload["data_mode"])
        self.assertTrue(payload["demo"])
        self.assertGreater(len(payload["items"]), 0)
        self.assertTrue(all(x["data_mode"] == "DEMO" and x["url"] is None for x in payload["items"]))
        urllib_open.assert_not_called()
        subprocess_run.assert_not_called()
        global_provider.assert_not_called()
        stock_provider.assert_not_called()

    def test_observatory_route_has_zero_mocked_external_transport_calls(self):
        with patch.object(serve.urllib.request, "urlopen", side_effect=AssertionError("outbound urllib must not be called")) as urllib_open, \
             patch("subprocess.run", side_effect=AssertionError("outbound subprocess must not be called")) as subprocess_run:
            connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
            connection.request("GET", "/api/finance/observatory")
            response = connection.getresponse()
            self.assertEqual(200, response.status)
            payload = json.loads(response.read().decode("utf-8"))
            connection.close()
        self.assertEqual("DEMO", payload["data_mode"])
        self.assertTrue(payload["demo"])
        urllib_open.assert_not_called()
        subprocess_run.assert_not_called()

    def test_legacy_news_cache_helpers_fail_closed_without_provider_calls(self):
        with patch.object(serve, "fetch_news_global", side_effect=AssertionError("provider must not be called")), \
             patch.object(serve, "fetch_news_stock", side_effect=AssertionError("provider must not be called")):
            self.assertEqual([], serve._news_global_cache_get())
            self.assertEqual([], serve._news_stock_cache_get("AAPL"))

    def test_background_refresh_thread_requires_explicit_opt_in(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(serve.threading, "Thread") as thread_factory:
            self.assertFalse(serve._start_background_thread(lambda: None))
            thread_factory.assert_not_called()
        with patch.dict(os.environ, {"FINANCE_ENABLE_BACKGROUND_REFRESH": "1"}, clear=True), \
             patch.object(serve.threading, "Thread") as thread_factory:
            self.assertTrue(serve._start_background_thread(lambda: None))
            thread_factory.assert_called_once()
            thread_factory.return_value.start.assert_called_once_with()

    def test_public_finance_pages_use_observatory_demo_not_legacy_news_route(self):
        for relative in ("app/markets.html", "app/trade.html", "public/finance.html"):
            source = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("/api/finance/observatory", source, relative)
            self.assertNotIn("/api/finance/news", source, relative)

    def test_run_sh_default_refresh_is_disabled_and_opt_in_is_explicit(self):
        source = (ROOT / "scripts" / "run.sh").read_text(encoding="utf-8")
        self.assertIn("WITH_MARKET_REFRESH=0", source)
        self.assertIn("--with-market-refresh", source)
        self.assertIn('FINANCE_ENABLE_BACKGROUND_REFRESH="$WITH_MARKET_REFRESH"', source)

    def test_run_sh_default_control_flow_starts_only_mocked_server(self):
        with tempfile.TemporaryDirectory(prefix="fusion-run-sh-test-") as temp_dir:
            shim = Path(temp_dir) / "python3"
            shim.write_text("#!/bin/sh\nprintf 'MOCK_SERVICE: %s\\n' \"$*\"\n", encoding="utf-8")
            shim.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = temp_dir + os.pathsep + env.get("PATH", "")
            result = subprocess.run(
                ["/bin/bash", "scripts/run.sh"], cwd=ROOT, env=env,
                capture_output=True, text=True, timeout=5,
            )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("live market background refresh disabled by default", result.stdout)
        self.assertIn("MOCK_SERVICE: scripts/serve.py", result.stdout)
        self.assertNotIn("spider-feeder", result.stdout)
        self.assertNotIn("AI trading crew explicitly enabled", result.stdout)
        self.assertNotIn("background refresh explicitly enabled", result.stdout)

    def test_spider_news_feeder_exits_without_opt_in_or_side_effects(self):
        env = os.environ.copy()
        env.pop("ENABLE_DEMO_SPIDER_FEEDER", None)
        result = subprocess.run(
            ["node", "scripts/spider_news_feeder.mjs"], cwd=ROOT, env=env,
            capture_output=True, text=True, timeout=5,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("disabled", result.stdout)
        self.assertFalse((ROOT / "data" / "spider-live.json").exists())
        self.assertFalse((ROOT / "data" / "spider-feeder.lock").exists())

    def test_spider_news_feeder_rejects_non_numeric_port_before_startup(self):
        env = os.environ.copy()
        env["ENABLE_DEMO_SPIDER_FEEDER"] = "1"
        env["FINANCE_PORT"] = "8772@external.invalid"
        result = subprocess.run(
            ["node", "scripts/spider_news_feeder.mjs"], cwd=ROOT, env=env,
            capture_output=True, text=True, timeout=5,
        )
        self.assertEqual(2, result.returncode)
        self.assertIn("invalid FINANCE_PORT", result.stderr)
        self.assertFalse((ROOT / "data" / "spider-feeder.lock").exists())

    def test_spider_news_feeder_is_loopback_observatory_demo_only(self):
        source = (ROOT / "scripts" / "spider_news_feeder.mjs").read_text(encoding="utf-8")
        self.assertIn("new URL('/api/finance/observatory', `http://127.0.0.1:${PORT}`)", source)
        self.assertNotIn("process.env.NEWS_URL", source)
        self.assertIn("data.data_mode !== 'DEMO' || data.demo !== true", source)
        self.assertIn("it.data_mode === 'DEMO'", source)

    def test_background_market_refresh_is_disabled_without_explicit_opt_in(self):
        target = Mock()
        with patch.dict(serve.os.environ, {"FINANCE_ENABLE_BACKGROUND_REFRESH": "0"}):
            self.assertFalse(serve._background_refresh_enabled())
            self.assertFalse(serve._start_background_thread(target))
        target.assert_not_called()


if __name__ == "__main__":
    unittest.main()
