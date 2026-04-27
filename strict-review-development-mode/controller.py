from __future__ import annotations

import argparse
from dataclasses import dataclass
import importlib.util
import json
from pathlib import Path
import re
import sys
from types import ModuleType
from typing import Any, Dict, List, Optional, Set, Tuple, Union


MODULE_DIR = Path(__file__).resolve().parent
VIEWER_DIR = MODULE_DIR / "viewer"
CONTROLLER_PATH = MODULE_DIR / "controller.py"

IMPLEMENTATION_LIMIT = 4
REVIEWER_LIMIT = 2
FINISHED_STATUS = "done"
DEFAULT_AGENT = "current"
STRICT_REVIEW_DOCUMENT_DIR = ".strict-review"
DEFAULT_TASK_DIRECTORY = "current-task"
DEFAULT_CHECKLIST_FILE = "checklist.md"
# controller 只表达“由协调者决定调用方式”，不承载任何平台私有调用参数。
INVOCATION_POLICY = "coordinator-decides"
KNOWN_STATUSES = {
    "blocked",
    "ready",
    "active",
    "implemented",
    "review-queued",
    "in-review",
    "changes-requested",
    "done",
}

PLACEHOLDER_VALUES = {
    "",
    "none",
    "n/a",
    "无",
    "待填写",
    "<待填写>",
    "未开始",
    "not-started",
    "no content recorded.",
}

APPROVAL_MARKERS = (
    "通过",
    "approved",
    "approve",
    "no issues",
    "no findings",
    "没有发现问题",
    "未发现问题",
)

WORKFLOW_STATE_DIAGRAM = """stateDiagram-v2
  [*] --> blocked: 有未完成依赖
  [*] --> ready: 无依赖或依赖已 done

  blocked --> ready: cycle --write / 上游 done
  ready --> ready: planning packet / controller plan
  ready --> active: controller start

  active --> implemented: controller mark-implemented
  implemented --> review_queued: controller queue-review
  implemented --> in_review: controller assign-reviewer / reviewer 槽位可用
  review_queued --> in_review: controller assign-reviewer
  in_review --> in_review: controller replace-reviewer / reviewer 超时

  in_review --> changes_requested: controller request-changes
  changes_requested --> active: controller start / rework
  changes_requested --> implemented: controller mark-implemented / 补验证

  in_review --> done: controller approve
  done --> [*]
"""

H2_RE = re.compile(r"^##\s+(.*\S)\s*$")
H3_RE = re.compile(r"^###\s+(.*\S)\s*$")
FENCE_RE = re.compile(r"^\s*(?P<fence>`{3,}|~{3,})")
STRUCTURED_LINE_RE = re.compile(r"^(\s*-\s+)([^：:]+?)(\s*[：:]\s*)(.*)$")
CHECKLIST_ITEM_RE = re.compile(r"^(\s*-\s+\[)(?: |x|X)(\]\s+\d+\.\s+)(.*)$")

# 不同 packet 类型会优先读取不同的路由字段；这些字段都是不透明 agent 标签。
ROUTE_KEYS_BY_PACKET_TYPE = {
    "planning": ("planning_agent",),
    "replan": ("planning_agent", "rework_agent"),
    "implementation": ("implementation_agent",),
    "rework": ("rework_agent", "implementation_agent"),
    "review": ("review_agent",),
}

ROLE_BY_PACKET_TYPE = {
    "planning": "planning",
    "replan": "planning",
    "implementation": "implementation",
    "rework": "rework",
    "review": "review",
}

GLOBAL_ROUTING_KEYS = (
    "coordinator_agent",
    "default_agent",
    "fallback_agent",
    "planning_agent",
    "implementation_agent",
    "rework_agent",
    "review_agent",
)

ITEM_ROUTING_KEYS = (
    "planning_agent",
    "implementation_agent",
    "rework_agent",
    "review_agent",
)


@dataclass(frozen=True)
class ControllerViolation:
    severity: str
    code: str
    message: str
    item_id: Optional[str] = None
    heading: Optional[str] = None
    key: Optional[str] = None

    def to_dict(self) -> Dict[str, object]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "item_id": self.item_id,
            "heading": self.heading,
            "key": self.key,
        }


@dataclass(frozen=True)
class DispatchPacket:
    packet_type: str
    role: str
    target_agent: str
    fallback_agent: str
    routing_source: str
    invocation_policy: str
    item_id: str
    title: str
    workflow_goal: str
    agent_objective: str
    local_scope: List[str]
    success_criteria: List[str]
    non_goals: List[str]
    handoff_requirements: List[str]
    input_artifacts: Dict[str, object]
    output_artifacts: Dict[str, str]
    commands: Dict[str, str]
    command: str
    prompt: str
    shared_surfaces: List[str]
    blocked_by: List[str]
    blocks: List[str]

    def to_dict(self) -> Dict[str, object]:
        return {
            "packet_type": self.packet_type,
            "role": self.role,
            "target_agent": self.target_agent,
            "fallback_agent": self.fallback_agent,
            "routing_source": self.routing_source,
            "invocation_policy": self.invocation_policy,
            "item_id": self.item_id,
            "title": self.title,
            "workflow_goal": self.workflow_goal,
            "agent_objective": self.agent_objective,
            "local_scope": self.local_scope,
            "success_criteria": self.success_criteria,
            "non_goals": self.non_goals,
            "handoff_requirements": self.handoff_requirements,
            "input_artifacts": self.input_artifacts,
            "output_artifacts": self.output_artifacts,
            "commands": self.commands,
            "command": self.command,
            "prompt": self.prompt,
            "shared_surfaces": self.shared_surfaces,
            "blocked_by": self.blocked_by,
            "blocks": self.blocks,
        }


def _load_local_module(module_basename: str) -> ModuleType:
    module_path = VIEWER_DIR / f"{module_basename}.py"
    spec = importlib.util.spec_from_file_location(f"strict_review_controller_{module_basename}", module_path)
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


def validate_checklist(checklist_path: Union[str, Path]) -> Dict[str, object]:
    parsed = parse_file(checklist_path)
    snapshot = build_snapshot(parsed)
    items = list(snapshot["items"])
    workflow_context = _workflow_context(parsed)
    violations = (
        _build_path_violations(checklist_path, workflow_context)
        + _build_parser_violations(snapshot)
        + _build_protocol_violations(parsed, snapshot)
    )
    return {
        "ok": not any(violation.severity == "error" for violation in violations),
        "checklist": str(Path(checklist_path)),
        "counts": snapshot["counts"],
        "violations": [violation.to_dict() for violation in violations],
        "items": items,
    }


def build_cycle_plan(checklist_path: Union[str, Path]) -> Dict[str, object]:
    parsed = parse_file(checklist_path)
    snapshot = build_snapshot(parsed)
    items = list(snapshot["items"])
    item_by_id = _item_by_id(items)
    agent_routing = _agent_routing_policy(parsed)
    workflow_context = _workflow_context(parsed)
    violations = (
        _build_path_violations(checklist_path, workflow_context)
        + _build_parser_violations(snapshot)
        + _build_protocol_violations(parsed, snapshot)
    )
    has_errors = any(violation.severity == "error" for violation in violations)
    status_updates = [] if has_errors else _recommended_status_updates(items, item_by_id)
    packets = [] if has_errors else _build_dispatch_packets(Path(checklist_path), items, item_by_id, agent_routing, workflow_context)
    active_items = [item["item_id"] for item in items if item.get("dispatch_status") == "active"]
    reviewing_items = [item["item_id"] for item in items if item.get("dispatch_status") == "in-review"]
    return {
        "ok": not has_errors,
        "checklist": str(Path(checklist_path)),
        "limits": {
            "implementation": IMPLEMENTATION_LIMIT,
            "reviewer": REVIEWER_LIMIT,
        },
        "active_items": active_items,
        "in_review_items": reviewing_items,
        "agent_routing": agent_routing,
        "workflow_context": workflow_context,
        "status_updates": status_updates,
        "dispatch_packets": [packet.to_dict() for packet in packets],
        "next_action": _next_action_text(status_updates, packets, violations),
        "violations": [violation.to_dict() for violation in violations],
    }


def write_status_updates(checklist_path: Union[str, Path], updates: List[Dict[str, object]]) -> None:
    path = Path(checklist_path)
    document = path.read_text(encoding="utf-8")
    for update in updates:
        item_id = str(update["item_id"])
        status = str(update["dispatch_status"])
        document = _set_structured_field(document, item_id, "dispatch_status", status)
        document = _set_structured_field(document, item_id, "next_action", str(update.get("reason") or "等待下一轮调度"))
    path.write_text(document, encoding="utf-8")


def plan_item(checklist_path: Union[str, Path], item_id: str, plan_text: str) -> Dict[str, object]:
    parsed = parse_file(checklist_path)
    snapshot = build_snapshot(parsed)
    _require_protocol_ready(parsed, snapshot)
    _require_item(snapshot["items"], item_id)

    path = Path(checklist_path)
    document = path.read_text(encoding="utf-8")
    document = _set_item_section(document, item_id, "计划", plan_text.strip() + "\n")
    document = _set_structured_field(document, item_id, "next_action", "计划已写入；等待 start 命令进入实施")
    path.write_text(document, encoding="utf-8")
    return validate_checklist(path)


