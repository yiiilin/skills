from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any

REQUIRED_GLOBAL_HEADINGS = (
    "模式",
    "审核设置",
    "当前执行状态",
    "Checklist",
    "DAG 概览",
    "Mermaid DAG",
    "Ready 队列",
    "Active 实现队列",
    "Active reviewer 队列",
    "Review Queue",
)

REQUIRED_ITEM_HEADINGS = (
    "结构化字段",
    "计划",
    "实施记录",
    "验证记录",
    "审核记录",
)

KNOWN_DISPATCH_STATUSES = {
    "blocked",
    "ready",
    "active",
    "implemented",
    "review-queued",
    "in-review",
    "changes-requested",
    "done",
}

REQUIRED_STRUCTURED_FIELDS = (
    "item_id",
    "blocked_by",
    "blocks",
    "shared_surfaces",
    "parallel_group",
    "dispatch_status",
    "assigned_subagent",
)

H1_RE = re.compile(r"^#\s+(.*\S)\s*$")
H2_RE = re.compile(r"^##\s+(.*\S)\s*$")
H3_RE = re.compile(r"^###\s+(.*\S)\s*$")
ITEM_HEADING_RE = re.compile(r"^Item\s+\d+\s*-\s*(.+?)\s*$")
STRUCTURED_BULLET_RE = re.compile(r"^\s*-\s+([^：:]+?)\s*[：:]\s*(.*)$")
FENCE_RE = re.compile(r"^\s*(?P<fence>`{3,}|~{3,})")


@dataclass(frozen=True)
class ParseWarning:
    code: str
    message: str
    heading: str | None = None
    key: str | None = None


@dataclass(frozen=True)
class ChecklistItem:
    heading: str
    title: str
    structured_fields: dict[str, Any]
    plan: str
    implementation_record: str
    verification_record: str
    review_record: str
    sections: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ParsedChecklist:
    title: str
    top_level_sections: dict[str, str]
    items: list[ChecklistItem]
    warnings: list[ParseWarning]


def parse_file(path: str | Path) -> ParsedChecklist:
    markdown_path = Path(path)
    return parse_markdown(markdown_path.read_text(encoding="utf-8"))


def parse_markdown(markdown: str) -> ParsedChecklist:
    normalized = markdown.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    title = _extract_title(lines)

    top_level_sections: dict[str, str] = {}
    items: list[ChecklistItem] = []
    warnings: list[ParseWarning] = []

    for heading, section_text in _split_sections(lines, H2_RE):
        item_match = ITEM_HEADING_RE.match(heading)
        if item_match:
            items.append(_parse_item(heading, item_match.group(1).strip(), section_text, warnings))
        else:
            top_level_sections[heading] = section_text

    for heading in REQUIRED_GLOBAL_HEADINGS:
        if heading not in top_level_sections:
            warnings.append(
                ParseWarning(
                    code="missing_global_heading",
                    message=f"Missing required global heading: {heading}",
                    heading=heading,
                )
            )

    seen_item_ids: set[str] = set()
    for item in items:
        item_id = item.structured_fields.get("item_id")
        if isinstance(item_id, str) and item_id:
            if item_id in seen_item_ids:
                warnings.append(
                    ParseWarning(
                        code="duplicate_item_id",
                        message=f"Duplicate item_id: {item_id}",
                        heading=item.heading,
                        key="item_id",
                    )
                )
            else:
                seen_item_ids.add(item_id)

    return ParsedChecklist(
        title=title,
        top_level_sections=top_level_sections,
        items=items,
        warnings=warnings,
    )


def _extract_title(lines: list[str]) -> str:
    for line in lines:
        match = H1_RE.match(line)
        if match:
            return match.group(1).strip()
    return ""


