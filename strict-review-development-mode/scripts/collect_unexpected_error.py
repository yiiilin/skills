from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import re
import subprocess
import sys
from typing import Any
import zipfile


SKILL_DIR = Path(__file__).resolve().parents[1]
CONTROLLER_PATH = SKILL_DIR / "controller.py"
DEFAULT_OUTPUT_ROOT = "strict-review-development-mode-error-reports"
SCHEMA_VERSION = "1"

REPORT_CATEGORIES = (
    "controller-error",
    "unexpected-dispatch",
    "workflow-violation",
    "agent-output-mismatch",
    "viewer-error",
    "other",
)

TEXT_FILE_SUFFIXES = {".md", ".txt", ".json", ".log"}

PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)
BEARER_RE = re.compile(r"(?i)\b(Bearer\s+)[A-Za-z0-9._~+/=-]+")
SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|secret|password|passwd|authorization|cookie|session(?:[_-]?id)?|"
    r"access[_-]?token|refresh[_-]?token|(?<!total_)token)\b(\s*[:=]\s*)([^\s`'\"<>]+)"
)
COMMON_SECRET_RE = re.compile(r"\b(sk-[A-Za-z0-9_-]{12,}|ghp_[A-Za-z0-9_]{12,}|xox[baprs]-[A-Za-z0-9-]{12,})\b")


def redact_text(text: str) -> str:
    """对常见密钥、Bearer token 和私钥块做保守脱敏，避免报告包直接泄露凭据。"""

    redacted = PRIVATE_KEY_RE.sub("<redacted-private-key>", text)
    redacted = BEARER_RE.sub(r"\1<redacted>", redacted)
    redacted = SECRET_ASSIGNMENT_RE.sub(r"\1\2<redacted>", redacted)
    redacted = COMMON_SECRET_RE.sub("<redacted-secret>", redacted)
    return redacted


def redact_payload(value: Any) -> Any:
    """递归脱敏 controller JSON，保留结构用于排错。"""

    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact_payload(item) for item in value]
    if isinstance(value, dict):
        return {str(key): redact_payload(item) for key, item in value.items()}
    return value


def collect_unexpected_error(
    checklist_path: Path,
    output_root: Path,
    category: str,
    message: str = "",
    observed: str = "",
    expected: str = "",
    command: str = "",
    include_task_files: bool = False,
    create_zip: bool = False,
    redact: bool = True,
    max_file_bytes: int = 200_000,
) -> dict[str, str]:
    """生成一次意外错误报告包；默认只写本地文件，不上传任何数据。"""

    checklist_path = checklist_path.expanduser()
    output_root = output_root.expanduser()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = _safe_slug(checklist_path.parent.name or checklist_path.stem or "checklist")
    report_dir = _create_unique_report_dir(output_root, timestamp, slug)
    controller_dir = report_dir / "controller"
    controller_dir.mkdir(parents=True, exist_ok=False)

    metadata = _build_metadata(checklist_path, category, message, observed, expected, command, redact)
    _write_json(report_dir / "metadata.json", metadata)

    checklist_text = _read_text_or_error(checklist_path)
    if checklist_text["ok"]:
        rendered = _maybe_redact(str(checklist_text["text"]), redact)
        (report_dir / "checklist.redacted.md").write_text(rendered, encoding="utf-8")
    else:
        (report_dir / "checklist-read-error.txt").write_text(str(checklist_text["error"]), encoding="utf-8")

    validate_run = _run_controller(("validate", "--checklist", str(checklist_path), "--json"), redact)
    cycle_run = _run_controller(("cycle", "--checklist", str(checklist_path), "--json"), redact)
    _write_json(controller_dir / "validate.json", validate_run)
    _write_json(controller_dir / "cycle.json", cycle_run)

    triage_summary = _build_triage_summary(validate_run, cycle_run)
    _write_json(report_dir / "triage-summary.json", triage_summary)

    if include_task_files and checklist_path.parent.exists():
        _copy_task_files(checklist_path, report_dir / "task-files", redact, max_file_bytes)

    (report_dir / "README.md").write_text(
        _build_readme(metadata, triage_summary, include_task_files),
        encoding="utf-8",
    )

    result = {"report_dir": str(report_dir)}
    if create_zip:
        zip_path = _zip_report(report_dir)
        result["zip_path"] = str(zip_path)
    return result


