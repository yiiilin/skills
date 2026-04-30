# 工作流状态转换图

这张图描述 `dispatch_status` 的合法主路径。实际迁移必须由 `controller.py` 命令执行。

```mermaid
stateDiagram-v2
  [*] --> blocked: 有未完成依赖
  [*] --> ready: 无依赖或依赖已 done

  blocked --> ready: cycle --write / 上游 done
  ready --> ready: planning packet / controller plan
  ready --> active: controller start

  active --> implemented: controller mark-implemented
  implemented --> review_queued: controller queue-review
  implemented --> in_review: controller assign-reviewer / 分配 reviewer
  review_queued --> in_review: controller assign-reviewer
  in_review --> in_review: controller replace-reviewer / reviewer 超时

  in_review --> changes_requested: controller request-changes
  changes_requested --> active: controller start / rework
  changes_requested --> implemented: controller mark-implemented / 补验证

  in_review --> done: controller approve
  done --> [*]
```

## 状态含义

- `blocked`：依赖未完成，或当前 cycle 与活跃事项存在不可并行冲突。
- `ready`：依赖已满足；如果计划缺失，下一步是 `planning` packet；如果计划已写明，下一步是 `implementation` packet。
- `active`：事项正在实施，尚未完成实施和验证闭环。
- `implemented`：实施和验证已记录，等待审核调度。
- `review-queued`：等待分配 reviewer。
- `in-review`：独立 reviewer 正在审核。
- `changes-requested`：reviewer 要求修改，下一 cycle 优先处理。
- `done`：最新审核通过，reviewer 已关闭。

## 调度优先级

```mermaid
flowchart TD
  A[cycle] --> B{有 error?}
  B -->|是| C[修复协议错误]
  B -->|否| D{有 status_updates?}
  D -->|是| E[cycle --write]
  D -->|否| F[生成 dispatch_packets]
  F --> G[rework / replan]
  G --> H[review]
  H --> I[planning / implementation]
  I --> J[下一 cycle]
```
