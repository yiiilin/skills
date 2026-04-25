# Codex Skills

一个用于存放和开源 Codex skills 的仓库。每个 skill 独立放在自己的目录中，便于单独复制、版本管理和后续持续追加。

## 仓库结构

每个 skill 使用类似下面的结构：

```text
<skill-name>/
  SKILL.md
  optional-supporting-files
```

当前仓库已包含：

- `strict-review-development-mode/`

## 已收录技能

### `strict-review-development-mode`

“强审开发模式”的开源版本。用于把实现任务强制收敛为：

1. 先写 checklist
2. 先规划 item 级 DAG（结构化字段 + Mermaid）
3. 依赖与冲突面明确后才允许实施
4. 每项先写计划，再按计划实施与验证
5. 通过 `controller.py` 固定化状态机，拒绝非法 `dispatch_status` 迁移
6. 只要存在互不冲突的 ready 节点，就必须按 `controller.py cycle --json` 的 `dispatch_packets` 派发工作包
7. 实现完成后按 reviewer 上限进入审核或 `review-queued`
8. 全部 item 审核通过并关闭 reviewer 后才允许结束
9. 如用户明确要求打开界面，可启动本地只读 web progress viewer 查看 DAG、队列和 item 详情；checklist 文档仍是唯一 source of truth

这个可选本地 progress viewer 只在任务执行期间使用，并会在 30 分钟内没有 page requests（no page requests）时自动退出。

`controller.py` 是通用 CLI，不绑定具体 agent 平台。它提供 B 方案能力（validator + state machine CLI），并通过 `dispatch_packets` 预留 C 方案能力（外部 orchestrator 可以消费 packet 后派发 subagent/reviewer，再用 controller 命令写回结果）。

`dispatch_packets` 只表达路由意图，不写死外部调用参数。用户只需要和 coordinator 沟通；coordinator 可按 `target_agent` 把 planning、implementation、rework、review 分别交给 Codex、Claude、Gemini、人工 reviewer 或其他 agent，并自行决定模型、参数和上下文传递方式。

目录内容：

- `strict-review-development-mode/SKILL.md`
- `strict-review-development-mode/checklist-template.md`
- `strict-review-development-mode/controller.py`（协议校验、状态机迁移、cycle 调度包输出）
- `strict-review-development-mode/references/protocol.md`（完整协议参考）
- `strict-review-development-mode/references/workflow-state-machine.md`（工作流状态转换图）
- `strict-review-development-mode/viewer/`（可选本地只读 progress viewer，用于查看 DAG / queue / item 详情，不作为调度真相）

查看状态转换图：

```bash
python3 strict-review-development-mode/controller.py diagram
```

## 适用前提

这些 skills 适用于支持以下能力的 agent 环境：

- 支持 `SKILL.md` 发现与加载
- 支持按目录组织多个 skill
- 部分 skill 可能依赖 subagent、reviewer 或指定模型能力

具体前提请以各 skill 目录中的 `SKILL.md` 为准。

## 安装

安装单个 skill：

```bash
mkdir -p "$CODEX_HOME/skills"
cp -R strict-review-development-mode "$CODEX_HOME/skills/"
```

安装整个仓库中的全部 skills：

```bash
mkdir -p "$CODEX_HOME/skills"
cp -R ./* "$CODEX_HOME/skills/"
```

如果你的 agent 使用的是项目内 `skills/` 目录，也可以直接 vendoring 需要的子目录。

## 后续扩展

如果继续追加 skill，建议保持以下约定：

- 一个 skill 一个目录
- 主文件统一命名为 `SKILL.md`
- 配套模板、脚本或参考文件放在对应 skill 目录内
- 仓库首页只做总览，具体用法写在各 skill 自己的文档里

## License

MIT
