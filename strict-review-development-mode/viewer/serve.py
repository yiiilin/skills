from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import ipaddress
import json
import mimetypes
import socket
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from urllib.parse import unquote, urlsplit

MODULE_DIR = Path(__file__).resolve().parent
STATIC_DIR = MODULE_DIR / "static"

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

DEFAULT_IDLE_TIMEOUT_SECONDS = 1800.0
DEFAULT_WATCHDOG_INTERVAL_SECONDS = 5.0


def _is_root_asset_alias(path: str) -> bool:
    return path.count("/") == 1 and "." in path.rsplit("/", 1)[-1]


def is_activity_path(path: str) -> bool:
    if path == "/health":
        return False
    if path in {"/", "/snapshot"}:
        return True
    if path.startswith("/static/"):
        return True
    return _is_root_asset_alias(path)


def _load_local_module(module_basename: str) -> ModuleType:
    module_path = MODULE_DIR / f"{module_basename}.py"
    spec = importlib.util.spec_from_file_location(f"{__name__}_{module_basename}", module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"unable to load {module_basename} module from {module_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


parser_module = _load_local_module("parser")
snapshot_module = _load_local_module("snapshot")
parse_file = parser_module.parse_file
build_snapshot = snapshot_module.build_snapshot


def _build_empty_meta() -> dict[str, object]:
    return {
        "title": "Strict Review Progress Viewer",
        "dag_degraded": False,
        "implementation_concurrency": 0,
        "reviewer_concurrency": 0,
    }


def _build_empty_counts() -> dict[str, int]:
    return dict(EMPTY_COUNTS)


def _build_empty_queues() -> dict[str, list[object]]:
    return {key: [] for key in EMPTY_QUEUES}


def _build_snapshot_error_warning(error_message: str) -> dict[str, object]:
    return {
        "code": "snapshot_error",
        "message": error_message,
        "heading": None,
        "key": None,
    }


def build_error_shell(error_message: str) -> dict[str, object]:
    return {
        "meta": _build_empty_meta(),
        "counts": _build_empty_counts(),
        "queues": _build_empty_queues(),
        "dag": {
            "nodes": [],
            "edges": [],
            "mermaid_reference": "",
        },
        "items": [],
        "warnings": [_build_snapshot_error_warning(error_message)],
        "stale": True,
        "error": error_message,
    }


def _normalize_snapshot_payload(payload: dict[str, object]) -> dict[str, object]:
    normalized = copy.deepcopy(payload)
    normalized.setdefault("meta", _build_empty_meta())
    normalized.setdefault("counts", _build_empty_counts())
    normalized.setdefault("queues", _build_empty_queues())
    normalized.setdefault("dag", {"nodes": [], "edges": [], "mermaid_reference": ""})
    normalized.setdefault("items", [])
    normalized.setdefault("warnings", [])
    normalized["stale"] = False
    normalized["error"] = ""
    return normalized


def state_file_for_checklist(
    checklist_path: str | Path,
    state_dir: str | Path | None = None,
) -> Path:
    canonical_path = str(Path(checklist_path).expanduser().resolve())
    digest = hashlib.sha256(canonical_path.encode("utf-8")).hexdigest()
    root_dir = Path(state_dir).expanduser().resolve() if state_dir is not None else Path(tempfile.gettempdir())
    return root_dir / f"strict-review-progress-viewer-{digest}.json"


class ViewerServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        checklist_path: Path,
        state_dir: Path | None = None,
        *,
        idle_timeout_seconds: float = DEFAULT_IDLE_TIMEOUT_SECONDS,
        watchdog_interval_seconds: float = DEFAULT_WATCHDOG_INTERVAL_SECONDS,
    ) -> None:
        self.checklist_path = Path(checklist_path)
        self.state_dir = Path(state_dir) if state_dir is not None else None
        self.state_file_path = state_file_for_checklist(self.checklist_path, self.state_dir)
        self.last_good_snapshot: dict[str, object] | None = None
        self.idle_timeout_seconds = float(idle_timeout_seconds)
        self.watchdog_interval_seconds = float(watchdog_interval_seconds)
        if self.idle_timeout_seconds <= 0:
            raise ValueError("idle_timeout_seconds must be positive")
        if self.watchdog_interval_seconds <= 0:
            raise ValueError("watchdog_interval_seconds must be positive")
        self._snapshot_lock = threading.Lock()
        self._activity_lock = threading.Lock()
        self._watchdog_start_lock = threading.Lock()
        self._idle_shutdown_lock = threading.Lock()
        self._watchdog_stop_event = threading.Event()
        self._watchdog_thread: threading.Thread | None = None
        self._idle_shutdown_started = False
        self.last_activity_monotonic = time.monotonic()
        if ":" in server_address[0]:
            self.address_family = socket.AF_INET6
        super().__init__(server_address, ViewerRequestHandler)

    @property
    def url(self) -> str:
        host, port = self.server_address[:2]
        host_text = f"[{host}]" if isinstance(host, str) and ":" in host and not host.startswith("[") else host
        return f"http://{host_text}:{port}"

    def serve_forever(self, poll_interval: float = 0.5) -> None:
        self.mark_activity()
        self._start_watchdog()
        effective_poll_interval = min(poll_interval, self.watchdog_interval_seconds)
        try:
            super().serve_forever(poll_interval=effective_poll_interval)
        finally:
            self._watchdog_stop_event.set()

    def shutdown(self) -> None:
        self._watchdog_stop_event.set()
        super().shutdown()

    def mark_activity(self) -> None:
        with self._activity_lock:
            self.last_activity_monotonic = time.monotonic()

    def seconds_since_last_activity(self) -> float:
        with self._activity_lock:
            last_activity_monotonic = self.last_activity_monotonic
        return time.monotonic() - last_activity_monotonic

    def _start_watchdog(self) -> None:
        with self._watchdog_start_lock:
            if self._watchdog_thread is not None and self._watchdog_thread.is_alive():
                return
            self._watchdog_thread = threading.Thread(
                target=self._run_watchdog,
                name="viewer-idle-watchdog",
                daemon=True,
            )
            self._watchdog_thread.start()

    def _run_watchdog(self) -> None:
        while not self._watchdog_stop_event.wait(self.watchdog_interval_seconds):
            try:
                self._watchdog_check_once()
            except Exception:
                continue

    def _watchdog_check_once(self) -> bool:
        try:
            if self.seconds_since_last_activity() < self.idle_timeout_seconds:
                return False
            return self._shutdown_for_idle()
        except Exception:
            return False

    def _shutdown_for_idle(self) -> bool:
        with self._idle_shutdown_lock:
            if self._idle_shutdown_started:
                return False
            self._idle_shutdown_started = True
        try:
            self.shutdown()
        except Exception:
            with self._idle_shutdown_lock:
                self._idle_shutdown_started = False
            raise
        return True

    def build_snapshot_payload(self) -> dict[str, object]:
        with self._snapshot_lock:
            try:
                payload = _normalize_snapshot_payload(build_snapshot(parse_file(self.checklist_path)))
            except Exception as exc:  # pragma: no cover - behavior asserted through contract tests
                error_message = str(exc) or exc.__class__.__name__
                if self.last_good_snapshot is not None:
                    fallback = copy.deepcopy(self.last_good_snapshot)
                    fallback_warnings = list(fallback.get("warnings", []))
                    fallback["warnings"] = [
                        *fallback_warnings,
                        _build_snapshot_error_warning(error_message),
                    ]
                    fallback["stale"] = True
                    fallback["error"] = error_message
                    return fallback
                return build_error_shell(error_message)

            self.last_good_snapshot = copy.deepcopy(payload)
            return payload


