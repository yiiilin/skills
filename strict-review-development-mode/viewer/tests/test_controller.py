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
            - 实施并行上限：不限制
            - reviewer 并行上限：不限制
            - 并行安全约束：不限制子 agent 数量；实施仍受 DAG 依赖和 shared_surfaces 冲突约束

            {routing_section}

            ## 任务归属判定
            - 当前请求：测试 controller
            - 判定结果：different-task
            - 判定依据：测试用新 checklist
            - 关联旧 checklist：none

            ## 完成契约
            - 用户原始请求范围：测试 controller
            - 本 checklist 覆盖范围：全部测试事项
            - 自划阶段是否可作为停止条件：否
            - 允许中途停止条件：用户明确要求暂停 / blocker / needs-clarification
            - 请求内未纳入事项：无
            - 后续建议边界：只能写请求外增强项，不能把用户原始要求内的事项放到后续建议

            ## 任务启动总规划
            - 任务目标：测试 controller
            - 非目标：无
            - 验收标准：测试通过
            - 已知约束：遵守强审状态机
            - 代码侦察证据：测试构造 fixture
            - 初步方案：按测试 checklist 推进
            - 工作包拆分理由：测试覆盖多个状态
            - 并行策略：由 DAG 和 shared_surfaces 决定
            - 主要风险：无
            - 需要用户确认的问题：无
            - 启动判定：ready

            ## 当前执行状态
            - 当前状态：进行中

            ## 交付总结
            - 完成状态：pending
            - 用户请求内交付内容：待填写
            - 用户请求内未交付内容：待完成
            - 请求外后续建议：无
            - 关键变更位置：待填写
            - 最终验证证据：待填写
            - 审核结果摘要：待填写
            - 遗留风险：待填写
            - 用户验收入口：待填写

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


def mark_startup_ready(path: Path) -> None:
    document = path.read_text(encoding="utf-8")
    replacements = {
        "- 请求内未纳入事项：待确认": "- 请求内未纳入事项：无",
        "- 非目标：待填写": "- 非目标：无",
        "- 验收标准：待填写": "- 验收标准：测试通过",
        "- 已知约束：待填写": "- 已知约束：遵守强审状态机",
        "- 代码侦察证据：待填写": "- 代码侦察证据：测试构造 fixture",
        "- 初步方案：待填写": "- 初步方案：按测试 checklist 推进",
        "- 主要风险：待填写": "- 主要风险：无",
        "- 启动判定：needs-clarification": "- 启动判定：ready",
    }
    for old, new in replacements.items():
        document = document.replace(old, new)
    path.write_text(document, encoding="utf-8")


