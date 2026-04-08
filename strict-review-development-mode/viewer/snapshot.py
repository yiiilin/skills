from __future__ import annotations

from collections import deque
import re
from typing import Any

KNOWN_STATUSES = (
    "blocked",
    "ready",
    "active",
    "implemented",
    "review-queued",
    "in-review",
    "changes-requested",
    "done",
)

QUEUE_KEY_BY_STATUS = {
    "blocked": "blocked",
    "ready": "ready",
    "active": "active",
    "implemented": "implemented",
    "review-queued": "review_queued",
    "in-review": "in_review",
    "changes-requested": "changes_requested",
    "done": "done",
}

DAG_DEGRADED_WARNING_CODES = {
    "duplicate_item_id",
    "missing_item_id",
    "missing_referenced_item",
    "asymmetric_dependency",
    "dag_cycle",
}

ITEM_ID_RE = re.compile(r"\bitem-[A-Za-z0-9._-]+\b")
ITEM_HEADING_LABEL_RE = re.compile(r"^Item\s+(?P<number>\d+)\s*-\s*(?P<label>\S(?:.*\S)?)\s*$")
MERMAID_LABEL_TEXT_RE = re.compile(r"\[[^\]]*\]|\([^\)]*\)|\{[^\}]*\}|\|[^|]*\||\"[^\"]*\"|'[^']*'")
MERMAID_NODE_REF_RE = re.compile(
    r"(?P<identifier>[A-Za-z0-9_][A-Za-z0-9._-]*)(?P<label>\[[^\]]*\]|\([^\)]*\)|\{[^\}]*\}|\"[^\"]*\"|'[^']*')?"
)
MERMAID_WORD_RE = re.compile(r"[A-Za-z0-9]+")
MERMAID_STOPWORDS = {"a", "an", "and", "for", "in", "into", "of", "on", "or", "over", "the", "to", "with"}
MERMAID_NON_GRAPH_PREFIXES = (
    "```",
    "graph ",
    "flowchart ",
    "subgraph ",
    "direction ",
    "end",
    "classdef ",
    "class ",
    "style ",
    "linkstyle ",
    "click ",
    "%%",
)


def build_snapshot(parsed: Any) -> dict[str, Any]:
    items = [_build_item_payload(item) for item in getattr(parsed, "items", [])]
    parser_warnings = [_warning_to_dict(warning) for warning in getattr(parsed, "warnings", [])]
    dag = _build_dag(items)
    warnings = parser_warnings + dag["warnings"]

    mermaid_reference = getattr(parsed, "top_level_sections", {}).get("Mermaid DAG", "")
    if mermaid_reference and not _can_validate_mermaid(mermaid_reference, items):
        warnings.append(
            {
                "code": "mermaid_validation_unavailable",
                "message": "Mermaid validation unavailable because stable item_id checking is impossible",
                "heading": "Mermaid DAG",
                "key": None,
            }
        )

    dag_degraded = any(warning["code"] in DAG_DEGRADED_WARNING_CODES for warning in warnings)

    return {
        "meta": {
            "title": getattr(parsed, "title", ""),
            "dag_degraded": dag_degraded,
            "implementation_concurrency": len(_build_queues(items)["active"]),
            "reviewer_concurrency": len(_build_queues(items)["in_review"]),
        },
        "counts": _build_counts(items),
        "queues": _build_queues(items),
        "dag": {
            "nodes": dag["nodes"],
            "edges": dag["edges"],
            "mermaid_reference": mermaid_reference,
        },
        "items": items,
        "warnings": warnings,
    }


