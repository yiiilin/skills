---
name: strict-review-development-mode
description: Use when the user explicitly asks for 强审开发模式, 使用强审开发, 按强审开发执行, 进入强审开发模式, or an equivalent strict review workflow for implementation tasks.
---

# 强审开发模式

这是强约束执行模式，不是建议清单。

## 核心规则

以下条件必须同时满足：

1. 开始实施前，必须先把任务落成 checklist 文档，且文档内必须同时包含：总 checklist、item-level DAG、Mermaid DAG 图、每个事项的结构化字段。
2. 每个 checklist 项都必须先写计划，再实施；没有计划就不能进入 `active`。
3. 调度必须由 DAG 和 `dispatch_status` 驱动，不以书写顺序、口头顺序或队列小节手工描述作为真实调度依据。
4. `dispatch_status` 是唯一调度真相；允许状态只有：`blocked`、`ready`、`active`、`implemented`、`review-queued`、`in-review`、`changes-requested`、`done`。
5. 所有队列小节都只是派生视图；每个 cycle 都必须重新读取整个 checklist + DAG，并从结构化字段重新推导未完成事项状态。
6. 每个 cycle 都必须核对 Mermaid DAG 与结构化字段；如果不一致，以结构化字段为准，并且必须立即修正文档，使 Mermaid、结构化字段、派生队列重新一致。
7. 每个 cycle 的调度优先级固定为：`changes-requested` -> `review-queued` -> 补满实施槽位。
8. 实施并发上限固定为 `4`，reviewer 并发上限固定为 `2`；只要存在可达且不冲突的可执行事项，就必须补满到当前可达上限，不能故意少派。
9. 事项间冲突必须通过 `shared_surfaces` 和 DAG 依赖显式建模；有共享写面、共享契约、共享状态机、共享迁移、共享权限/配置面时，不得同时进入 `active`。
10. 实施完成后必须记录验证结果；没有验证记录不得进入审核调度。
11. 每个事项都必须交给独立 subagent 审核，并维护 reviewer 生命周期；优先使用当前可用的最强最新模型，默认目标是 `gpt-5.4` + `xhigh`。
12. `wait_agent` 单次超时只视为软超时；只有在二次探测后仍无结果，且达到硬超时门槛时，才允许关闭 reviewer 并安排 replacement reviewer。
13. 只要仍有未完成事项、可执行动作、待处理 reviewer 分配、活跃 reviewer、待处理审查意见，agent 就不得静默停止；只有在“没有任何可执行事项且没有任何 reviewer assignment 可继续推进”时，才允许向用户报告 blocker。
14. 只有全部事项进入 `done` 后，才允许宣布完成。

## 启动流程

### 1. 先创建 checklist 文档

默认位置：

- 仓库已有 `docs/` 时，使用 `docs/checklists/`
- 否则使用仓库根目录下的 `checklists/`

默认文件名：

- `<task-slug>.md`

不要只在对话里口头列 checklist，必须落成文档。

### 2. 创建实施前基线

在开始任何实施前，文档里必须先写好：

- 总 checklist
- item-level DAG
- Mermaid DAG 图
- 每个事项的结构化字段
- 每个事项的计划区

### 3. 开始前先同步给用户

必须说明：

- checklist 文档路径
- checklist 项列表
- DAG 摘要
- 当前 `ready` / `active` / `review-queued` 事项概览

### 4. 初始化调度字段

开始实施前，每个事项的结构化字段必须至少包含以下批准字段：

- `item_id`
- `blocked_by`
- `blocks`
- `shared_surfaces`
- `parallel_group`
- `dispatch_status`
- `assigned_subagent`

如需补充实施/验证/审核信息，可额外维护辅助字段（例如 `verification`、`reviewer_id`、`reviewer_state`、`next_action`），但这些都是补充元数据，不能替代上述批准字段作为调度真相。

### 5. 进入 cycle 驱动执行

每个 cycle 开始时必须：

1. 重新读取整个 checklist 文档，而不是只看局部小节。
2. 重新核对 item-level DAG、Mermaid、结构化字段。
3. 以结构化字段为准修正 Mermaid 与派生队列视图。
4. 重新计算所有未完成事项的 `dispatch_status`。
5. 按固定优先级执行：`changes-requested` -> `review-queued` -> 补满实施槽位。

