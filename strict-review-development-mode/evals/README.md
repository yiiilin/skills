# 强审开发模式评估套件

这个目录用于评估 `strict-review-development-mode` 是否真的把大任务固定成强审、DAG、可并行、独立审核的工作流。

## 评估目标

评估采用 C 方案：普通场景 + 对抗场景 + 多 agent 路由场景 + 多轮复杂协作场景。

重点看 agent 是否做到：

- 触发强审开发模式，而不是直接自由发挥。
- 创建或复用 controller-compatible checklist。
- 正确拆 item-level DAG。
- 使用 `controller.py validate` 和 `controller.py cycle`。
- 按 `dispatch_packets` 推进 planning、implementation、review、rework。
- 遵守 shared_surfaces 并行 gate。
- 用户主动指定 agent 时，使用 `set-routing` 写入路由。
- 给目标 agent 清晰敏捷 ticket：目的、目标、范围、完成标准、非目标、交付物和最小上下文。
- 不跳过独立 review，不 reopened 已 done checklist，不伪造完成。
- 在 reviewer 返工、shared_surfaces 冲突、reviewer 超时、计划质量不足、验证不可信、checklist 损坏时，先恢复协议和状态机，再继续推进。

## 推荐跑法

对每个 eval 同时跑两组：

- `with_skill`：显式给 agent `strict-review-development-mode/SKILL.md`。
- `baseline`：不给 skill，使用同样 prompt。

比较两组输出是否满足 `evals.json` 中的 `expectations`。

## 评分建议

每条 expectation 按 0/1 评分。

- `pass_rate >= 0.90`：强审流程基本可靠。
- `0.75 <= pass_rate < 0.90`：流程有效，但仍有跑偏风险。
- `0.60 <= pass_rate < 0.75`：skill 有触发价值，但约束不够稳定。
- `pass_rate < 0.60`：未达到强审模式目标。

建议额外记录：

- 是否创建或更新 checklist。
- 是否实际运行 controller 命令。
- 是否出现非法手改状态字段。
- 是否过早宣布完成。
- 是否把外部 agent 调用参数写死到 controller 协议。
- 是否在复杂多轮场景里保留 item 级目标、reviewer 独立性和 controller gate。

## 本地结构校验

运行：

```bash
python3 strict-review-development-mode/evals/validate_evals.py
```

这个脚本只校验 eval 文件结构，不会调用外部 agent。