def start_item(checklist_path: Union[str, Path], item_id: str, agent_id: str) -> Dict[str, object]:
    parsed = parse_file(checklist_path)
    snapshot = build_snapshot(parsed)
    item = _require_item(snapshot["items"], item_id)
    item_by_id = _item_by_id(snapshot["items"])

    # 这里把“能不能进入 active”的判断固定在代码里，避免弱 agent 只凭文字协议自行推进。
    _require_protocol_ready(parsed, snapshot)
    _ensure_plan_ready(item)
    _ensure_dependencies_done(item, item_by_id)
    _ensure_active_slot_available(snapshot["items"], item)
    _ensure_no_active_surface_conflict(snapshot["items"], item)

    path = Path(checklist_path)
    document = path.read_text(encoding="utf-8")
    document = _set_structured_field(document, item_id, "dispatch_status", "active")
    document = _set_structured_field(document, item_id, "assigned_subagent", agent_id)
    document = _set_structured_field(document, item_id, "当前状态", "实施中")
    document = _set_structured_field(document, item_id, "next_action", "按计划实施并记录验证结果")
    path.write_text(document, encoding="utf-8")
    return validate_checklist(path)


def mark_implemented(
    checklist_path: Union[str, Path],
    item_id: str,
    implementation_text: str,
    verification_text: str,
) -> Dict[str, object]:
    parsed = parse_file(checklist_path)
    snapshot = build_snapshot(parsed)
    _require_protocol_ready(parsed, snapshot)
    item = _require_item(snapshot["items"], item_id)
    status = item.get("dispatch_status")
    if status not in {"active", "changes-requested"}:
        raise ValueError(f"{item_id} must be active or changes-requested before mark-implemented")

    if _is_blankish(implementation_text) or _is_blankish(verification_text):
        raise ValueError("implementation and verification records are required before implemented")

    path = Path(checklist_path)
    document = path.read_text(encoding="utf-8")
    document = _set_item_section(document, item_id, "实施记录", implementation_text.strip() + "\n")
    document = _set_item_section(document, item_id, "验证记录", verification_text.strip() + "\n")
    document = _set_structured_field(document, item_id, "dispatch_status", "implemented")
    document = _set_structured_field(document, item_id, "next_action", "实现和验证已记录；等待 queue-review")
    path.write_text(document, encoding="utf-8")
    return validate_checklist(path)


def queue_review(checklist_path: Union[str, Path], item_id: str) -> Dict[str, object]:
    parsed = parse_file(checklist_path)
    snapshot = build_snapshot(parsed)
    _require_protocol_ready(parsed, snapshot)
    item = _require_item(snapshot["items"], item_id)
    if item.get("dispatch_status") != "implemented":
        raise ValueError(f"{item_id} must be implemented before queue-review")
    _ensure_implementation_ready(item)
    _ensure_verification_ready(item)

    path = Path(checklist_path)
    document = path.read_text(encoding="utf-8")
    document = _set_structured_field(document, item_id, "dispatch_status", "review-queued")
    document = _set_structured_field(document, item_id, "next_action", "等待 reviewer 槽位")
    path.write_text(document, encoding="utf-8")
    return validate_checklist(path)


def assign_reviewer(checklist_path: Union[str, Path], item_id: str, reviewer_id: str) -> Dict[str, object]:
    parsed = parse_file(checklist_path)
    snapshot = build_snapshot(parsed)
    _require_protocol_ready(parsed, snapshot)
    item = _require_item(snapshot["items"], item_id)
    if item.get("dispatch_status") not in {"implemented", "review-queued"}:
        raise ValueError(f"{item_id} must be implemented or review-queued before assign-reviewer")
    _ensure_reviewer_slot_available(snapshot["items"], item)
    _ensure_implementation_ready(item)
    _ensure_verification_ready(item)
    _ensure_independent_reviewer(item, reviewer_id)

    path = Path(checklist_path)
    document = path.read_text(encoding="utf-8")
    document = _set_structured_field(document, item_id, "dispatch_status", "in-review")
    document = _set_structured_field(document, item_id, "reviewer_id", reviewer_id)
    document = _set_structured_field(document, item_id, "reviewer_state", "reviewing")
    document = _set_structured_field(document, item_id, "next_action", "等待 reviewer 返回审核结论")
    document = _set_review_field(document, item_id, "Reviewer", reviewer_id)
    document = _set_review_field(document, item_id, "Reviewer 状态", "reviewing")
    document = _set_review_field(document, item_id, "关闭状态", "open")
    path.write_text(document, encoding="utf-8")
    return validate_checklist(path)


def replace_reviewer(
    checklist_path: Union[str, Path],
    item_id: str,
    from_reviewer: str,
    to_reviewer: str,
    reason_text: str,
) -> Dict[str, object]:
    parsed = parse_file(checklist_path)
    snapshot = build_snapshot(parsed)
    _require_protocol_ready(parsed, snapshot)
    item = _require_item(snapshot["items"], item_id)
    if item.get("dispatch_status") != "in-review":
        raise ValueError(f"{item_id} must be in-review before replace-reviewer")

    current_reviewer = _field(item, "reviewer_id")
    replacement_reviewer = _agent_name(to_reviewer)
    if _agent_name(from_reviewer) != current_reviewer:
        raise ValueError(f"{item_id} reviewer_id is {current_reviewer}; cannot replace from {from_reviewer}")
    if not replacement_reviewer:
        raise ValueError("replacement reviewer is required")
    if replacement_reviewer == current_reviewer:
        raise ValueError("replacement reviewer must differ from current reviewer")
    _ensure_independent_reviewer(item, replacement_reviewer, "replacement reviewer")
    if _is_blankish(reason_text):
        raise ValueError("replacement reason is required")

    reason = reason_text.strip()
    replacement_note = f"{replacement_reviewer}（替换 {current_reviewer}；原因：{reason}）"

    path = Path(checklist_path)
    document = path.read_text(encoding="utf-8")
    # reviewer replacement 是受控状态迁移：保持 item in-review，只替换独立审核人和审计记录。
    document = _set_structured_field(document, item_id, "reviewer_id", replacement_reviewer)
    document = _set_structured_field(document, item_id, "reviewer_state", "reviewing")
    document = _set_structured_field(document, item_id, "next_action", "等待 replacement reviewer 返回审核结论")
    document = _set_review_field(document, item_id, "Reviewer", replacement_reviewer)
    document = _set_review_field(document, item_id, "Reviewer 状态", "reviewing")
    document = _set_review_field(document, item_id, "原 Reviewer 状态", "replaced")
    document = _set_review_field(document, item_id, "Replacement Reviewer", replacement_note)
    document = _set_review_field(document, item_id, "关闭状态", "open")
    document = _set_review_field(document, item_id, "关闭原因", f"原 reviewer {current_reviewer} 已替换：{reason}")
    path.write_text(document, encoding="utf-8")
    return validate_checklist(path)


def request_changes(checklist_path: Union[str, Path], item_id: str, review_text: str) -> Dict[str, object]:
    parsed = parse_file(checklist_path)
    snapshot = build_snapshot(parsed)
    _require_protocol_ready(parsed, snapshot)
    item = _require_item(snapshot["items"], item_id)
    if item.get("dispatch_status") != "in-review":
        raise ValueError(f"{item_id} must be in-review before request-changes")
    if _is_blankish(review_text):
        raise ValueError("review text is required when requesting changes")

    path = Path(checklist_path)
    document = path.read_text(encoding="utf-8")
    document = _set_item_section(document, item_id, "审核记录", review_text.strip() + "\n")
    document = _set_structured_field(document, item_id, "dispatch_status", "changes-requested")
    document = _set_structured_field(document, item_id, "next_action", "优先处理 reviewer 反馈并补充验证")
    path.write_text(document, encoding="utf-8")
    return validate_checklist(path)


def approve_item(checklist_path: Union[str, Path], item_id: str, review_text: str) -> Dict[str, object]:
    parsed = parse_file(checklist_path)
    snapshot = build_snapshot(parsed)
    _require_protocol_ready(parsed, snapshot)
    item = _require_item(snapshot["items"], item_id)
    if item.get("dispatch_status") != "in-review":
        raise ValueError(f"{item_id} must be in-review before approve")
    if _is_blankish(review_text):
        raise ValueError("approval review text is required")
    if not _contains_approval(review_text):
        raise ValueError("approval text must contain an explicit approval marker")

    path = Path(checklist_path)
    document = path.read_text(encoding="utf-8")
    document = _set_item_section(document, item_id, "审核记录", review_text.strip() + "\n")
    document = _set_structured_field(document, item_id, "dispatch_status", "done")
    document = _set_structured_field(document, item_id, "reviewer_state", "closed")
    document = _set_structured_field(document, item_id, "next_action", "事项已完成")
    document = _set_review_field(document, item_id, "关闭状态", "closed")
    document = _set_review_field(document, item_id, "关闭原因", "审核通过后关闭")
    document = _set_checklist_checkbox(document, item["title"], checked=True)
    path.write_text(document, encoding="utf-8")
    return validate_checklist(path)


