# 强审模式 Web Progress Viewer 设计

## 背景

当前 `strict-review-development-mode` 已经把执行状态收敛到 checklist 文档中的结构化字段、Mermaid DAG、队列区块和 item 级记录中。agent 可以依靠这些信息做 DAG 驱动调度、reviewer 生命周期管理和恢复执行。

问题在于：

- 进度信息目前只存在于 markdown 文档和终端输出里
- 用户想查看整体进度时，需要在长文档里手动滚动
- 虽然已有 Mermaid DAG 和结构化字段，但没有专门的浏览器视图来同时呈现 DAG、队列、item 详情和 reviewer 状态

本设计的目标，是为强审模式补一个**本地、只读、按需打开**的 web progress viewer，用于查看执行进度，但不改变现有调度真相来源。

---

## 目标

新增一个本地 web progress viewer，满足以下要求：

1. 以 checklist 文档为唯一输入源，渲染强审模式进度
2. 页面可查看：
   - DAG / 依赖关系
   - `blocked / ready / active / implemented / review-queued / in-review / changes-requested / done` 计数
   - ready / active / review-queued / in-review / done 队列
   - reviewer 生命周期与超时相关状态
   - item 详情（计划、实施记录、验证记录、审核记录）
3. viewer 只在用户明确要求时打开，而不是每次自动弹出
4. viewer 必须保持只读，不允许在页面中直接修改 `dispatch_status`、reviewer 分配或其他调度字段
5. 页面刷新后能反映 checklist 文档最新状态
6. 实现优先采用零依赖方案，不引入新的前端构建链路或外部服务

---

## 非目标

本次设计不包含以下内容：

- 不把 viewer 做成远程共享 dashboard
- 不在页面中提供调度控制、状态编辑、人工改 reviewer、人工改 DAG 的能力
- 不引入第二份状态存储或数据库
- 不把 viewer 抽象成通用 markdown 可视化平台，仅支持强审 checklist 约定结构
- 不要求自动随强审模式启动；仅在用户要求时启动
- 不依赖 Mermaid 前端运行时或额外 npm/pip 包

---

## 设计概览

### 核心原则

1. **checklist 是唯一真相来源**：viewer 只是派生视图。
2. **只读优先**：页面只能查看，不能修改调度状态。
3. **按需打开**：只有用户要求“打开界面 / 看进度”时才启动 viewer。
4. **零依赖实现**：优先使用标准库和原生 HTML/CSS/JS。
5. **结构化字段优先于 Mermaid**：如果 Mermaid 图和结构化字段冲突，viewer 必须提示告警，并以结构化字段推导的结果为准。

### 用户流程

1. agent 继续按照强审模式维护 checklist 文档。
2. 当用户说“打开界面”或“查看进度”时，agent 启动本地 viewer。
3. viewer 返回一个本地 URL 给用户打开。
4. 页面读取 checklist 快照并渲染总览、DAG、队列和 item 详情。
5. checklist 文档更新后，用户可以手动刷新，或由页面自动轮询刷新。

---

## 信息架构

viewer 页面分为 4 个主要区域。

### 1. 顶部总览区

显示：

- checklist 路径
- 最近刷新时间
- item 总数
- 每个 `dispatch_status` 的计数
- 当前 implementation 并发占用 / reviewer 并发占用
- 是否检测到文档告警（如 Mermaid 不一致、缺字段、重复 `item_id`）

这个区域负责让用户快速判断系统整体节奏和瓶颈位置。

### 2. DAG 视图区

显示：

- 基于结构化字段推导的依赖图
- 节点按 `dispatch_status` 着色
- 节点点击后联动 item 详情区
- Mermaid 原文摘要或折叠面板（用于对照）
- Mermaid/结构化字段不一致时的显式告警

由于本次要求零依赖，不引入 Mermaid 前端运行时。viewer 应以结构化字段生成一个原生 SVG 或简化图形布局；Mermaid 文本作为辅助展示，而不是渲染依赖。

