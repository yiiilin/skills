from __future__ import annotations

import importlib.util
import contextlib
import io
import json
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from typing import Dict, Set

CONTROLLER_PATH = Path(__file__).resolve().parents[2] / "controller.py"
WORKFLOW_REFERENCE_PATH = Path(__file__).resolve().parents[2] / "references" / "workflow-state-machine.md"


def _load_controller_module():
    if not CONTROLLER_PATH.exists():
        raise ImportError(f"controller module not found at {CONTROLLER_PATH}")

    spec = importlib.util.spec_from_file_location("strict_review_controller_for_tests", CONTROLLER_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"unable to load controller module from {CONTROLLER_PATH}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


controller = _load_controller_module()


def make_item(
    number: int,
    item_id: str,
    title: str,
    *,
    blocked_by: str = "[]",
    blocks: str = "[]",
    surfaces: str = "[]",
    status: str = "ready",
    assigned: str = "none",
    reviewer_id: str = "none",
    reviewer_state: str = "not-started",
    plan: str = "- 计划：按事项边界实施",
    implementation: str = "- 待填写",
    verification: str = "- 待填写",
    review: str = "- Reviewer：待填写\n- Reviewer 状态：待填写\n- 审核结论：待填写\n- 关闭状态：待填写",
    extra_fields: str = "",
) -> str:
    extra_block = (extra_fields.rstrip() + "\n") if extra_fields else ""
    return (
        f"## Item {number} - {title}\n"
        "### 结构化字段\n"
        f"- item_id：{item_id}\n"
        f"- blocked_by：{blocked_by}\n"
        f"- blocks：{blocks}\n"
        f"- shared_surfaces：{surfaces}\n"
        f"- parallel_group：wave-{number}\n"
        f"- dispatch_status：{status}\n"
        f"- assigned_subagent：{assigned}\n"
        f"- reviewer_id：{reviewer_id}\n"
        f"- reviewer_state：{reviewer_state}\n"
        f"{extra_block}"
        "- next_action：等待调度\n\n"
        "### 计划\n"
        f"{plan}\n\n"
        "### 实施记录\n"
        f"{implementation}\n\n"
        "### 验证记录\n"
        f"{verification}\n\n"
        "### 审核记录\n"
        f"{review}\n"
    )


def build_checklist(*item_blocks: str, routing: str = "") -> str:
    routing_section = routing or textwrap.dedent(
        """\
        ## Agent 路由策略
        - coordinator_agent：current
        - default_agent：current
        - fallback_agent：current
        - planning_agent：current
        - implementation_agent：current
        - rework_agent：current
        - review_agent：current
        - invocation_policy：coordinator-decides
        """
    )
    return (
        textwrap.dedent(
            """\
            # Controller Test Checklist

            ## 模式
            - 强审开发模式（controller-enforced DAG-first）

            ## 审核设置
            - 审核模型目标：gpt-5.4
            - 推理强度目标：xhigh
            - 实施并行上限：4
            - reviewer 并行上限：2

            {routing_section}

            ## 任务归属判定
            - 当前请求：测试 controller
            - 判定结果：different-task
            - 判定依据：测试用新 checklist
            - 关联旧 checklist：none

            ## 当前执行状态
            - 当前状态：进行中

            ## Checklist
            - [ ] 1. One
            - [ ] 2. Two
            - [ ] 3. Three
            - [ ] 4. Four

            ## DAG 概览
            - 关键串行路径：由结构化字段决定

            ## Mermaid DAG
            ```mermaid
            graph TD
              item-1[One] --> item-2[Two]
              item-3[Three]
              item-4[Four]
            ```
            """
        ).format(routing_section=routing_section.rstrip())
        + "\n"
        + "\n".join(item_blocks)
    )


def write_temp_checklist(text: str) -> Path:
    path = Path(tempfile.mkdtemp()) / "checklist.md"
    path.write_text(text, encoding="utf-8")
    return path


def violation_codes(payload: Dict[str, object]) -> Set[str]:
    return {str(violation["code"]) for violation in payload["violations"]}  # type: ignore[index]


class ControllerValidationTests(unittest.TestCase):
    def test_validate_reports_lifecycle_dependency_and_concurrency_errors(self) -> None:
        path = write_temp_checklist(
            build_checklist(
                make_item(
                    1,
                    "item-1",
                    "One",
                    blocks="[item-2]",
                    surfaces="[shared-api]",
                    status="active",
                    assigned="none",
                    plan="- 待填写",
                ),
                make_item(2, "item-2", "Two", blocked_by="[item-1]", status="ready"),
                make_item(3, "item-3", "Three", surfaces="[shared-api]", status="active", assigned="agent-3"),
                make_item(4, "item-4", "Four", status="done", assigned="agent-4"),
            )
        )

        payload = controller.validate_checklist(path)

        codes = violation_codes(payload)
        self.assertFalse(payload["ok"])
        self.assertIn("active_without_assigned_subagent", codes)
        self.assertIn("active_without_plan", codes)
        self.assertIn("dependency_not_done", codes)
        self.assertIn("active_shared_surface_conflict", codes)
        self.assertIn("missing_implementation_record", codes)
        self.assertIn("missing_verification_record", codes)
        self.assertIn("done_without_closed_reviewer", codes)
        self.assertIn("done_without_approval", codes)

    def test_cycle_emits_external_dispatch_packets_in_protocol_priority_order(self) -> None:
        path = write_temp_checklist(
            build_checklist(
                make_item(
                    1,
                    "item-1",
                    "One",
                    status="changes-requested",
                    assigned="agent-1",
                    surfaces="[item-one]",
                    implementation="- 已实现",
                    verification="- 已验证",
                    review="- 审核结论：需要修改边界条件\n- 关闭状态：open",
                ),
                make_item(
                    2,
                    "item-2",
                    "Two",
                    status="implemented",
                    implementation="- 已实现",
                    verification="- 已验证",
                ),
                make_item(
                    3,
                    "item-3",
                    "Three",
                    status="review-queued",
                    implementation="- 已实现",
                    verification="- 已验证",
                ),
                make_item(4, "item-4", "Four", surfaces="[item-four]", status="ready"),
            )
        )

        payload = controller.build_cycle_plan(path)

        self.assertTrue(payload["ok"])
        packets = payload["dispatch_packets"]
        self.assertEqual(["rework", "review", "review", "implementation"], [packet["packet_type"] for packet in packets])
        self.assertEqual(["item-1", "item-2", "item-3", "item-4"], [packet["item_id"] for packet in packets])
        self.assertIn("assign-reviewer", packets[1]["command"])
        self.assertEqual(["current", "current", "current", "current"], [packet["target_agent"] for packet in packets])
        self.assertEqual("coordinator-decides", packets[0]["invocation_policy"])

    def test_cycle_emits_planning_packet_before_unplanned_ready_implementation(self) -> None:
        path = write_temp_checklist(
            build_checklist(
                make_item(1, "item-1", "One", blocks="[item-2]", status="ready", plan="- 待填写"),
                make_item(2, "item-2", "Two", blocked_by="[item-1]", blocks="[]", status="blocked"),
                make_item(3, "item-3", "Three", status="ready"),
                make_item(4, "item-4", "Four", status="ready"),
            )
        )

        payload = controller.build_cycle_plan(path)

        packets = payload["dispatch_packets"]
        self.assertEqual("planning", packets[0]["packet_type"])
        self.assertEqual("item-1", packets[0]["item_id"])
        self.assertIn("controller.py plan", packets[0]["command"])

    def test_cycle_routes_packets_with_global_agent_policy_without_invocation_parameters(self) -> None:
        routing = textwrap.dedent(
            """\
            ## Agent 路由策略
            - coordinator_agent：codex
            - default_agent：current
            - fallback_agent：current
            - planning_agent：codex
            - implementation_agent：claude
            - rework_agent：claude
            - review_agent：gemini
            - invocation_policy：claude --model hardcoded
            """
        )
        path = write_temp_checklist(
            build_checklist(
                make_item(1, "item-1", "One", status="ready", plan="- 待填写"),
                make_item(
                    2,
                    "item-2",
                    "Two",
                    status="implemented",
                    implementation="- 已实现",
                    verification="- 已验证",
                ),
                make_item(3, "item-3", "Three", status="ready"),
                make_item(4, "item-4", "Four", status="ready"),
                routing=routing,
            )
        )

        payload = controller.build_cycle_plan(path)

        packets = payload["dispatch_packets"]
        self.assertEqual("codex", payload["agent_routing"]["coordinator_agent"])
        self.assertEqual("coordinator-decides", payload["agent_routing"]["invocation_policy"])
        self.assertEqual(["review", "planning", "implementation", "implementation"], [packet["packet_type"] for packet in packets])
        self.assertEqual(["gemini", "codex", "claude", "claude"], [packet["target_agent"] for packet in packets])
        self.assertEqual(["global:review_agent", "global:planning_agent", "global:implementation_agent", "global:implementation_agent"], [packet["routing_source"] for packet in packets])
        self.assertNotIn("model", packets[0])
        self.assertNotIn("temperature", packets[0])

    def test_dispatch_packets_include_agile_ticket_contract(self) -> None:
        path = write_temp_checklist(
            build_checklist(
                make_item(1, "item-1", "One", status="ready", plan="- 待填写"),
                make_item(
                    2,
                    "item-2",
                    "Two",
                    status="implemented",
                    blocks="[item-4]",
                    implementation="- 已实现登录校验",
                    verification="- 已运行认证测试",
                ),
                make_item(3, "item-3", "Three", status="ready", plan="- 计划：只修改 three.py"),
                make_item(4, "item-4", "Four", status="blocked", blocked_by="[item-2]"),
            )
        )

        payload = controller.build_cycle_plan(path)

        packets = payload["dispatch_packets"]
        packet_by_type = {packet["packet_type"]: packet for packet in packets}
        self.assertEqual("测试 controller", packets[0]["workflow_goal"])
        self.assertIn("不要操心全局调度", packets[0]["non_goals"][0])
        self.assertIn("只处理", packets[0]["local_scope"][0])
        self.assertIn("agent_objective", packets[0])
        self.assertIn("success_criteria", packets[0])
        self.assertIn("handoff_requirements", packets[0])

        planning_packet = packet_by_type["planning"]
        implementation_packet = packet_by_type["implementation"]
        review_packet = packet_by_type["review"]
        self.assertIn("不要直接实施代码", "\n".join(planning_packet["non_goals"]))
        self.assertIn("plan", implementation_packet["input_artifacts"])
        self.assertIn("implementation_record", review_packet["input_artifacts"])
        self.assertIn("verification_record", review_packet["input_artifacts"])

    def test_item_agent_override_wins_over_global_policy(self) -> None:
        routing = textwrap.dedent(
            """\
            ## Agent 路由策略
            - default_agent：current
            - implementation_agent：claude
            - review_agent：gemini
            - invocation_policy：coordinator-decides
            """
        )
        path = write_temp_checklist(
            build_checklist(
                make_item(
                    1,
                    "item-1",
                    "One",
                    status="implemented",
                    blocks="[item-3, item-4]",
                    implementation="- 已实现",
                    verification="- 已验证",
                    extra_fields="- review_agent：human:alice",
                ),
                make_item(
                    2,
                    "item-2",
                    "Two",
                    status="ready",
                    extra_fields="- implementation_agent：openai:gpt-5.4",
                ),
                make_item(3, "item-3", "Three", status="blocked", blocked_by="[item-1]", blocks="[]"),
                make_item(4, "item-4", "Four", status="blocked", blocked_by="[item-1]", blocks="[]"),
                routing=routing,
            )
        )

        payload = controller.build_cycle_plan(path)

        packets = payload["dispatch_packets"]
        self.assertEqual(["human:alice", "openai:gpt-5.4"], [packet["target_agent"] for packet in packets])
        self.assertEqual(["item:review_agent", "item:implementation_agent"], [packet["routing_source"] for packet in packets])

    def test_set_routing_records_user_global_agent_preferences(self) -> None:
        path = write_temp_checklist(
            build_checklist(
                make_item(1, "item-1", "One", status="ready", plan="- 待填写"),
                make_item(
                    2,
                    "item-2",
                    "Two",
                    status="implemented",
                    implementation="- 已实现",
                    verification="- 已验证",
                ),
                make_item(3, "item-3", "Three", status="ready"),
                make_item(4, "item-4", "Four", status="ready"),
            )
        )

        payload = controller.set_agent_routing(
            path,
            planning_agent="codex",
            implementation_agent="claude",
            review_agent="gemini",
        )

        document = path.read_text(encoding="utf-8")
        packets = payload["dispatch_packets"]
        self.assertIn("- planning_agent：codex", document)
        self.assertIn("- implementation_agent：claude", document)
        self.assertIn("- review_agent：gemini", document)
        self.assertEqual(["gemini", "codex", "claude", "claude"], [packet["target_agent"] for packet in packets])

    def test_set_routing_records_user_item_agent_preference(self) -> None:
        path = write_temp_checklist(
            build_checklist(
                make_item(1, "item-1", "One", status="ready"),
                make_item(2, "item-2", "Two", status="ready"),
                make_item(3, "item-3", "Three", status="ready"),
                make_item(4, "item-4", "Four", status="ready"),
            )
        )

        payload = controller.set_agent_routing(path, item_id="item-2", implementation_agent="openai:gpt-5.4")

        packets = payload["dispatch_packets"]
        packet_by_item = {packet["item_id"]: packet for packet in packets}
        self.assertIn("- implementation_agent：openai:gpt-5.4", path.read_text(encoding="utf-8"))
        self.assertEqual("openai:gpt-5.4", packet_by_item["item-2"]["target_agent"])
        self.assertEqual("item:implementation_agent", packet_by_item["item-2"]["routing_source"])

    def test_item_routing_rejects_global_only_fields(self) -> None:
        path = write_temp_checklist(
            build_checklist(
                make_item(1, "item-1", "One"),
                make_item(2, "item-2", "Two"),
                make_item(3, "item-3", "Three"),
                make_item(4, "item-4", "Four"),
            )
        )

        with self.assertRaisesRegex(ValueError, "item routing only supports"):
            controller.set_agent_routing(path, item_id="item-1", default_agent="claude")

    def test_start_rejects_shared_surface_conflict_with_active_item(self) -> None:
        path = write_temp_checklist(
            build_checklist(
                make_item(1, "item-1", "One", surfaces="[shared-file]", status="active", assigned="agent-1"),
                make_item(2, "item-2", "Two", surfaces="[shared-file]", status="ready"),
                make_item(3, "item-3", "Three"),
                make_item(4, "item-4", "Four"),
            )
        )

        with self.assertRaisesRegex(ValueError, "conflicts with active item"):
            controller.start_item(path, "item-2", "agent-2")

    def test_state_machine_transitions_write_checklist_and_close_done_item(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "checklist.md"
            controller.init_checklist(
                path,
                "Controller Init Checklist",
                "新增控制器",
                [
                    {
                        "item_id": "item-1",
                        "title": "Build controller",
                        "blocked_by": [],
                        "shared_surfaces": ["controller.py"],
                        "parallel_group": "wave-1",
                    }
                ],
            )

            self.assertTrue(controller.plan_item(path, "item-1", "- 计划：实现控制器并补测试")["ok"])
            self.assertTrue(controller.start_item(path, "item-1", "agent-1")["ok"])
            self.assertTrue(
                controller.mark_implemented(
                    path,
                    "item-1",
                    "- 已实现 controller.py",
                    "- python3 -m unittest discover 通过",
                )["ok"]
            )
            self.assertTrue(controller.queue_review(path, "item-1")["ok"])
            self.assertTrue(controller.assign_reviewer(path, "item-1", "reviewer-1")["ok"])
            payload = controller.approve_item(
                path,
                "item-1",
                "- Reviewer：reviewer-1\n- 审核结论：通过，没有发现问题\n- 关闭状态：closed",
            )

            self.assertTrue(payload["ok"])
            document = path.read_text(encoding="utf-8")
            self.assertIn("- dispatch_status：done", document)
            self.assertIn("- reviewer_state：closed", document)
            self.assertIn("- [x] 1. Build controller", document)

    def test_replace_reviewer_records_replacement_without_closing_review(self) -> None:
        path = write_temp_checklist(
            build_checklist(
                make_item(
                    1,
                    "item-1",
                    "One",
                    status="in-review",
                    assigned="claude",
                    reviewer_id="gemini",
                    reviewer_state="suspect-stalled",
                    implementation="- 已实现",
                    verification="- 已验证",
                    review="- Reviewer：gemini\n- Reviewer 状态：suspect-stalled\n- Replacement Reviewer：待填写\n- 关闭状态：open\n- 关闭原因：待填写",
                ),
                make_item(2, "item-2", "Two"),
                make_item(3, "item-3", "Three"),
                make_item(4, "item-4", "Four"),
            )
        )

        payload = controller.replace_reviewer(path, "item-1", "gemini", "human:alice", "Gemini 超过 35min 未返回")

        document = path.read_text(encoding="utf-8")
        self.assertTrue(payload["ok"])
        self.assertIn("- dispatch_status：in-review", document)
        self.assertIn("- reviewer_id：human:alice", document)
        self.assertIn("- reviewer_state：reviewing", document)
        self.assertIn("- 原 Reviewer 状态：replaced", document)
        self.assertIn("- Replacement Reviewer：human:alice（替换 gemini；原因：Gemini 超过 35min 未返回）", document)
        self.assertIn("- 关闭状态：open", document)
        self.assertIn("- 关闭原因：原 reviewer gemini 已替换：Gemini 超过 35min 未返回", document)

    def test_replace_reviewer_rejects_implementation_agent_self_review(self) -> None:
        path = write_temp_checklist(
            build_checklist(
                make_item(
                    1,
                    "item-1",
                    "One",
                    status="in-review",
                    assigned="claude",
                    reviewer_id="gemini",
                    reviewer_state="reviewing",
                    implementation="- 已实现",
                    verification="- 已验证",
                ),
                make_item(2, "item-2", "Two"),
                make_item(3, "item-3", "Three"),
                make_item(4, "item-4", "Four"),
            )
        )

        with self.assertRaisesRegex(ValueError, "must not be the implementation agent"):
            controller.replace_reviewer(path, "item-1", "gemini", "claude", "Gemini 超时")

    def test_assign_reviewer_rejects_implementation_agent_self_review(self) -> None:
        path = write_temp_checklist(
            build_checklist(
                make_item(
                    1,
                    "item-1",
                    "One",
                    status="implemented",
                    assigned="claude",
                    implementation="- 已实现",
                    verification="- 已验证",
                ),
                make_item(2, "item-2", "Two"),
                make_item(3, "item-3", "Three"),
                make_item(4, "item-4", "Four"),
            )
        )

        with self.assertRaisesRegex(ValueError, "reviewer must not be the implementation agent"):
            controller.assign_reviewer(path, "item-1", "claude")

    def test_validate_rejects_existing_self_review_state(self) -> None:
        path = write_temp_checklist(
            build_checklist(
                make_item(
                    1,
                    "item-1",
                    "One",
                    status="in-review",
                    assigned="claude",
                    reviewer_id="claude",
                    reviewer_state="reviewing",
                    implementation="- 已实现",
                    verification="- 已验证",
                ),
                make_item(2, "item-2", "Two"),
                make_item(3, "item-3", "Three"),
                make_item(4, "item-4", "Four"),
            )
        )

        payload = controller.validate_checklist(path)

        self.assertFalse(payload["ok"])
        self.assertIn("reviewer_same_as_assigned_subagent", violation_codes(payload))

    def test_cli_validate_returns_nonzero_for_protocol_errors(self) -> None:
        path = write_temp_checklist(
            build_checklist(
                make_item(1, "item-1", "One", status="active", assigned="none", plan="- 待填写"),
                make_item(2, "item-2", "Two", blocked_by="[item-1]", status="blocked"),
                make_item(3, "item-3", "Three"),
                make_item(4, "item-4", "Four"),
            )
        )

        with contextlib.redirect_stdout(io.StringIO()):
            exit_code = controller.main(["validate", "--checklist", str(path), "--json"])

        self.assertEqual(1, exit_code)

    def test_cli_init_accepts_json_item_definitions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "checklist.md"
            with contextlib.redirect_stdout(io.StringIO()):
                exit_code = controller.main(
                    [
                        "init",
                        "--checklist",
                        str(path),
                        "--title",
                        "CLI Init",
                        "--request",
                        "测试 init",
                        "--items-json",
                        json.dumps(
                            [
                                {
                                    "item_id": "item-1",
                                    "title": "First item",
                                    "blocked_by": [],
                                    "shared_surfaces": ["first.py"],
                                    "parallel_group": "wave-1",
                                }
                            ]
                        ),
                    ]
                )

            self.assertEqual(0, exit_code)
            self.assertIn("## 任务归属判定", path.read_text(encoding="utf-8"))
            self.assertIn("- fallback_agent：current", path.read_text(encoding="utf-8"))

    def test_cli_set_routing_accepts_user_agent_preferences(self) -> None:
        path = write_temp_checklist(
            build_checklist(
                make_item(1, "item-1", "One"),
                make_item(2, "item-2", "Two"),
                make_item(3, "item-3", "Three"),
                make_item(4, "item-4", "Four"),
            )
        )

        with contextlib.redirect_stdout(io.StringIO()):
            exit_code = controller.main(
                [
                    "set-routing",
                    "--checklist",
                    str(path),
                    "--planning-agent",
                    "codex",
                    "--implementation-agent",
                    "claude",
                    "--review-agent",
                    "gemini",
                    "--json",
                ]
            )

        document = path.read_text(encoding="utf-8")
        self.assertEqual(0, exit_code)
        self.assertIn("- planning_agent：codex", document)
        self.assertIn("- implementation_agent：claude", document)
        self.assertIn("- review_agent：gemini", document)

    def test_cli_replace_reviewer_accepts_reason_text(self) -> None:
        path = write_temp_checklist(
            build_checklist(
                make_item(
                    1,
                    "item-1",
                    "One",
                    status="in-review",
                    assigned="claude",
                    reviewer_id="gemini",
                    reviewer_state="slow",
                    implementation="- 已实现",
                    verification="- 已验证",
                ),
                make_item(2, "item-2", "Two"),
                make_item(3, "item-3", "Three"),
                make_item(4, "item-4", "Four"),
            )
        )

        with contextlib.redirect_stdout(io.StringIO()):
            exit_code = controller.main(
                [
                    "replace-reviewer",
                    "--checklist",
                    str(path),
                    "--item",
                    "item-1",
                    "--from-reviewer",
                    "gemini",
                    "--to-reviewer",
                    "human:alice",
                    "--reason",
                    "Gemini 超过硬超时门槛",
                    "--json",
                ]
            )

        document = path.read_text(encoding="utf-8")
        self.assertEqual(0, exit_code)
        self.assertIn("- reviewer_id：human:alice", document)
        self.assertIn("- Replacement Reviewer：human:alice（替换 gemini；原因：Gemini 超过硬超时门槛）", document)

    def test_cli_diagram_prints_mermaid_state_machine(self) -> None:
        stdout = io.StringIO()

        with contextlib.redirect_stdout(stdout):
            exit_code = controller.main(["diagram"])

        output = stdout.getvalue()
        self.assertEqual(0, exit_code)
        self.assertIn("```mermaid", output)
        self.assertIn("stateDiagram-v2", output)
        self.assertIn("in_review --> done", output)

    def test_reference_workflow_diagram_matches_controller_diagram(self) -> None:
        reference_text = WORKFLOW_REFERENCE_PATH.read_text(encoding="utf-8")

        self.assertIn(controller.WORKFLOW_STATE_DIAGRAM.strip(), reference_text)


if __name__ == "__main__":
    unittest.main()