def set_agent_routing(
    checklist_path: Union[str, Path],
    item_id: Optional[str] = None,
    coordinator_agent: Optional[str] = None,
    default_agent: Optional[str] = None,
    fallback_agent: Optional[str] = None,
    planning_agent: Optional[str] = None,
    implementation_agent: Optional[str] = None,
    rework_agent: Optional[str] = None,
    review_agent: Optional[str] = None,
) -> Dict[str, object]:
    routes = {
        "coordinator_agent": coordinator_agent,
        "default_agent": default_agent,
        "fallback_agent": fallback_agent,
        "planning_agent": planning_agent,
        "implementation_agent": implementation_agent,
        "rework_agent": rework_agent,
        "review_agent": review_agent,
    }
    updates = _routing_updates(routes)
    if not updates:
        raise ValueError("at least one agent routing field is required")

    path = Path(checklist_path)
    document = path.read_text(encoding="utf-8")
    if item_id:
        invalid_keys = [key for key in updates if key not in ITEM_ROUTING_KEYS]
        if invalid_keys:
            raise ValueError("item routing only supports: " + ", ".join(ITEM_ROUTING_KEYS))
        # item 级路由只影响指定事项，适合响应用户“这个事项交给某个 agent”的偏好。
        for key in ITEM_ROUTING_KEYS:
            if key in updates:
                document = _set_structured_field(document, item_id, key, updates[key])
    else:
        # 全局路由表达用户对不同工作阶段的默认偏好，实际调用参数仍由 coordinator 决定。
        for key in GLOBAL_ROUTING_KEYS:
            if key in updates:
                document = _set_top_level_bullet_field(document, "Agent 路由策略", key, updates[key])
        document = _set_top_level_bullet_field(document, "Agent 路由策略", "invocation_policy", INVOCATION_POLICY)

    path.write_text(document, encoding="utf-8")
    return build_cycle_plan(path)


def init_checklist(
    checklist_path: Union[str, Path],
    title: str,
    request: str,
    items: List[Dict[str, object]],
) -> None:
    if not items:
        raise ValueError("items must not be empty")
    item_ids = [str(item["item_id"]) for item in items]
    if len(item_ids) != len(set(item_ids)):
        raise ValueError("item_id values must be unique")

    checklist_file = Path(checklist_path)
    document_dir = _strict_review_document_dir(checklist_file, {"workflow_goal": request or title})
    item_by_id = {str(item["item_id"]): item for item in items}
    lines: List[str] = [
        f"# {title}",
        "",
        "## 模式",
        "- 强审开发模式（controller-enforced DAG-first）",
        "",
        "## 文档位置",
        f"- 专属目录：{document_dir}",
        f"- checklist：{checklist_file}",
        f"- 派工文档命名：{document_dir}/<item_id>-<kind>.md",
        "- 约束：强审流程产生的计划、实施、验证、审核等文档只写入上述点号目录",
        "",
        "## 审核设置",
        "- 审核模型目标：gpt-5.4",
        "- 推理强度目标：xhigh",
        f"- 实施并行上限：{IMPLEMENTATION_LIMIT}",
        f"- reviewer 并行上限：{REVIEWER_LIMIT}",
        "- 首次等待窗口：10min",
        "- 二次探测窗口：10-20min",
        "- 硬超时门槛：30min",
        "",
        "## Agent 路由策略",
        "- coordinator_agent：current",
        "- default_agent：current",
        "- fallback_agent：current",
        "- planning_agent：current",
        "- implementation_agent：current",
        "- rework_agent：current",
        "- review_agent：current",
        "- invocation_policy：coordinator-decides",
        "",
        "## 任务归属判定",
        f"- 当前请求：{request}",
        "- 判定结果：different-task",
        "- 判定依据：controller init 创建的新 checklist",
        "- 关联旧 checklist：none",
        "- 处理动作：在当前 checklist 记录本次任务进度",
        "",
        "## 当前执行状态",
        "- 当前状态：进行中",
        "- 当前阻塞原因：无",
        "- 当前调度摘要：由 controller cycle 推导",
        "- 当前可执行动作摘要：运行 controller cycle 获取调度包",
        "",
        "## 任务重整摘要",
        "- 原始任务形态：由调用方提供 item-level 工作包",
        "- 是否触发任务重整：否",
        "- 重整触发原因：无",
        "- 工作包映射：" + "、".join(item_ids),
        "- 并行批次说明：由 blocked_by 与 shared_surfaces 推导",
        "- 关键不可并行约束：由 DAG 与 shared_surfaces 固定化校验",
        "",
        "## Checklist",
    ]

    for index, item in enumerate(items, start=1):
        lines.append(f"- [ ] {index}. {item.get('title', item['item_id'])}")

    lines.extend(["", "## DAG 概览", "- 关键串行路径：由 blocked_by / blocks 推导", "- 依赖分层摘要：运行 controller cycle 查看", "- 可并行批次：运行 controller cycle 查看", "", "## Mermaid DAG", "```mermaid", "graph TD"])
    for item in items:
        item_id = str(item["item_id"])
        blocked_by = _normalize_list(item.get("blocked_by"))
        label = str(item.get("title") or item_id)
        if not blocked_by:
            lines.append(f"  {item_id}[{label}]")
        for dependency_id in blocked_by:
            dependency_title = str(item_by_id.get(dependency_id, {}).get("title") or dependency_id)
            lines.append(f"  {dependency_id}[{dependency_title}] --> {item_id}[{label}]")
    lines.extend(["```", ""])

    for index, item in enumerate(items, start=1):
        item_id = str(item["item_id"])
        blocked_by = _normalize_list(item.get("blocked_by"))
        blocks = [candidate_id for candidate_id, candidate in item_by_id.items() if item_id in _normalize_list(candidate.get("blocked_by"))]
        status = "blocked" if blocked_by else "ready"
        title_text = str(item.get("title") or item_id)
        lines.extend(
            [
                f"## Item {index} - {title_text}",
                "### 结构化字段",
                f"- item_id：{item_id}",
                f"- blocked_by：{_format_list(blocked_by)}",
                f"- blocks：{_format_list(blocks)}",
                f"- shared_surfaces：{_format_list(_normalize_list(item.get('shared_surfaces')))}",
                f"- parallel_group：{item.get('parallel_group') or 'wave-1'}",
                f"- dispatch_status：{status}",
                "- assigned_subagent：none",
                "- reviewer_id：none",
                "- reviewer_state：not-started",
                "- 当前状态：未开始",
                "- 阻塞原因：无" if status == "ready" else "- 阻塞原因：等待上游依赖完成",
                "- next_action：写入计划后等待 start 命令",
                "",
                "### 计划",
                "- 待填写",
                "",
                "### 实施记录",
                "- 待填写",
                "",
                "### 验证记录",
                "- 待填写",
                "",
                "### 审核记录",
                "- Reviewer：待填写",
                "- Reviewer 状态：待填写",
                "- 开始时间：待填写",
                "- 累计等待时长：待填写",
                "- 超时次数：待填写",
                "- 审核轮次：待填写",
                "- 审核结论：待填写",
                "- Replacement Reviewer：待填写",
                "- 关闭状态：待填写",
                "- 关闭原因：待填写",
                "",
            ]
        )
    # init 是创建新强审文档的入口，必须负责把点号专属目录建好。
    checklist_file.parent.mkdir(parents=True, exist_ok=True)
    document_dir.mkdir(parents=True, exist_ok=True)
    checklist_file.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _build_parser_violations(snapshot: Dict[str, Any]) -> List[ControllerViolation]:
    violations: List[ControllerViolation] = []
    error_codes = {
        "missing_global_heading",
        "missing_item_heading",
        "missing_structured_field",
        "missing_item_id",
        "duplicate_item_id",
        "unknown_dispatch_status",
        "duplicate_structured_key",
        "malformed_structured_bullet",
        "missing_referenced_item",
        "asymmetric_dependency",
        "dag_cycle",
        "mermaid_validation_unavailable",
    }
    for warning in snapshot.get("warnings", []):
        code = str(warning.get("code") or "warning")
        severity = "error" if code in error_codes else "warning"
        violations.append(
            ControllerViolation(
                severity=severity,
                code=code,
                message=str(warning.get("message") or ""),
                heading=warning.get("heading"),
                key=warning.get("key"),
            )
        )
    return violations


def _build_path_violations(
    checklist_path: Union[str, Path],
    workflow_context: Optional[Dict[str, str]] = None,
) -> List[ControllerViolation]:
    path = Path(checklist_path)
    if _is_strict_review_task_path(path):
        return []
    return [
        ControllerViolation(
            severity="warning",
            code="checklist_outside_strict_review_task_directory",
            message=(
                "Strict-review documents should live in a task-specific directory under "
                f"{STRICT_REVIEW_DOCUMENT_DIR}/; recommended checklist path is "
                f"{_strict_review_document_dir(path, workflow_context) / DEFAULT_CHECKLIST_FILE}"
            ),
        )
    ]