def _build_metadata(
    checklist_path: Path,
    category: str,
    message: str,
    observed: str,
    expected: str,
    command: str,
    redact: bool,
) -> dict[str, Any]:
    cwd = Path.cwd()
    checklist_text = _read_text_or_error(checklist_path)
    checklist_sha256 = ""
    if checklist_text["ok"]:
        checklist_sha256 = hashlib.sha256(str(checklist_text["text"]).encode("utf-8")).hexdigest()

    skill_status_path = str(SKILL_DIR.relative_to(cwd)) if _is_relative_to(SKILL_DIR, cwd) else str(SKILL_DIR)

    return {
        "schema_version": SCHEMA_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "category": category,
        "message": _maybe_redact(message, redact),
        "observed_behavior": _maybe_redact(observed, redact),
        "expected_behavior": _maybe_redact(expected, redact),
        "reproduction_command": _maybe_redact(command, redact),
        "checklist_path": _safe_path_label(checklist_path, cwd),
        "checklist_path_sha256": _path_hash(checklist_path),
        "checklist_sha256": checklist_sha256,
        "cwd_basename": cwd.name,
        "cwd_sha256": _path_hash(cwd),
        "skill_dir": _safe_path_label(SKILL_DIR, cwd),
        "skill_git_sha": _git_output(("rev-parse", "--short", "HEAD")),
        "skill_git_dirty": bool(_git_output(("status", "--short", "--", skill_status_path))),
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "redaction_enabled": redact,
    }


def _run_controller(args: tuple[str, ...], redact: bool) -> dict[str, Any]:
    command = (sys.executable, str(CONTROLLER_PATH), *args)
    try:
        completed = subprocess.run(
            command,
            cwd=str(SKILL_DIR.parent),
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except Exception as exc:
        return {
            "command": list(command),
            "exit_code": None,
            "error": _maybe_redact(str(exc), redact),
        }

    payload: dict[str, Any] = {
        "command": list(command),
        "exit_code": completed.returncode,
        "stderr": _maybe_redact(completed.stderr, redact),
    }
    stdout = completed.stdout
    try:
        payload["json"] = redact_payload(json.loads(stdout)) if redact else json.loads(stdout)
    except Exception:
        payload["stdout"] = _maybe_redact(stdout, redact)
    return payload


def _build_triage_summary(validate_run: dict[str, Any], cycle_run: dict[str, Any]) -> dict[str, Any]:
    validate_json = validate_run.get("json") if isinstance(validate_run.get("json"), dict) else {}
    cycle_json = cycle_run.get("json") if isinstance(cycle_run.get("json"), dict) else {}
    violations = list(validate_json.get("violations") or []) + list(cycle_json.get("violations") or [])
    violation_codes = sorted({str(item.get("code")) for item in violations if isinstance(item, dict) and item.get("code")})
    dispatch_packets = cycle_json.get("dispatch_packets") if isinstance(cycle_json, dict) else []
    status_updates = cycle_json.get("status_updates") if isinstance(cycle_json, dict) else []

    return {
        "validate_exit_code": validate_run.get("exit_code"),
        "cycle_exit_code": cycle_run.get("exit_code"),
        "validate_ok": validate_json.get("ok") if isinstance(validate_json, dict) else None,
        "cycle_ok": cycle_json.get("ok") if isinstance(cycle_json, dict) else None,
        "violation_codes": violation_codes,
        "status_update_count": len(status_updates) if isinstance(status_updates, list) else 0,
        "dispatch_packet_count": len(dispatch_packets) if isinstance(dispatch_packets, list) else 0,
        "dispatch_packet_types": [
            str(packet.get("packet_type"))
            for packet in dispatch_packets
            if isinstance(packet, dict) and packet.get("packet_type")
        ]
        if isinstance(dispatch_packets, list)
        else [],
    }


def _copy_task_files(
    checklist_path: Path,
    target_dir: Path,
    redact: bool,
    max_file_bytes: int,
) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    for source in sorted(checklist_path.parent.iterdir()):
        if source == checklist_path or not source.is_file():
            continue
        if source.suffix.lower() not in TEXT_FILE_SUFFIXES:
            continue
        if source.stat().st_size > max_file_bytes:
            (target_dir / f"{source.name}.skipped.txt").write_text(
                f"文件超过 {max_file_bytes} bytes，未自动收集：{source.name}\n",
                encoding="utf-8",
            )
            continue
        text = source.read_text(encoding="utf-8", errors="replace")
        (target_dir / source.name).write_text(_maybe_redact(text, redact), encoding="utf-8")


def _build_readme(metadata: dict[str, Any], triage_summary: dict[str, Any], include_task_files: bool) -> str:
    files = [
        "- `metadata.json`：报告元信息、skill 版本、用户补充的错误描述。",
        "- `checklist.redacted.md`：默认脱敏后的 checklist 快照。",
        "- `controller/validate.json`：`controller.py validate --json` 的输出。",
        "- `controller/cycle.json`：`controller.py cycle --json` 的输出。",
        "- `triage-summary.json`：自动提取的 violation code、packet 数量和状态摘要。",
    ]
    if include_task_files:
        files.append("- `task-files/`：同一任务目录下的可读文本附件，已按同样规则脱敏。")

    return (
        "# 强审 skill 意外错误报告\n\n"
        "## 人工补充\n"
        "- 触发动作：\n"
        "- 是否稳定复现：\n"
        "- 你认为正确行为应该是：\n"
        "- 实际看到的错误或跑偏：\n"
        "- 是否允许维护者把这个样本转成 regression eval：\n\n"
        "## 自动摘要\n"
        f"- 类别：{metadata.get('category')}\n"
        f"- validate_ok：{triage_summary.get('validate_ok')}\n"
        f"- cycle_ok：{triage_summary.get('cycle_ok')}\n"
        f"- violation_codes：{', '.join(triage_summary.get('violation_codes') or []) or 'none'}\n"
        f"- dispatch_packet_count：{triage_summary.get('dispatch_packet_count')}\n\n"
        "## 文件说明\n"
        + "\n".join(files)
        + "\n\n"
        "默认报告包不包含静默遥测，也不会自动上传。共享前仍建议人工检查一次脱敏效果。\n"
    )


def _zip_report(report_dir: Path) -> Path:
    zip_path = report_dir.with_suffix(".zip")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(report_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(report_dir.parent))
    return zip_path


def _create_unique_report_dir(output_root: Path, timestamp: str, slug: str) -> Path:
    """同一秒内重复收集时追加序号，避免覆盖前一次报告。"""

    for index in range(1, 100):
        suffix = "" if index == 1 else f"-{index}"
        report_dir = output_root / f"{timestamp}-{slug}{suffix}"
        try:
            report_dir.mkdir(parents=True, exist_ok=False)
            return report_dir
        except FileExistsError:
            continue
    raise FileExistsError(f"too many reports already exist for {timestamp}-{slug}")


def _read_text_or_error(path: Path) -> dict[str, Any]:
    try:
        return {"ok": True, "text": path.read_text(encoding="utf-8")}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _maybe_redact(text: str, redact: bool) -> str:
    return redact_text(text) if redact else text


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-")
    return slug or "report"


def _path_hash(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).encode("utf-8", errors="replace")).hexdigest()