### 3. 队列与 reviewer 面板

按列显示：

- Ready 队列（`dispatch_status = ready`）
- Active 实现队列（`dispatch_status = active`）
- Active reviewer 队列（`dispatch_status = in-review`）
- Review Queue（`dispatch_status = review-queued`）
- Changes Requested（`dispatch_status = changes-requested`）
- Implemented 摘要（`dispatch_status = implemented`；默认不单独做主列板，只在总览计数和 item 详情中突出显示，也可在队列区作为次级列表展示）
- Done 摘要（`dispatch_status = done`）

viewer 里的所有队列都必须由 `dispatch_status` 派生，而不是直接信任文档顶部的队列区块；如果文档顶部队列区块存在且与派生结果不一致，只显示告警，不把它作为真相来源。

对 reviewer 相关 item，还应展示：

- reviewer id
- reviewer state
- 当前审核轮次
- 累计等待时长
- soft timeout / hard timeout 状态
- replacement reviewer 是否已安排

这个区域负责让用户快速看清当前主调度压力来自实现侧还是审核侧。

### 4. Item 详情抽屉

点击 item 后显示：

- `item_id`
- `blocked_by`
- `blocks`
- `shared_surfaces`
- `parallel_group`
- `dispatch_status`
- `assigned_subagent`
- `reviewer_id`
- `reviewer_state`
- `next_action`
- 计划
- 实施记录
- 验证记录
- 审核记录

这个区域负责承接“为什么这个 item 现在是这个状态”的问题，避免用户回到 markdown 里手工搜索。

---

## 实现方案

### 目录布局

建议在 skill 目录下新增：

```text
strict-review-development-mode/
  viewer/
    serve.py
    parser.py
    renderer.py
    static/
      index.html
      app.js
      styles.css
```

也可在实现时进一步压缩文件数，但职责应保持分离：解析、快照构建、页面渲染分别独立。

### 组件划分

#### 1. checklist 解析层

职责：

- 读取 checklist markdown
- 定位顶部全局区块（模式、审核设置、当前执行状态、队列）
- 定位每个 `## Item N - ...` 区块
- 提取结构化字段、计划、实施记录、验证记录、审核记录
- 提取 Mermaid DAG 原文

解析策略：

- 只支持当前 `strict-review-development-mode/checklist-template.md` 约定的标题结构
- 采用基于标题和 bullet 前缀的轻量解析，不做完整 markdown AST
- 必须把以下 section 视为固定契约：`## 模式`、`## 审核设置`、`## 当前执行状态`、`## Checklist`、`## DAG 概览`、`## Mermaid DAG`、`## Ready 队列`、`## Active 实现队列`、`## Active reviewer 队列`、`## Review Queue`，以及每个 item 下的 `### 结构化字段` / `### 计划` / `### 实施记录` / `### 验证记录` / `### 审核记录`
- `### 结构化字段` 中只把显式 bullet 字段视为权威值；多行 bullet 内容应继续并入前一字段的值，直到遇到下一个同级 bullet 或下一个 heading
- `### 计划`、`### 实施记录`、`### 验证记录`、`### 审核记录` 中的自由文本和代码块按原样保留为 section 内容，不在 viewer 侧做进一步语义拆解
- reviewer 超时/replacement 展示只读取已命名字段（如 `Reviewer 状态`、`累计等待时长`、`超时次数`、`Replacement Reviewer`）；若文档未提供，则页面显示缺失而不是猜测
- 对缺失字段、重复字段、重复 `item_id`、未知 `dispatch_status` 输出告警

输出应为标准化内存对象，而不是直接把 markdown 文本塞给前端。

#### 2. snapshot 构建层

职责：

- 由解析结果生成页面需要的统一 JSON 快照
- 计算每个状态计数
- 按 `dispatch_status` 派生各类队列
- 根据 `blocked_by` / `blocks` 构建 DAG 边集合
- 检查 Mermaid 与结构化字段的一致性
- 计算 implementation 并发占用和 reviewer 并发占用
- 生成页面告警列表