def _build_protocol_violations(parsed: Any, snapshot: Dict[str, Any]) -> List[ControllerViolation]:
    violations: List[ControllerViolation] = []
    items = list(snapshot.get("items", []))
    item_by_id = _item_by_id(items)

    if "任务归属判定" not in getattr(parsed, "top_level_sections", {}):
        violations.append(
            ControllerViolation(
                severity="error",
                code="missing_task_identity_gate",
                message="Checklist must include 任务归属判定 before strict-review execution",
                heading="任务归属判定",
            )
        )
    else:
        identity_result = _extract_bullet_value(parsed.top_level_sections["任务归属判定"], "判定结果")
        if identity_result and identity_result not in {"same-task", "different-task", "uncertain"}:
            violations.append(
                ControllerViolation(
                    severity="error",
                    code="invalid_task_identity_result",
                    message=f"Invalid 任务归属判定 result: {identity_result}",
                    heading="任务归属判定",
                    key="判定结果",
                )
            )

    active_items = [item for item in items if item.get("dispatch_status") == "active"]
    in_review_items = [item for item in items if item.get("dispatch_status") == "in-review"]
    if len(active_items) > IMPLEMENTATION_LIMIT:
        violations.append(
            ControllerViolation(
                severity="error",
                code="implementation_concurrency_exceeded",
                message=f"Active implementation count exceeds {IMPLEMENTATION_LIMIT}",
            )
        )
    if len(in_review_items) > REVIEWER_LIMIT:
        violations.append(
            ControllerViolation(
                severity="error",
                code="reviewer_concurrency_exceeded",
                message=f"In-review count exceeds {REVIEWER_LIMIT}",
            )
        )

    violations.extend(_surface_conflict_violations(active_items, "active_shared_surface_conflict"))

    for item in items:
        item_id = _item_id(item)
        status = item.get("dispatch_status")
        if status not in KNOWN_STATUSES:
            continue
        if status != "blocked":
            missing_dependencies = _missing_done_dependencies(item, item_by_id)
            if missing_dependencies:
                violations.append(
                    ControllerViolation(
                        severity="error",
                        code="dependency_not_done",
                        message=f"{item_id} cannot be {status}; dependencies not done: {', '.join(missing_dependencies)}",
                        item_id=item_id,
                        heading=item.get("heading"),
                        key="blocked_by",
                    )
                )
        if status == "active":
            if _is_blankish(item.get("assigned_subagent")):
                violations.append(_item_error(item, "active_without_assigned_subagent", "Active item must record assigned_subagent", "assigned_subagent"))
            if _is_blankish(item.get("plan")):
                violations.append(_item_error(item, "active_without_plan", "Active item must have a concrete plan", "计划"))
        if status in {"implemented", "review-queued", "in-review", "changes-requested", "done"}:
            if _is_blankish(item.get("implementation_record")):
                violations.append(_item_error(item, "missing_implementation_record", f"{status} item must record implementation", "实施记录"))
            if _is_blankish(item.get("verification_record")):
                violations.append(_item_error(item, "missing_verification_record", f"{status} item must record verification", "验证记录"))
        if status == "in-review":
            if _is_blankish(_field(item, "reviewer_id")):
                violations.append(_item_error(item, "in_review_without_reviewer", "in-review item must record reviewer_id", "reviewer_id"))
            reviewer_state = _field(item, "reviewer_state")
            if reviewer_state not in {"reviewing", "slow", "suspect-stalled"}:
                violations.append(_item_error(item, "invalid_in_review_reviewer_state", "in-review reviewer_state must be reviewing, slow, or suspect-stalled", "reviewer_state"))
        if status in {"in-review", "changes-requested", "done"}:
            reviewer_id = _field(item, "reviewer_id")
            assigned_subagent = _field(item, "assigned_subagent")
            if reviewer_id and assigned_subagent and reviewer_id == assigned_subagent:
                violations.append(_item_error(item, "reviewer_same_as_assigned_subagent", "Reviewer must be independent from implementation agent", "reviewer_id"))
        if status == "changes-requested" and _is_blankish(item.get("review_record")):
            violations.append(_item_error(item, "changes_requested_without_review", "changes-requested item must record reviewer feedback", "审核记录"))
        if status == "done":
            if not _reviewer_closed(item):
                violations.append(_item_error(item, "done_without_closed_reviewer", "done item must close reviewer", "审核记录"))
            if not _contains_approval(item.get("review_record")):
                violations.append(_item_error(item, "done_without_approval", "done item must contain explicit approval", "审核记录"))

    return violations


def _surface_conflict_violations(items: List[Dict[str, Any]], code: str) -> List[ControllerViolation]:
    violations: List[ControllerViolation] = []
    for index, left in enumerate(items):
        left_surfaces = set(left.get("shared_surfaces") or [])
        if not left_surfaces:
            continue
        for right in items[index + 1 :]:
            overlap = sorted(left_surfaces.intersection(right.get("shared_surfaces") or []))
            if not overlap:
                continue
            violations.append(
                ControllerViolation(
                    severity="error",
                    code=code,
                    message=f"{_item_id(left)} and {_item_id(right)} share active surfaces: {', '.join(overlap)}",
                    item_id=_item_id(left),
                    key="shared_surfaces",
                )
            )
    return violations


def _recommended_status_updates(items: List[Dict[str, Any]], item_by_id: Dict[str, Dict[str, Any]]) -> List[Dict[str, object]]:
    updates: List[Dict[str, object]] = []
    for item in items:
        item_id = _item_id(item)
        status = item.get("dispatch_status")
        if status not in {"blocked", "ready"}:
            continue
        missing_dependencies = _missing_done_dependencies(item, item_by_id)
        recommended = "blocked" if missing_dependencies else "ready"
        if status != recommended:
            reason = (
                "等待依赖完成：" + ", ".join(missing_dependencies)
                if missing_dependencies
                else "依赖已完成，可以进入 ready"
            )
            updates.append({"item_id": item_id, "dispatch_status": recommended, "reason": reason})
    return updates


def _agent_routing_policy(parsed: Any) -> Dict[str, str]:
    section_text = getattr(parsed, "top_level_sections", {}).get("Agent 路由策略", "")
    fields = _parse_bullet_fields(section_text)
    # 保持调用策略固定：即使 checklist 误填了平台参数，controller 也不会把它变成协议输出。
    policy: Dict[str, str] = {
        "coordinator_agent": _agent_name(fields.get("coordinator_agent")) or DEFAULT_AGENT,
        "default_agent": _agent_name(fields.get("default_agent")) or DEFAULT_AGENT,
        "fallback_agent": _agent_name(fields.get("fallback_agent")) or DEFAULT_AGENT,
        "planning_agent": _agent_name(fields.get("planning_agent")) or "",
        "implementation_agent": _agent_name(fields.get("implementation_agent")) or "",
        "rework_agent": _agent_name(fields.get("rework_agent")) or "",
        "review_agent": _agent_name(fields.get("review_agent")) or "",
        "invocation_policy": INVOCATION_POLICY,
    }
    return policy


def _workflow_context(parsed: Any) -> Dict[str, str]:
    top_level_sections = getattr(parsed, "top_level_sections", {})
    assignment_text = top_level_sections.get("任务归属判定", "")
    restructuring_text = top_level_sections.get("任务重整摘要", "")
    status_text = top_level_sections.get("当前执行状态", "")
    current_request = _meaningful_text(_extract_bullet_value(assignment_text, "当前请求"))
    work_mapping = _meaningful_text(_extract_bullet_value(restructuring_text, "工作包映射"))
    parallel_summary = _meaningful_text(_extract_bullet_value(restructuring_text, "并行批次说明"))
    current_summary = _meaningful_text(_extract_bullet_value(status_text, "当前调度摘要"))
    return {
        "current_request": current_request,
        "workflow_goal": current_request or "完成当前 checklist 记录的大任务",
        "work_mapping": work_mapping,
        "parallel_summary": parallel_summary,
        "current_summary": current_summary,
    }


def _resolve_agent_route(
    packet_type: str,
    item: Dict[str, Any],
    agent_routing: Dict[str, str],
) -> Dict[str, str]:
    role = ROLE_BY_PACKET_TYPE.get(packet_type, packet_type)
    structured_fields = item.get("structured_fields") if isinstance(item.get("structured_fields"), dict) else {}
    route_keys = ROUTE_KEYS_BY_PACKET_TYPE.get(packet_type, ())

    # item 级覆盖最精确，适合把某个高风险事项交给指定 reviewer 或指定实现 agent。
    for route_key in route_keys:
        agent_name = _agent_name(structured_fields.get(route_key))
        if agent_name:
            return _route_result(role, agent_name, _fallback_agent(agent_routing), f"item:{route_key}")

    # 全局角色路由适合“规划用 A、开发用 B、审核用 C”的常规多 agent 工作流。
    for route_key in route_keys:
        agent_name = _agent_name(agent_routing.get(route_key))
        if agent_name:
            return _route_result(role, agent_name, _fallback_agent(agent_routing), f"global:{route_key}")

    # 没有显式路由时回到 current，使 skill 在不支持多 agent 的环境里仍可直接使用。
    default_agent = _agent_name(agent_routing.get("default_agent")) or DEFAULT_AGENT
    routing_source = "global:default_agent" if _agent_name(agent_routing.get("default_agent")) else "default:current"
    return _route_result(role, default_agent, _fallback_agent(agent_routing), routing_source)


def _route_result(role: str, target_agent: str, fallback_agent: str, routing_source: str) -> Dict[str, str]:
    return {
        "role": role,
        "target_agent": target_agent,
        "fallback_agent": fallback_agent,
        "routing_source": routing_source,
    }


def _fallback_agent(agent_routing: Dict[str, str]) -> str:
    return _agent_name(agent_routing.get("fallback_agent")) or DEFAULT_AGENT