def _safe_path_label(path: Path, cwd: Path) -> str:
    resolved = path.resolve()
    if _is_relative_to(resolved, cwd.resolve()):
        return str(resolved.relative_to(cwd.resolve()))
    return "<outside-cwd:" + _path_hash(resolved)[:12] + ">"


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _git_output(args: tuple[str, ...]) -> str:
    try:
        completed = subprocess.run(
            ("git", *args),
            cwd=str(SKILL_DIR.parent),
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect a local unexpected-error report for strict-review-development-mode")
    parser.add_argument("--checklist", required=True, help="Path to the strict-review checklist.md")
    parser.add_argument("--category", choices=REPORT_CATEGORIES, default="other")
    parser.add_argument("--message", default="", help="Short error message or symptom")
    parser.add_argument("--observed", default="", help="What actually happened")
    parser.add_argument("--expected", default="", help="What should have happened")
    parser.add_argument("--command", default="", help="Command or agent action that reproduced the issue")
    parser.add_argument("--output-root", default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--include-task-files", action="store_true", help="Also collect redacted sibling .md/.txt/.json/.log files")
    parser.add_argument("--max-file-bytes", type=int, default=200_000)
    parser.add_argument("--zip", action="store_true", help="Create a zip archive next to the report directory")
    parser.add_argument("--no-redact", action="store_true", help="Disable built-in redaction; use only for private local debugging")
    parser.add_argument("--json", action="store_true", help="Print machine-readable result")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = collect_unexpected_error(
        checklist_path=Path(args.checklist),
        output_root=Path(args.output_root),
        category=args.category,
        message=args.message,
        observed=args.observed,
        expected=args.expected,
        command=args.command,
        include_task_files=args.include_task_files,
        create_zip=args.zip,
        redact=not args.no_redact,
        max_file_bytes=args.max_file_bytes,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("report_dir: " + result["report_dir"])
        if "zip_path" in result:
            print("zip_path: " + result["zip_path"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