## Checklist 结构

至少包含以下部分：

- 总 checklist
- item-level DAG 说明
- Mermaid DAG 图
- 每一项的结构化字段区
- 每一项的计划区
- 每一项的实施记录区
- 每一项的验证记录区
- 每一项的审核记录区
- 派生队列视图区（可选，但若存在只能由结构化字段推导）
- `当前状态` / `阻塞原因` / `下一动作`

每个事项的结构化字段至少要覆盖以下批准调度字段：

- `item_id`
- `blocked_by`
- `blocks`
- `shared_surfaces`
- `parallel_group`
- `dispatch_status`
- `assigned_subagent`

如需记录补充信息，可追加辅助字段（例如 `verification`、`reviewer_id`、`reviewer_state`、`last_review_result`、`next_action`），但不得用替代命名（如把 `blocked_by` 改写成 `depends_on`）充当主调度字段。

Mermaid 必须表达 item-level DAG，而不是只画阶段说明。`blocked_by`、`blocks`、`shared_surfaces`、`parallel_group`、`dispatch_status` 必须能直接支撑调度决策。

## 并行执行规则

### 调度真相来源

调度只能读取：

- item-level DAG
- 结构化字段
- 当前 cycle 重新计算得到的 `dispatch_status`

任何 ready queue、review queue、摘要表都只是派生视图，不能替代 `dispatch_status` 作为调度依据。

### 每个 cycle 的一致性校对

每个 cycle 都必须重新读取整个 checklist 文档，并执行：

1. 对照 Mermaid DAG 与结构化字段。
2. 如果不一致，以结构化字段为准。
3. 立即修正文档，让 Mermaid 和派生视图恢复一致。
4. 重新计算所有未完成事项，而不是只看上一轮正在处理的事项。

### dispatch_status 语义

`dispatch_status` 允许且只允许以下值：

- `blocked`：仍有 `blocked_by` 中未满足依赖，或与当前 `active` / `in-review` 事项在 `shared_surfaces` 或 `parallel_group` 上冲突，当前 cycle 不能推进。
- `ready`：`blocked_by` 依赖已满足，且当前 cycle 可进入实施槽位。
- `active`：正在实施中，尚未完成本轮实现/验证闭环。
- `implemented`：实现已完成，验证已记录，等待被当前 cycle 收敛到审核调度。
- `review-queued`：实现和验证已完成，但 reviewer 槽位暂时不可用，或等待本 cycle 分配 reviewer。
- `in-review`：已分配 reviewer，当前正在等待 reviewer 结果或执行 reviewer 超时处理。
- `changes-requested`：reviewer 已返回问题，必须优先修正并补充验证。
- `done`：最新审核明确通过，且该事项相关 reviewer 已关闭并记录。

除 `done` 外的所有状态都属于未完成事项，必须在每个 cycle 重新计算。

### 每个 cycle 的固定调度优先级

每个 cycle 必须严格按以下顺序调度：

1. 先处理全部 `changes-requested` 事项。
2. 再处理全部 `review-queued` 事项，优先占满 reviewer 槽位。
3. 最后用 `ready` 事项补满实施槽位。

不要跳过高优先级状态去启动新的低优先级事项。

### 实施并发上限

实施并发上限固定为 `4`。

每个 cycle 必须计算：

`target_impl_concurrency = min(current active items + non-conflicting ready items that can be filled now, 4)`

要求：

- `current active items` 指当前已处于 `active` 的事项数。
- `non-conflicting ready items that can be filled now` 指当前 cycle 中 `blocked_by` 已满足、与现有 `active` 事项及彼此之间在 `shared_surfaces` 和 `parallel_group` 上都不冲突、且可以立刻开工的 `ready` 事项数。
- 只要存在可达的非冲突 `ready` 事项，就必须把这些事项显式分配给 implementation subagents，并并行派发直到达到当前可达上限或实施上限 `4`；不能只在抽象上“占满槽位”而不实际 dispatch subagent。
- 当 implementation 槽位可用且存在多个可并行的 `ready` 事项时，必须在同一 cycle 中并行 dispatch 对应数量的 implementation subagents，数量上限为当前可达 cap。
- 每个进入 `active` 的事项都必须记录其 `assigned_subagent`；若某槽位为空，必须能从 DAG 依赖或 `shared_surfaces` / `parallel_group` 冲突中解释为什么当前 cycle 不能再派发。
- 如果当前可达上限小于 `4`，原因必须能从 DAG 依赖或 `shared_surfaces` / `parallel_group` 冲突中解释出来。

