# 强审开发模式协议参考

本文档保留完整执行语义。日常执行优先读 `SKILL.md`，只有需要判断边界、冲突、恢复或 reviewer 生命周期时再读这里。

## 核心边界

- checklist 是唯一 source of truth。
- `dispatch_status` 是唯一调度真相。
- 队列小节只是派生视图，不参与真实调度。
- 关键字段只能通过 `controller.py` 修改：`dispatch_status`、`assigned_subagent`、`reviewer_id`、`reviewer_state`、勾选状态。
- controller 返回 error 时，不得继续状态迁移。
- 用户只和 coordinator 沟通。其他 agent 的选择由 checklist 路由策略和 coordinator 决策共同决定。
- 强审流程产生的文档必须收敛到当前任务的 `.strict-review/<task-slug>/` 专属任务目录。

## 文档目录

默认路径。`task-slug` 由 coordinator 根据任务目标取一个稳定、可读、简短的目录名，必须描述当前强审开发任务解决的问题：

- `.strict-review/<task-slug>/checklist.md`
- `.strict-review/<task-slug>/<item_id>-plan.md`
- `.strict-review/<task-slug>/<item_id>-implementation.md`
- `.strict-review/<task-slug>/<item_id>-verification.md`
- `.strict-review/<task-slug>/<item_id>-review.md`
- `.strict-review/<task-slug>/<item_id>-reviewer-replacement.md`

coordinator 和各角色 agent 不应在项目根目录生成 `strict-review-*.md`、`.strict-review-item-*.md` 或 `strict-review-artifacts/`，也不应把多轮任务日志直接平铺到 `.strict-review/` 根目录。历史 checklist 可以继续读取旧路径，但新产生的计划、实施、验证、审核和 reviewer replacement 原因必须写入 `.strict-review/<task-slug>/`。

## 派工文档 Markdown 格式

计划、实施、验证、审核和 reviewer replacement 文档是写回 checklist 小节的正文片段，不是独立页面。agent 不应在这些文档中使用 H1/H2/H3 标题，也不应生成新的 `## Item ...`、`### 结构化字段`、`### 计划`、`### 实施记录`、`### 验证记录` 或 `### 审核记录`。需要分节时使用 H4-H6 或无序列表。

controller 在执行 `plan`、`mark-implemented`、`request-changes`、`approve` 和 `replace-reviewer` 时会对外部 Markdown 正文做结构安全化：代码块外的 H1-H3 会被降级为 H4-H6，防止常见 Markdown 标题被解析成 checklist 结构标题。

## 任务归属判定

在创建或复用 checklist 前，必须判定当前请求：

- `same-task`：继续同一目标、同一验收标准或同一批工作包。
- `different-task`：新目标、新交付物，或需要追加不属于原 checklist 的工作包。
- `uncertain`：无法安全判断，必须先问用户澄清。

只有 `same-task` 可以继续旧 checklist。已 `done` 的 checklist 视为封存，不得 reopened。

## Checklist 必备结构

- 总 checklist
- `文档位置`
- `任务归属判定`
- `完成契约`
- `任务启动总规划`
- `任务重整摘要`
- `交付总结`
- item-level DAG 说明
- Mermaid DAG
- 每项的结构化字段、计划、实施记录、验证记录、审核记录
- `当前状态` / `阻塞原因` / `下一动作`

`完成契约` 固定用户原始请求范围，禁止把 coordinator 自行划出的阶段作为停止条件：

- `用户原始请求范围`
- `本 checklist 覆盖范围`
- `自划阶段是否可作为停止条件`：必须为 `否`
- `允许中途停止条件`：用户明确要求暂停 / blocker / needs-clarification
- `请求内未纳入事项`：`启动判定` 为 `ready` 时必须为 `无`
- `后续建议边界`：只能写请求外增强项，不能把用户原始要求内的事项放到后续建议

`任务启动总规划` 是实施前的总规划 gate：

