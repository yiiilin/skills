# 强审开发模式协议参考

本文档保留完整执行语义。日常执行优先读 `SKILL.md`，只有需要判断边界、冲突、恢复或 reviewer 生命周期时再读这里。

## 核心边界

- checklist 是唯一 source of truth。
- `dispatch_status` 是唯一调度真相。
- 队列小节只是派生视图，不参与真实调度。
- 关键字段只能通过 `controller.py` 修改：`dispatch_status`、`assigned_subagent`、`reviewer_id`、`reviewer_state`、勾选状态。
- controller 返回 error 时，不得继续状态迁移。

## 任务归属判定

在创建或复用 checklist 前，必须判定当前请求：

- `same-task`：继续同一目标、同一验收标准或同一批工作包。
- `different-task`：新目标、新交付物，或需要追加不属于原 checklist 的工作包。
- `uncertain`：无法安全判断，必须先问用户澄清。

只有 `same-task` 可以继续旧 checklist。已 `done` 的 checklist 视为封存，不得 reopened。

## Checklist 必备结构

- 总 checklist
- `任务归属判定`
- `任务重整摘要`
- item-level DAG 说明
- Mermaid DAG
- 每项的结构化字段、计划、实施记录、验证记录、审核记录
- `当前状态` / `阻塞原因` / `下一动作`

每项结构化字段至少包含：

- `item_id`
- `blocked_by`
- `blocks`
- `shared_surfaces`
- `parallel_group`
- `dispatch_status`
- `assigned_subagent`

## 状态语义

- `blocked`：依赖未完成，或当前 cycle 不能安全推进。
- `ready`：依赖满足，等待计划或实施。
- `active`：正在实施。
- `implemented`：实施和验证已记录，等待审核调度。
- `review-queued`：等待 reviewer 槽位。
- `in-review`：reviewer 正在审核。
- `changes-requested`：reviewer 要求修改。
- `done`：审核通过且 reviewer 已关闭。

## 调度规则

每个 cycle 必须运行：

```bash
python3 strict-review-development-mode/controller.py cycle --checklist <checklist.md> --json
```

调度优先级固定：

1. `changes-requested` -> `rework` / `replan`
2. `implemented` 或 `review-queued` -> `review`
3. `ready` -> `planning` 或 `implementation`

实施并发上限为 `4`，reviewer 并发上限为 `2`。

## shared_surfaces 冲突

`shared_surfaces` 必须列出可能引发调度冲突的共享面，例如：

- 共享文件或目录写入面
- 公共 API / 接口契约
- 数据模型、schema、migration
- 权限、安全、鉴权逻辑
- 状态机、缓存一致性、并发控制
- 共享配置、入口、基础设施

两个事项的 `shared_surfaces` 有实质重叠时，不得同时进入会互相污染的状态，通常不得同时 `active`。

## 单项闭环

1. 写计划：用 `controller.py plan` 写入计划。计划必须说明改动范围、文件 ownership、依赖、共享面、并行性、验证方式和风险。
2. 开始实施：用 `controller.py start`。controller 会检查计划、依赖、并发上限和共享面冲突。
3. 完成实施：用 `controller.py mark-implemented` 写入实施记录和验证记录。
4. 进入审核：用 `controller.py queue-review` 和 `controller.py assign-reviewer`。
5. 审核不通过：用 `controller.py request-changes`。
6. 审核通过：用 `controller.py approve`，controller 会进入 `done` 并勾选 checklist。

## Reviewer 生命周期

- 每个 item 使用独立 reviewer。
- 同一 item 的复审优先复用原 reviewer。
- reviewer 不得跨 item 复用。
- item 进入 `done` 前，相关 reviewer 必须关闭并记录。
- 单次等待超时只是软超时；达到硬超时门槛后才允许 replacement reviewer。

推荐 reviewer_state：

- `reviewing`
- `slow`
- `suspect-stalled`
- `closed`
- `replaced`

## 结束条件

结束前必须再次运行 `cycle`。只有同时满足以下条件，才允许宣布完成：

- 没有 controller error
- 没有 `status_updates`
- 没有 `dispatch_packets`
- 所有 item 均为 `done`
- reviewer 均已关闭
- checklist 勾选状态已同步

