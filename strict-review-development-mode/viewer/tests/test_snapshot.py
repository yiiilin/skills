from __future__ import annotations

import importlib.util
import sys
import textwrap
import unittest
from pathlib import Path

VIEWER_DIR = Path(__file__).resolve().parents[1]
PARSER_PATH = VIEWER_DIR / "parser.py"
SNAPSHOT_PATH = VIEWER_DIR / "snapshot.py"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
SAMPLE_FIXTURE = FIXTURES_DIR / "sample_checklist.md"
INVALID_FIXTURE = FIXTURES_DIR / "invalid_cycle_checklist.md"
REAL_PROGRESS_CHECKLIST = VIEWER_DIR.parents[1] / "docs" / "checklists" / "strict-review-progress-viewer.md"


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


parser = _load_module("strict_review_viewer_parser_for_snapshot_tests", PARSER_PATH)
snapshot_module = _load_module("strict_review_viewer_snapshot", SNAPSHOT_PATH)
parse_file = parser.parse_file
parse_markdown = parser.parse_markdown
build_snapshot = snapshot_module.build_snapshot


DEFAULT_GLOBAL_SECTIONS = textwrap.dedent(
    """\
    ## 模式
    - 强审开发模式（DAG-first）

    ## 审核设置
    - 审核模型目标：gpt-5.4

    ## 当前执行状态
    - 当前状态：进行中

    ## Checklist
    - [ ] 1. Example item

    ## DAG 概览
    - 关键串行路径：Item 1

    ## Mermaid DAG
    ```mermaid
    graph TD
      A[Item 1 - Example item]
    ```
    """
)


def make_item(
    heading: str,
    structured_lines: list[str],
    *,
    plan: str = "- 写出计划",
    implementation: str = "- 写出实施记录",
    verification: str = "- 写出验证记录",
    review: str = "- 写出审核记录",
) -> str:
    structured_block = "\n".join(structured_lines)
    return (
        f"## {heading}\n"
        "### 结构化字段\n"
        f"{structured_block}\n\n"
        "### 计划\n"
        f"{plan}\n\n"
        "### 实施记录\n"
        f"{implementation}\n\n"
        "### 验证记录\n"
        f"{verification}\n\n"
        "### 审核记录\n"
        f"{review}\n"
    )


def build_checklist(
    *item_blocks: str,
    ready_queue: str = "- 无",
    active_queue: str = "- 无",
    active_reviewer_queue: str = "- 无",
    review_queue: str = "- 无",
    mermaid: str | None = None,
) -> str:
    mermaid_block = mermaid or "```mermaid\ngraph TD\n  A[Item 1 - Example item]\n```"
    queue_sections = textwrap.dedent(
        f"""\

        ## Ready 队列
        {ready_queue}

        ## Active 实现队列
        {active_queue}

        ## Active reviewer 队列
        {active_reviewer_queue}

        ## Review Queue
        {review_queue}
        """
    )
    base = DEFAULT_GLOBAL_SECTIONS.replace(
        "```mermaid\ngraph TD\n  A[Item 1 - Example item]\n```",
        mermaid_block,
    )
    return "# Snapshot Test Checklist\n\n" + base + queue_sections + "\n" + "\n".join(item_blocks)


def warning_codes(snapshot: dict) -> list[str]:
    return [warning["code"] for warning in snapshot["warnings"]]


def queue_ids(snapshot: dict, queue_name: str) -> list[str | None]:
    return [item["item_id"] for item in snapshot["queues"][queue_name]]