class ViewerRequestHandler(BaseHTTPRequestHandler):
    server: ViewerServer

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if is_activity_path(path):
            self.server.mark_activity()
        if path == "/health":
            self._send_json(200, {"ok": True})
            return
        if path == "/snapshot":
            self._send_json(200, self.server.build_snapshot_payload())
            return
        if path == "/":
            self._send_file(STATIC_DIR / "index.html")
            return

        static_path = self._resolve_static_path(path)
        if static_path is not None:
            self._send_file(static_path)
            return

        self.send_error(404)

    def log_message(self, format: str, *args: object) -> None:
        return

    def _resolve_static_path(self, path: str) -> Path | None:
        if path.startswith("/static/"):
            relative_path = path.removeprefix("/static/")
        elif _is_root_asset_alias(path):
            relative_path = path.removeprefix("/")
        else:
            return None

        decoded_path = Path(unquote(relative_path))
        if any(part in {"", ".", ".."} for part in decoded_path.parts):
            return None

        candidate = (STATIC_DIR / decoded_path).resolve()
        try:
            candidate.relative_to(STATIC_DIR.resolve())
        except ValueError:
            return None

        return candidate if candidate.is_file() else None

    def _send_file(self, file_path: Path) -> None:
        if not file_path.is_file():
            self.send_error(404)
            return

        body = file_path.read_bytes()
        content_type = self._guess_content_type(file_path)
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _guess_content_type(self, file_path: Path) -> str:
        if file_path.suffix == ".html":
            return "text/html; charset=utf-8"
        if file_path.suffix == ".js":
            return "application/javascript; charset=utf-8"
        if file_path.suffix == ".css":
            return "text/css; charset=utf-8"
        guessed_type, _encoding = mimetypes.guess_type(file_path.name)
        return guessed_type or "application/octet-stream"

    def _send_json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _is_allowed_bind_host(host: str) -> bool:
    normalized = host.strip()
    if not normalized:
        return False
    if normalized == "0.0.0.0":
        return True
    if normalized.lower() == "localhost":
        return True
    if normalized.startswith("[") and normalized.endswith("]"):
        normalized = normalized[1:-1]
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False

def create_server(
    *,
    checklist_path: str | Path,
    host: str = "0.0.0.0",
    port: int = 0,
    state_dir: str | Path | None = None,
    idle_timeout_seconds: float = DEFAULT_IDLE_TIMEOUT_SECONDS,
    watchdog_interval_seconds: float = DEFAULT_WATCHDOG_INTERVAL_SECONDS,
) -> ViewerServer:
    if not _is_allowed_bind_host(host):
        raise ValueError("Viewer server must bind to a loopback host or 0.0.0.0")

    return ViewerServer(
        (host, port),
        checklist_path=Path(checklist_path),
        state_dir=Path(state_dir) if state_dir is not None else None,
        idle_timeout_seconds=idle_timeout_seconds,
        watchdog_interval_seconds=watchdog_interval_seconds,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve the strict review progress viewer")
    parser.add_argument("--checklist", required=True, help="Path to the checklist markdown file")
    parser.add_argument("--host", default="0.0.0.0", help="Loopback host or 0.0.0.0 to bind")
    parser.add_argument("--port", type=int, default=0, help="Port to bind (use 0 for any available port)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    server = create_server(
        checklist_path=Path(args.checklist),
        host=args.host,
        port=args.port,
    )
    print(server.url)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
