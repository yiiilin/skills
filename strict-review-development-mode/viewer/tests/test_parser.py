from __future__ import annotations

import importlib.util
import sys
import textwrap
import unittest
from pathlib import Path

PARSER_PATH = Path(__file__).resolve().parents[1] / "parser.py"
FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "sample_checklist.md"
TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "checklist-template.md"


def _load_parser_module():
    if not PARSER_PATH.exists():
        raise ImportError(f"parser module not found at {PARSER_PATH}")

    spec = importlib.util.spec_from_file_location("strict_review_viewer_parser", PARSER_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"unable to load parser module from {PARSER_PATH}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


parser = _load_parser_module()
parse_markdown = parser.parse_markdown
parse_file = parser.parse_file


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

    ## Ready 队列
    - 无

    ## Active 实现队列
    - 无

    ## Active reviewer 队列
    - 无

    ## Review Queue
    - 无
    """
)

CONTRACT_FIELD_NAMES = {
    "item_id",
    "blocked_by",
    "blocks",
    "shared_surfaces",
    "parallel_group",
    "dispatch_status",
    "assigned_subagent",
}


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


def build_checklist(*item_blocks: str) -> str:
    return "# Test Checklist\n\n" + DEFAULT_GLOBAL_SECTIONS + "\n" + "\n".join(item_blocks)


def warning_codes(parsed_checklist) -> list[str]:
    return [warning.code for warning in parsed_checklist.warnings]


class ParseChecklistContractTests(unittest.TestCase):
    def test_parse_sample_fixture_extracts_title_sections_and_normalized_fields(self) -> None:
        parsed = parse_file(FIXTURE_PATH)

        self.assertEqual("Strict Review Progress Viewer", parsed.title)
        self.assertIn("审核设置", parsed.top_level_sections)
        self.assertIn("Mermaid DAG", parsed.top_level_sections)
        self.assertNotIn("Item 1 - Build parser contract", parsed.top_level_sections)
        self.assertEqual(2, len(parsed.items))
        self.assertEqual([], parsed.warnings)

        first_item, second_item = parsed.items
        self.assertEqual("Build parser contract", first_item.title)
        self.assertEqual([], first_item.structured_fields["blocked_by"])
        self.assertEqual(["item-2"], first_item.structured_fields["blocks"])
        self.assertEqual("in-review", first_item.structured_fields["dispatch_status"])
        self.assertEqual(
            ["viewer-snapshot-contract", "viewer-ui"],
            second_item.structured_fields["shared_surfaces"],
        )

    def test_parse_template_file_matches_parser_contract_without_exact_template_shape(self) -> None:
        parsed = parse_file(TEMPLATE_PATH)

        self.assertTrue(parsed.title)
        self.assertGreater(len(parsed.items), 0)
        self.assertIn("Checklist", parsed.top_level_sections)
        self.assertIn("Mermaid DAG", parsed.top_level_sections)
        self.assertIn("```mermaid", parsed.top_level_sections["Mermaid DAG"])
        self.assertNotIn("missing_global_heading", warning_codes(parsed))

        for item in parsed.items:
            self.assertTrue(CONTRACT_FIELD_NAMES.issubset(item.structured_fields))

    def test_template_includes_task_repartition_summary_section(self) -> None:
        parsed = parse_file(TEMPLATE_PATH)

        self.assertIn("任务重整摘要", parsed.top_level_sections)
        self.assertIn("原始任务", parsed.top_level_sections["任务重整摘要"])

    def test_template_includes_task_identity_section(self) -> None:
        parsed = parse_file(TEMPLATE_PATH)

        self.assertIn("任务归属判定", parsed.top_level_sections)
        self.assertIn("判定结果", parsed.top_level_sections["任务归属判定"])

    def test_task_identity_heading_is_optional_but_preserved(self) -> None:
        parsed = parse_markdown(
            "# Task Identity Checklist\n\n"
            "## 模式\n"
            "- 强审开发模式（DAG-first）\n\n"
            "## 审核设置\n"
            "- 审核模型目标：gpt-5.4\n\n"
            "## 当前执行状态\n"
            "- 当前状态：进行中\n\n"
            "## 任务归属判定\n"
            "- 当前请求：继续修复 parser\n"
            "- 判定结果：same-task\n"
            "- 判定依据：用户明确要求继续上次同一任务\n"
            "- 关联旧 checklist：/abs/path/checklists/parser.md\n\n"
            "## Checklist\n"
            "- [ ] 1. Example item\n\n"
            "## DAG 概览\n"
            "- 关键串行路径：Item 1\n\n"
            "## Mermaid DAG\n"
            "```mermaid\n"
            "graph TD\n"
            "  A[Item 1 - Example item]\n"
            "```\n\n"
            + make_item(
                "Item 1 - Example item",
                [
                    "- item_id：item-1",
                    "- blocked_by：[]",
                    "- blocks：[]",
                    "- shared_surfaces：[]",
                    "- parallel_group：wave-1",
                    "- dispatch_status：ready",
                    "- assigned_subagent：none",
                ],
            )
        )

        self.assertIn("任务归属判定", parsed.top_level_sections)
        self.assertIn("same-task", parsed.top_level_sections["任务归属判定"])

    def test_task_repartition_summary_heading_is_optional_but_preserved(self) -> None:
        parsed = parse_markdown(
            "# Repartition Checklist\n\n"
            "## 模式\n"
            "- 强审开发模式（DAG-first）\n\n"
            "## 审核设置\n"
            "- 审核模型目标：gpt-5.4\n\n"
            "## 当前执行状态\n"
            "- 当前状态：进行中\n\n"
            "## 任务重整摘要\n"
            "- 原始任务：单块需求，尚未拆包\n"
            "- 重整结果：拆为 item-1 与 item-2 两个工作包\n\n"
            "## Checklist\n"
            "- [ ] 1. Example item\n\n"
            "## DAG 概览\n"
            "- 关键串行路径：Item 1\n\n"
            "## Mermaid DAG\n"
            "```mermaid\n"
            "graph TD\n"
            "  A[Item 1 - Example item]\n"
            "```\n\n"
            + make_item(
                "Item 1 - Example item",
                [
                    "- item_id：item-1",
                    "- blocked_by：[]",
                    "- blocks：[]",
                    "- shared_surfaces：[]",
                    "- parallel_group：wave-1",
                    "- dispatch_status：ready",
                    "- assigned_subagent：none",
                ],
            )
        )

        self.assertIn("任务重整摘要", parsed.top_level_sections)
        self.assertIn("拆为 item-1 与 item-2 两个工作包", parsed.top_level_sections["任务重整摘要"])

    def test_template_initial_dispatch_status_matches_dependency_reachability(self) -> None:
        parsed = parse_file(TEMPLATE_PATH)

        statuses_by_id = {
            item.structured_fields["item_id"]: item.structured_fields["dispatch_status"]
            for item in parsed.items
            if "item_id" in item.structured_fields and "dispatch_status" in item.structured_fields
        }

        self.assertEqual("ready", statuses_by_id["item-1"])
        self.assertEqual("ready", statuses_by_id["item-2"])
        self.assertEqual("blocked", statuses_by_id["item-3"])
        self.assertEqual("ready", statuses_by_id["item-4"])

    def test_queue_headings_are_optional_when_structured_fields_drive_dispatch(self) -> None:
        parsed = parse_markdown(
            "# Optional Queue Checklist\n\n"
            "## 模式\n"
            "- 强审开发模式（DAG-first）\n\n"
            "## 审核设置\n"
            "- 审核模型目标：gpt-5.4\n\n"
            "## 当前执行状态\n"
            "- 当前状态：进行中\n\n"
            "## Checklist\n"
            "- [ ] 1. Example item\n\n"
            "## DAG 概览\n"
            "- 关键串行路径：Item 1\n\n"
            "## Mermaid DAG\n"
            "```mermaid\n"
            "graph TD\n"
            "  A[Item 1 - Example item]\n"
            "```\n\n"
            + make_item(
                "Item 1 - Example item",
                [
                    "- item_id：item-1",
                    "- blocked_by：[]",
                    "- blocks：[]",
                    "- shared_surfaces：[]",
                    "- parallel_group：wave-1",
                    "- dispatch_status：ready",
                    "- assigned_subagent：none",
                ],
            )
        )

        warning_pairs = {(warning.code, warning.heading) for warning in parsed.warnings}
        self.assertNotIn(("missing_global_heading", "Ready 队列"), warning_pairs)
        self.assertNotIn(("missing_global_heading", "Active 实现队列"), warning_pairs)
        self.assertNotIn(("missing_global_heading", "Active reviewer 队列"), warning_pairs)
        self.assertNotIn(("missing_global_heading", "Review Queue"), warning_pairs)
        self.assertEqual("ready", parsed.items[0].structured_fields["dispatch_status"])

    def test_ignores_h2_headings_inside_fenced_code_blocks(self) -> None:
        parsed = parse_markdown(
            "# Fence Checklist\n\n"
            "## 模式\n"
            "- 强审开发模式（DAG-first）\n\n"
            "## Mermaid DAG\n"
            "```mermaid\n"
            "graph TD\n"
            "  A[Start]\n"
            "## not a real section\n"
            "  B[Finish]\n"
            "```\n\n"
            "## Review Queue\n"
            "- 无\n"
        )

        self.assertIn("Mermaid DAG", parsed.top_level_sections)
        self.assertEqual(
            "```mermaid\n"
            "graph TD\n"
            "  A[Start]\n"
            "## not a real section\n"
            "  B[Finish]\n"
            "```",
            parsed.top_level_sections["Mermaid DAG"],
        )
        self.assertEqual("- 无", parsed.top_level_sections["Review Queue"])
        self.assertNotIn("not a real section", parsed.top_level_sections)

    def test_ignores_h2_headings_inside_tilde_fenced_code_blocks(self) -> None:
        parsed = parse_markdown(
            "# Fence Checklist\n\n"
            "## 模式\n"
            "- 强审开发模式（DAG-first）\n\n"
            "## Mermaid DAG\n"
            "~~~mermaid\n"
            "graph TD\n"
            "  A[Start]\n"
            "## not a real section\n"
            "  B[Finish]\n"
            "~~~\n\n"
            "## Review Queue\n"
            "- 无\n"
        )

        self.assertIn("Mermaid DAG", parsed.top_level_sections)
        self.assertEqual(
            "~~~mermaid\n"
            "graph TD\n"
            "  A[Start]\n"
            "## not a real section\n"
            "  B[Finish]\n"
            "~~~",
            parsed.top_level_sections["Mermaid DAG"],
        )
        self.assertEqual("- 无", parsed.top_level_sections["Review Queue"])
        self.assertNotIn("not a real section", parsed.top_level_sections)

    def test_ignores_h3_headings_inside_item_fenced_code_blocks(self) -> None:
        plan = "- Keep fenced code intact.\n```bash\n### not a real subsection\nprintf 'hello\\n'\n```"
        parsed = parse_markdown(
            build_checklist(
                make_item(
                    "Item 1 - Preserve fenced headings",
                    [
                        "- item_id：item-1",
                        "- blocked_by：[]",
                        "- blocks：[]",
                        "- shared_surfaces：[]",
                        "- parallel_group：wave-1",
                        "- dispatch_status：ready",
                        "- assigned_subagent：none",
                    ],
                    plan=plan,
                )
            )
        )

        self.assertEqual(plan, parsed.items[0].plan)
        self.assertNotIn("not a real subsection", parsed.items[0].sections)

    def test_ignores_h3_headings_inside_indented_item_fenced_code_blocks(self) -> None:
        plan = (
            "- Keep nested fenced code intact.\n"
            "  ```bash\n"
            "### not a real subsection\n"
            "printf 'hello\\n'\n"
            "  ```"
        )
        parsed = parse_markdown(
            build_checklist(
                make_item(
                    "Item 1 - Preserve indented fenced headings",
                    [
                        "- item_id：item-1",
                        "- blocked_by：[]",
                        "- blocks：[]",
                        "- shared_surfaces：[]",
                        "- parallel_group：wave-1",
                        "- dispatch_status：ready",
                        "- assigned_subagent：none",
                    ],
                    plan=plan,
                )
            )
        )

        self.assertEqual(plan, parsed.items[0].plan)
        self.assertNotIn("not a real subsection", parsed.items[0].sections)

    def test_warns_when_contract_structured_fields_are_missing(self) -> None:
        parsed = parse_markdown(
            build_checklist(
                make_item(
                    "Item 1 - Missing contract fields",
                    [
                        "- item_id：item-1",
                        "- dispatch_status：ready",
                    ],
                )
            )
        )

        warning_pairs = {(warning.code, warning.key) for warning in parsed.warnings}
        self.assertIn(("missing_structured_field", "blocked_by"), warning_pairs)
        self.assertIn(("missing_structured_field", "blocks"), warning_pairs)
        self.assertIn(("missing_structured_field", "shared_surfaces"), warning_pairs)
        self.assertIn(("missing_structured_field", "parallel_group"), warning_pairs)
        self.assertIn(("missing_structured_field", "assigned_subagent"), warning_pairs)
        self.assertEqual("item-1", parsed.items[0].structured_fields["item_id"])
        self.assertEqual("ready", parsed.items[0].structured_fields["dispatch_status"])

    def test_warns_on_duplicate_structured_keys_and_keeps_last_value(self) -> None:
        parsed = parse_markdown(
            build_checklist(
                make_item(
                    "Item 1 - Duplicate key",
                    [
                        "- item_id：item-1",
                        "- blocked_by：[]",
                        "- blocks：[]",
                        "- shared_surfaces：[]",
                        "- parallel_group：wave-1",
                        "- dispatch_status：ready",
                        "- dispatch_status：active",
                        "- assigned_subagent：agent-1",
                    ],
                )
            )
        )

        self.assertIn("duplicate_structured_key", warning_codes(parsed))
        self.assertEqual("active", parsed.items[0].structured_fields["dispatch_status"])

    def test_warns_on_duplicate_item_id(self) -> None:
        parsed = parse_markdown(
            build_checklist(
                make_item(
                    "Item 1 - First item",
                    [
                        "- item_id：item-1",
                        "- blocked_by：[]",
                        "- blocks：[]",
                        "- shared_surfaces：[]",
                        "- parallel_group：wave-1",
                        "- dispatch_status：ready",
                        "- assigned_subagent：none",
                    ],
                ),
                make_item(
                    "Item 2 - Second item",
                    [
                        "- item_id：item-1",
                        "- blocked_by：[item-1]",
                        "- blocks：[]",
                        "- shared_surfaces：[]",
                        "- parallel_group：wave-2",
                        "- dispatch_status：blocked",
                        "- assigned_subagent：none",
                    ],
                ),
            )
        )

        self.assertIn("duplicate_item_id", warning_codes(parsed))

    def test_warns_when_item_id_is_missing(self) -> None:
        parsed = parse_markdown(
            build_checklist(
                make_item(
                    "Item 1 - Missing item id",
                    [
                        "- blocked_by：[]",
                        "- blocks：[]",
                        "- shared_surfaces：[]",
                        "- parallel_group：wave-1",
                        "- dispatch_status：ready",
                        "- assigned_subagent：none",
                    ],
                )
            )
        )

        self.assertIn("missing_item_id", warning_codes(parsed))

    def test_warns_on_unknown_dispatch_status(self) -> None:
        parsed = parse_markdown(
            build_checklist(
                make_item(
                    "Item 1 - Unknown status",
                    [
                        "- item_id：item-1",
                        "- blocked_by：[]",
                        "- blocks：[]",
                        "- shared_surfaces：[]",
                        "- parallel_group：wave-1",
                        "- dispatch_status：paused",
                        "- assigned_subagent：none",
                    ],
                )
            )
        )

        self.assertIn("unknown_dispatch_status", warning_codes(parsed))
        self.assertEqual("paused", parsed.items[0].structured_fields["dispatch_status"])

    def test_keeps_partial_checklist_and_collects_warnings(self) -> None:
        markdown = textwrap.dedent(
            """\
            # Partial Checklist

            ## 模式
            - 强审开发模式（DAG-first）

            ## Item 1 - Partial item
            ### 结构化字段
            - dispatch_status：ready
            - malformed bullet without colon

            ### 计划
            - Keep parsing even when headings are missing.
            """
        )

        parsed = parse_markdown(markdown)

        self.assertEqual("Partial Checklist", parsed.title)
        self.assertEqual(1, len(parsed.items))
        self.assertEqual("Partial item", parsed.items[0].title)
        self.assertEqual(
            "- Keep parsing even when headings are missing.",
            parsed.items[0].plan,
        )

        codes = warning_codes(parsed)
        self.assertIn("missing_global_heading", codes)
        self.assertIn("malformed_structured_bullet", codes)
        self.assertIn("missing_item_heading", codes)
        self.assertIn("missing_item_id", codes)


if __name__ == "__main__":
    unittest.main()
