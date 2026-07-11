# Codex Skills

一个用于存放和开源 Codex skills 的仓库。每个 skill 独立放在自己的目录中，便于复制、版本管理和持续演进。

## 仓库结构

每个 skill 使用类似下面的结构：

```text
<skill-name>/
  SKILL.md
```

当前仓库已包含：

- `strict-review-workflow/`

## 已收录技能

### `strict-review-workflow`

纯文字的强审开发工作流。它把协调、实现和独立审查分成不同角色，并通过以下规则控制质量和收敛：

1. 实现前冻结范围、验收标准、自治边界、验证方式和停止条件。
2. 高风险无人值守任务先进行独立计划审查。
3. 实现 Agent 完成改动和新鲜验证后，由未参与实现的 Agent 做只读审查。
4. 只有证据充分的阻塞项触发返工；建议和剩余风险单独披露。
5. 复审保持固定范围和稳定 Reviewer，并设置硬返工预算。
6. 只有验收通过且没有有效阻塞项时才能 `complete`；超出自治边界或预算耗尽时进入 `blocked`。

skill 本身只有 `strict-review-workflow/SKILL.md`，不依赖 controller、脚本、数据库、viewer、引用文件或状态文件。纯文字规则无法技术性强制身份独立、并发锁或原子状态转换，具体保证边界以 `SKILL.md` 为准。

## 适用前提

这些 skills 适用于支持以下能力的 agent 环境：

- 支持 `SKILL.md` 发现与加载
- 支持按目录组织多个 skill
- `strict-review-workflow` 的独立实现与审查流程需要运行时支持子 Agent；能力不足时按 skill 规则降级或报告 `blocked`

具体前提请以各 skill 目录中的 `SKILL.md` 为准。

## 安装

安装单个 skill：

```bash
mkdir -p "$CODEX_HOME/skills"
cp -R strict-review-workflow "$CODEX_HOME/skills/"
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
- 只添加完成工作流确实需要的配套文件
- 仓库首页只做总览，具体用法写在各 skill 自己的文档里

## License

MIT
