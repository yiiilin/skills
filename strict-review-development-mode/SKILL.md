---
name: strict-review-development-mode
description: Use when the user explicitly asks for 强审开发模式, 使用强审开发, 按强审开发执行, 进入强审开发模式, or asks for a strict controller-enforced implementation workflow with checklist, DAG, parallel implementation, independent review, or reviewer gating.
---

# 强审开发模式

这是 controller-enforced 的开发工作流。`SKILL.md` 只保留执行入口；完整协议见 `references/protocol.md`，状态图见 `references/workflow-state-machine.md`，完整 checklist 结构见 `checklist-template.md`。

## 不可变规则

1. 先做 `任务归属判定`，结果只允许 `same-task`、`different-task`、`uncertain`。
2. `different-task` 必须新建 checklist；已 `done` 的 checklist 不得 reopened。
3. checklist 是唯一 source of truth，但关键状态字段必须由 `controller.py` 写入。
4. 禁止手改 `dispatch_status`、`assigned_subagent`、`reviewer_id`、`reviewer_state` 和 checklist 勾选状态。
5. 每个 cycle 必须先运行 controller；controller 返回 error 时先修 error，不得继续推进。
6. 有 `dispatch_packets` 时必须消费 packet；能并行就并行派发，不能并行就按 packet 顺序本地执行。
7. 结束前必须再次运行 `cycle`；只有所有 item 都是 `done` 且 `交付总结` 合规，才能宣布完成。
8. 用户只需要和 coordinator 沟通；planner、developer、reviewer 可以是任意 agent，由 coordinator 根据 packet 路由和当前环境决定如何调用。
9. 强审流程产生的文档必须放入 `.strict-review/<task-slug>/` 专属任务目录，不要在项目根目录散落 `strict-review-*.md`、`.strict-review-item-*.md` 或 `strict-review-artifacts/`。
10. 用户原始请求的完整范围是默认完成边界；coordinator 自行拆出的 phase、阶段、wave、批次不得作为完成边界。
11. 如果 coordinator 识别出多阶段任务，所有属于用户原始请求范围的阶段必须进入 checklist，或明确记录 blocker / needs-clarification。
12. “后续工作”“下一阶段”“可选优化”不得包含用户原始请求内尚未完成的事项；请求内未完成事项必须保持 checklist 未完成或报告 blocked。
13. 除非用户明确说“只做第一阶段”“先做到这里停”，否则第一阶段完成后必须继续运行 controller cycle，直到所有请求内 item done。

## 入口流程

1. 判定当前请求属于 `same-task`、`different-task` 还是 `uncertain`。
2. 为 `same-task` 选择既有未完成 checklist；为 `different-task` 新建 checklist。
3. 补齐 `完成契约` 和 `任务启动总规划`，确认用户原始请求范围、验收标准、代码侦察证据、工作包拆分理由、并行策略、主要风险和 `启动判定`。
4. 若任务尚未拆成可并行工作包，先拆成 item-level DAG，并明确 `blocked_by`、`blocks`、`shared_surfaces`；不得只把“第一阶段”放入 checklist 后就停。
5. 如果用户主动说明 agent 偏好，用 `set-routing` 写入 checklist；不要只记在对话里，也不要把 agent 选择当作开场必问题。
6. 创建或补齐 checklist 后运行 `validate`，每个执行循环都运行 `cycle`。
7. 如果 `cycle` 输出 `status_updates`，运行 `cycle --write` 同步派生状态。
8. 如果 `cycle` 输出 `dispatch_packets`，coordinator 根据 packet 的 `target_agent`、`role`、`prompt`、`commands` 派发工作包；能并行消费的 packet 应尽量并行派发。

最小命令：

