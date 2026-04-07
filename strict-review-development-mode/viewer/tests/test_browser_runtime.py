from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

VIEWER_DIR = Path(__file__).resolve().parents[1]
STATIC_DIR = VIEWER_DIR / "static"
PARSER_PATH = VIEWER_DIR / "parser.py"
SNAPSHOT_PATH = VIEWER_DIR / "snapshot.py"
SAMPLE_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sample_checklist.md"


class BrowserRuntimeSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        browser_path = shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome") or shutil.which("google-chrome-stable")
        driver_path = shutil.which("chromedriver") or shutil.which("geckodriver")
        selenium_spec = importlib.util.find_spec("selenium")
        if browser_path is None or driver_path is None or selenium_spec is None:
            raise unittest.SkipTest(
                "browser runtime unavailable: requires selenium plus chromium/chrome and chromedriver/geckodriver"
            )

    def test_runtime_renders_snapshot_click_updates_detail_and_poll_refreshes(self) -> None:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options as ChromeOptions
        from selenium.webdriver.chrome.service import Service as ChromeService
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        parser = _load_module("strict_review_viewer_parser_for_browser_runtime", PARSER_PATH)
        snapshot_module = _load_module("strict_review_viewer_snapshot_for_browser_runtime", SNAPSHOT_PATH)

        with tempfile.TemporaryDirectory() as tmp_dir:
            checklist_path = Path(tmp_dir) / "sample_checklist.md"
            checklist_path.write_text(SAMPLE_FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")

            handler = _build_handler(static_dir=STATIC_DIR, checklist_path=checklist_path, parser=parser, snapshot_module=snapshot_module)
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"

            browser_path = shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome") or shutil.which("google-chrome-stable")
            driver_path = shutil.which("chromedriver")
            if browser_path is None or driver_path is None:
                server.shutdown()
                thread.join(timeout=5)
                self.skipTest("browser runtime unavailable: chromium/chromedriver not installed")

            options = ChromeOptions()
            options.binary_location = browser_path
            options.add_argument("--headless=new")
            options.add_argument("--disable-gpu")
            options.add_argument("--no-sandbox")
            options.add_argument("--window-size=1440,1200")
            service = ChromeService(executable_path=driver_path)
            driver = webdriver.Chrome(service=service, options=options)
            wait = WebDriverWait(driver, 20)
            try:
                driver.get(base_url + "/#item-1")
                wait.until(lambda current: current.find_element(By.ID, "item-detail").text and "item-1" in current.find_element(By.ID, "item-detail").text)
                self.assertEqual("item-1", driver.execute_script("return window.location.hash.slice(1);"))
                self.assertIn("reviewer_id: reviewer-parser-item-1", driver.find_element(By.CSS_SELECTOR, '[data-item-id="item-1"] .item-meta').text)
                self.assertIn("累计等待时长", driver.find_element(By.ID, "item-detail").text)
                self.assertIn("6min", driver.find_element(By.ID, "item-detail").text)
                self.assertIn("Review Queue (0)", driver.find_element(By.ID, "queues").text)

                mutated_text = checklist_path.read_text(encoding="utf-8")
                mutated_text = mutated_text.replace("- dispatch_status：in-review", "- dispatch_status：done", 1)
                mutated_text = mutated_text.replace("- reviewer_state：slow", "- reviewer_state：done", 1)
                mutated_text = mutated_text.replace("- 累计等待时长：6min", "- 累计等待时长：16min", 1)
                mutated_text = mutated_text.replace("- 超时次数：1", "- 超时次数：2", 1)
                mutated_text = mutated_text.replace("- 审核结论：等待 reviewer 返回", "- 审核结论：review complete", 1)
                checklist_path.write_text(mutated_text, encoding="utf-8")

                wait.until(lambda current: "done" in current.find_element(By.ID, "item-detail").text)
                wait.until(lambda current: "16min" in current.find_element(By.ID, "item-detail").text)
                wait.until(lambda current: "Done (1)" in current.find_element(By.ID, "queues").text)
                wait.until(lambda current: "In Review (0)" in current.find_element(By.ID, "queues").text)
                self.assertIn("reviewer_state: done", driver.find_element(By.CSS_SELECTOR, '[data-item-id="item-1"] .item-meta').text)
                self.assertIn("review complete", driver.find_element(By.ID, "item-detail").text)
                self.assertEqual("item-1", driver.execute_script("return window.location.hash.slice(1);"))
            finally:
                driver.quit()
                server.shutdown()
                thread.join(timeout=5)


def _load_module(name: str, path: Path):
    if not path.exists():
        raise ImportError(f"module not found at {path}")

    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"unable to load module from {path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build_handler(*, static_dir: Path, checklist_path: Path, parser, snapshot_module):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            if path == "/":
                return self._send_file(static_dir / "index.html", "text/html; charset=utf-8")
            if path == "/app.js":
                return self._send_file(static_dir / "app.js", "application/javascript; charset=utf-8")
            if path == "/styles.css":
                return self._send_file(static_dir / "styles.css", "text/css; charset=utf-8")
            if path == "/snapshot":
                snapshot = snapshot_module.build_snapshot(parser.parse_file(checklist_path))
                snapshot["stale"] = False
                return self._send_json(snapshot)
            self.send_error(404)

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            return None

        def _send_file(self, path: Path, content_type: str) -> None:
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_json(self, payload: dict) -> None:
            data = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


if __name__ == "__main__":
    unittest.main()