- `任务目标`
- `非目标`
- `验收标准`
- `已知约束`
- `代码侦察证据`
- `初步方案`
- `工作包拆分理由`
- `并行策略`
- `主要风险`
- `需要用户确认的问题`
- `启动判定`：只允许 `ready` 或 `needs-clarification`

`init` 默认写入 `启动判定：needs-clarification` 和 `请求内未纳入事项：待确认`。coordinator 必须补齐启动规划、确认请求内事项全部纳入 checklist 后，才能把 `启动判定` 改为 `ready`。`ready` 至少要求 `任务目标`、`验收标准`、`代码侦察证据`、`工作包拆分理由`、`并行策略` 不为空或占位。

`交付总结` 是结束前的交付 gate：

- `完成状态`：只允许 `pending`、`complete` 或 `blocked`
- `用户请求内交付内容`
- `用户请求内未交付内容`：`完成状态` 为 `complete` 时必须为 `无`
- `请求外后续建议`
- `关键变更位置`
- `最终验证证据`
- `审核结果摘要`
- `遗留风险`
- `用户验收入口`

每项结构化字段至少包含：

- `item_id`
- `blocked_by`
- `blocks`
- `shared_surfaces`
- `parallel_group`
- `dispatch_status`
- `assigned_subagent`

可选 item 级路由覆盖：

- `planning_agent`
- `implementation_agent`
- `rework_agent`
- `review_agent`

## Agent 路由策略

全局路由策略写在 checklist 顶层：

```md
## Agent 路由策略
- coordinator_agent：current
- default_agent：current
- fallback_agent：current
- planning_agent：current
- implementation_agent：current
- rework_agent：current
- review_agent：current
- invocation_policy：coordinator-decides
```

路由优先级：

1. item 级 agent 覆盖
2. 全局角色 agent
3. `default_agent`
4. `current`

agent 名称是不透明字符串，controller 不校验具体平台，也不生成外部调用参数。`codex`、`claude`、`gemini`、`human:alice`、`openai:gpt-5.4` 都只是路由标签。coordinator 负责和用户沟通、选择实际调用工具、模型、参数、认证方式和上下文传递方式。

coordinator 不需要在每次启动时主动询问 agent 选择。当用户主动表达 agent 偏好，或请求本身已经包含 agent 偏好时，coordinator 必须把偏好写入 checklist：

```bash
python3 strict-review-development-mode/controller.py set-routing --checklist .strict-review/<task-slug>/checklist.md --planning-agent codex --implementation-agent claude --review-agent gemini --json
python3 strict-review-development-mode/controller.py set-routing --checklist .strict-review/<task-slug>/checklist.md --item item-2 --implementation-agent gemini --json
```

全局 `set-routing` 可写入 `coordinator_agent`、`default_agent`、`fallback_agent`、`planning_agent`、`implementation_agent`、`rework_agent`、`review_agent`。带 `--item` 时只允许写入 `planning_agent`、`implementation_agent`、`rework_agent`、`review_agent`，避免把全局偏好误绑定到单个事项。

## 状态语义

- `blocked`：依赖未完成，或当前 cycle 不能安全推进。
- `ready`：依赖满足，等待计划或实施。
- `active`：正在实施。
- `implemented`：实施和验证已记录，等待审核调度。
- `review-queued`：等待分配 reviewer。
- `in-review`：reviewer 正在审核。
- `changes-requested`：reviewer 要求修改。
- `done`：审核通过且 reviewer 已关闭。

## 调度规则

每个 cycle 必须运行：

```bash
python3 strict-review-development-mode/controller.py cycle --checklist .strict-review/<task-slug>/checklist.md --json
```

调度优先级固定：

1. `changes-requested` -> `rework` / `replan`
2. `implemented` 或 `review-queued` -> `review`
3. `ready` -> `planning` 或 `implementation`

如果 `任务启动总规划` 的 `启动判定` 为 `needs-clarification`，controller 不得派发 `implementation` 或 `rework` packet，也不得允许 `start` 进入实施；只能继续补规划、澄清问题或处理已经进入 review 的事项。缺少该章节的历史 checklist 先报 warning 并保持兼容。

