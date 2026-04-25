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
    item_id: str
    title: str
    command: str
    prompt: str
    shared_surfaces: List[str]
    blocked_by: List[str]
    blocks: List[str]

    def to_dict(self) -> Dict[str, object]:
        return {
            "packet_type": self.packet_type,
            "item_id": self.item_id,
            "title": self.title,
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
    violations = _build_parser_violations(snapshot) + _build_protocol_violations(parsed, snapshot)
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
    violations = _build_parser_violations(snapshot) + _build_protocol_violations(parsed, snapshot)
    has_errors = any(violation.severity == "error" for violation in violations)
    item_by_id = _item_by_id(items)
    status_updates = [] if has_errors else _recommended_status_updates(items, item_by_id)
    packets = [] if has_errors else _build_dispatch_packets(Path(checklist_path), items, item_by_id)
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

    item_by_id = {str(item["item_id"]): item for item in items}
    lines: List[str] = [
        f"# {title}",
        "",
        "## 模式",
        "- 强审开发模式（controller-enforced DAG-first）",
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

    Path(checklist_path).write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


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


def _build_dispatch_packets(
    checklist_path: Path,
    items: List[Dict[str, Any]],
    item_by_id: Dict[str, Dict[str, Any]],
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
            packets.append(_planning_packet(checklist_path, item, "replan"))
        else:
            packets.append(_implementation_packet(checklist_path, item, "rework"))
        selected_surfaces.update(surfaces)
        active_count += 1

    for item in items:
        if item.get("dispatch_status") not in {"implemented", "review-queued"}:
            continue
        if reviewer_count >= REVIEWER_LIMIT:
            break
        if _is_blankish(item.get("implementation_record")) or _is_blankish(item.get("verification_record")):
            continue
        packets.append(_review_packet(checklist_path, item))
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
            packets.append(_planning_packet(checklist_path, item, "planning"))
        else:
            packets.append(_implementation_packet(checklist_path, item, "implementation"))
        selected_surfaces.update(surfaces)
        active_count += 1

    return packets


def _planning_packet(checklist_path: Path, item: Dict[str, Any], packet_type: str) -> DispatchPacket:
    item_id = _item_id(item)
    title = str(item.get("title") or item.get("heading") or item_id)
    command = f"python3 {CONTROLLER_PATH} plan --checklist {checklist_path} --item {item_id} --text-file <plan-file>"
    prompt = (
        f"为 {item_id} - {title} 写计划。计划必须写清改动范围、文件 ownership、DAG 依赖、"
        "shared_surfaces、并行性、验证方式和风险边界；写好后用 controller plan 写回。"
    )
    return DispatchPacket(
        packet_type=packet_type,
        item_id=item_id,
        title=title,
        command=command,
        prompt=prompt,
        shared_surfaces=list(item.get("shared_surfaces") or []),
        blocked_by=list(item.get("blocked_by") or []),
        blocks=list(item.get("blocks") or []),
    )


def _implementation_packet(checklist_path: Path, item: Dict[str, Any], packet_type: str) -> DispatchPacket:
    item_id = _item_id(item)
    title = str(item.get("title") or item.get("heading") or item_id)
    command = f"python3 {CONTROLLER_PATH} start --checklist {checklist_path} --item {item_id} --agent <agent-id>"
    prompt = (
        f"实施 {item_id} - {title}。只处理该事项计划内内容；完成后用 controller mark-implemented "
        "写入实施记录和验证记录。"
    )
    return DispatchPacket(
        packet_type=packet_type,
        item_id=item_id,
        title=title,
        command=command,
        prompt=prompt,
        shared_surfaces=list(item.get("shared_surfaces") or []),
        blocked_by=list(item.get("blocked_by") or []),
        blocks=list(item.get("blocks") or []),
    )


def _review_packet(checklist_path: Path, item: Dict[str, Any]) -> DispatchPacket:
    item_id = _item_id(item)
    title = str(item.get("title") or item.get("heading") or item_id)
    command = f"python3 {CONTROLLER_PATH} assign-reviewer --checklist {checklist_path} --item {item_id} --reviewer <reviewer-id>"
    prompt = (
        f"审核 {item_id} - {title}。检查是否符合计划、DAG、shared_surfaces 和验证记录；"
        "若有问题用 request-changes，若通过用 approve。"
    )
    return DispatchPacket(
        packet_type="review",
        item_id=item_id,
        title=title,
        command=command,
        prompt=prompt,
        shared_surfaces=list(item.get("shared_surfaces") or []),
        blocked_by=list(item.get("blocked_by") or []),
        blocks=list(item.get("blocks") or []),
    )


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