### Reviewer 并发上限

- 同时最多 `2` 个 `in-review` 事项
- `review-queued` 可多于 `2`，但 reviewer 分配时必须按优先顺序消化

当 reviewer 槽位不足时，事项保持在 `review-queued`，直到本 cycle 或后续 cycle 有 reviewer 空位。

### shared_surfaces 冲突规则

`shared_surfaces` 必须显式列出可能引发调度冲突的共享面，例如：

- 共享文件或目录写入面
- 公共 API / 接口契约
- 数据模型、schema、migration
- 权限、安全、鉴权逻辑
- 状态机、缓存一致性、并发控制
- 共享配置、入口、基础设施

若两个未完成事项的 `shared_surfaces` 实质重叠，则不能同时处于会相互污染的状态；通常不得同时进入 `active`，必要时也不得并行 `in-review` 后继续基于旧上下文实施。

## 单项执行闭环

### 第一步：先写计划

开始实现前，先把该项计划写入 checklist 文档。计划至少要写清楚：

- 这项要改什么
- 会涉及哪些文件或模块
- 依赖哪些前置事项（对应 `blocked_by` / `blocks`）
- 会触碰哪些 `shared_surfaces`
- 属于哪个 `parallel_group`，以及为何可与哪些事项并行
- 如何验证完成
- 已知风险和边界条件

计划不具体就先补具体，再进入 `active`。

### 第二步：按计划实施并验证

只处理当前事项计划内内容，不顺手实现其他未计划内容。

实施后更新：

- `dispatch_status`
- `assigned_subagent`
- `实施记录`
- `验证记录`
- `next_action`

实现和验证完成后，先把事项收敛到 `implemented`，再由当前 cycle 判断是直接进入 `in-review` 还是进入 `review-queued`。

如果没有任何验证动作，不允许离开实施闭环进入审核调度。

### 第三步：进入审核调度

审核必须使用独立 subagent。优先选择当前环境里可用的最强最新模型，默认目标为：

- 模型：`gpt-5.4`
- 推理强度：`xhigh`

若当前环境不可用，允许降级到可用替代，但必须在审核记录中写明原因。

审核输入至少要包含：

- 原始任务目标
- checklist 文档路径
- 当前事项名称与 `item_id`
- 当前事项计划
- 当前事项在 DAG 中的依赖与后继关系
- `shared_surfaces`
- 涉及文件和改动摘要
- 验证结果

审核时优先检查：

- 是否符合当前事项计划
- 是否与 DAG 依赖假设一致
- 是否遗漏需求
- 是否引入行为回归
- 是否存在明显 bug 或边界问题
- 是否缺少必要验证
- 是否做了不必要的额外实现

## 可选 Web Progress Viewer

当用户明确要求“打开界面”“看进度”“打开 web 界面”或等价意图时，可以启动本地只读 web viewer，帮助查看：

- DAG / 依赖关系
- ready / active / review-queued / in-review / done 等派生队列
- item 详情（计划、实施记录、验证记录、审核记录）
- reviewer 相关状态

边界规则：

- 启动前先询问用户是否需要打开界面，不要默认自动弹出
- viewer 只作为派生视图，不参与调度决策
- checklist 文档始终是唯一 source of truth
- 不允许通过 viewer 直接修改 `dispatch_status`、reviewer 分配或其他调度字段
- 若 viewer 展示与 checklist 不一致，以 checklist 为准

推荐启动命令：

```bash
python3 strict-review-development-mode/viewer/serve.py --checklist <checklist-path> --port 0
```

即使 viewer 已打开，终端中的关键调度摘要仍必须继续输出，不得把 viewer 当成新的控制台。

## 审查 agent 生命周期

