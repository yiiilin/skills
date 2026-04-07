from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

APP_JS = Path(__file__).resolve().parents[1] / "static" / "app.js"
INDEX_HTML = Path(__file__).resolve().parents[1] / "static" / "index.html"


def _read_text(path: Path) -> str:
    if not path.exists():
        raise AssertionError(f"Expected asset to exist: {path}")
    return path.read_text(encoding="utf-8")


def _run_app_js_runtime_smoke(*, hash_value: str = "") -> dict[str, object]:
    node_path = shutil.which("node")
    if node_path is None:
        raise unittest.SkipTest("node runtime unavailable")

    js = _read_text(APP_JS)
    runtime_script = """
const vm = require('vm');
const scriptSource = process.argv[1];
const hashValue = process.argv[2] || '';
const eventHandlers = {};

function createElement(tagName) {
  return {
    tagName: String(tagName).toUpperCase(),
    children: [],
    attributes: {},
    className: '',
    textContent: '',
    listeners: {},
    appendChild(child) {
      this.children.push(child);
      return child;
    },
    replaceChildren(...children) {
      this.children = children;
    },
    setAttribute(name, value) {
      this.attributes[name] = String(value);
    },
    getAttribute(name) {
      return this.attributes[name];
    },
    addEventListener(type, handler) {
      this.listeners[type] = handler;
    },
    classList: {
      add() {},
      remove() {},
      contains() { return false; },
    },
  };
}

const elements = new Map();
const document = {
  addEventListener(type, handler) {
    eventHandlers[type] = handler;
  },
  getElementById(id) {
    if (!elements.has(id)) {
      const element = createElement('div');
      element.id = id;
      elements.set(id, element);
    }
    return elements.get(id);
  },
  createElement,
  createElementNS(namespace, tagName) {
    return createElement(tagName);
  },
};

const sandbox = {
  console,
  document,
  window: {
    location: { hash: hashValue },
    addEventListener(type, handler) {
      eventHandlers['window:' + type] = handler;
    },
  },
  location: {
    hash: hashValue,
  },
  fetch: async function () {
    return {
      ok: true,
      json: async function () {
        return {
          meta: { title: 'Runtime Smoke', implementation_concurrency: 0, reviewer_concurrency: 0, dag_degraded: false },
          counts: {},
          queues: {},
          dag: { nodes: [], edges: [], mermaid_reference: '' },
          items: [],
          warnings: [],
          stale: false,
          error: '',
        };
      },
    };
  },
  setInterval: function () { return 1; },
  clearInterval: function () {},
  Date,
  Error,
  RegExp,
  Set,
  Map,
  Array,
  Object,
  String,
  Number,
  Boolean,
  Math,
  JSON,
  encodeURIComponent,
  decodeURIComponent,
};
sandbox.window.document = document;
sandbox.window.fetch = sandbox.fetch;
sandbox.window.setInterval = sandbox.setInterval;
sandbox.window.clearInterval = sandbox.clearInterval;
sandbox.window.console = console;
sandbox.window.encodeURIComponent = encodeURIComponent;
sandbox.window.decodeURIComponent = decodeURIComponent;
sandbox.globalThis = sandbox;

let result;
try {
  vm.createContext(sandbox);
  vm.runInContext(scriptSource, sandbox);
  if (!eventHandlers['DOMContentLoaded']) {
    throw new Error('DOMContentLoaded handler missing');
  }
  eventHandlers['DOMContentLoaded']();
  result = {
    ok: true,
    selectedItemId: vm.runInContext('state.selectedItemId', sandbox),
    hash: sandbox.location.hash,
  };
} catch (error) {
  result = {
    ok: false,
    name: error && error.name ? error.name : 'Error',
    message: error && error.message ? error.message : String(error),
  };
}

process.stdout.write(JSON.stringify(result));
"""
    completed = subprocess.run(
        [node_path, "-e", runtime_script, js, hash_value],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


class FrontendBehaviorContractTests(unittest.TestCase):
    def test_app_js_initializes_without_reference_errors_and_reads_hash_selection(self) -> None:
        result = _run_app_js_runtime_smoke(hash_value="#item-2")

        self.assertTrue(result["ok"], result)
        self.assertEqual("item-2", result["selectedItemId"])

    def test_app_js_handles_blocked_implemented_and_unknown_statuses(self) -> None:
        js = _read_text(APP_JS)

        self.assertRegex(js, r"blocked")
        self.assertRegex(js, r"implemented")
        self.assertIn("status-unknown", js)
        self.assertIn("function getStatusMeta", js)

    def test_app_js_renders_stale_banner_and_first_load_error_shell(self) -> None:
        js = _read_text(APP_JS)
        html = _read_text(INDEX_HTML)

        self.assertIn("function isFirstLoadErrorShell", js)
        self.assertIn("snapshot.stale", js)
        self.assertIn("snapshot.error", js)
        self.assertIn("items.length", js)
        self.assertIn('id="stale-banner"', html)
        self.assertIn('id="error-state"', html)
        self.assertIn('id="error-message"', html)

    def test_app_js_preserves_selected_item_when_possible(self) -> None:
        js = _read_text(APP_JS)

        self.assertIn("function pickSelectedItemId", js)
        self.assertIn("function readHashSelectedItemId", js)
        self.assertIn("selectedItemId", js)
        self.assertRegex(js, r"window\.location\.hash")
        self.assertRegex(js, r"decodeURIComponent")
        self.assertRegex(js, r"item\.item_id\s*===\s*currentItemId")

    def test_app_js_includes_reviewer_timeout_fields_in_queue_and_detail_rendering(self) -> None:
        js = _read_text(APP_JS)

        self.assertIn("reviewer_state", js)
        self.assertIn("reviewer_id", js)
        self.assertIn("累计等待时长", js)
        self.assertIn("超时次数", js)
        self.assertIn("Replacement Reviewer", js)
        self.assertIn("function extractReviewField", js)
        self.assertIn("function renderQueues", js)
        self.assertIn("function renderItemDetail", js)

    def test_app_js_derives_unknown_status_queue_from_items(self) -> None:
        js = _read_text(APP_JS)

        self.assertIn("function getUnknownStatusItems", js)
        self.assertRegex(js, r"!KNOWN_STATUS_SET\.has\(")

    def test_app_js_updates_live_regions_only_when_meaningful_state_changes(self) -> None:
        js = _read_text(APP_JS)

        self.assertIn("function updateWarningsRegion", js)
        self.assertIn("function updateStaleBanner", js)
        self.assertIn("function renderErrorState", js)
        self.assertIn("dom.warningsList", js)
        self.assertIn("dom.warningsEmpty", js)
        self.assertIn("dom.errorMessage", js)
        self.assertIn("replaceChildren()", js)
        self.assertRegex(js, r"if \(dom\.staleBanner\.textContent !== message\)")
        self.assertRegex(js, r"if \(dom\.errorMessage\.textContent !== message\)")


if __name__ == "__main__":
    unittest.main()