class BuildSnapshotContractTests(unittest.TestCase):
    def test_builds_counts_queues_dag_and_mermaid_reference_from_sample_fixture(self) -> None:
        parsed = parse_file(SAMPLE_FIXTURE)

        snapshot = build_snapshot(parsed)

        self.assertEqual(0, snapshot["counts"]["blocked"])
        self.assertEqual(1, snapshot["counts"]["ready"])
        self.assertEqual(0, snapshot["counts"]["active"])
        self.assertEqual(0, snapshot["counts"]["implemented"])
        self.assertEqual(0, snapshot["counts"]["review-queued"])
        self.assertEqual(1, snapshot["counts"]["in-review"])
        self.assertEqual(0, snapshot["counts"]["changes-requested"])
        self.assertEqual(0, snapshot["counts"]["done"])
        self.assertEqual(["item-2"], queue_ids(snapshot, "ready"))
        self.assertEqual(["item-1"], queue_ids(snapshot, "in_review"))
        self.assertEqual([], queue_ids(snapshot, "implemented"))
        self.assertFalse(snapshot["meta"]["dag_degraded"])
        self.assertEqual(
            [{"source": "item-1", "target": "item-2"}],
            snapshot["dag"]["edges"],
        )
        self.assertEqual(
            parsed.top_level_sections["Mermaid DAG"],
            snapshot["dag"]["mermaid_reference"],
        )
        self.assertIn("mermaid_validation_unavailable", warning_codes(snapshot))

    def test_derives_queues_from_dispatch_status_and_computes_concurrency(self) -> None:
        parsed = parse_markdown(
            build_checklist(
                make_item(
                    "Item 1 - Active implementation item",
                    [
                        "- item_id：item-1",
                        "- blocked_by：[]",
                        "- blocks：[]",
                        "- shared_surfaces：[viewer-snapshot-contract]",
                        "- parallel_group：wave-a",
                        "- dispatch_status：active",
                        "- assigned_subagent：agent-impl-item-1",
                    ],
                ),
                make_item(
                    "Item 2 - Implemented item",
                    [
                        "- item_id：item-2",
                        "- blocked_by：[]",
                        "- blocks：[]",
                        "- shared_surfaces：[viewer-snapshot-contract]",
                        "- parallel_group：wave-a",
                        "- dispatch_status：implemented",
                        "- assigned_subagent：agent-impl-item-2",
                    ],
                ),
                make_item(
                    "Item 3 - Review queued item",
                    [
                        "- item_id：item-3",
                        "- blocked_by：[]",
                        "- blocks：[]",
                        "- shared_surfaces：[viewer-review]",
                        "- parallel_group：wave-b",
                        "- dispatch_status：review-queued",
                        "- assigned_subagent：none",
                    ],
                ),
                make_item(
                    "Item 4 - In review item",
                    [
                        "- item_id：item-4",
                        "- blocked_by：[]",
                        "- blocks：[]",
                        "- shared_surfaces：[viewer-review]",
                        "- parallel_group：wave-b",
                        "- dispatch_status：in-review",
                        "- assigned_subagent：none",
                    ],
                ),
                ready_queue="- item-2 — Implemented item",
                active_queue="- 无",
                active_reviewer_queue="- item-1 — Active implementation item",
                review_queue="- item-4 — In review item",
            )
        )

        snapshot = build_snapshot(parsed)

        self.assertEqual([], queue_ids(snapshot, "ready"))
        self.assertEqual(["item-1"], queue_ids(snapshot, "active"))
        self.assertEqual(["item-2"], queue_ids(snapshot, "implemented"))
        self.assertEqual(["item-3"], queue_ids(snapshot, "review_queued"))
        self.assertEqual(["item-4"], queue_ids(snapshot, "in_review"))
        self.assertEqual(1, snapshot["meta"]["implementation_concurrency"])
        self.assertEqual(1, snapshot["meta"]["reviewer_concurrency"])

    def test_marks_invalid_cycle_fixture_as_degraded_and_keeps_known_graph_data(self) -> None:
        snapshot = build_snapshot(parse_file(INVALID_FIXTURE))

        self.assertTrue(snapshot["meta"]["dag_degraded"])
        self.assertEqual(2, len(snapshot["items"]))
        self.assertEqual(
            {"item-1", "item-2"},
            {node["node_id"] for node in snapshot["dag"]["nodes"]},
        )
        self.assertEqual(
            {("item-1", "item-2"), ("item-2", "item-1")},
            {(edge["source"], edge["target"]) for edge in snapshot["dag"]["edges"]},
        )
        codes = warning_codes(snapshot)
        self.assertIn("missing_referenced_item", codes)
        self.assertIn("asymmetric_dependency", codes)
        self.assertIn("dag_cycle", codes)

    def test_preserves_partial_data_for_duplicate_missing_item_ids_and_unknown_status(self) -> None:
        parsed = parse_markdown(
            build_checklist(
                make_item(
                    "Item 1 - Unknown status item",
                    [
                        "- item_id：item-1",
                        "- blocked_by：[]",
                        "- blocks：[]",
                        "- shared_surfaces：[viewer-snapshot-contract]",
                        "- parallel_group：wave-a",
                        "- dispatch_status：paused",
                        "- assigned_subagent：none",
                    ],
                ),
                make_item(
                    "Item 2 - Duplicate id implemented item",
                    [
                        "- item_id：item-1",
                        "- blocked_by：[]",
                        "- blocks：[]",
                        "- shared_surfaces：[viewer-snapshot-contract]",
                        "- parallel_group：wave-a",
                        "- dispatch_status：implemented",
                        "- assigned_subagent：agent-impl-item-2",
                    ],
                ),
                make_item(
                    "Item 3 - Missing item id blocked item",
                    [
                        "- blocked_by：[]",
                        "- blocks：[]",
                        "- shared_surfaces：[viewer-snapshot-contract]",
                        "- parallel_group：wave-b",
                        "- dispatch_status：blocked",
                        "- assigned_subagent：none",
                    ],
                ),
            )
        )

        snapshot = build_snapshot(parsed)

        self.assertEqual(3, len(snapshot["items"]))
        self.assertEqual(1, snapshot["counts"]["paused"])
        self.assertEqual(1, snapshot["counts"]["implemented"])
        self.assertEqual(1, snapshot["counts"]["blocked"])
        self.assertEqual(["item-1"], queue_ids(snapshot, "implemented"))
        self.assertEqual([None], queue_ids(snapshot, "blocked"))
        self.assertTrue(snapshot["meta"]["dag_degraded"])
        codes = warning_codes(snapshot)
        self.assertIn("duplicate_item_id", codes)
        self.assertIn("missing_item_id", codes)
        self.assertIn("unknown_dispatch_status", codes)

    def test_uses_first_duplicate_item_as_canonical_source_for_dag_nodes_and_edges(self) -> None:
        parsed = parse_markdown(
            build_checklist(
                make_item(
                    "Item 1 - Canonical duplicate item",
                    [
                        "- item_id：item-1",
                        "- blocked_by：[]",
                        "- blocks：[item-2]",
                        "- shared_surfaces：[viewer-snapshot-contract]",
                        "- parallel_group：wave-a",
                        "- dispatch_status：ready",
                        "- assigned_subagent：none",
                    ],
                ),
                make_item(
                    "Item 2 - Downstream item",
                    [
                        "- item_id：item-2",
                        "- blocked_by：[item-1]",
                        "- blocks：[]",
                        "- shared_surfaces：[viewer-snapshot-contract]",
                        "- parallel_group：wave-a",
                        "- dispatch_status：blocked",
                        "- assigned_subagent：none",
                    ],
                ),
                make_item(
                    "Item 3 - Unrelated dependency source",
                    [
                        "- item_id：item-3",
                        "- blocked_by：[]",
                        "- blocks：[]",
                        "- shared_surfaces：[viewer-snapshot-contract]",
                        "- parallel_group：wave-b",
                        "- dispatch_status：ready",
                        "- assigned_subagent：none",
                    ],
                ),
                make_item(
                    "Item 4 - Later duplicate item with conflicting dependencies",
                    [
                        "- item_id：item-1",
                        "- blocked_by：[item-3]",
                        "- blocks：[]",
                        "- shared_surfaces：[viewer-snapshot-contract]",
                        "- parallel_group：wave-c",
                        "- dispatch_status：done",
                        "- assigned_subagent：none",
                    ],
                ),
            )
        )

        snapshot = build_snapshot(parsed)

        self.assertEqual(
            [
                {"source": "item-1", "target": "item-2"},
            ],
            snapshot["dag"]["edges"],
        )
        nodes_by_id = {node["item_id"]: node for node in snapshot["dag"]["nodes"]}
        self.assertEqual(3, len(nodes_by_id))
        self.assertEqual("Item 1 - Canonical duplicate item", nodes_by_id["item-1"]["heading"])
        self.assertEqual("Canonical duplicate item", nodes_by_id["item-1"]["title"])
        self.assertEqual("ready", nodes_by_id["item-1"]["dispatch_status"])

    def test_treats_mermaid_as_unstable_when_any_item_lacks_item_id(self) -> None:
        parsed = parse_markdown(
            build_checklist(
                make_item(
                    "Item 1 - Stable source item",
                    [
                        "- item_id：item-1",
                        "- blocked_by：[]",
                        "- blocks：[item-2]",
                        "- shared_surfaces：[]",
                        "- parallel_group：wave-a",
                        "- dispatch_status：ready",
                        "- assigned_subagent：none",
                    ],
                ),
                make_item(
                    "Item 2 - Stable target item",
                    [
                        "- item_id：item-2",
                        "- blocked_by：[item-1]",
                        "- blocks：[]",
                        "- shared_surfaces：[]",
                        "- parallel_group：wave-a",
                        "- dispatch_status：blocked",
                        "- assigned_subagent：none",
                    ],
                ),
                make_item(
                    "Item 3 - Missing item id item",
                    [
                        "- blocked_by：[]",
                        "- blocks：[]",
                        "- shared_surfaces：[]",
                        "- parallel_group：wave-b",
                        "- dispatch_status：ready",
                        "- assigned_subagent：none",
                    ],
                ),
                mermaid=textwrap.dedent(
                    """\
                    ```mermaid
                    graph TD
                      item-1[Stable source item] --> item-2[Stable target item]
                    ```
                    """
                ),
            )
        )

        snapshot = build_snapshot(parsed)

        self.assertIn("mermaid_validation_unavailable", warning_codes(snapshot))

    def test_accepts_alias_node_labels_that_match_unique_item_headings(self) -> None:
        parsed = parse_markdown(
            build_checklist(
                make_item(
                    "Item 1 - Stable source item",
                    [
                        "- item_id：item-1",
                        "- blocked_by：[]",
                        "- blocks：[item-2]",
                        "- shared_surfaces：[]",
                        "- parallel_group：wave-a",
                        "- dispatch_status：ready",
                        "- assigned_subagent：none",
                    ],
                ),
                make_item(
                    "Item 2 - Stable target item",
                    [
                        "- item_id：item-2",
                        "- blocked_by：[item-1]",
                        "- blocks：[]",
                        "- shared_surfaces：[]",
                        "- parallel_group：wave-a",
                        "- dispatch_status：blocked",
                        "- assigned_subagent：none",
                    ],
                ),
                mermaid=textwrap.dedent(
                    """\
                    ```mermaid
                    graph TD
                      A[Item 1 - Stable source item] --> B[Item 2 - Stable target item]
                    ```
                    """
                ),
            )
        )

        snapshot = build_snapshot(parsed)

        self.assertNotIn("mermaid_validation_unavailable", warning_codes(snapshot))

    def test_accepts_mixed_raw_item_ids_and_alias_node_labels_when_aliases_resolve_stably(self) -> None:
        parsed = parse_markdown(
            build_checklist(
                make_item(
                    "Item 1 - Stable source item",
                    [
                        "- item_id：item-1",
                        "- blocked_by：[]",
                        "- blocks：[item-2]",
                        "- shared_surfaces：[]",
                        "- parallel_group：wave-a",
                        "- dispatch_status：ready",
                        "- assigned_subagent：none",
                    ],
                ),
                make_item(
                    "Item 2 - Stable target item",
                    [
                        "- item_id：item-2",
                        "- blocked_by：[item-1]",
                        "- blocks：[]",
                        "- shared_surfaces：[]",
                        "- parallel_group：wave-a",
                        "- dispatch_status：blocked",
                        "- assigned_subagent：none",
                    ],
                ),
                mermaid=textwrap.dedent(
                    """\
                    ```mermaid
                    graph TD
                      item-1 --> B[Item 2 - Stable target item]
                    ```
                    """
                ),
            )
        )

        snapshot = build_snapshot(parsed)

        self.assertNotIn("mermaid_validation_unavailable", warning_codes(snapshot))

    def test_accepts_alias_node_labels_in_real_progress_checklist_regression(self) -> None:
        parsed = parse_file(REAL_PROGRESS_CHECKLIST)

        snapshot = build_snapshot(parsed)

        self.assertNotIn("mermaid_validation_unavailable", warning_codes(snapshot))

    def test_keeps_mermaid_validation_warning_when_title_only_alias_labels_are_ambiguous(self) -> None:
        parsed = parse_markdown(
            build_checklist(
                make_item(
                    "Item 1 - Shared alias target",
                    [
                        "- item_id：item-1",
                        "- blocked_by：[]",
                        "- blocks：[item-2]",
                        "- shared_surfaces：[]",
                        "- parallel_group：wave-a",
                        "- dispatch_status：ready",
                        "- assigned_subagent：none",
                    ],
                ),
                make_item(
                    "Item 2 - Shared alias target",
                    [
                        "- item_id：item-2",
                        "- blocked_by：[item-1]",
                        "- blocks：[]",
                        "- shared_surfaces：[]",
                        "- parallel_group：wave-a",
                        "- dispatch_status：blocked",
                        "- assigned_subagent：none",
                    ],
                ),
                mermaid=textwrap.dedent(
                    """\
                    ```mermaid
                    graph TD
                      A[Shared alias target] --> B[Shared alias target]
                    ```
                    """
                ),
            )
        )

        snapshot = build_snapshot(parsed)

        self.assertIn("mermaid_validation_unavailable", warning_codes(snapshot))

    def test_keeps_mermaid_validation_warning_when_alias_labels_do_not_stably_map(self) -> None:
        parsed = parse_markdown(
            build_checklist(
                make_item(
                    "Item 1 - Stable source item",
                    [
                        "- item_id：item-1",
                        "- blocked_by：[]",
                        "- blocks：[item-2]",
                        "- shared_surfaces：[]",
                        "- parallel_group：wave-a",
                        "- dispatch_status：ready",
                        "- assigned_subagent：none",
                    ],
                ),
                make_item(
                    "Item 2 - Stable target item",
                    [
                        "- item_id：item-2",
                        "- blocked_by：[item-1]",
                        "- blocks：[]",
                        "- shared_surfaces：[]",
                        "- parallel_group：wave-a",
                        "- dispatch_status：blocked",
                        "- assigned_subagent：none",
                    ],
                ),
                mermaid=textwrap.dedent(
                    """\
                    ```mermaid
                    graph TD
                      A[Investigate parser gap] --> B[Ship viewer release]
                    ```
                    """
                ),
            )
        )

        snapshot = build_snapshot(parsed)

        self.assertIn("mermaid_validation_unavailable", warning_codes(snapshot))


if __name__ == "__main__":
    unittest.main()
