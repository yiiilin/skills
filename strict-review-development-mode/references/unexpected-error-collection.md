# 意外错误收集指南

这个文档用于收集真实用户在使用 `strict-review-development-mode` 时遇到的意料之外错误、跑偏行为或 controller 协议漏洞。目标是把零散反馈变成可复现、可脱敏、可转成 regression eval 的样本。

## 收集原则

- 只收集异常样本，不做静默遥测。
- 默认只在本地生成报告包，不自动上传。
- 默认脱敏 checklist、controller 输出和文本附件中的常见密钥、Bearer token、cookie、私钥块。
- 优先收集最小复现：用户请求、checklist 快照、controller `validate` / `cycle` 输出、实际错误、期望行为。
- 共享报告前仍需要人工检查一次，避免业务代码、客户数据或内部路径泄露。

## 快速生成报告

在用户遇到意外错误时，让使用者在项目根目录执行：

```bash
python3 strict-review-development-mode/scripts/collect_unexpected_error.py \
  --checklist .strict-review/<task-slug>/checklist.md \
  --category controller-error \
  --message "cycle 输出了意料之外的 dispatch packet" \
  --observed "item-2 依赖未完成，但仍然出现 implementation packet" \
  --expected "item-2 应保持 blocked，直到上游 item done" \
  --command "python3 strict-review-development-mode/controller.py cycle --checklist .strict-review/<task-slug>/checklist.md --json" \
  --zip
```

生成位置默认是：

```text
strict-review-development-mode-error-reports/
  <timestamp>-<task-slug>/
    metadata.json
    checklist.redacted.md
    controller/
      validate.json
      cycle.json
    triage-summary.json
    README.md
```

如果需要同时收集同一任务目录下的计划、实施、验证、审核文档，可显式加：

```bash
--include-task-files
```

这个选项只收集同目录下的 `.md`、`.txt`、`.json`、`.log` 文件，并受 `--max-file-bytes` 限制。

## 分类建议

- `controller-error`：controller 报错、违反协议、输出不符合预期。
- `unexpected-dispatch`：`cycle` 派发了不该派发的 packet，或没有派发应该出现的 packet。
- `workflow-violation`：agent 绕过计划、审核、shared_surfaces gate、done gate 等强审规则。
- `agent-output-mismatch`：skill 触发了，但 agent 输出没有遵守工作包、ticket 或路由要求。
- `viewer-error`：progress viewer 展示错误、解析错误或前端交互异常。
- `other`：暂时无法归类的问题。

## Triage 流程

1. 查看 `README.md` 中的人工补充字段，确认实际行为和期望行为。
2. 查看 `triage-summary.json`，先判断是否有 controller violation、status update 或 dispatch packet 数量异常。
3. 查看 `controller/validate.json` 和 `controller/cycle.json`，定位是 checklist 格式问题、状态机问题还是 agent 执行问题。
4. 如果是 skill 约束不够清楚，把样本转成 `evals/evals.json` 的新用例。
5. 如果是 controller 漏洞，先补 controller 单测，再修状态机或校验逻辑。
6. 修复后用同一个报告包重跑复现，确认错误不再出现。

## 转成 regression eval 的最小字段

```json
{
  "id": 16,
  "name": "descriptive_regression_name",
  "category": "protocol_recovery",
  "adversarial": true,
  "prompt": "从报告包 README.md 和 metadata.json 中提炼出来的用户原始请求或复现动作",
  "expected_output": "修复后 agent/controller 应表现出的行为",
  "files": [
    "reports/<case>/checklist.redacted.md",
    "reports/<case>/controller/cycle.json"
  ],
  "expectations": [
    "至少五条可人工或脚本判定的检查点"
  ]
}
```

报告包不应长期直接堆在 `evals/` 中。稳定复现后，把它压缩成精简 fixture 或 eval prompt，只保留能解释问题的最小数据。