coordinator 自行拆出的 phase、阶段、wave、批次只用于内部调度，不能成为停止条件。所有属于用户原始请求范围的事项必须进入 checklist；未纳入事项必须记录在 `完成契约`，且不能在 `启动判定：ready` 时继续推进。

子 agent 数量不设固定并发上限。controller 仍会用 DAG 依赖和 `shared_surfaces` 冲突控制实施安全边界，reviewer 仍必须保持独立且不能自审。

planning 和 review packet 可并行时优先并行。共享面相同的 ready item 可以同时派发 planning packet，因为计划写作不进入实施状态；implementation / rework packet 仍受 DAG 依赖和 `shared_surfaces` 冲突约束，避免并行写同一共享面。

packet 包含路由字段：

- `role`
- `target_agent`
- `fallback_agent`
- `routing_source`
- `invocation_policy`

`invocation_policy` 固定为 `coordinator-decides`，表示 controller 只给出路由意图，不规定外部 agent 调用参数。

packet 还必须包含本 item 的敏捷 ticket 合同：

- `workflow_goal`：当前大任务目的
- `agent_objective`：本 agent 在本 packet 中唯一需要完成的目标
- `local_scope`：本 item 局部范围、依赖和 shared_surfaces
- `success_criteria`：完成标准
- `non_goals`：不需要处理的事项，包括其他 item 和全局调度
- `handoff_requirements`：完成后要交付或写回的内容
- `input_artifacts`：本角色需要看的最小上下文
- `output_artifacts`：本角色要写入的 `.strict-review/<task-slug>/` 专属任务目录文档路径
- `commands`：领取、写回、审核通过或要求修改时可用的 controller 命令

coordinator 派发工作时，应把这些字段连同 `prompt` 一起给目标 agent。目标 agent 像领取敏捷 ticket 一样执行：理解大任务目的，但只完成自己的局部目标，并且只把强审流程文档写入 `output_artifacts` 指向的 `.strict-review/<task-slug>/` 路径。

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
2. 开始实施：用 `controller.py start`。controller 会检查计划、依赖和共享面冲突。
3. 完成实施：用 `controller.py mark-implemented` 写入实施记录和验证记录。
4. 进入审核：用 `controller.py queue-review` 和 `controller.py assign-reviewer`。
5. reviewer 达到硬超时且需要替换：用 `controller.py replace-reviewer`，保持 item 在 `in-review`，但替换独立 reviewer 并记录原因。
6. 审核不通过：用 `controller.py request-changes`。
7. 审核通过：用 `controller.py approve`，controller 会进入 `done` 并勾选 checklist。

## Reviewer 生命周期

- 每个 item 使用独立 reviewer。
- 同一 item 的复审优先复用原 reviewer。
- reviewer 不得跨 item 复用。
- item 进入 `done` 前，相关 reviewer 必须关闭并记录。
- 单次等待超时只是软超时；达到硬超时门槛后才允许 replacement reviewer。
- replacement reviewer 必须通过 `controller.py replace-reviewer` 写入；不能手改 `reviewer_id`、`reviewer_state` 或审核记录。
- 初次 reviewer 和 replacement reviewer 都不能是该 item 的 implementation agent。
- 替换时 item 保持 `in-review`，新 reviewer 的 `reviewer_state` 为 `reviewing`，审核记录中必须保留原 reviewer 已 `replaced` 的审计线索。

替换 reviewer：

```bash
python3 strict-review-development-mode/controller.py replace-reviewer --checklist .strict-review/<task-slug>/checklist.md --item item-1 --from-reviewer <old-reviewer> --to-reviewer <new-reviewer> --reason-file .strict-review/<task-slug>/item-1-reviewer-replacement.md
```

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
- `交付总结` 已补齐且 `完成状态` 为 `complete`
- `用户请求内未交付内容` 为 `无`