一致性与合法性校验至少包括：

- `blocked_by` 指向不存在的 `item_id`
- `blocks` 指向不存在的 `item_id`
- `blocked_by` / `blocks` 互相不对称
- item 依赖图存在 cycle
- 存在无法分配层级的节点

若发现非法 DAG：

- 页面继续展示可解析出的 item 列表和状态计数
- DAG 区进入 degraded 模式，至少展示节点和已知边，并显式标记“依赖图无效”
- 告警列表中必须写明问题节点或问题边
- 不得因为单个 DAG 错误让整个页面空白

Mermaid 一致性检查规则需要收敛到稳定标识：

- 若模板中的 Mermaid 节点标签未显式包含 `item_id`，viewer 只提示“无法做精确一致性校验”，不做严格逐边比对
- 若后续模板升级为在 Mermaid 节点中显式携带 `item_id`，再执行严格的一致性校验

输出建议形态：

```json
{
  "meta": {...},
  "counts": {...},
  "queues": {...},
  "dag": {"nodes": [...], "edges": [...]},
  "items": [...],
  "warnings": [...]
}
```

这个 snapshot 是 viewer 的唯一页面输入；页面不要自行二次推导业务逻辑。

#### 3. 本地 viewer 服务层

职责：

- 启动本地 HTTP 服务
- 暴露 HTML 页面
- 暴露 snapshot 接口
- 支持手动刷新和轻量自动轮询

建议接口：

- `GET /`：返回 viewer 页面
- `GET /snapshot`：返回当前 checklist 快照 JSON；若本轮解析失败但存在上一版有效快照，则返回上一版数据并带 `stale: true` 与错误说明
- `GET /health`：健康检查

技术约束：

- 使用 Python 标准库（如 `http.server`）实现
- 默认仅绑定 loopback 地址（如 `127.0.0.1`）
- 启动时要求传入 checklist 文件路径
- 如果文件不存在或解析失败，页面显示明确错误信息
- 若用户在同一 checklist 上重复请求“打开界面”，优先复用已有 viewer 进程和端口；仅在原进程不存在时重新启动
- 若默认端口被占用，应自动回退到可用随机本地端口，并把最终 URL 返回给用户

#### 4. 前端展示层

职责：

- 展示总览卡片
- 展示 DAG 图
- 展示队列列板
- 展示 item 详情抽屉
- 支持轮询刷新
- 保留查看上下文（例如当前选中的 item）

技术约束：

- 使用原生 HTML/CSS/JS
- 不引入构建工具
- 不引入外部 CDN 资源
- 不提供任何写操作按钮

---

## 启动与接入规则

### 启动方式

viewer 默认不自动启动。

强审模式运行过程中，只有当用户明确表达以下意图时，agent 才应启动 viewer：

- 打开界面
- 看进度
- 打开 web 界面
- 查看当前 DAG / 队列 / 状态

启动后，agent 应返回：

- viewer URL
- 当前绑定的 checklist 路径
- 若存在解析告警，则一并提示

### 与强审模式的关系

需要在 `strict-review-development-mode/SKILL.md` 中增加一段规则，明确：

- web viewer 是可选的派生视图，不参与调度决策
- 若 viewer 和 checklist 文档展示不一致，以 checklist 文档为准
- viewer 只在用户要求时启动
- viewer 不得替代终端中必须输出的关键调度摘要

### 与 checklist 模板的关系

viewer 优先兼容当前模板结构，不为了 viewer 引入额外复杂字段。

只在以下情况下调整模板：

- 当前标题或字段命名不够稳定，导致解析容易歧义
- 某些 reviewer 字段缺失，无法支撑详情展示
- 某些状态只能在自然语言文本里出现，无法稳定提取

优先原则是**尽量小改模板，尽量多做派生解析**。

