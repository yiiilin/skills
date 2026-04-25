from __future__ import print_function

import json
from pathlib import Path
import sys


EVALS_PATH = Path(__file__).resolve().parent / "evals.json"
REQUIRED_TOP_LEVEL_KEYS = ("skill_name", "evals")
REQUIRED_EVAL_KEYS = ("id", "name", "prompt", "expected_output", "files", "expectations")


def _error(message):
    print("error: " + message, file=sys.stderr)
    return 1


def _require_string(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + " must be a non-empty string")


def _require_string_list(value, label):
    if not isinstance(value, list):
        raise ValueError(label + " must be a list")
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(label + "[" + str(index) + "] must be a non-empty string")


def validate_payload(payload):
    for key in REQUIRED_TOP_LEVEL_KEYS:
        if key not in payload:
            raise ValueError("missing top-level key: " + key)
    if payload.get("skill_name") != "strict-review-development-mode":
        raise ValueError("skill_name must be strict-review-development-mode")
    evals = payload.get("evals")
    if not isinstance(evals, list) or not evals:
        raise ValueError("evals must be a non-empty list")

    seen_ids = set()
    seen_names = set()
    adversarial_count = 0
    categories = set()
    for index, item in enumerate(evals):
        if not isinstance(item, dict):
            raise ValueError("evals[" + str(index) + "] must be an object")
        for key in REQUIRED_EVAL_KEYS:
            if key not in item:
                raise ValueError("evals[" + str(index) + "] missing key: " + key)

        eval_id = item["id"]
        if not isinstance(eval_id, int):
            raise ValueError("eval id must be an integer: " + str(eval_id))
        if eval_id in seen_ids:
            raise ValueError("duplicate eval id: " + str(eval_id))
        seen_ids.add(eval_id)

        _require_string(item["name"], "eval " + str(eval_id) + " name")
        if item["name"] in seen_names:
            raise ValueError("duplicate eval name: " + item["name"])
        seen_names.add(item["name"])

        _require_string(item["prompt"], "eval " + str(eval_id) + " prompt")
        _require_string(item["expected_output"], "eval " + str(eval_id) + " expected_output")
        _require_string_list(item["expectations"], "eval " + str(eval_id) + " expectations")
        if len(item["expectations"]) < 5:
            raise ValueError("eval " + str(eval_id) + " should have at least five expectations")
        if not isinstance(item["files"], list):
            raise ValueError("eval " + str(eval_id) + " files must be a list")

        category = item.get("category")
        if isinstance(category, str) and category.strip():
            categories.add(category)
        if item.get("adversarial") is True:
            adversarial_count += 1

    # 这里校验 C 方案的覆盖面：必须有对抗用例，也必须覆盖多 agent 路由和完成门禁。
    if adversarial_count < 3:
        raise ValueError("C suite should include at least three adversarial evals")
    for category in ("multi_agent_routing", "controller_gating", "finish_gate"):
        if category not in categories:
            raise ValueError("missing required category: " + category)


def main():
    try:
        with EVALS_PATH.open(encoding="utf-8") as handle:
            payload = json.load(handle)
        validate_payload(payload)
    except Exception as exc:
        return _error(str(exc))

    print("ok: validated " + str(len(payload["evals"])) + " evals")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
