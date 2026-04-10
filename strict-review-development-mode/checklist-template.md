# <任务标题>

## 模式
- 强审开发模式（DAG-first）

## 审核设置
- 审核模型目标：gpt-5.4
- 推理强度目标：xhigh
- 实施并行上限：4
- reviewer 并行上限：2
- 首次等待窗口：5min
- 二次探测窗口：5-10min
- 硬超时门槛：15min

## 任务归属判定
- 当前请求：<待填写>
- 判定结果：different-task
- 判定依据：默认模板用于当前请求的新 checklist；若确认同任务续做，应改为 same-task 并继续既有 checklist
- 关联旧 checklist：none
- 处理动作：在当前 checklist 记录本次任务进度；不要向无关旧 checklist 追加工作项

## 当前执行状态
- 当前状态：进行中
- 当前阻塞原因：无
- 当前调度摘要：初始可达事项为 item-1、item-2、item-4；item-3 仍受依赖阻塞
- 当前可执行动作摘要：先补齐可达事项计划，再按 DAG 与 shared_surfaces 补满实施槽位

## 任务重整摘要
- 原始任务形态：已拆为可并行工作包
- 是否触发任务重整：否
- 重整触发原因：无；原始任务已具备稳定工作包边界
- 工作包映射：item-1、item-2、item-3、item-4
- 并行批次说明：Wave 1 = item-1 + item-2 + item-4；Wave 2 = item-3
- 关键不可并行约束：item-3 依赖 item-1 与 item-2 完成后才能进入 ready

## Checklist
- [ ] 1. <事项一>
- [ ] 2. <事项二>
- [ ] 3. <事项三>
- [ ] 4. <事项四>

## DAG 概览
- 关键串行路径：Item 1 / Item 2 -> Item 3
- 依赖分层摘要：Wave 1 为可并行实施项，Wave 2 为依赖 Wave 1 的集成项
- 可并行批次：Wave 1 = Item 1 + Item 2 + Item 4；Wave 2 = Item 3

## Mermaid DAG
```mermaid
graph TD
  A[Item 1 - <事项一>] --> C[Item 3 - <事项三>]
  B[Item 2 - <事项二>] --> C[Item 3 - <事项三>]
  D[Item 4 - <事项四>]
```

## Ready 队列
- item-1 — <事项一>
- item-2 — <事项二>
- item-4 — <事项四>

## Active 实现队列
- 无

## Active reviewer 队列
- 无

## Review Queue
- 无

## Item 1 - <事项一>
### 结构化字段
- item_id：item-1
- blocked_by：[]
- blocks：[item-3]
- shared_surfaces：[]
- parallel_group：wave-1
- dispatch_status：ready
- assigned_subagent：none
- reviewer_id：none
- reviewer_state：not-started
- 风险等级：待填写
- 当前状态：未开始
- 阻塞原因：无
- next_action：补齐计划后进入实施调度

### 计划
- 待填写

### 实施记录
- 待填写

### 验证记录
- 待填写

### 审核记录
- Reviewer：待填写
- Reviewer 状态：待填写
- 开始时间：待填写
- 累计等待时长：待填写
- 超时次数：待填写
- 审核轮次：待填写
- 审核结论：待填写
- Replacement Reviewer：待填写
- 关闭状态：待填写
- 关闭原因：待填写

## Item 2 - <事项二>
### 结构化字段
- item_id：item-2
- blocked_by：[]
- blocks：[item-3]
- shared_surfaces：[]
- parallel_group：wave-1
- dispatch_status：ready
- assigned_subagent：none
- reviewer_id：none
- reviewer_state：not-started
- 风险等级：待填写
- 当前状态：未开始
- 阻塞原因：无
- next_action：补齐计划后进入实施调度

### 计划
- 待填写

### 实施记录
- 待填写

### 验证记录
- 待填写

### 审核记录
- Reviewer：待填写
- Reviewer 状态：待填写
- 开始时间：待填写
- 累计等待时长：待填写
- 超时次数：待填写
- 审核轮次：待填写
- 审核结论：待填写
- Replacement Reviewer：待填写
- 关闭状态：待填写
- 关闭原因：待填写

## Item 3 - <事项三>
### 结构化字段
- item_id：item-3
- blocked_by：[item-1, item-2]
- blocks：[]
- shared_surfaces：[]
- parallel_group：wave-2
- dispatch_status：blocked
- assigned_subagent：none
- reviewer_id：none
- reviewer_state：not-started
- 风险等级：待填写
- 当前状态：未开始
- 阻塞原因：等待 item-1 与 item-2 进入 done
- next_action：等待上游依赖完成后重算 dispatch_status

### 计划
- 待填写

### 实施记录
- 待填写

### 验证记录
- 待填写

### 审核记录
- Reviewer：待填写
- Reviewer 状态：待填写
- 开始时间：待填写
- 累计等待时长：待填写
- 超时次数：待填写
- 审核轮次：待填写
- 审核结论：待填写
- Replacement Reviewer：待填写
- 关闭状态：待填写
- 关闭原因：待填写

## Item 4 - <事项四>
### 结构化字段
- item_id：item-4
- blocked_by：[]
- blocks：[]
- shared_surfaces：[]
- parallel_group：wave-1
- dispatch_status：ready
- assigned_subagent：none
- reviewer_id：none
- reviewer_state：not-started
- 风险等级：待填写
- 当前状态：未开始
- 阻塞原因：无
- next_action：补齐计划后进入实施调度

### 计划
- 待填写

### 实施记录
- 待填写

### 验证记录
- 待填写

### 审核记录
- Reviewer：待填写
- Reviewer 状态：待填写
- 开始时间：待填写
- 累计等待时长：待填写
- 超时次数：待填写
- 审核轮次：待填写
- 审核结论：待填写
- Replacement Reviewer：待填写
- 关闭状态：待填写
- 关闭原因：待填写