def _build_item_payload(item: Any) -> dict[str, Any]:
    structured_fields = dict(getattr(item, "structured_fields", {}))
    return {
        "heading": getattr(item, "heading", ""),
        "title": getattr(item, "title", ""),
        "item_id": _normalize_optional_string(structured_fields.get("item_id")),
        "blocked_by": _normalize_string_list(structured_fields.get("blocked_by")),
        "blocks": _normalize_string_list(structured_fields.get("blocks")),
        "shared_surfaces": _normalize_string_list(structured_fields.get("shared_surfaces")),
        "parallel_group": _normalize_optional_string(structured_fields.get("parallel_group")),
        "dispatch_status": _normalize_optional_string(structured_fields.get("dispatch_status")),
        "assigned_subagent": _normalize_optional_string(structured_fields.get("assigned_subagent")),
        "structured_fields": structured_fields,
        "plan": getattr(item, "plan", ""),
        "implementation_record": getattr(item, "implementation_record", ""),
        "verification_record": getattr(item, "verification_record", ""),
        "review_record": getattr(item, "review_record", ""),
        "sections": dict(getattr(item, "sections", {})),
    }


def _build_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts = {status: 0 for status in KNOWN_STATUSES}
    for item in items:
        dispatch_status = item.get("dispatch_status")
        if isinstance(dispatch_status, str) and dispatch_status:
            counts[dispatch_status] = counts.get(dispatch_status, 0) + 1
    return counts