每个 checklist 事项默认只保留一个可复用 reviewer 身份。

- 事项第一次进入审核时，为其创建专属 reviewer，并在 `审核记录` 中记录 agent 标识。
- 同一事项后续若进入 `changes-requested` -> 复审循环，优先复用同一个 reviewer，不要为同一事项反复新开 reviewer。
- `review-queued` 表示该事项已准备好审核，但尚未占用 reviewer 槽位；只有真正分配 reviewer 后才转为 `in-review`。
- 只有在 reviewer 已不可用、上下文明显失真、或必须切换到更强可用模型时，才允许创建 replacement reviewer。
- 若必须更换 reviewer，先关闭旧 reviewer；如果做不到，必须在 `审核记录` 中写明原因和剩余 reviewer 的处理计划。
- reviewer 不得跨事项复用。
- 事项变为 `done` 前，相关 reviewer 必须全部关闭并记录。
- DAG 并行模式下也不得突破 reviewer 并发上限 `2`。

## Reviewer 超时状态机

`wait_agent` 超时不等于 reviewer 已失活。必须区分 `slow` 和 `stalled`，并把处理动作与 `review-queued` / `in-review` 调度衔接起来。

推荐默认阈值：

- 首次等待窗口：`5` 分钟
- 二次探测窗口：再等 `5-10` 分钟
- 硬超时门槛：累计等待至少 `15` 分钟，且至少连续 `2` 次 `wait_agent` 超时

推荐 reviewer_state：

- `reviewing`：正常等待 reviewer 结果
- `slow`：发生过一次超时，但仍可能在继续工作
- `suspect-stalled`：达到硬超时门槛，疑似卡死
- `closed`：已显式关闭
- `replaced`：旧 reviewer 已关闭，并已切换到 replacement reviewer

处理规则：

- 第一次 `wait_agent` 超时时，只把 reviewer 标记为 `slow`，更新 `审核记录`，事项保持 `in-review`，不要立即关闭。
- 第一次超时后，必须做一次二次探测，并给更长等待窗口。
- 二次探测期间只做被动等待或轮询，不要用打断式手段探活，以免误杀慢 reviewer。
- 只有在二次探测后仍无结果，且达到硬超时门槛时，才允许把 reviewer 标记为 `suspect-stalled` 并执行 `close_agent`。
- 关闭后如果当前事项仍需审核：
  - reviewer 槽位可立即分配时，创建 replacement reviewer，并保持事项为 `in-review`；
  - reviewer 槽位暂不可用时，把事项置为 `review-queued`，等待后续 cycle 重新分配。
- 每个事项最多自动创建 `1` 个 replacement reviewer。若 replacement reviewer 仍达到硬超时，停止自动替换，升级给主 agent 或用户决定。

## 审核循环

只要审查者还提出任何需要处理的问题，都视为未通过。必须循环：

1. 把 reviewer 问题记录到 `审核记录`。
2. 将 `dispatch_status` 设为 `changes-requested`。
3. 在下一调度 cycle 优先修正实现或补充验证。
4. 更新 `实施记录`、`验证记录`、`next_action`。
5. 优先把新结果发回当前事项既有 reviewer 复审；只有该 reviewer 不可复用时才创建 replacement reviewer。
6. 复审请求发出后，事项进入 `in-review`；若 reviewer 槽位不足，则先进入 `review-queued`。
7. 若等待 reviewer 时发生超时，按 Reviewer 超时状态机处理；不要把单次超时直接当成失败或卡死。
8. 只有最新一轮审核明确表示“没有发现问题”或等价批准时，才允许准备进入 `done` 收口。

## 冲突处理

如果任一 cycle 发现 DAG、`blocked_by` / `blocks`、`shared_surfaces`、`parallel_group`、实施事实或 reviewer 反馈之间存在冲突：

1. 立即重新读取整个 checklist 文档。
2. 重新核对 Mermaid、结构化字段、实施记录、审核记录。
3. 以结构化字段为准修正文档，并重算所有未完成事项。
4. 对受影响事项重新判定 `blocked` / `ready` / `changes-requested` / `review-queued` / `in-review`。
5. 若发现原先并行假设失效，立即撤销错误并行，把冲突事项移出错误状态，并写明新的 `blocked_by` / `blocks`、`shared_surfaces` 或 `parallel_group`。
6. 只要仍有其他可执行事项或 reviewer assignment 可推进，就继续调度，不要因为局部冲突直接向用户报 blocker。