def _agent_name(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text or text.casefold() in PLACEHOLDER_VALUES:
        return ""
    return text


def _meaningful_text(value: Any) -> str:
    if value is None or _is_blankish(value):
        return ""
    return str(value).strip()


def _build_dispatch_packets(
    checklist_path: Path,
    items: List[Dict[str, Any]],
    item_by_id: Dict[str, Dict[str, Any]],
    agent_routing: Dict[str, str],
    workflow_context: Dict[str, str],
) -> List[DispatchPacket]:
    packets: List[DispatchPacket] = []
    active_count = sum(1 for item in items if item.get("dispatch_status") == "active")
    reviewer_count = sum(1 for item in items if item.get("dispatch_status") == "in-review")
    active_surfaces = set().union(*(set(item.get("shared_surfaces") or []) for item in items if item.get("dispatch_status") == "active")) if items else set()
    selected_surfaces: Set[str] = set()

    # C 方案的预留能力：这里不直接 spawn agent，而是输出外部编排器可消费的派工包。
    for item in items:
        if item.get("dispatch_status") != "changes-requested":
            continue
        if active_count >= IMPLEMENTATION_LIMIT:
            break
        surfaces = set(item.get("shared_surfaces") or [])
        if surfaces.intersection(active_surfaces | selected_surfaces):
            continue
        if _is_blankish(item.get("plan")):
            packets.append(_planning_packet(checklist_path, item, "replan", agent_routing, workflow_context))
        else:
            packets.append(_implementation_packet(checklist_path, item, "rework", agent_routing, workflow_context))
        selected_surfaces.update(surfaces)
        active_count += 1

    for item in items:
        if item.get("dispatch_status") not in {"implemented", "review-queued"}:
            continue
        if reviewer_count >= REVIEWER_LIMIT:
            break
        if _is_blankish(item.get("implementation_record")) or _is_blankish(item.get("verification_record")):
            continue
        packets.append(_review_packet(checklist_path, item, agent_routing, workflow_context))
        reviewer_count += 1

    for item in items:
        if active_count >= IMPLEMENTATION_LIMIT:
            break
        if item.get("dispatch_status") != "ready":
            continue
        if _missing_done_dependencies(item, item_by_id):
            continue
        surfaces = set(item.get("shared_surfaces") or [])
        if surfaces.intersection(active_surfaces | selected_surfaces):
            continue
        if _is_blankish(item.get("plan")):
            packets.append(_planning_packet(checklist_path, item, "planning", agent_routing, workflow_context))
        else:
            packets.append(_implementation_packet(checklist_path, item, "implementation", agent_routing, workflow_context))
        selected_surfaces.update(surfaces)
        active_count += 1

    return packets


def _planning_packet(
    checklist_path: Path,
    item: Dict[str, Any],
    packet_type: str,
    agent_routing: Dict[str, str],
    workflow_context: Dict[str, str],
) -> DispatchPacket:
    item_id = _item_id(item)
    title = str(item.get("title") or item.get("heading") or item_id)
    output_artifacts = _packet_output_artifacts(checklist_path, item_id, packet_type, workflow_context)
    plan_file = output_artifacts["plan_file"]
    command = f"python3 {CONTROLLER_PATH} plan --checklist {checklist_path} --item {item_id} --text-file {plan_file}"
    prompt = (
        f"为 {item_id} - {title} 写计划。计划必须写清改动范围、文件 ownership、DAG 依赖、"
        f"shared_surfaces、并行性、验证方式和风险边界；计划文档写到 {plan_file}，写好后用 controller plan 写回。"
    )
    route = _resolve_agent_route(packet_type, item, agent_routing)
    contract = _planning_contract(item, packet_type, workflow_context)
    return DispatchPacket(
        packet_type=packet_type,
        role=route["role"],
        target_agent=route["target_agent"],
        fallback_agent=route["fallback_agent"],
        routing_source=route["routing_source"],
        invocation_policy=INVOCATION_POLICY,
        item_id=item_id,
        title=title,
        workflow_goal=contract["workflow_goal"],
        agent_objective=contract["agent_objective"],
        local_scope=contract["local_scope"],
        success_criteria=contract["success_criteria"],
        non_goals=contract["non_goals"],
        handoff_requirements=contract["handoff_requirements"],
        input_artifacts=contract["input_artifacts"],
        output_artifacts=output_artifacts,
        commands={"write_plan": command},
        command=command,
        prompt=prompt,
        shared_surfaces=list(item.get("shared_surfaces") or []),
        blocked_by=list(item.get("blocked_by") or []),
        blocks=list(item.get("blocks") or []),
    )


def _implementation_packet(
    checklist_path: Path,
    item: Dict[str, Any],
    packet_type: str,
    agent_routing: Dict[str, str],
    workflow_context: Dict[str, str],
) -> DispatchPacket:
    item_id = _item_id(item)
    title = str(item.get("title") or item.get("heading") or item_id)
    output_artifacts = _packet_output_artifacts(checklist_path, item_id, packet_type, workflow_context)
    implementation_file = output_artifacts["implementation_file"]
    verification_file = output_artifacts["verification_file"]
    command = f"python3 {CONTROLLER_PATH} start --checklist {checklist_path} --item {item_id} --agent <agent-id>"
    mark_command = (
        f"python3 {CONTROLLER_PATH} mark-implemented --checklist {checklist_path} --item {item_id} "
        f"--implementation-file {implementation_file} --verification-file {verification_file}"
    )
    prompt = (
        f"实施 {item_id} - {title}。只处理该事项计划内内容；完成后用 controller mark-implemented "
        f"写入实施记录和验证记录，文档分别放到 {implementation_file} 和 {verification_file}。"
    )
    route = _resolve_agent_route(packet_type, item, agent_routing)
    contract = _implementation_contract(item, packet_type, workflow_context)
    return DispatchPacket(
        packet_type=packet_type,
        role=route["role"],
        target_agent=route["target_agent"],
        fallback_agent=route["fallback_agent"],
        routing_source=route["routing_source"],
        invocation_policy=INVOCATION_POLICY,
        item_id=item_id,
        title=title,
        workflow_goal=contract["workflow_goal"],
        agent_objective=contract["agent_objective"],
        local_scope=contract["local_scope"],
        success_criteria=contract["success_criteria"],
        non_goals=contract["non_goals"],
        handoff_requirements=contract["handoff_requirements"],
        input_artifacts=contract["input_artifacts"],
        output_artifacts=output_artifacts,
        commands={"start": command, "mark_implemented": mark_command},
        command=command,
        prompt=prompt,
        shared_surfaces=list(item.get("shared_surfaces") or []),
        blocked_by=list(item.get("blocked_by") or []),
        blocks=list(item.get("blocks") or []),
    )


def _review_packet(
    checklist_path: Path,
    item: Dict[str, Any],
    agent_routing: Dict[str, str],
    workflow_context: Dict[str, str],
) -> DispatchPacket:
    item_id = _item_id(item)
    title = str(item.get("title") or item.get("heading") or item_id)
    output_artifacts = _packet_output_artifacts(checklist_path, item_id, "review", workflow_context)
    review_file = output_artifacts["review_file"]
    command = f"python3 {CONTROLLER_PATH} assign-reviewer --checklist {checklist_path} --item {item_id} --reviewer <reviewer-id>"
    request_changes_command = (
        f"python3 {CONTROLLER_PATH} request-changes --checklist {checklist_path} --item {item_id} "
        f"--review-file {review_file}"
    )
    approve_command = f"python3 {CONTROLLER_PATH} approve --checklist {checklist_path} --item {item_id} --review-file {review_file}"
    prompt = (
        f"审核 {item_id} - {title}。检查是否符合计划、DAG、shared_surfaces 和验证记录；"
        f"审核文档写到 {review_file}，若有问题用 request-changes，若通过用 approve。"
    )
    route = _resolve_agent_route("review", item, agent_routing)
    contract = _review_contract(item, workflow_context)
    return DispatchPacket(
        packet_type="review",
        role=route["role"],
        target_agent=route["target_agent"],
        fallback_agent=route["fallback_agent"],
        routing_source=route["routing_source"],
        invocation_policy=INVOCATION_POLICY,
        item_id=item_id,
        title=title,
        workflow_goal=contract["workflow_goal"],
        agent_objective=contract["agent_objective"],
        local_scope=contract["local_scope"],
        success_criteria=contract["success_criteria"],
        non_goals=contract["non_goals"],
        handoff_requirements=contract["handoff_requirements"],
        input_artifacts=contract["input_artifacts"],
        output_artifacts=output_artifacts,
        commands={
            "assign_reviewer": command,
            "request_changes": request_changes_command,
            "approve": approve_command,
        },
        command=command,
        prompt=prompt,
        shared_surfaces=list(item.get("shared_surfaces") or []),
        blocked_by=list(item.get("blocked_by") or []),
        blocks=list(item.get("blocks") or []),
    )


def _planning_contract(item: Dict[str, Any], packet_type: str, workflow_context: Dict[str, str]) -> Dict[str, Any]:
    item_id = _item_id(item)
    title = str(item.get("title") or item.get("heading") or item_id)
    return _packet_contract(
        item,
        workflow_context,
        f"为 {item_id} - {title} 产出可执行计划，让后续实施 agent 能只按本 item 推进。",
        [
            "计划写清改动范围、文件 ownership、DAG 依赖、shared_surfaces、并行性、验证方式和风险边界",
            "计划能被 implementation agent 直接执行，不需要重新理解整个大任务",
            "计划通过 controller plan 写回 checklist",
        ],
        [
            "不要直接实施代码或修改项目文件",
            "不要重排无关 item 的 DAG",
        ],
        [
            "将计划正文写入 packet.output_artifacts.plan_file",
            "使用 packet.command 对应的 controller plan 命令写回",
        ],
        _input_artifacts(item, workflow_context, include_plan=False, include_review=packet_type == "replan"),
    )


def _implementation_contract(item: Dict[str, Any], packet_type: str, workflow_context: Dict[str, str]) -> Dict[str, Any]:
    item_id = _item_id(item)
    title = str(item.get("title") or item.get("heading") or item_id)
    action = "返工修复" if packet_type == "rework" else "实施"
    return _packet_contract(
        item,
        workflow_context,
        f"按已写入计划{action} {item_id} - {title}，完成本 item 的实现与验证闭环。",
        [
            "只修改本 item 计划覆盖的范围",
            "完成必要验证并记录可复现的验证结果",
            "实施记录说明改了什么，验证记录说明如何证明它已完成",
            "完成后通过 controller mark-implemented 写回实施记录和验证记录",
        ],
        [
            "不要处理其他 item",
            "不要重新定义本 item 目标；计划不清时报告 blocker",
            "不要自行进入审核或批准自己的实现",
        ],
        [
            "先使用 packet.command 对应的 controller start 命令领取本 item",
            "将实施记录写入 packet.output_artifacts.implementation_file",
            "将验证记录写入 packet.output_artifacts.verification_file",
            "完成后使用 packet.commands.mark_implemented 写回实施记录和验证记录",
        ],
        _input_artifacts(item, workflow_context, include_plan=True, include_review=packet_type == "rework"),
    )


def _review_contract(item: Dict[str, Any], workflow_context: Dict[str, str]) -> Dict[str, Any]:
    item_id = _item_id(item)
    title = str(item.get("title") or item.get("heading") or item_id)
    return _packet_contract(
        item,
        workflow_context,
        f"独立审核 {item_id} - {title} 是否按计划完成，并决定通过或要求修改。",
        [
            "审核计划、实施记录、验证记录是否一致",
            "确认实现没有越过本 item 范围或破坏 shared_surfaces 约束",
            "发现问题时给出明确修改要求并使用 request-changes 写回",
            "确认通过时给出明确通过结论并使用 approve 关闭 item",
        ],
        [
            "不要直接修代码",
            "不要审核无关 item",
            "不要放宽计划或验证标准来迁就实现结果",
        ],
        [
            "先使用 packet.command 对应的 controller assign-reviewer 命令领取审核",
            "将审核意见或通过结论写入 packet.output_artifacts.review_file",
            "不通过时用 packet.commands.request_changes 写回审核意见",
            "通过时用 packet.commands.approve 写回通过结论",
        ],
        _input_artifacts(
            item,
            workflow_context,
            include_plan=True,
            include_implementation=True,
            include_verification=True,
            include_review=False,
        ),
    )


def _packet_contract(
    item: Dict[str, Any],
    workflow_context: Dict[str, str],
    agent_objective: str,
    success_criteria: List[str],
    role_non_goals: List[str],
    handoff_requirements: List[str],
    input_artifacts: Dict[str, object],
) -> Dict[str, Any]:
    non_goals = [
        "不要操心全局调度、其他 agent 分工或未分配给本 item 的事项",
        "不要手改 dispatch_status、assigned_subagent、reviewer_id、reviewer_state 或 checklist 勾选状态",
        "不要把强审流程文档写到项目根目录；只使用 packet.output_artifacts 指向的点号专属目录",
    ]
    non_goals.extend(role_non_goals)
    return {
        "workflow_goal": workflow_context.get("workflow_goal") or "完成当前 checklist 记录的大任务",
        "agent_objective": agent_objective,
        "local_scope": _local_scope(item),
        "success_criteria": success_criteria,
        "non_goals": non_goals,
        "handoff_requirements": handoff_requirements,
        "input_artifacts": input_artifacts,
    }


def _local_scope(item: Dict[str, Any]) -> List[str]:
    item_id = _item_id(item)
    title = str(item.get("title") or item.get("heading") or item_id)
    scope = [f"只处理 {item_id} - {title}"]
    blocked_by = list(item.get("blocked_by") or [])
    blocks = list(item.get("blocks") or [])
    shared_surfaces = list(item.get("shared_surfaces") or [])
    if blocked_by:
        scope.append("上游依赖：" + _format_list(blocked_by))
    if blocks:
        scope.append("下游影响：" + _format_list(blocks))
    if shared_surfaces:
        scope.append("共享面：" + _format_list(shared_surfaces))
    return scope


def _input_artifacts(
    item: Dict[str, Any],
    workflow_context: Dict[str, str],
    include_plan: bool = False,
    include_implementation: bool = False,
    include_verification: bool = False,
    include_review: bool = False,
) -> Dict[str, object]:
    artifacts: Dict[str, object] = {
        "workflow_context": workflow_context,
        "item_id": _item_id(item),
        "title": str(item.get("title") or item.get("heading") or _item_id(item)),
        "blocked_by": list(item.get("blocked_by") or []),
        "blocks": list(item.get("blocks") or []),
        "shared_surfaces": list(item.get("shared_surfaces") or []),
    }
    if include_plan:
        _add_artifact(artifacts, "plan", item.get("plan"))
    if include_implementation:
        _add_artifact(artifacts, "implementation_record", item.get("implementation_record"))
    if include_verification:
        _add_artifact(artifacts, "verification_record", item.get("verification_record"))
    if include_review:
        _add_artifact(artifacts, "review_record", item.get("review_record"))
    return artifacts


def _add_artifact(artifacts: Dict[str, object], key: str, value: Any) -> None:
    text = _meaningful_text(value)
    if text:
        artifacts[key] = text


def _next_action_text(
    updates: List[Dict[str, object]],
    packets: List[DispatchPacket],
    violations: List[ControllerViolation],
) -> str:
    if any(violation.severity == "error" for violation in violations):
        return "先修复 validate 报出的 error；不要继续推进状态迁移"
    if updates:
        return "运行 cycle --write 同步 blocked/ready 派生状态"
    if packets:
        return f"按 dispatch_packets 派发 {len(packets)} 个外部 agent 工作包"
    return "当前没有可执行调度包；若全部 done 则可以收口，否则检查阻塞原因"


def _require_no_errors(violations: List[ControllerViolation]) -> None:
    errors = [violation for violation in violations if violation.severity == "error"]
    if errors:
        raise ValueError(errors[0].message)


def _require_protocol_ready(parsed: Any, snapshot: Dict[str, Any]) -> None:
    violations = _build_parser_violations(snapshot) + _build_protocol_violations(parsed, snapshot)
    _require_no_errors(violations)


def _ensure_plan_ready(item: Dict[str, Any]) -> None:
    if _is_blankish(item.get("plan")):
        raise ValueError(f"{_item_id(item)} requires a concrete plan before start")


def _ensure_implementation_ready(item: Dict[str, Any]) -> None:
    if _is_blankish(item.get("implementation_record")):
        raise ValueError(f"{_item_id(item)} requires implementation record")


def _ensure_verification_ready(item: Dict[str, Any]) -> None:
    if _is_blankish(item.get("verification_record")):
        raise ValueError(f"{_item_id(item)} requires verification record")


def _ensure_dependencies_done(item: Dict[str, Any], item_by_id: Dict[str, Dict[str, Any]]) -> None:
    missing = _missing_done_dependencies(item, item_by_id)
    if missing:
        raise ValueError(f"{_item_id(item)} has unfinished dependencies: {', '.join(missing)}")


def _ensure_active_slot_available(items: List[Dict[str, Any]], item: Dict[str, Any]) -> None:
    active_count = sum(1 for candidate in items if candidate.get("dispatch_status") == "active" and _item_id(candidate) != _item_id(item))
    if active_count >= IMPLEMENTATION_LIMIT:
        raise ValueError(f"implementation concurrency limit {IMPLEMENTATION_LIMIT} is full")


def _ensure_reviewer_slot_available(items: List[Dict[str, Any]], item: Dict[str, Any]) -> None:
    reviewer_count = sum(1 for candidate in items if candidate.get("dispatch_status") == "in-review" and _item_id(candidate) != _item_id(item))
    if reviewer_count >= REVIEWER_LIMIT:
        raise ValueError(f"reviewer concurrency limit {REVIEWER_LIMIT} is full")


def _ensure_independent_reviewer(item: Dict[str, Any], reviewer_id: str, label: str = "reviewer") -> None:
    reviewer = _agent_name(reviewer_id)
    assigned_subagent = _agent_name(_field(item, "assigned_subagent"))
    if reviewer and assigned_subagent and reviewer == assigned_subagent:
        raise ValueError(f"{label} must not be the implementation agent")


def _ensure_no_active_surface_conflict(items: List[Dict[str, Any]], item: Dict[str, Any]) -> None:
    surfaces = set(item.get("shared_surfaces") or [])
    if not surfaces:
        return
    for candidate in items:
        if _item_id(candidate) == _item_id(item) or candidate.get("dispatch_status") != "active":
            continue
        overlap = surfaces.intersection(candidate.get("shared_surfaces") or [])
        if overlap:
            raise ValueError(f"{_item_id(item)} conflicts with active item {_item_id(candidate)} on shared_surfaces: {', '.join(sorted(overlap))}")


def _missing_done_dependencies(item: Dict[str, Any], item_by_id: Dict[str, Dict[str, Any]]) -> List[str]:
    missing: List[str] = []
    for dependency_id in item.get("blocked_by") or []:
        dependency = item_by_id.get(dependency_id)
        if dependency is None or dependency.get("dispatch_status") != FINISHED_STATUS:
            missing.append(str(dependency_id))
    return missing


def _item_by_id(items: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for item in items:
        item_id = item.get("item_id")
        if isinstance(item_id, str) and item_id and item_id not in result:
            result[item_id] = item
    return result


def _require_item(items: List[Dict[str, Any]], item_id: str) -> Dict[str, Any]:
    for item in items:
        if item.get("item_id") == item_id:
            return item
    raise ValueError(f"item not found: {item_id}")


def _item_id(item: Dict[str, Any]) -> str:
    value = item.get("item_id")
    return str(value) if value else "unknown-item"


def _item_error(item: Dict[str, Any], code: str, message: str, key: Optional[str]) -> ControllerViolation:
    return ControllerViolation(
        severity="error",
        code=code,
        message=message,
        item_id=_item_id(item),
        heading=item.get("heading"),
        key=key,
    )


def _field(item: Dict[str, Any], key: str) -> str:
    structured_fields = item.get("structured_fields") if isinstance(item.get("structured_fields"), dict) else {}
    value = structured_fields.get(key)
    return "" if value is None else str(value).strip()


def _reviewer_closed(item: Dict[str, Any]) -> bool:
    reviewer_state = _field(item, "reviewer_state").casefold()
    review_record = str(item.get("review_record") or "").casefold()
    return reviewer_state in {"closed", "done"} or "关闭状态：closed" in review_record or "关闭状态: closed" in review_record


def _contains_approval(value: Any) -> bool:
    text = str(value or "").casefold()
    return any(marker in text for marker in APPROVAL_MARKERS)


def _is_blankish(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, list):
        return len(value) == 0
    text = str(value).strip()
    normalized = text.casefold()
    if normalized in PLACEHOLDER_VALUES:
        return True
    lines = [line.strip().lstrip("-").strip().casefold() for line in text.splitlines() if line.strip()]
    return not lines or all(line in PLACEHOLDER_VALUES for line in lines)


def _extract_bullet_value(section_text: str, key: str) -> str:
    for line in section_text.splitlines():
        match = STRUCTURED_LINE_RE.match(line)
        if match and match.group(2).strip() == key:
            return match.group(4).strip()
    return ""


def _parse_bullet_fields(section_text: str) -> Dict[str, str]:
    fields: Dict[str, str] = {}
    for line in section_text.splitlines():
        match = STRUCTURED_LINE_RE.match(line)
        if not match:
            continue
        fields[match.group(2).strip()] = match.group(4).strip()
    return fields


def _routing_updates(routes: Dict[str, Optional[str]]) -> Dict[str, str]:
    updates: Dict[str, str] = {}
    for key, value in routes.items():
        agent_name = _agent_name(value)
        if agent_name:
            updates[key] = agent_name
    return updates


def _normalize_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text or text == "[]":
        return []
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    return [part.strip() for part in text.split(",") if part.strip()]


def _format_list(values: List[str]) -> str:
    return "[]" if not values else "[" + ", ".join(values) + "]"


def _read_text_arg(text: Optional[str], text_file: Optional[str]) -> str:
    if text_file:
        return Path(text_file).read_text(encoding="utf-8")
    return text or ""


def _is_strict_review_task_path(checklist_path: Union[str, Path]) -> bool:
    path = Path(checklist_path)
    return path.name == DEFAULT_CHECKLIST_FILE and path.parent.parent.name == STRICT_REVIEW_DOCUMENT_DIR


def _strict_review_document_dir(
    checklist_path: Union[str, Path],
    workflow_context: Optional[Dict[str, str]] = None,
) -> Path:
    path = Path(checklist_path)
    if _is_strict_review_task_path(path):
        return path.parent
    root_dir = path.parent if path.parent.name == STRICT_REVIEW_DOCUMENT_DIR else path.parent / STRICT_REVIEW_DOCUMENT_DIR
    return root_dir / _task_directory_slug(workflow_context)


def _task_directory_slug(workflow_context: Optional[Dict[str, str]]) -> str:
    raw_text = ""
    if workflow_context:
        raw_text = workflow_context.get("workflow_goal") or workflow_context.get("current_request") or ""
    return _filesystem_slug(raw_text, fallback=DEFAULT_TASK_DIRECTORY)


def _artifact_slug(item_id: str) -> str:
    # 文件名只保留稳定安全字符，避免 agent 生成的 item_id 把文档写出专属目录。
    return _filesystem_slug(item_id, fallback="item")


def _filesystem_slug(value: str, fallback: str) -> str:
    # 任务目录需要描述当前工作方向；保留中文、字母、数字、下划线、点和短横线。
    slug = re.sub(r"[^\w.-]+", "-", str(value).strip(), flags=re.UNICODE).strip(".-_")
    return (slug[:80].strip(".-_") or fallback)


def _packet_output_artifacts(
    checklist_path: Union[str, Path],
    item_id: str,
    packet_type: str,
    workflow_context: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    document_dir = _strict_review_document_dir(checklist_path, workflow_context)
    slug = _artifact_slug(item_id)
    artifacts = {"document_dir": str(document_dir), "task_dir": str(document_dir.name)}
    if packet_type in {"planning", "replan"}:
        artifacts["plan_file"] = str(document_dir / f"{slug}-plan.md")
    if packet_type in {"implementation", "rework"}:
        artifacts["implementation_file"] = str(document_dir / f"{slug}-implementation.md")
        artifacts["verification_file"] = str(document_dir / f"{slug}-verification.md")
    if packet_type == "review":
        artifacts["review_file"] = str(document_dir / f"{slug}-review.md")
    return artifacts


def _split_h2_ranges(lines: List[str]) -> List[Tuple[str, int, int]]:
    ranges: List[Tuple[str, int, int]] = []
    current_heading: Optional[str] = None
    current_start = 0
    in_fence = False
    active_fence: Optional[str] = None

    for index, line in enumerate(lines):
        in_fence, active_fence = _next_fence_state(line, in_fence, active_fence)
        match = None if in_fence else H2_RE.match(line)
        if not match:
            continue
        if current_heading is not None:
            ranges.append((current_heading, current_start, index))
        current_heading = match.group(1).strip()
        current_start = index
    if current_heading is not None:
        ranges.append((current_heading, current_start, len(lines)))
    return ranges


def _split_h3_ranges(lines: List[str], start: int, end: int) -> List[Tuple[str, int, int]]:
    ranges: List[Tuple[str, int, int]] = []
    current_heading: Optional[str] = None
    current_start = start
    in_fence = False
    active_fence: Optional[str] = None

    for index in range(start, end):
        line = lines[index]
        in_fence, active_fence = _next_fence_state(line, in_fence, active_fence)
        match = None if in_fence else H3_RE.match(line)
        if not match:
            continue
        if current_heading is not None:
            ranges.append((current_heading, current_start, index))
        current_heading = match.group(1).strip()
        current_start = index
    if current_heading is not None:
        ranges.append((current_heading, current_start, end))
    return ranges


def _next_fence_state(line: str, in_fence: bool, active_fence: Optional[str]) -> Tuple[bool, Optional[str]]:
    match = FENCE_RE.match(line)
    if not match:
        return in_fence, active_fence
    fence = match.group("fence")
    if not in_fence:
        return True, fence
    if active_fence is not None and fence[0] == active_fence[0] and len(fence) >= len(active_fence):
        return False, None
    return in_fence, active_fence


def _find_item_range(lines: List[str], item_id: str) -> Tuple[int, int]:
    parsed = parser_module.parse_markdown("\n".join(lines))
    heading = None
    for item in parsed.items:
        if item.structured_fields.get("item_id") == item_id:
            heading = item.heading
            break
    if heading is None:
        raise ValueError(f"item not found: {item_id}")
    for candidate_heading, start, end in _split_h2_ranges(lines):
        if candidate_heading == heading:
            return start, end
    raise ValueError(f"item range not found: {item_id}")


def _set_item_section(document: str, item_id: str, section_heading: str, body: str) -> str:
    lines = document.splitlines()
    item_start, item_end = _find_item_range(lines, item_id)
    section_ranges = _split_h3_ranges(lines, item_start + 1, item_end)
    for heading, start, end in section_ranges:
        if heading != section_heading:
            continue
        replacement = [lines[start], *body.rstrip("\n").splitlines()]
        return "\n".join([*lines[:start], *replacement, *lines[end:]]) + "\n"
    insert_at = item_end
    replacement = [f"### {section_heading}", *body.rstrip("\n").splitlines(), ""]
    return "\n".join([*lines[:insert_at], *replacement, *lines[insert_at:]]) + "\n"


def _set_structured_field(document: str, item_id: str, key: str, value: str) -> str:
    section_body = _get_item_section(document, item_id, "结构化字段")
    lines = section_body.splitlines()
    replacement_line = f"- {key}：{value}"
    for index, line in enumerate(lines):
        match = STRUCTURED_LINE_RE.match(line)
        if match and match.group(2).strip() == key:
            lines[index] = replacement_line
            break
    else:
        lines.append(replacement_line)
    return _set_item_section(document, item_id, "结构化字段", "\n".join(lines) + "\n")


def _set_review_field(document: str, item_id: str, key: str, value: str) -> str:
    review_body = _get_item_section(document, item_id, "审核记录")
    lines = review_body.splitlines()
    replacement_line = f"- {key}：{value}"
    for index, line in enumerate(lines):
        match = STRUCTURED_LINE_RE.match(line)
        if match and match.group(2).strip() == key:
            lines[index] = replacement_line
            break
    else:
        lines.append(replacement_line)
    return _set_item_section(document, item_id, "审核记录", "\n".join(lines) + "\n")


def _set_top_level_bullet_field(document: str, section_heading: str, key: str, value: str) -> str:
    lines = document.splitlines()
    replacement_line = f"- {key}：{value}"
    for heading, start, end in _split_h2_ranges(lines):
        if heading != section_heading:
            continue
        for index in range(start + 1, end):
            match = STRUCTURED_LINE_RE.match(lines[index])
            if match and match.group(2).strip() == key:
                lines[index] = replacement_line
                return "\n".join(lines) + "\n"

        # 尽量插在 section 末尾空行之前，让全局路由区块保持紧凑。
        insert_at = end
        if insert_at > start + 1 and lines[insert_at - 1].strip() == "":
            insert_at -= 1
        return "\n".join([*lines[:insert_at], replacement_line, *lines[insert_at:]]) + "\n"

    insert_at = len(lines)
    for heading, start, _end in _split_h2_ranges(lines):
        if heading == "任务归属判定":
            insert_at = start
            break
    # 兼容旧 checklist：缺少 Agent 路由策略时由 controller 补齐该顶层小节。
    section_lines = ["", f"## {section_heading}", replacement_line, ""]
    return "\n".join([*lines[:insert_at], *section_lines, *lines[insert_at:]]) + "\n"


def _get_item_section(document: str, item_id: str, section_heading: str) -> str:
    lines = document.splitlines()
    item_start, item_end = _find_item_range(lines, item_id)
    for heading, start, end in _split_h3_ranges(lines, item_start + 1, item_end):
        if heading == section_heading:
            return "\n".join(lines[start + 1 : end]).strip()
    return ""


def _set_checklist_checkbox(document: str, title: str, *, checked: bool) -> str:
    lines = document.splitlines()
    marker = "x" if checked else " "
    in_checklist = False
    for index, line in enumerate(lines):
        if H2_RE.match(line):
            in_checklist = H2_RE.match(line).group(1).strip() == "Checklist"
            continue
        if not in_checklist:
            continue
        match = CHECKLIST_ITEM_RE.match(line)
        if match and match.group(3).strip() == str(title).strip():
            lines[index] = f"{match.group(1)}{marker}{match.group(2)}{match.group(3)}"
            break
    return "\n".join(lines) + "\n"


def _print_payload(payload: Dict[str, object], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if "violations" in payload:
        for violation in payload["violations"]:
            print(f"{violation['severity']} {violation['code']}: {violation['message']}")
    if "dispatch_packets" in payload:
        print(payload["next_action"])
        for packet in payload["dispatch_packets"]:
            print(f"- {packet['packet_type']} {packet['item_id']}: {packet['prompt']}")


def _print_diagram(as_json: bool) -> None:
    if as_json:
        print(json.dumps({"diagram": WORKFLOW_STATE_DIAGRAM}, ensure_ascii=False, indent=2))
        return
    print("```mermaid")
    print(WORKFLOW_STATE_DIAGRAM.rstrip())
    print("```")


def _add_text_args(parser: argparse.ArgumentParser, label: str) -> None:
    parser.add_argument(f"--{label}", help="Inline text")
    parser.add_argument(f"--{label}-file", help="Read text from a UTF-8 file")


def _add_routing_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--coordinator-agent", help="Agent label for the coordinator")
    parser.add_argument("--default-agent", help="Fallback default agent label")
    parser.add_argument("--fallback-agent", help="Agent label used when target_agent is unavailable")
    parser.add_argument("--planning-agent", help="Agent label for planning packets")
    parser.add_argument("--implementation-agent", help="Agent label for implementation packets")
    parser.add_argument("--rework-agent", help="Agent label for rework packets")
    parser.add_argument("--review-agent", help="Agent label for review packets")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Strict review checklist controller")
    subparsers = parser.add_subparsers(dest="command")

    validate_parser = subparsers.add_parser("validate", help="Validate checklist protocol")
    validate_parser.add_argument("--checklist", required=True)
    validate_parser.add_argument("--json", action="store_true")

    cycle_parser = subparsers.add_parser("cycle", help="Compute deterministic cycle plan")
    cycle_parser.add_argument("--checklist", required=True)
    cycle_parser.add_argument("--write", action="store_true", help="Write blocked/ready status corrections")
    cycle_parser.add_argument("--json", action="store_true")

    diagram_parser = subparsers.add_parser("diagram", help="Print workflow state machine diagram")
    diagram_parser.add_argument("--json", action="store_true")

    routing_parser = subparsers.add_parser("set-routing", help="Set coordinator-controlled agent routing")
    routing_parser.add_argument("--checklist", required=True)
    routing_parser.add_argument("--item", help="Optional item_id for item-level routing override")
    _add_routing_args(routing_parser)
    routing_parser.add_argument("--json", action="store_true")

    init_parser = subparsers.add_parser("init", help="Create a controller-compatible checklist")
    init_parser.add_argument("--checklist", required=True)
    init_parser.add_argument("--title", required=True)
    init_parser.add_argument("--request", required=True)
    init_parser.add_argument("--items-json", required=True, help="JSON array of item definitions")

    plan_parser = subparsers.add_parser("plan", help="Write item plan")
    plan_parser.add_argument("--checklist", required=True)
    plan_parser.add_argument("--item", required=True)
    _add_text_args(plan_parser, "text")
    plan_parser.add_argument("--json", action="store_true")

    start_parser = subparsers.add_parser("start", help="Move item to active")
    start_parser.add_argument("--checklist", required=True)
    start_parser.add_argument("--item", required=True)
    start_parser.add_argument("--agent", required=True)
    start_parser.add_argument("--json", action="store_true")

    implemented_parser = subparsers.add_parser("mark-implemented", help="Record implementation and verification")
    implemented_parser.add_argument("--checklist", required=True)
    implemented_parser.add_argument("--item", required=True)
    _add_text_args(implemented_parser, "implementation")
    _add_text_args(implemented_parser, "verification")
    implemented_parser.add_argument("--json", action="store_true")

    review_parser = subparsers.add_parser("queue-review", help="Move implemented item to review-queued")
    review_parser.add_argument("--checklist", required=True)
    review_parser.add_argument("--item", required=True)
    review_parser.add_argument("--json", action="store_true")

    assign_parser = subparsers.add_parser("assign-reviewer", help="Move item to in-review")
    assign_parser.add_argument("--checklist", required=True)
    assign_parser.add_argument("--item", required=True)
    assign_parser.add_argument("--reviewer", required=True)
    assign_parser.add_argument("--json", action="store_true")

    replace_parser = subparsers.add_parser("replace-reviewer", help="Replace a stalled in-review reviewer")
    replace_parser.add_argument("--checklist", required=True)
    replace_parser.add_argument("--item", required=True)
    replace_parser.add_argument("--from-reviewer", required=True)
    replace_parser.add_argument("--to-reviewer", required=True)
    _add_text_args(replace_parser, "reason")
    replace_parser.add_argument("--json", action="store_true")

    changes_parser = subparsers.add_parser("request-changes", help="Record reviewer changes")
    changes_parser.add_argument("--checklist", required=True)
    changes_parser.add_argument("--item", required=True)
    _add_text_args(changes_parser, "review")
    changes_parser.add_argument("--json", action="store_true")

    approve_parser = subparsers.add_parser("approve", help="Approve item and close reviewer")
    approve_parser.add_argument("--checklist", required=True)
    approve_parser.add_argument("--item", required=True)
    _add_text_args(approve_parser, "review")
    approve_parser.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    if args.command is None:
        parser.error("a command is required")
    return args


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    if args.command == "diagram":
        _print_diagram(getattr(args, "json", False))
        return 0

    try:
        if args.command == "validate":
            payload = validate_checklist(args.checklist)
        elif args.command == "cycle":
            payload = build_cycle_plan(args.checklist)
            if args.write and payload["status_updates"]:
                write_status_updates(args.checklist, payload["status_updates"])
                payload = build_cycle_plan(args.checklist)
        elif args.command == "set-routing":
            payload = set_agent_routing(
                args.checklist,
                args.item,
                args.coordinator_agent,
                args.default_agent,
                args.fallback_agent,
                args.planning_agent,
                args.implementation_agent,
                args.rework_agent,
                args.review_agent,
            )
        elif args.command == "init":
            init_checklist(args.checklist, args.title, args.request, json.loads(args.items_json))
            payload = validate_checklist(args.checklist)
        elif args.command == "plan":
            payload = plan_item(args.checklist, args.item, _read_text_arg(args.text, args.text_file))
        elif args.command == "start":
            payload = start_item(args.checklist, args.item, args.agent)
        elif args.command == "mark-implemented":
            payload = mark_implemented(
                args.checklist,
                args.item,
                _read_text_arg(args.implementation, args.implementation_file),
                _read_text_arg(args.verification, args.verification_file),
            )
        elif args.command == "queue-review":
            payload = queue_review(args.checklist, args.item)
        elif args.command == "assign-reviewer":
            payload = assign_reviewer(args.checklist, args.item, args.reviewer)
        elif args.command == "replace-reviewer":
            payload = replace_reviewer(
                args.checklist,
                args.item,
                args.from_reviewer,
                args.to_reviewer,
                _read_text_arg(args.reason, args.reason_file),
            )
        elif args.command == "request-changes":
            payload = request_changes(args.checklist, args.item, _read_text_arg(args.review, args.review_file))
        elif args.command == "approve":
            payload = approve_item(args.checklist, args.item, _read_text_arg(args.review, args.review_file))
        else:  # pragma: no cover - argparse 会阻止未知命令。
            raise ValueError(f"unknown command: {args.command}")
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2

    _print_payload(payload, getattr(args, "json", False))
    return 0 if payload.get("ok", False) else 1


if __name__ == "__main__":
    raise SystemExit(main())