def _build_queues(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    queues = {
        "blocked": [],
        "ready": [],
        "active": [],
        "implemented": [],
        "review_queued": [],
        "in_review": [],
        "changes_requested": [],
        "done": [],
    }
    for item in items:
        queue_key = QUEUE_KEY_BY_STATUS.get(item.get("dispatch_status"))
        if queue_key is not None:
            queues[queue_key].append(item)
    return queues


def _build_dag(items: list[dict[str, Any]]) -> dict[str, Any]:
    ordered_ids: list[str] = []
    first_item_by_id: dict[str, dict[str, Any]] = {}
    for item in items:
        item_id = item.get("item_id")
        if isinstance(item_id, str) and item_id and item_id not in first_item_by_id:
            first_item_by_id[item_id] = item
            ordered_ids.append(item_id)

    warnings: list[dict[str, Any]] = []
    seen_warning_keys: set[tuple[Any, ...]] = set()
    edge_pairs: list[tuple[str, str]] = []
    seen_edge_pairs: set[tuple[str, str]] = set()

    def add_warning(code: str, message: str, heading: str | None, key: str | None) -> None:
        warning_key = (code, message, heading, key)
        if warning_key in seen_warning_keys:
            return
        seen_warning_keys.add(warning_key)
        warnings.append({"code": code, "message": message, "heading": heading, "key": key})

    def add_edge(source: str, target: str) -> None:
        edge_key = (source, target)
        if edge_key in seen_edge_pairs:
            return
        seen_edge_pairs.add(edge_key)
        edge_pairs.append(edge_key)

    for item_id in ordered_ids:
        item = first_item_by_id[item_id]
        heading = item.get("heading")

        for dependency_id in item.get("blocked_by", []):
            if dependency_id not in first_item_by_id:
                add_warning(
                    "missing_referenced_item",
                    f"Referenced item_id not found: {dependency_id}",
                    heading,
                    "blocked_by",
                )
                continue
            add_edge(dependency_id, item_id)
            if item_id not in first_item_by_id[dependency_id].get("blocks", []):
                add_warning(
                    "asymmetric_dependency",
                    f"Dependency is not mirrored between {dependency_id} and {item_id}",
                    heading,
                    "blocked_by",
                )

        for blocked_id in item.get("blocks", []):
            if blocked_id not in first_item_by_id:
                add_warning(
                    "missing_referenced_item",
                    f"Referenced item_id not found: {blocked_id}",
                    heading,
                    "blocks",
                )
                continue
            add_edge(item_id, blocked_id)
            if item_id not in first_item_by_id[blocked_id].get("blocked_by", []):
                add_warning(
                    "asymmetric_dependency",
                    f"Dependency is not mirrored between {item_id} and {blocked_id}",
                    heading,
                    "blocks",
                )

    if _has_cycle(ordered_ids, edge_pairs):
        add_warning(
            "dag_cycle",
            "Cycle detected in item dependency graph",
            None,
            None,
        )

    nodes = [
        {
            "node_id": item_id,
            "item_id": item_id,
            "heading": first_item_by_id[item_id].get("heading"),
            "title": first_item_by_id[item_id].get("title"),
            "dispatch_status": first_item_by_id[item_id].get("dispatch_status"),
        }
        for item_id in ordered_ids
    ]
    edges = [{"source": source, "target": target} for source, target in edge_pairs]

    return {"nodes": nodes, "edges": edges, "warnings": warnings}


def _has_cycle(node_ids: list[str], edge_pairs: list[tuple[str, str]]) -> bool:
    if not node_ids:
        return False

    incoming_counts = {node_id: 0 for node_id in node_ids}
    adjacency: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    for source, target in edge_pairs:
        if source not in incoming_counts or target not in incoming_counts:
            continue
        adjacency[source].append(target)
        incoming_counts[target] += 1

    queue: deque[str] = deque(node_id for node_id, incoming in incoming_counts.items() if incoming == 0)
    visited = 0
    while queue:
        node_id = queue.popleft()
        visited += 1
        for neighbor in adjacency[node_id]:
            incoming_counts[neighbor] -= 1
            if incoming_counts[neighbor] == 0:
                queue.append(neighbor)

    return visited != len(node_ids)


def _warning_to_dict(warning: Any) -> dict[str, Any]:
    return {
        "code": getattr(warning, "code", None),
        "message": getattr(warning, "message", ""),
        "heading": getattr(warning, "heading", None),
        "key": getattr(warning, "key", None),
    }


def _normalize_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    normalized = _normalize_optional_string(value)
    return [normalized] if normalized else []


def _normalize_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _mermaid_identifier_item_ids(mermaid_reference: str) -> set[str]:
    referenced_item_ids: set[str] = set()
    stripped_reference = MERMAID_LABEL_TEXT_RE.sub("", mermaid_reference)
    for raw_line in stripped_reference.splitlines():
        line = raw_line.strip()
        if not line or line.lower().startswith(MERMAID_NON_GRAPH_PREFIXES):
            continue
        referenced_item_ids.update(ITEM_ID_RE.findall(line))
    return referenced_item_ids


def _mermaid_referenced_item_ids(mermaid_reference: str, items: list[dict[str, Any]]) -> set[str]:
    referenced_item_ids = _mermaid_identifier_item_ids(mermaid_reference)
    alias_to_item_id: dict[str, str] = {}
    referenced_aliases: set[str] = set()
    stable_labels: dict[str, str] | None | bool = None

    for raw_line in mermaid_reference.splitlines():
        line = raw_line.strip()
        if not line or line.lower().startswith(MERMAID_NON_GRAPH_PREFIXES):
            continue

        for match in MERMAID_NODE_REF_RE.finditer(line):
            identifier = match.group("identifier")
            if identifier in {"graph", "flowchart", "subgraph", "direction", "end"}:
                continue

            if ITEM_ID_RE.fullmatch(identifier):
                referenced_item_ids.add(identifier)
                if match.group("label") is None:
                    continue

            raw_label = match.group("label")
            if raw_label is None:
                referenced_aliases.add(identifier)
                continue

            label_text = _normalize_mermaid_label_text(raw_label)
            if not label_text:
                return set()

            if ITEM_ID_RE.fullmatch(label_text):
                item_id = label_text
            else:
                item_id = _resolve_mermaid_label_item_id(label_text, items, stable_labels)
                if item_id is None:
                    return set()

            existing_item_id = alias_to_item_id.get(identifier)
            if existing_item_id is not None and existing_item_id != item_id:
                return set()
            alias_to_item_id[identifier] = item_id
            referenced_item_ids.add(item_id)

    for alias in referenced_aliases:
        item_id = alias_to_item_id.get(alias)
        if item_id is None:
            return set()
        referenced_item_ids.add(item_id)

    return referenced_item_ids


def _stable_mermaid_label_map(items: list[dict[str, Any]]) -> dict[str, str] | None:
    label_to_item_id: dict[str, str] = {}
    duplicate_labels: set[str] = set()

    for item in items:
        item_id = item.get("item_id")
        if not isinstance(item_id, str) or not item_id:
            return None

        for label in _candidate_mermaid_labels(item):
            existing_item_id = label_to_item_id.get(label)
            if existing_item_id is None:
                label_to_item_id[label] = item_id
                continue
            if existing_item_id != item_id:
                duplicate_labels.add(label)

    if duplicate_labels:
        return None
    return label_to_item_id


def _resolve_mermaid_label_item_id(
    label_text: str,
    items: list[dict[str, Any]],
    stable_labels: dict[str, str] | None | bool,
) -> str | None:
    if stable_labels is None:
        stable_labels = _stable_mermaid_label_map(items)
    if stable_labels is None:
        return None

    item_id = stable_labels.get(label_text)
    if item_id is not None:
        return item_id

    item_heading_match = ITEM_HEADING_LABEL_RE.match(label_text)
    if item_heading_match is None:
        return None

    label_number = item_heading_match.group("number")
    label_words = _meaningful_mermaid_label_words(item_heading_match.group("label"))
    if not label_words:
        return None

    best_item_id: str | None = None
    best_score = 0.0
    for item in items:
        candidate_item_id = item.get("item_id")
        if not isinstance(candidate_item_id, str) or not candidate_item_id:
            return None

        candidate_heading_match = ITEM_HEADING_LABEL_RE.match(str(item.get("heading") or ""))
        if candidate_heading_match is None or candidate_heading_match.group("number") != label_number:
            continue

        candidate_word_sets = [
            set(_meaningful_mermaid_label_words(candidate_heading_match.group("label"))),
            set(_meaningful_mermaid_label_words(str(item.get("title") or ""))),
        ]
        score = max(_mermaid_label_overlap_score(label_words, candidate_words) for candidate_words in candidate_word_sets)
        if score < 0.6:
            continue
        if score == best_score and best_item_id is not None and best_item_id != candidate_item_id:
            return None
        if score > best_score:
            best_score = score
            best_item_id = candidate_item_id

    return best_item_id


def _mermaid_label_words(value: str) -> tuple[str, ...]:
    return tuple(MERMAID_WORD_RE.findall(value.casefold()))


def _meaningful_mermaid_label_words(value: str) -> tuple[str, ...]:
    return tuple(word for word in _mermaid_label_words(value) if word not in MERMAID_STOPWORDS)


def _mermaid_label_overlap_score(needles: tuple[str, ...], candidate_words: set[str]) -> float:
    if not needles or not candidate_words:
        return 0.0
    overlap = sum(1 for word in needles if word in candidate_words)
    return overlap / len(needles)


def _candidate_mermaid_labels(item: dict[str, Any]) -> set[str]:
    labels: set[str] = set()

    heading = _normalize_mermaid_label_text(item.get("heading"))
    if heading:
        labels.add(heading)

    title = _normalize_mermaid_label_text(item.get("title"))
    if title:
        labels.add(title)

    return labels


def _normalize_mermaid_label_text(value: Any) -> str:
    if value is None:
        return ""

    text = str(value).strip()
    if len(text) >= 2:
        paired_wrappers = {"[": "]", "(": ")", "{": "}", '"': '"', "'": "'"}
        closing = paired_wrappers.get(text[0])
        if closing is not None and text[-1] == closing:
            text = text[1:-1]
    return re.sub(r"\s+", " ", text).strip()


def _can_validate_mermaid(mermaid_reference: str, items: list[dict[str, Any]]) -> bool:
    item_ids = [item["item_id"] for item in items if item.get("item_id")]
    if len(item_ids) != len(items) or not item_ids or len(set(item_ids)) != len(item_ids):
        return False

    referenced_item_ids = _mermaid_referenced_item_ids(mermaid_reference, items)
    if not referenced_item_ids:
        return False

    return set(item_ids).issubset(referenced_item_ids)