---

## DAG 展示策略

由于本次采用零依赖方案，DAG 不使用 Mermaid 前端渲染。

viewer 应采用以下策略：

1. 以 `blocked_by` / `blocks` 作为 DAG 真正输入
2. 计算节点层级或波次
3. 用原生 SVG 或简化布局渲染依赖图
4. 节点颜色映射到 `dispatch_status`
5. 保留 Mermaid 原文作为折叠参考
6. 若 Mermaid 与结构化字段不一致，在页面顶部和 DAG 区同时提示告警

这样可以保证：

- 页面不依赖额外库
- DAG 与真实调度状态保持一致
- Mermaid 仍保留给人类核对和复制使用

---

## 错误处理

viewer 至少需要覆盖以下错误场景：

1. **checklist 文件不存在**
   - 服务可启动，但页面显示“文件不存在”错误

2. **checklist 结构不完整**
   - 页面显示告警
   - 尽可能展示能解析出的部分
   - 不因单个 item 缺字段导致全页崩溃

3. **`item_id` 重复或缺失**
   - 标记为严重告警
   - DAG 和详情页仍尽量展示，但注明结果可能不可靠

4. **未知 `dispatch_status`**
   - 归入 warning
   - 页面上按“未知状态”显示，而不是静默丢弃

5. **Mermaid 与结构化字段不一致**
   - 以结构化字段派生 DAG 为准
   - 页面醒目标注“建议修正文档”

6. **轮询过程中 checklist 内容变化**
   - 重新拉取 snapshot 后局部刷新页面
   - 若当前选中 item 仍存在，则保持选中
   - 若已不存在，则回到未选中状态

7. **轮询恰逢文件写入中间态**
   - 若本轮读取失败或只解析出明显不完整快照，viewer 保留上一版成功快照继续展示
   - 页面标记“最近一次刷新失败，当前显示为上一版有效快照”
   - 下一轮轮询继续重试，而不是清空页面

---

## 测试与验证

### 解析层验证

至少覆盖：

- 能正确解析当前模板示例
- 能提取所有 item 的结构化字段
- 能正确派生状态计数和队列
- 能识别 Mermaid 缺失或不一致
- 能识别重复 `item_id`

### viewer 层验证

至少覆盖：

- 服务能在本地启动并返回 URL
- 浏览器打开后能看到总览、DAG、队列、详情抽屉
- 修改 checklist 后，刷新或自动轮询可看到更新
- 对损坏文档能显示错误与告警，而不是空白页

### 强审流程接入验证

至少覆盖：

- 不请求查看界面时，强审模式不受影响
- 请求“打开界面”后，agent 能正确启动 viewer
- viewer 打开后仍保持 checklist 为唯一调度真相

---

## 成功标准

完成后，应满足以下验收条件：

1. 使用当前强审 checklist 模板创建的文档，可以被 viewer 正确解析
2. 用户要求“打开界面”时，agent 可以启动本地 viewer 并返回 URL
3. 页面能展示：
   - DAG
   - 各状态计数
   - ready / active / review-queued / in-review / done 等队列
   - reviewer 状态
   - item 详情
4. 页面全程只读，不能改 `dispatch_status`
5. checklist 更新后，刷新页面能看到最新状态
6. viewer 与 checklist 冲突时，页面明确提示以 checklist 为准

---

## 取舍说明

本设计刻意没有选择“可操作控制台”方案。

原因是：

- 强审模式当前最大价值在于把调度真相收敛到 checklist 文档
- 一旦允许页面直接修改状态，就会引入新的真相入口
- 这会显著提高一致性维护、恢复协议和 reviewer 生命周期管理的复杂度

因此，这次只做**轻交互只读面板**：

- 可以点选 item 查看详情
- 可以刷新
- 可以轮询更新
- 但不能写状态

这与现有 DAG-first 强审设计保持一致，也更适合快速落地最小可用版本。