def _split_sections(lines: list[str], pattern: re.Pattern[str]) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    current_heading: str | None = None
    current_lines: list[str] = []
    in_fenced_block = False
    active_fence: str | None = None

    for line in lines:
        fence_match = FENCE_RE.match(line)
        if fence_match:
            fence = fence_match.group("fence")
            if not in_fenced_block:
                in_fenced_block = True
                active_fence = fence
            elif active_fence is not None and fence[0] == active_fence[0] and len(fence) >= len(active_fence):
                in_fenced_block = False
                active_fence = None

        match = None if in_fenced_block else pattern.match(line)
        if match:
            if current_heading is not None:
                sections.append((current_heading, _normalize_block(current_lines)))
            current_heading = match.group(1).strip()
            current_lines = []
            continue

        if current_heading is not None:
            current_lines.append(line)

    if current_heading is not None:
        sections.append((current_heading, _normalize_block(current_lines)))

    return sections


def _parse_item(
    heading: str,
    title: str,
    section_text: str,
    warnings: list[ParseWarning],
) -> ChecklistItem:
    sections = dict(_split_sections(section_text.split("\n"), H3_RE))

    for required_heading in REQUIRED_ITEM_HEADINGS:
        if required_heading not in sections:
            warnings.append(
                ParseWarning(
                    code="missing_item_heading",
                    message=f"Missing required item heading: {required_heading}",
                    heading=heading,
                )
            )

    structured_fields = _parse_structured_fields(sections.get("结构化字段", ""), heading, warnings)

    for required_field in REQUIRED_STRUCTURED_FIELDS:
        value = structured_fields.get(required_field)
        if value is None or value == "":
            warnings.append(
                ParseWarning(
                    code="missing_structured_field",
                    message=f"Missing structured field: {required_field}",
                    heading=heading,
                    key=required_field,
                )
            )

    item_id = structured_fields.get("item_id")
    if not isinstance(item_id, str) or not item_id:
        warnings.append(
            ParseWarning(
                code="missing_item_id",
                message="Missing item_id in structured fields",
                heading=heading,
                key="item_id",
            )
        )

    dispatch_status = structured_fields.get("dispatch_status")
    if isinstance(dispatch_status, str) and dispatch_status and dispatch_status not in KNOWN_DISPATCH_STATUSES:
        warnings.append(
            ParseWarning(
                code="unknown_dispatch_status",
                message=f"Unknown dispatch_status: {dispatch_status}",
                heading=heading,
                key="dispatch_status",
            )
        )

    return ChecklistItem(
        heading=heading,
        title=title,
        structured_fields=structured_fields,
        plan=sections.get("计划", ""),
        implementation_record=sections.get("实施记录", ""),
        verification_record=sections.get("验证记录", ""),
        review_record=sections.get("审核记录", ""),
        sections=sections,
    )


def _parse_structured_fields(
    section_text: str,
    heading: str,
    warnings: list[ParseWarning],
) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    raw_values: dict[str, list[str]] = {}
    current_key: str | None = None

    for line in section_text.split("\n"):
        bullet_match = STRUCTURED_BULLET_RE.match(line)
        if bullet_match:
            key = bullet_match.group(1).strip()
            value = bullet_match.group(2).strip()
            if key in raw_values:
                warnings.append(
                    ParseWarning(
                        code="duplicate_structured_key",
                        message=f"Duplicate structured key: {key}",
                        heading=heading,
                        key=key,
                    )
                )
            raw_values[key] = [value]
            current_key = key
            continue

        stripped = line.strip()
        if not stripped:
            if current_key is not None and current_key in raw_values and raw_values[current_key]:
                raw_values[current_key].append("")
            continue

        if re.match(r"^\s*-\s+", line):
            warnings.append(
                ParseWarning(
                    code="malformed_structured_bullet",
                    message=f"Malformed structured bullet: {line.strip()}",
                    heading=heading,
                )
            )
            current_key = None
            continue

        if current_key is not None:
            raw_values[current_key].append(line.rstrip())

    for key, value_lines in raw_values.items():
        joined_value = "\n".join(_trim_blank_line_edges(value_lines)).strip()
        fields[key] = _normalize_structured_value(joined_value)

    return fields


def _normalize_structured_value(value: str) -> Any:
    if value == "[]":
        return []

    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [part.strip() for part in inner.split(",") if part.strip()]

    return value


def _normalize_block(lines: list[str]) -> str:
    return "\n".join(_trim_blank_line_edges(lines))


def _trim_blank_line_edges(lines: list[str]) -> list[str]:
    start = 0
    end = len(lines)

    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1

    return lines[start:end]