## 不得静默停止

未完成不等于可停止。以下情况都视为违规停止：

- 仍有未完成事项，但 agent 直接结束本轮响应
- 未明确说明阻塞原因，就停在半途
- 尚有 `review-queued`、`in-review`、`changes-requested` 或可补位的 `ready` 事项，却没有继续推进或明确交接
- 仅输出阶段性说明，却没有写明下一动作和恢复点

如果当前回合不能继续推进，允许进入“受控阻塞”，但不允许静默结束。

## 结束响应前检查

在结束任何一次响应前，必须执行以下检查：

1. 重新读取整个 checklist 文档。
2. 重新核对 item-level DAG、Mermaid、结构化字段是否一致；若不一致，立即修正，且以结构化字段为准。
3. 重新计算所有未完成事项的 `dispatch_status`，不要只看本轮刚处理的事项。
4. 确认当前是否仍存在：`changes-requested`、`review-queued`、可补位的 `ready`、需要继续实施的 `active`、需要等待/探测/关闭/替换的 reviewer assignment。
5. 若仍有任何可执行事项或 reviewer assignment 可继续推进，就继续推进，不要结束响应。
6. 只有在“没有任何可执行事项且没有任何 reviewer assignment 可继续推进”时，才允许写入 blocker，并先把 `当前状态`、`阻塞原因`、`下一动作` 更新到 checklist。
7. 恢复或结束前用于同步给用户/主 agent 的输出，至少要显式包含：`当前 ready 队列`、`当前 active 实现队列`、`当前 active reviewer 队列`、`当前最优先动作`。
8. 只有当所有事项都已进入 `done`，且相关 reviewer 已关闭后，才允许发送完成结论。

结束响应时，只允许处于以下三种状态之一：

- `继续执行`：不结束，直接做下一动作
- `受控阻塞`：已经重读 checklist + DAG、完成一致性修正、确认没有可执行事项或 reviewer assignment 后，再向用户报告 blocker
- `全部完成`：所有 checklist 项均已进入 `done`

任何不属于这三种状态的结束方式，都视为违规。

## 中断与恢复协议

若会话因中断、超时、上下文漂移、reviewer 替换或其他原因恢复，必须先执行恢复协议，再继续推进：

1. 重新读取整个 checklist 文档，而不是只读当前事项小节。
2. 重新核对 item-level DAG、Mermaid、结构化字段，并立即修正不一致处；以结构化字段为准。
3. 重新计算所有未完成事项，找出全部 `blocked`、`ready`、`active`、`implemented`、`review-queued`、`in-review`、`changes-requested`。
4. 重新核对 reviewer 状态，确认哪些事项仍在等待 reviewer、需要二次探测、需要关闭、需要 replacement reviewer、或可从 `review-queued` 重新分配 reviewer。
5. 按固定优先级重新调度：`changes-requested` -> `review-queued` -> 补满实施槽位。
6. 明确当前 cycle 的实际下一动作并恢复执行，而不是停留在摘要或阶段性说明。

如果恢复后发现状态不一致：

- 先修正文档中的 DAG、结构化字段与派生视图
- 再恢复执行
- 不要因为状态不一致而直接结束响应

## 勾选规则

只有同时满足以下条件，才允许把 `- [ ]` 改为 `- [x]`：

- 计划已写明
- 实现已完成
- 验证已执行并记录
- 最新 `dispatch_status` 已进入 `done`
- 独立强审已完成
- 最新审核不再提出问题
- 当前事项相关 reviewer 已显式关闭并记录

## 停止条件

完成前必须回看整个 checklist 文档和 item-level DAG。

只要还有任何未完成事项、活跃 reviewer、待处理 reviewer assignment、可执行事项或待处理审查意见：

- 不要宣布完成
- 不要停止流程
- 继续按固定优先级调度下一动作

最终汇报必须说明 checklist 文档路径，并确认所有事项都已进入 `done`。
