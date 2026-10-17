import importlib.util
import shutil
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGES = (
    "app/index.html",
    "app/trade.html",
    "app/markets.html",
    "app/race.html",
    "app/spider.html",
    "app/finance-observatory.html",
    "app/settings.html",
    "app/rea.html",
    "public/finance.html",
)
CHROMIUM = shutil.which("chromium") or shutil.which("chromium-browser")
PLAYWRIGHT_AVAILABLE = importlib.util.find_spec("playwright") is not None


class BackToTopTests(unittest.TestCase):
    def test_shared_button_is_loaded_on_all_primary_pages(self):
        for relative in PAGES:
            with self.subTest(page=relative):
                text = (ROOT / relative).read_text(encoding="utf-8")
                self.assertIn('<script src="/app/back-to-top.js"></script>', text)

    def test_shared_script_has_accessible_reduced_motion_and_safe_area_contract(self):
        script = (ROOT / "app/back-to-top.js").read_text(encoding="utf-8")
        self.assertIn("createElement('button')", script)
        self.assertIn("aria-label", script)
        self.assertIn("返回顶部", script)
        self.assertIn("Back to top", script)
        self.assertIn("env(safe-area-inset-bottom", script)
        self.assertIn("env(safe-area-inset-right", script)
        self.assertIn("prefers-reduced-motion: reduce", script)
        self.assertIn("window.scrollTo({ top: 0", script)
        self.assertIn("y > 320", script)

    @unittest.skipUnless(CHROMIUM and PLAYWRIGHT_AVAILABLE,
                         "Chromium and Python Playwright are required")
    def test_narrow_and_wide_viewports(self):
        from playwright.sync_api import sync_playwright

        fixture = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<style>html,body{margin:0}main{height:4200px;background:linear-gradient(#fff,#ddd)}
#bottom-control{position:fixed;right:10px;bottom:10px;width:120px;height:40px;background:#ddd}</style></head>
<body><main></main><button id="bottom-control" type="button">existing control</button></body></html>"""
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=CHROMIUM,
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            try:
                for width, height in ((390, 844), (1440, 900)):
                    with self.subTest(viewport=f"{width}x{height}"):
                        page = browser.new_page(viewport={"width": width, "height": height})
                        page.emulate_media(reduced_motion="reduce")
                        page.set_content(fixture)
                        page.add_script_tag(path=str(ROOT / "app/back-to-top.js"))
                        page.evaluate("window.scrollTo(0, 500)")
                        button = page.locator("#cue-back-to-top")
                        button.wait_for(state="visible", timeout=3000)
                        self.assertEqual("返回顶部", button.get_attribute("aria-label"))
                        self.assertIn("返回顶部", button.inner_text())

                        page.locator("#bottom-control").focus()
                        page.keyboard.press("Tab")
                        self.assertTrue(button.evaluate("el => document.activeElement === el"),
                                        "native Tab navigation must reach the top button")

                        rect = button.bounding_box()
                        control = page.locator("#bottom-control").bounding_box()
                        self.assertIsNotNone(rect)
                        self.assertIsNotNone(control)
                        self.assertLessEqual(rect["x"], width)
                        self.assertGreaterEqual(rect["x"], 0)
                        self.assertLessEqual(rect["y"] + rect["height"], height)
                        self.assertTrue(
                            rect["y"] + rect["height"] <= control["y"] or rect["y"] >= control["y"] + control["height"],
                            "floating button must not overlap the existing bottom control",
                        )
                        self.assertLessEqual(
                            page.evaluate("document.documentElement.scrollWidth"), width,
                            "button must not cause horizontal overflow",
                        )

                        page.keyboard.press("Enter")
                        page.wait_for_function("window.scrollY < 3", timeout=3000)
                        page.close()
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