class ControllerValidationTests(unittest.TestCase):
    def test_validate_reports_lifecycle_dependency_and_surface_errors(self) -> None:
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

    def test_startup_needs_clarification_blocks_implementation_dispatch(self) -> None:
        path = write_temp_checklist(
            build_checklist(
                make_item(1, "item-1", "One", status="ready", plan="- 计划：已补齐"),
                make_item(2, "item-2", "Two", status="ready", plan="- 待填写"),
                make_item(3, "item-3", "Three"),
                make_item(4, "item-4", "Four"),
            ).replace("- 启动判定：ready", "- 启动判定：needs-clarification")
        )

        payload = controller.build_cycle_plan(path)

        packets = payload["dispatch_packets"]
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["implementation_allowed"])
        self.assertIn("startup_needs_clarification_blocks_dispatch", violation_codes(payload))
        self.assertNotIn("implementation", [packet["packet_type"] for packet in packets])
        self.assertIn("planning", [packet["packet_type"] for packet in packets])

        with self.assertRaisesRegex(ValueError, "启动判定不是 ready"):
            controller.start_item(path, "item-1", "agent-1")

    def test_init_defaults_to_needs_clarification_until_startup_plan_is_completed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / ".strict-review" / "startup-gate" / "checklist.md"
            controller.init_checklist(
                path,
                "Startup Gate",
                "完成完整需求",
                [
                    {
                        "item_id": "item-1",
                        "title": "First item",
                        "blocked_by": [],
                        "shared_surfaces": [],
                        "parallel_group": "wave-1",
                    }
                ],
            )

            payload = controller.build_cycle_plan(path)
            document = path.read_text(encoding="utf-8")

            self.assertTrue(payload["ok"])
            self.assertFalse(payload["implementation_allowed"])
            self.assertIn("- 请求内未纳入事项：待确认", document)
            self.assertIn("- 启动判定：needs-clarification", document)
            self.assertEqual(["planning"], [packet["packet_type"] for packet in payload["dispatch_packets"]])

    def test_startup_ready_requires_key_startup_plan_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / ".strict-review" / "startup-ready-fields" / "checklist.md"
            controller.init_checklist(
                path,
                "Startup Ready Fields",
                "完成完整需求",
                [
                    {
                        "item_id": "item-1",
                        "title": "First item",
                        "blocked_by": [],
                        "shared_surfaces": [],
                        "parallel_group": "wave-1",
                    }
                ],
            )
            document = path.read_text(encoding="utf-8")
            document = document.replace("- 请求内未纳入事项：待确认", "- 请求内未纳入事项：无")
            document = document.replace("- 启动判定：needs-clarification", "- 启动判定：ready")
            path.write_text(document, encoding="utf-8")

            payload = controller.validate_checklist(path)

            self.assertFalse(payload["ok"])
            self.assertIn("startup_ready_with_incomplete_startup_plan", violation_codes(payload))

    def test_cycle_emits_parallel_planning_packets_even_when_shared_surfaces_overlap(self) -> None:
        path = write_temp_checklist(
            build_checklist(
                make_item(1, "item-1", "One", surfaces="[shared-file]", status="ready", plan="- 待填写"),
                make_item(2, "item-2", "Two", surfaces="[shared-file]", status="ready", plan="- 待填写"),
                make_item(3, "item-3", "Three", status="ready"),
                make_item(4, "item-4", "Four", status="ready"),
            )
        )

        payload = controller.build_cycle_plan(path)

        packets = payload["dispatch_packets"]
        packet_by_item = {packet["item_id"]: packet for packet in packets}
        self.assertEqual("planning", packet_by_item["item-1"]["packet_type"])
        self.assertEqual("planning", packet_by_item["item-2"]["packet_type"])

    def test_delivery_summary_complete_cannot_hide_unfinished_request_scope(self) -> None:
        path = write_temp_checklist(
            build_checklist(
                make_item(
                    1,
                    "item-1",
                    "One",
                    status="done",
                    assigned="agent-1",
                    reviewer_id="reviewer-1",
                    reviewer_state="closed",
                    implementation="- 已实现",
                    verification="- 已验证",
                    review="- 审核结论：通过\n- 关闭状态：closed",
                ),
                make_item(
                    2,
                    "item-2",
                    "Two",
                    status="done",
                    assigned="agent-2",
                    reviewer_id="reviewer-2",
                    reviewer_state="closed",
                    implementation="- 已实现",
                    verification="- 已验证",
                    review="- 审核结论：通过\n- 关闭状态：closed",
                ),
                make_item(
                    3,
                    "item-3",
                    "Three",
                    status="done",
                    assigned="agent-3",
                    reviewer_id="reviewer-3",
                    reviewer_state="closed",
                    implementation="- 已实现",
                    verification="- 已验证",
                    review="- 审核结论：通过\n- 关闭状态：closed",
                ),
                make_item(
                    4,
                    "item-4",
                    "Four",
                    status="done",
                    assigned="agent-4",
                    reviewer_id="reviewer-4",
                    reviewer_state="closed",
                    implementation="- 已实现",
                    verification="- 已验证",
                    review="- 审核结论：通过\n- 关闭状态：closed",
                ),
            )
            .replace("- 完成状态：pending", "- 完成状态：complete")
            .replace("- 用户请求内未交付内容：待完成", "- 用户请求内未交付内容：还有第二阶段")
        )

        payload = controller.validate_checklist(path)

        self.assertFalse(payload["ok"])
        self.assertIn("delivery_complete_with_unfinished_request_scope", violation_codes(payload))

    def test_all_items_done_warns_until_delivery_summary_is_complete(self) -> None:
        path = write_temp_checklist(
            build_checklist(
                make_item(
                    1,
                    "item-1",
                    "One",
                    status="done",
                    assigned="agent-1",
                    reviewer_id="reviewer-1",
                    reviewer_state="closed",
                    implementation="- 已实现",
                    verification="- 已验证",
                    review="- 审核结论：通过\n- 关闭状态：closed",
                ),
                make_item(
                    2,
                    "item-2",
                    "Two",
                    status="done",
                    assigned="agent-2",
                    reviewer_id="reviewer-2",
                    reviewer_state="closed",
                    implementation="- 已实现",
                    verification="- 已验证",
                    review="- 审核结论：通过\n- 关闭状态：closed",
                ),
                make_item(
                    3,
                    "item-3",
                    "Three",
                    status="done",
                    assigned="agent-3",
                    reviewer_id="reviewer-3",
                    reviewer_state="closed",
                    implementation="- 已实现",
                    verification="- 已验证",
                    review="- 审核结论：通过\n- 关闭状态：closed",
                ),
                make_item(
                    4,
                    "item-4",
                    "Four",
                    status="done",
                    assigned="agent-4",
                    reviewer_id="reviewer-4",
                    reviewer_state="closed",
                    implementation="- 已实现",
                    verification="- 已验证",
                    review="- 审核结论：通过\n- 关闭状态：closed",
                ),
            )
        )

        payload = controller.validate_checklist(path)

        self.assertTrue(payload["ok"])
        self.assertIn("delivery_summary_pending_after_all_items_done", violation_codes(payload))

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

    def test_cycle_emits_all_eligible_packets_without_subagent_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / ".strict-review" / "no-subagent-limit" / "checklist.md"
            controller.init_checklist(
                path,
                "No Subagent Limit",
                "取消子 agent 并发数量限制",
                [
                    {
                        "item_id": f"item-{index}",
                        "title": f"Item {index}",
                        "blocked_by": [],
                        "shared_surfaces": [f"surface-{index}"],
                        "parallel_group": "wave-1",
                    }
                    for index in range(1, 9)
                ],
            )
            mark_startup_ready(path)

            for index in range(1, 4):
                item_id = f"item-{index}"
                self.assertTrue(controller.plan_item(path, item_id, f"- 计划：准备审核事项 {index}")["ok"])
                self.assertTrue(controller.start_item(path, item_id, f"agent-{index}")["ok"])
                self.assertTrue(
                    controller.mark_implemented(
                        path,
                        item_id,
                        f"- 已实现事项 {index}",
                        f"- 已验证事项 {index}",
                    )["ok"]
                )

            for index in range(4, 9):
                self.assertTrue(controller.plan_item(path, f"item-{index}", f"- 计划：准备实施事项 {index}")["ok"])

            payload = controller.build_cycle_plan(path)

            packets = payload["dispatch_packets"]
            self.assertEqual({"implementation": "unlimited", "reviewer": "unlimited"}, payload["limits"])
            self.assertEqual(8, len(packets))
            self.assertEqual(["review", "review", "review"], [packet["packet_type"] for packet in packets[:3]])
            self.assertEqual(["implementation"] * 5, [packet["packet_type"] for packet in packets[3:]])

    def test_start_and_assign_reviewer_allow_more_than_legacy_subagent_limits(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / ".strict-review" / "legacy-limit-removal" / "checklist.md"
            controller.init_checklist(
                path,
                "Legacy Limit Removal",
                "验证旧子 agent 并发上限已取消",
                [
                    {
                        "item_id": f"item-{index}",
                        "title": f"Item {index}",
                        "blocked_by": [],
                        "shared_surfaces": [f"surface-{index}"],
                        "parallel_group": "wave-1",
                    }
                    for index in range(1, 6)
                ],
            )
            mark_startup_ready(path)

            for index in range(1, 6):
                item_id = f"item-{index}"
                self.assertTrue(controller.plan_item(path, item_id, f"- 计划：实施事项 {index}")["ok"])
                self.assertTrue(controller.start_item(path, item_id, f"agent-{index}")["ok"])

            active_payload = controller.validate_checklist(path)
            self.assertTrue(active_payload["ok"])
            self.assertEqual(5, active_payload["counts"]["active"])

            for index in range(1, 4):
                item_id = f"item-{index}"
                self.assertTrue(
                    controller.mark_implemented(
                        path,
                        item_id,
                        f"- 已实现事项 {index}",
                        f"- 已验证事项 {index}",
                    )["ok"]
                )
                self.assertTrue(controller.queue_review(path, item_id)["ok"])
                self.assertTrue(controller.assign_reviewer(path, item_id, f"reviewer-{index}")["ok"])

            review_payload = controller.validate_checklist(path)
            self.assertTrue(review_payload["ok"])
            self.assertEqual(3, review_payload["counts"]["in-review"])
            self.assertNotIn("implementation_concurrency_exceeded", violation_codes(review_payload))
            self.assertNotIn("reviewer_concurrency_exceeded", violation_codes(review_payload))

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
        self.assertIn("不要使用 H1/H2/H3", "\n".join(packets[0]["handoff_requirements"]))

        planning_packet = packet_by_type["planning"]
        implementation_packet = packet_by_type["implementation"]
        review_packet = packet_by_type["review"]
        self.assertIn("不要直接实施代码", "\n".join(planning_packet["non_goals"]))
        self.assertIn("plan", implementation_packet["input_artifacts"])
        self.assertIn("implementation_record", review_packet["input_artifacts"])
        self.assertIn("verification_record", review_packet["input_artifacts"])
        self.assertTrue(planning_packet["output_artifacts"]["document_dir"].endswith("/.strict-review/测试-controller"))
        self.assertEqual("测试-controller", planning_packet["output_artifacts"]["task_dir"])
        self.assertTrue(planning_packet["output_artifacts"]["plan_file"].endswith("/.strict-review/测试-controller/item-1-plan.md"))
        self.assertTrue(implementation_packet["output_artifacts"]["implementation_file"].endswith("/.strict-review/测试-controller/item-3-implementation.md"))
        self.assertTrue(implementation_packet["output_artifacts"]["verification_file"].endswith("/.strict-review/测试-controller/item-3-verification.md"))
        self.assertTrue(review_packet["output_artifacts"]["review_file"].endswith("/.strict-review/测试-controller/item-2-review.md"))
        self.assertIn(".strict-review", implementation_packet["commands"]["mark_implemented"])
        self.assertIn(".strict-review", review_packet["commands"]["approve"])

    def test_validate_warns_when_checklist_is_not_in_dot_document_directory(self) -> None:
        path = write_temp_checklist(
            build_checklist(
                make_item(1, "item-1", "One"),
                make_item(2, "item-2", "Two"),
                make_item(3, "item-3", "Three"),
                make_item(4, "item-4", "Four"),
            )
        )

        payload = controller.validate_checklist(path)

        self.assertTrue(payload["ok"])
        self.assertIn("checklist_outside_strict_review_task_directory", violation_codes(payload))

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
            mark_startup_ready(path)

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

    def test_lifecycle_normalizes_heading_rich_artifact_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / ".strict-review" / "markdown-artifacts" / "checklist.md"
            controller.init_checklist(
                path,
                "Markdown Artifact Checklist",
                "复现并修复 artifact Markdown 标题冲突",
                [
                    {
                        "item_id": "item-1",
                        "title": "Handle markdown artifacts",
                        "blocked_by": [],
                        "shared_surfaces": [],
                        "parallel_group": "wave-1",
                    }
                ],
            )
            mark_startup_ready(path)

            plan_payload = controller.plan_item(
                path,
                "item-1",
                "# 计划总览\n## 改动范围\n- 修改 controller\n\n### 验证方式\n```markdown\n## 代码块中的标题保持原样\n```",
            )
            self.assertTrue(plan_payload["ok"])
            self.assertIn("#### 计划总览", plan_payload["items"][0]["plan"])
            self.assertIn("##### 改动范围", plan_payload["items"][0]["plan"])
            self.assertIn("###### 验证方式", plan_payload["items"][0]["plan"])
            self.assertIn("## 代码块中的标题保持原样", plan_payload["items"][0]["plan"])

            self.assertTrue(controller.start_item(path, "item-1", "agent-1")["ok"])
            implemented_payload = controller.mark_implemented(
                path,
                "item-1",
                "## 实施摘要\n- 已降级 artifact 标题",
                "### 验证结果\n- 回归测试通过",
            )
            self.assertTrue(implemented_payload["ok"])
            self.assertTrue(controller.queue_review(path, "item-1")["ok"])
            self.assertTrue(controller.assign_reviewer(path, "item-1", "reviewer-1")["ok"])
            approval_payload = controller.approve_item(
                path,
                "item-1",
                "## 审核结论\n- 审核结论：通过，未发现问题",
            )

            document = path.read_text(encoding="utf-8")
            self.assertTrue(approval_payload["ok"])
            self.assertIn("##### 实施摘要", document)
            self.assertIn("###### 验证结果", document)
            self.assertIn("##### 审核结论", document)
            self.assertNotIn("missing_item_heading", violation_codes(approval_payload))

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
            path = Path(temp_dir) / ".strict-review" / "cli-init" / "checklist.md"
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
            self.assertIn("## 文档位置", path.read_text(encoding="utf-8"))
            self.assertIn(f"- 专属目录：{path.parent}", path.read_text(encoding="utf-8"))
            self.assertIn("- fallback_agent：current", path.read_text(encoding="utf-8"))
            self.assertTrue(path.parent.is_dir())

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
