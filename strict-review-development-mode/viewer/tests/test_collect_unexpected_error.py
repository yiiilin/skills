from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


COLLECTOR_PATH = Path(__file__).resolve().parents[2] / "scripts" / "collect_unexpected_error.py"
FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "sample_checklist.md"


def _load_collector_module():
    if not COLLECTOR_PATH.exists():
        raise ImportError(f"collector module not found at {COLLECTOR_PATH}")

    spec = importlib.util.spec_from_file_location("strict_review_unexpected_error_collector_for_tests", COLLECTOR_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"unable to load collector module from {COLLECTOR_PATH}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


collector = _load_collector_module()


class UnexpectedErrorCollectorTests(unittest.TestCase):
    def test_redacts_common_secret_shapes_without_redacting_token_counts(self) -> None:
        text = "\n".join(
            [
                "OPENAI_API_KEY=sk-secretvalue123456",
                "Authorization: Bearer abc.def.ghi",
                "cookie=session=private-cookie-value",
                "total_tokens: 42",
            ]
        )

        redacted = collector.redact_text(text)

        self.assertNotIn("sk-secretvalue123456", redacted)
        self.assertNotIn("abc.def.ghi", redacted)
        self.assertNotIn("private-cookie-value", redacted)
        self.assertIn("total_tokens: 42", redacted)

    def test_collects_report_with_redacted_controller_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            task_dir = root / ".strict-review" / "collector-demo"
            task_dir.mkdir(parents=True)
            checklist = task_dir / "checklist.md"
            checklist.write_text(FIXTURE_PATH.read_text(encoding="utf-8"), encoding="utf-8")

            result = collector.collect_unexpected_error(
                checklist_path=checklist,
                output_root=root / "reports",
                category="unexpected-dispatch",
                message="Authorization: Bearer abc.def.ghi",
                observed="cycle 派发了不符合预期的 packet",
                expected="等待上游 item done",
                command="python3 controller.py cycle --json",
                include_task_files=False,
                create_zip=False,
                redact=True,
            )

            report_dir = Path(result["report_dir"])
            self.assertTrue((report_dir / "metadata.json").exists())
            self.assertTrue((report_dir / "checklist.redacted.md").exists())
            self.assertTrue((report_dir / "controller" / "validate.json").exists())
            self.assertTrue((report_dir / "controller" / "cycle.json").exists())
            self.assertTrue((report_dir / "triage-summary.json").exists())

            metadata = json.loads((report_dir / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["category"], "unexpected-dispatch")
            self.assertNotIn("abc.def.ghi", json.dumps(metadata, ensure_ascii=False))

            triage_summary = json.loads((report_dir / "triage-summary.json").read_text(encoding="utf-8"))
            self.assertIn("dispatch_packet_count", triage_summary)


if __name__ == "__main__":
    unittest.main()
