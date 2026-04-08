from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

VIEWER_DIR = Path(__file__).resolve().parents[1]
SERVE_PATH = VIEWER_DIR / "serve.py"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
SAMPLE_FIXTURE = FIXTURES_DIR / "sample_checklist.md"
HTTP_TIMEOUT_SECONDS = 1.0

EMPTY_COUNTS = {
    "blocked": 0,
    "ready": 0,
    "active": 0,
    "implemented": 0,
    "review-queued": 0,
    "in-review": 0,
    "changes-requested": 0,
    "done": 0,
}

EMPTY_QUEUES = {
    "blocked": [],
    "ready": [],
    "active": [],
    "implemented": [],
    "review_queued": [],
    "in_review": [],
    "changes_requested": [],
    "done": [],
}


def _load_module(name: str, path: Path):
    if not path.exists():
        raise ImportError(f"{name} module not found at {path}")

    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"unable to load {name} module from {path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


serve_module = _load_module("strict_review_viewer_serve", SERVE_PATH)


class ServeViewerContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

    def start_server(self, checklist_path: Path):
        server = serve_module.create_server(
            checklist_path=checklist_path,
            host="127.0.0.1",
            port=0,
            state_dir=Path(self.temp_dir.name),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def get_response(self, url: str) -> tuple[int, object, bytes]:
        with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return response.getcode(), response.headers, response.read()

    def get_json(self, url: str) -> tuple[int, dict[str, object]]:
        status, _headers, body = self.get_response(url)
        return status, json.loads(body.decode("utf-8"))

    def get_text(self, url: str) -> tuple[int, object, str]:
        status, headers, body = self.get_response(url)
        return status, headers, body.decode("utf-8")

    def assert_http_error_status(self, url: str, expected_status: int) -> None:
        with self.assertRaises(urllib.error.HTTPError) as context:
            urllib.request.urlopen(url, timeout=HTTP_TIMEOUT_SECONDS)

        self.assertEqual(expected_status, context.exception.code)

    def test_create_server_accepts_wildcard_host(self) -> None:
        server = serve_module.create_server(
            checklist_path=SAMPLE_FIXTURE,
            host="0.0.0.0",
            port=0,
            state_dir=Path(self.temp_dir.name),
        )
        self.addCleanup(server.server_close)

        self.assertEqual("0.0.0.0", server.server_address[0])

    def test_create_server_rejects_non_loopback_non_wildcard_host(self) -> None:
        with self.assertRaises(ValueError):
            serve_module.create_server(
                checklist_path=SAMPLE_FIXTURE,
                host="192.168.1.10",
                port=0,
                state_dir=Path(self.temp_dir.name),
            )

    def test_health_returns_ok_response(self) -> None:
        server = self.start_server(SAMPLE_FIXTURE)

        status, payload = self.get_json(server.url + "/health")

        self.assertEqual(200, status)
        self.assertEqual({"ok": True}, payload)

    def test_snapshot_returns_fresh_snapshot_json(self) -> None:
        server = self.start_server(SAMPLE_FIXTURE)

        status, payload = self.get_json(server.url + "/snapshot")

        self.assertEqual(200, status)
        self.assertFalse(payload["stale"])
        self.assertEqual("", payload["error"])
        self.assertIn("counts", payload)
        self.assertIn("queues", payload)
        self.assertIn("meta", payload)
        self.assertIn("warnings", payload)

    def test_snapshot_returns_last_good_payload_when_refresh_later_fails(self) -> None:
        checklist_path = Path(self.temp_dir.name) / "checklist.md"
        checklist_path.write_text(SAMPLE_FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
        server = self.start_server(checklist_path)

        _status, first_payload = self.get_json(server.url + "/snapshot")
        checklist_path.write_bytes(b"\xff")

        status, second_payload = self.get_json(server.url + "/snapshot")

        self.assertEqual(200, status)
        self.assertFalse(first_payload["stale"])
        self.assertTrue(second_payload["stale"])
        error_message = second_payload["error"]
        self.assertIn("utf-8", error_message.lower())
        self.assertEqual(
            {
                **first_payload,
                "warnings": [
                    *first_payload["warnings"],
                    {
                        "code": "snapshot_error",
                        "message": error_message,
                        "heading": None,
                        "key": None,
                    },
                ],
                "stale": True,
                "error": error_message,
            },
            second_payload,
        )

    def test_snapshot_returns_error_shell_when_first_load_fails(self) -> None:
        missing_checklist = Path(self.temp_dir.name) / "missing-checklist.md"
        server = self.start_server(missing_checklist)

        status, payload = self.get_json(server.url + "/snapshot")

        self.assertEqual(200, status)
        self.assertTrue(payload["stale"])
        self.assertTrue(payload["error"])
        self.assertEqual(EMPTY_COUNTS, payload["counts"])
        self.assertEqual(EMPTY_QUEUES, payload["queues"])
        self.assertEqual([], payload["items"])
        self.assertEqual([], payload["dag"]["nodes"])
        self.assertEqual([], payload["dag"]["edges"])
        self.assertEqual("snapshot_error", payload["warnings"][0]["code"])

    def test_root_serves_index_html(self) -> None:
        server = self.start_server(SAMPLE_FIXTURE)

        status, headers, body = self.get_text(server.url + "/")

        self.assertEqual(200, status)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn("<title>Strict Review Progress Viewer</title>", body)

    def test_static_asset_serves_javascript_with_content_type(self) -> None:
        server = self.start_server(SAMPLE_FIXTURE)

        status, headers, body = self.get_text(server.url + "/static/app.js")

        self.assertEqual(200, status)
        self.assertIn("javascript", headers["Content-Type"])
        self.assertIn('fetch("/snapshot")', body)

    def test_static_requests_reject_missing_assets_and_escape_attempts(self) -> None:
        server = self.start_server(SAMPLE_FIXTURE)

        for path in (
            "/static/missing.js",
            "/static/%2e%2e/serve.py",
            "/static/..%2fserve.py",
            "/../serve.py",
        ):
            with self.subTest(path=path):
                self.assert_http_error_status(server.url + path, 404)

    def test_state_file_for_checklist_is_deterministic_and_uses_temp_dir(self) -> None:
        checklist_path = Path(self.temp_dir.name) / "sample.md"
        other_checklist_path = Path(self.temp_dir.name) / "other.md"

        first = serve_module.state_file_for_checklist(checklist_path)
        second = serve_module.state_file_for_checklist(checklist_path)
        third = serve_module.state_file_for_checklist(other_checklist_path)

        self.assertEqual(first, second)
        self.assertNotEqual(first, third)
        self.assertIn(Path(tempfile.gettempdir()), first.parents)


class ServeViewerIdleTimeoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

    def start_server(
        self,
        *,
        idle_timeout_seconds: float = 0.4,
        watchdog_interval_seconds: float = 0.05,
    ):
        server = serve_module.create_server(
            checklist_path=SAMPLE_FIXTURE,
            host="127.0.0.1",
            port=0,
            state_dir=Path(self.temp_dir.name),
            idle_timeout_seconds=idle_timeout_seconds,
            watchdog_interval_seconds=watchdog_interval_seconds,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server, thread

    def get_json(self, url: str) -> tuple[int, dict[str, object]]:
        with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return response.getcode(), json.loads(response.read().decode("utf-8"))

    def get_text(self, url: str) -> tuple[int, object, str]:
        with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return response.getcode(), response.headers, response.read().decode("utf-8")

    def assert_thread_stops_within(
        self,
        thread: threading.Thread,
        timeout: float,
        message: str | None = None,
    ) -> float:
        started = time.monotonic()
        thread.join(timeout)
        elapsed = time.monotonic() - started
        self.assertFalse(
            thread.is_alive(),
            message or f"server thread remained alive after {timeout:.2f}s",
        )
        return elapsed

    def test_create_server_stores_idle_timeout_configuration(self) -> None:
        server = serve_module.create_server(
            checklist_path=SAMPLE_FIXTURE,
            host="127.0.0.1",
            port=0,
            state_dir=Path(self.temp_dir.name),
            idle_timeout_seconds=1.5,
            watchdog_interval_seconds=0.1,
        )
        self.addCleanup(server.server_close)

        self.assertEqual(1.5, server.idle_timeout_seconds)
        self.assertEqual(0.1, server.watchdog_interval_seconds)

    def test_parse_args_keeps_cli_shape_and_default_idle_timeout_enabled(self) -> None:
        args = serve_module.parse_args(["--checklist", str(SAMPLE_FIXTURE)])

        self.assertEqual(str(SAMPLE_FIXTURE), args.checklist)
        self.assertEqual("0.0.0.0", args.host)
        self.assertEqual(0, args.port)

        server = serve_module.create_server(
            checklist_path=Path(args.checklist),
            host=args.host,
            port=args.port,
            state_dir=Path(self.temp_dir.name),
        )
        self.addCleanup(server.server_close)

        self.assertEqual(1800.0, server.idle_timeout_seconds)
        self.assertGreater(server.watchdog_interval_seconds, 0.0)

    def test_is_activity_path_classifies_viewer_routes(self) -> None:
        for path in ("/", "/snapshot", "/static/app.js", "/app.js"):
            with self.subTest(path=path):
                self.assertTrue(serve_module.is_activity_path(path))

        self.assertFalse(serve_module.is_activity_path("/health"))

    def test_watchdog_shuts_down_server_after_idle_timeout_without_activity(self) -> None:
        _server, thread = self.start_server()

        elapsed = self.assert_thread_stops_within(thread, timeout=1.5)

        self.assertGreaterEqual(elapsed, 0.25)
        self.assertLess(elapsed, 1.2)

    def test_snapshot_requests_keep_server_alive_past_base_timeout(self) -> None:
        server, thread = self.start_server()

        status, payload = self.get_json(server.url + "/snapshot")
        self.assertEqual(200, status)
        self.assertFalse(payload["stale"])

        time.sleep(0.25)

        status, payload = self.get_json(server.url + "/snapshot")
        self.assertEqual(200, status)
        self.assertFalse(payload["stale"])

        time.sleep(0.22)

        self.assertTrue(thread.is_alive(), "snapshot requests should extend the idle deadline")
        self.assert_thread_stops_within(thread, timeout=2.0)

    def test_health_requests_do_not_keep_server_alive_past_base_timeout(self) -> None:
        server, thread = self.start_server()

        status, payload = self.get_json(server.url + "/health")
        self.assertEqual(200, status)
        self.assertEqual({"ok": True}, payload)

        time.sleep(0.25)

        status, payload = self.get_json(server.url + "/health")
        self.assertEqual(200, status)
        self.assertEqual({"ok": True}, payload)

        time.sleep(0.22)

        self.assert_thread_stops_within(
            thread,
            timeout=0.15,
            message="health requests must not extend the idle deadline",
        )

    def test_static_asset_request_refreshes_activity_timestamp(self) -> None:
        server, thread = self.start_server()

        time.sleep(0.25)

        status, headers, body = self.get_text(server.url + "/static/app.js")
        self.assertEqual(200, status)
        self.assertIn("javascript", headers["Content-Type"])
        self.assertIn('fetch("/snapshot")', body)

        time.sleep(0.22)

        self.assertTrue(thread.is_alive(), "/static/app.js should extend the idle deadline")
        self.assert_thread_stops_within(thread, timeout=2.0)

    def test_top_level_asset_request_refreshes_activity_timestamp(self) -> None:
        server, thread = self.start_server()

        time.sleep(0.25)

        status, headers, body = self.get_text(server.url + "/app.js")
        self.assertEqual(200, status)
        self.assertIn("javascript", headers["Content-Type"])
        self.assertIn('fetch("/snapshot")', body)

        time.sleep(0.22)

        self.assertTrue(thread.is_alive(), "/app.js should extend the idle deadline")
        self.assert_thread_stops_within(thread, timeout=2.0)

    def test_watchdog_check_tolerates_single_internal_exception_and_shutdowns_once(self) -> None:
        server = serve_module.create_server(
            checklist_path=SAMPLE_FIXTURE,
            host="127.0.0.1",
            port=0,
            state_dir=Path(self.temp_dir.name),
            idle_timeout_seconds=0.2,
            watchdog_interval_seconds=0.05,
        )
        self.addCleanup(server.server_close)
        server.last_activity_monotonic = 100.0

        with mock.patch.object(server, "shutdown") as shutdown:
            with mock.patch.object(
                serve_module.time,
                "monotonic",
                side_effect=[RuntimeError("boom"), 100.35, 100.4],
            ):
                self.assertFalse(server._watchdog_check_once())
                self.assertTrue(server._watchdog_check_once())
                self.assertFalse(server._watchdog_check_once())

        shutdown.assert_called_once_with()

    def test_watchdog_retries_idle_shutdown_after_transient_shutdown_error(self) -> None:
        server = serve_module.create_server(
            checklist_path=SAMPLE_FIXTURE,
            host="127.0.0.1",
            port=0,
            state_dir=Path(self.temp_dir.name),
            idle_timeout_seconds=0.2,
            watchdog_interval_seconds=0.05,
        )
        self.addCleanup(server.server_close)

        with (
            mock.patch.object(server, "seconds_since_last_activity", return_value=0.5),
            mock.patch.object(server, "shutdown", side_effect=[RuntimeError("boom"), None]) as shutdown,
        ):
            self.assertFalse(server._watchdog_check_once())
            self.assertTrue(server._watchdog_check_once())
            self.assertFalse(server._watchdog_check_once())

        self.assertEqual(2, shutdown.call_count)


class ServeViewerCliTests(unittest.TestCase):
    def test_main_starts_server_from_cli_arguments_and_prints_bound_url(self) -> None:
        fake_server = mock.Mock()
        fake_server.url = "http://127.0.0.1:43123"
        stdout = io.StringIO()

        with (
            mock.patch.object(serve_module, "create_server", return_value=fake_server) as create_server,
            mock.patch.object(sys, "argv", [
                str(SERVE_PATH),
                "--checklist",
                str(SAMPLE_FIXTURE),
                "--port",
                "0",
            ]),
            contextlib.redirect_stdout(stdout),
        ):
            serve_module.main()

        create_server.assert_called_once_with(
            checklist_path=SAMPLE_FIXTURE,
            host="0.0.0.0",
            port=0,
        )
        fake_server.serve_forever.assert_called_once_with()
        self.assertEqual("http://127.0.0.1:43123\n", stdout.getvalue())

    def test_main_accepts_explicit_host_argument(self) -> None:
        fake_server = mock.Mock()
        fake_server.url = "http://[::1]:43123"

        with (
            mock.patch.object(serve_module, "create_server", return_value=fake_server) as create_server,
            mock.patch.object(sys, "argv", [
                str(SERVE_PATH),
                "--checklist",
                str(SAMPLE_FIXTURE),
                "--host",
                "::1",
                "--port",
                "8080",
            ]),
        ):
            serve_module.main()

        create_server.assert_called_once_with(
            checklist_path=SAMPLE_FIXTURE,
            host="::1",
            port=8080,
        )
        fake_server.serve_forever.assert_called_once_with()

    def test_main_accepts_explicit_wildcard_host_argument(self) -> None:
        fake_server = mock.Mock()
        fake_server.url = "http://0.0.0.0:43123"

        with (
            mock.patch.object(serve_module, "create_server", return_value=fake_server) as create_server,
            mock.patch.object(sys, "argv", [
                str(SERVE_PATH),
                "--checklist",
                str(SAMPLE_FIXTURE),
                "--host",
                "0.0.0.0",
                "--port",
                "8080",
            ]),
        ):
            serve_module.main()

        create_server.assert_called_once_with(
            checklist_path=SAMPLE_FIXTURE,
            host="0.0.0.0",
            port=8080,
        )
        fake_server.serve_forever.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
