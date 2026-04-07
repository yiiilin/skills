from __future__ import annotations

import unittest
from pathlib import Path

VIEWER_DIR = Path(__file__).resolve().parents[1]
STATIC_DIR = VIEWER_DIR / "static"
INDEX_HTML = STATIC_DIR / "index.html"
APP_JS = STATIC_DIR / "app.js"
STYLES_CSS = STATIC_DIR / "styles.css"


def _read_text(path: Path) -> str:
    if not path.exists():
        raise AssertionError(f"Expected asset to exist: {path}")
    return path.read_text(encoding="utf-8")


class FrontendAssetContractTests(unittest.TestCase):
    def test_index_html_defines_core_regions_and_links_assets(self) -> None:
        html = _read_text(INDEX_HTML)

        self.assertIn('id="overview"', html)
        self.assertIn('id="dag"', html)
        self.assertIn('id="queues"', html)
        self.assertIn('id="item-detail"', html)
        self.assertIn('id="mermaid-reference"', html)
        self.assertIn('id="warnings"', html)
        self.assertIn('id="warnings-empty"', html)
        self.assertIn('id="warnings-list"', html)
        self.assertIn('id="stale-banner"', html)
        self.assertIn('id="error-state"', html)
        self.assertIn('id="error-message"', html)
        self.assertIn('href="styles.css"', html)
        self.assertIn('src="app.js"', html)

    def test_app_js_declares_snapshot_polling_and_named_renderers(self) -> None:
        js = _read_text(APP_JS)

        self.assertIn('fetch("/snapshot")', js)
        self.assertIn('function renderOverview', js)
        self.assertIn('function renderDag', js)
        self.assertIn('function renderMermaidReference', js)
        self.assertIn('function renderQueues', js)
        self.assertIn('function renderItemDetail', js)
        self.assertIn('function selectItem', js)
        self.assertIn('function isFirstLoadErrorShell', js)
        self.assertIn('function pickSelectedItemId', js)
        self.assertIn('function readHashSelectedItemId', js)
        self.assertIn('setInterval', js)

    def test_styles_css_includes_status_classes_and_panel_shells(self) -> None:
        css = _read_text(STYLES_CSS)

        for expected_token in (
            '.status-blocked',
            '.status-ready',
            '.status-active',
            '.status-implemented',
            '.status-review-queued',
            '.status-in-review',
            '.status-changes-requested',
            '.status-done',
            '.status-unknown',
            '.queue-column',
            '.banner',
            '#item-detail',
        ):
            self.assertIn(expected_token, css)


if __name__ == "__main__":
    unittest.main()