```bash
python3 strict-review-development-mode/controller.py init --checklist .strict-review/<task-slug>/checklist.md --title "<任务标题>" --request "<用户请求>" --items-json '<json-array>'
python3 strict-review-development-mode/controller.py validate --checklist .strict-review/<task-slug>/checklist.md --json
python3 strict-review-development-mode/controller.py cycle --checklist .strict-review/<task-slug>/checklist.md --json
python3 strict-review-development-mode/controller.py cycle --checklist .strict-review/<task-slug>/checklist.md --write --json
```

`items-json` 是工作包数组，每项至少包含 `item_id` 和 `title`，可包含 `blocked_by`、`shared_surfaces`、`parallel_group`。后续 `plan/start/mark-implemented/review/approve` 等命令优先使用 `dispatch_packets[*].commands` 中给出的命令。

## 启动与收口

新 checklist 必须包含 `完成契约`、`任务启动总规划`、`交付总结`。字段定义见 `references/protocol.md`；完整模板见 `checklist-template.md`。

- `启动判定：needs-clarification` 时，不得派发 implementation / rework；只能继续补规划、澄清问题或处理已进入 review 的事项。
- `启动判定：ready` 时，`完成契约` 中的 `请求内未纳入事项` 必须为 `无`。
- `init` 默认使用 `启动判定：needs-clarification`；coordinator 补齐启动规划后才能改成 `ready`。
- `交付总结` 标记 `complete` 时，所有 item 必须为 `done`，且 `用户请求内未交付内容` 必须为 `无`。
- `请求外后续建议` 只能记录请求外增强项，不能隐藏用户原始请求内尚未完成的事项。

## Packet 处理

`cycle --json` 会按固定优先级输出 packet：

1. `rework` / `replan`：优先处理 reviewer 要求修改的事项。
2. `review-batch` / `review`：实现和验证已完成，等待独立审核。
3. `planning`：ready 但尚无具体计划。
4. `implementation`：ready 且计划已写明，可进入实施。

packet 是唯一派工合同。目标 agent 只负责 packet 的 `agent_objective` 和 `success_criteria`，只把强审流程文档写入 packet 指向的 `.strict-review/<task-slug>/` 路径，并用 packet 给出的 controller 命令写回结果。完整字段语义见 `references/protocol.md`。

提速原则：planning 和 review packet 可并行时优先并行；implementation / rework 仍受 DAG 依赖和 `shared_surfaces` 冲突约束。共享面相同的事项可以并行写计划，但不能同时进入会互相污染的实施状态。低/中风险且 `review_mode：batch-eligible`、`review_group` 相同的事项可由 controller 输出 `review-batch`；批量只合并阅读上下文，`assign-reviewer`、`request-changes`、`approve` 仍逐项执行，高风险事项保持单审。

## 文档格式

所有计划、实施、验证、审核和 reviewer replacement 文档都必须写成可嵌入 checklist 小节的 Markdown 正文片段。不要使用 H1/H2/H3 标题，不要写新的 `## Item ...`、`### 结构化字段`、`### 计划`、`### 实施记录`、`### 验证记录` 或 `### 审核记录`；需要分节时使用 H4-H6 或列表。

## 何时读参考

- 需要完整协议、字段定义、冲突规则、reviewer 生命周期时，读 `references/protocol.md`。
- 需要状态机和 Mermaid 图时，读 `references/workflow-state-machine.md` 或运行 `controller.py diagram`。
- 用户遇到 controller 报错、错误派工、agent 跑偏或 viewer 异常时，读 `references/unexpected-error-collection.md`，并优先使用 `scripts/collect_unexpected_error.py` 生成本地报告包。
- 用户要求打开进度界面时，再看 `viewer/serve.py`；viewer 只读，不参与调度。

## 结束前检查

结束前必须运行：

```bash
python3 strict-review-development-mode/controller.py cycle --checklist .strict-review/<task-slug>/checklist.md --json
```

如果仍有 error、`status_updates`、`dispatch_packets`、未完成 item、活跃 reviewer、待处理 reviewer assignment，或 `交付总结` 未合规，就继续推进或报告明确 blocker；不要宣布完成。
