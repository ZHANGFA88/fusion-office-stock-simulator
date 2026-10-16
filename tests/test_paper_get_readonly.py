import datetime
import http.client
import importlib.util
import json
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
_spec = importlib.util.spec_from_file_location("paper_get_serve", ROOT / "scripts" / "serve.py")
serve = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(serve)


class PaperGetReadOnlyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fusion-paper-get-")
        self.original_paper_db_path = serve.PAPER_DB_PATH
        serve.PAPER_DB_PATH = str(Path(self.tmp.name) / "paper.db")
        serve.init_paper_db()
        self._seed_ledger()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        serve.PAPER_DB_PATH = self.original_paper_db_path
        self.tmp.cleanup()

    def _seed_ledger(self):
        yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        conn = serve.paper_db()
        try:
            conn.execute(
                "INSERT INTO positions(market,symbol,name,qty,avg_cost,sellable_qty,unsettled_qty,unsettled_date,last_buy_date,updated_ts) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("CN", "600519.SS", "Test CN", 100, 100.0, 0, 100, yesterday, yesterday, int(time.time())),
            )
            conn.execute(
                "INSERT INTO positions(market,symbol,name,qty,avg_cost,sellable_qty,unsettled_qty,unsettled_date,last_buy_date,updated_ts) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("US", "AAPL", "Test US", 10, 100.0, 10, 0, None, None, int(time.time())),
            )
            conn.execute(
                "INSERT INTO orders(ts,market,symbol,name,side,qty,type,limit_price,status,fill_price,fee,note) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (int(time.time()), "CN", "600519.SS", "Test CN", "buy", 100, "limit", 100.0, "open", None, None, None),
            )
            conn.commit()
        finally:
            conn.close()

    def _snapshot(self):
        conn = serve.paper_db()
        try:
            result = {}
            for table in ("accounts", "positions", "orders", "fills"):
                result[table] = [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
            return result
        finally:
            conn.close()

    def _get(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            body = response.read().decode("utf-8")
            return response.status, json.loads(body)
        finally:
            conn.close()

    def test_account_positions_orders_get_do_not_mutate_ledger(self):
        before = self._snapshot()
        crossed_quote = {"600519.SS": {"price": 90.0, "stale": False, "name": "Test CN"}}
        with patch.object(serve, "fetch_quotes", return_value=crossed_quote), \
             patch.object(serve, "_paper_sweep", wraps=serve._paper_sweep) as sweep, \
             patch.object(serve, "_trade_settle_roll", wraps=serve._trade_settle_roll) as settle:
            for path in ("/api/trade/account", "/api/trade/positions", "/api/trade/orders"):
                status, payload = self._get(path)
                self.assertEqual(200, status, (path, payload))
                self.assertTrue(payload.get("ok"), (path, payload))
            sweep.assert_not_called()
            settle.assert_not_called()
        self.assertEqual(before, self._snapshot(), "GET must not create accounts, fill orders, or roll T+1")

    def test_missing_or_stale_quotes_make_valuation_explicitly_incomplete(self):
        quotes = {"600519.SS": {"price": 90.0, "stale": True, "name": "Test CN"}}
        with patch.object(serve, "fetch_quotes", return_value=quotes):
            status, account_payload = self._get("/api/trade/account")
            self.assertEqual(200, status)
            accounts = {account["market"]: account for account in account_payload["accounts"]}
            self.assertIsNone(accounts["CN"]["positions_value"])
            self.assertIsNone(accounts["CN"]["equity"])
            self.assertFalse(accounts["CN"]["valuation_complete"])
            self.assertEqual([{"symbol": "600519.SS", "status": "stale"}], accounts["CN"]["unavailable_quotes"])
            self.assertIsNone(accounts["US"]["positions_value"])
            self.assertIsNone(accounts["US"]["equity"])
            self.assertFalse(accounts["US"]["valuation_complete"])
            self.assertEqual([{"symbol": "AAPL", "status": "missing"}], accounts["US"]["unavailable_quotes"])

            status, positions_payload = self._get("/api/trade/positions")
            self.assertEqual(200, status)
            positions = {position["symbol"]: position for position in positions_payload["positions"]}
            self.assertEqual("stale", positions["600519.SS"]["quote_status"])
            self.assertIsNone(positions["600519.SS"]["market_value"])
            self.assertIsNone(positions["600519.SS"]["unrealized_pnl"])
            self.assertEqual("missing", positions["AAPL"]["quote_status"])
            self.assertIsNone(positions["AAPL"]["market_value"])
            self.assertIsNone(positions["AAPL"]["unrealized_pnl"])

    def test_only_cn_buy_is_locked_against_same_day_sell(self):
        conn = serve.paper_db()
        try:
            for market, symbol, name, qty in (
                ("CN", "600001.SS", "Test CN intraday", 100),
                ("HK", "0700.HK", "Test HK intraday", 1),
                ("US", "MSFT", "Test US intraday", 1),
            ):
                serve._trade_apply_buy(conn, market, symbol, name, qty, 10.0, 0.0)
                position = serve._trade_get_position(conn, market, symbol)
                if market == "CN":
                    self.assertEqual(0, position["sellable_qty"])
                    with self.assertRaises(serve.TradeError) as error:
                        serve._paper_validate_funds_position(
                            conn, market, symbol, "sell", qty, 10.0, fee=0.0)
                    self.assertEqual("insufficient_position", error.exception.code)
                    self.assertIn("T+1", error.exception.msg)
                else:
                    self.assertEqual(qty, position["sellable_qty"])
                    serve._paper_validate_funds_position(
                        conn, market, symbol, "sell", qty, 10.0, fee=0.0)
                    serve._trade_apply_sell(conn, market, symbol, qty, 10.0, 0.0)
                    self.assertIsNone(serve._trade_get_position(conn, market, symbol))
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
