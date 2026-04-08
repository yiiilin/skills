# Viewer Idle Timeout Design

## 背景

当前 `strict-review-development-mode/viewer/serve.py` 已经支持本地启动一个只读 web progress viewer，用于查看 strict-review checklist 的 DAG、派生队列、item 详情和 reviewer 状态。

但 viewer 目前仍是显式启动、显式关闭模型：只要进程被启动，它就会一直存活，除非外部中断或用户手动结束。对于“只在任务运行期间按需打开”的定位来说，这还不够收敛。

用户希望保留现有启动命令，同时让 viewer 在**无人使用一段时间后自动退出**，从而避免它演变成常驻服务。

---

## 目标

为 viewer 增加默认空闲超时机制，满足以下要求：

1. 保留现有启动命令形态，不要求用户改用新的 wrapper 或 supervisor。
2. 默认超时时间为 **30 分钟**。
3. 只要页面仍在请求 viewer，就持续存活。
4. 连续 30 分钟没有页面请求时，viewer 自动优雅退出。
5. 该机制只影响 viewer 进程生命周期，不影响 checklist、DAG、reviewer、任务状态等调度真相。
6. 仍然保持 viewer 的定位是“按需打开的临时只读界面”，而不是常驻后台服务。

---

## 非目标

本次设计不包含以下内容：

- 不把 viewer 生命周期和 strict-review 任务状态强绑定
- 不因为 checklist 中 item 状态变化而自动关闭 viewer
- 不新增外部守护进程、systemd、supervisor、tmux 依赖
- 不为 auto-exit 引入数据库或持久化心跳存储
- 不改变 `/health`、`/snapshot`、静态资源的语义
- 不新增复杂配置系统；先做默认行为即可

---

## 方案比较

### 方案 A：请求时间戳 + 惰性退出

- 记录最近一次活动时间
- 只有下一次请求进来时才检查是否超时
- 若已超时，则在该次请求中返回失败或关闭

优点：实现最简单。  
缺点：如果再也没有请求，进程会一直挂着，不能真正自动退出。

### 方案 B：请求时间戳 + 后台 watchdog 线程（推荐）

- server 启动时记录 `last_activity_at`
- 每次页面相关请求到来时刷新时间戳
- 同时启动一个轻量 watchdog 线程，周期性检查空闲时长
- 若连续 30 分钟没有有效请求，则主动关闭 server

优点：
- 真正自动退出
- 生命周期逻辑集中在 `serve.py`
- 不需要改现有启动命令
- 易于测试

缺点：比惰性退出多一个后台线程，但复杂度仍然很低。

### 方案 C：外部进程或 wrapper 控制超时

- 用 shell wrapper、cron、supervisor 控制超时
- server 本身不感知空闲时间

优点：server 代码改动少。  
缺点：行为分散到外部，测试困难，也不符合当前仓库结构。

---

## 推荐方案

采用 **方案 B：请求时间戳 + 后台 watchdog 线程**。

原因：

- 用户的真实诉求是“viewer 只在任务运行期间按需打开，不应常驻”
- 只有 watchdog 方案才能在无人访问时真正自动退出
- 该方案可以在不改变现有启动命令的前提下实现目标
- 生命周期逻辑仍留在 `serve.py` 内，便于测试和维护

---

## 活跃定义

默认把以下请求视为“仍在使用 viewer”，需要刷新 `last_activity_at`：

- `/`
- `/snapshot`
- `/static/*`
- 当前实现里作为静态资源别名暴露的根级资源路径（例如 `/app.js`、`/styles.css`）

默认把以下请求视为**不续命**：

- `/health`

这样做的意义：

- 正常打开页面、轮询刷新、拉取静态资源都算真实使用
- 外部健康检查不应该把 viewer 误续命成常驻服务

---

## 运行时行为

### 启动

viewer 启动后：

1. 初始化 `last_activity_at = now`
2. 初始化默认空闲超时常量为 30 分钟
3. 初始化 watchdog 检查周期常量（例如 10 秒）
4. 启动 HTTP server
5. 启动 watchdog 线程
6. watchdog 以固定短周期检查空闲时长

为了保证可测试性，空闲超时值与检查周期必须通过**非 CLI 内部参数或可覆写构造参数**注入到 server 实例中，测试可以传入极短值（例如几百毫秒到几秒），生产默认值仍为 30 分钟和短周期检查。换句话说：

- 不要求暴露新的用户 CLI 参数
- 但实现上必须提供测试专用注入点
- 不能依赖长时间 `sleep` 或 monkeypatch 实时时钟才能测试

### 请求处理

当请求路径属于以下集合时：

- `/`
- `/snapshot`
- `/static/*`

在正式处理请求前，刷新：

- `last_activity_at = now`

当请求是 `/health` 时：

- 不刷新活跃时间

### 空闲退出

当 watchdog 发现：

- `now - last_activity_at >= 30 minutes`

则：

1. 记录一条简短日志（可选）
2. 发出一次性 shutdown 信号
3. 由主 server 正常从 `serve_forever()` 返回并进入已有的 `server_close()` 收尾路径
4. 进程自然退出

这里的 canonical 顺序必须是：

- watchdog **只负责触发 shutdown 一次**
- `main()` / 主启动路径负责正常 unwind 和 `server_close()`
- 不允许 watchdog 和主线程重复竞争 `server_close()` 的所有权
- 若 watchdog 检查时恰好有请求先刷新了 `last_activity_at`，则该次请求获胜，不应被误关停

推荐日志文案：

```text
Viewer idle for 30m, shutting down.
```

这条日志是可选的，用于本地调试；不是用户协议的一部分。

---

## CLI 与配置边界

本次设计要求**保留现有启动命令**：

```bash
python3 strict-review-development-mode/viewer/serve.py --checklist <checklist-path> --port 0
```

默认行为变为：

- 默认空闲超时 30 分钟
- 默认启用 auto-exit

本次不要求新增用户可调参数（例如 `--idle-timeout-minutes` 或 `--disable-idle-timeout`）。如果后续确有需要，再单独设计。

也就是说，这次改动是：

- **改变默认运行时行为**
- **不改变用户的启动命令形态**

---

## 组件设计

### 1. 活跃时间状态

在 server 实例上新增：

- `last_activity_at`
- 保护该字段的轻量锁（若当前 server 已有锁，可复用）

用途：

- 请求线程写入最新活跃时间
- watchdog 线程读取空闲时长

### 2. 活跃路径判定函数

新增一个小型辅助函数，例如：

- 判断某个请求路径是否应刷新活跃时间

要求：

- `/`、`/snapshot`、`/static/*` 返回 true
- `/health` 返回 false
- 其他路径默认 false

### 3. watchdog 线程

新增一个内部 watchdog 线程：

- daemon 线程即可
- 固定短周期检查（例如 5-15 秒）
- 发现超时后调用 server 关闭

要求：

- 不 busy loop
- 不因一次异常导致主服务崩溃
- 关闭时不产生重复 shutdown 问题

### 4. 优雅关闭

watchdog 触发后应使用已有 server 关闭路径，不要用粗暴退出方式：

- 优先调用 `shutdown()` / `server_close()` 对应的安全关闭机制
- 避免留下端口占用或半关闭状态

---

## 测试设计

至少覆盖以下验证。

### 1. 活跃路径刷新测试

验证：

- `/snapshot` 请求会刷新活跃时间
- `/health` 请求不会刷新活跃时间

### 2. watchdog 超时关闭测试

使用短超时（测试专用）验证：

- 在没有有效请求时，server 会自动退出
- `serve_forever()` 不会无限挂起
- 允许的关闭时机窗口应为：`idle_timeout <= actual_shutdown_time <= idle_timeout + watchdog_interval + 小量调度抖动`

### 3. 周期续命测试

验证：

- 在 watcher 超时前持续请求 `/snapshot`
- server 不会被误关停

### 4. 静态资源续命测试

验证：

- `/static/app.js` 或 `/` 请求也能刷新活跃时间

### 5. `/health` 不续命测试

验证：

- 即使持续请求 `/health`
- 只要没有页面请求，server 仍会在超时后退出

6. CLI 默认行为测试

验证：

- 不增加额外参数时，默认空闲超时逻辑存在
- 启动命令形态不变
- 根级静态资源别名路径（如 `/app.js`、`/styles.css`）与 `/static/*` 一样会续命

---

## 风险与边界

### 风险 1：watchdog 和请求线程竞争

处理方式：

- 用轻量锁保护活跃时间访问
- watchdog 只读，页面请求只写，保持最小共享状态

### 风险 2：关闭时机与活跃请求重叠

处理方式：

- 只要最近活动时间已刷新，就不关闭
- watchdog 触发时走已有优雅关闭路径
- 不追求“绝对零 race”，只保证行为稳定、可接受

### 风险 3：健康检查误续命

处理方式：

- 明确规定 `/health` 不算活跃请求
- 用测试锁住这个规则

### 风险 4：未来用户想自定义超时

处理方式：

- 本次先不做参数化
- 若未来出现明确需求，再单独扩展 CLI

---

## 成功标准

完成后应满足：

1. 现有启动命令不变
2. viewer 默认空闲 30 分钟后自动退出
3. `/`、`/snapshot`、`/static/*` 会续命
4. `/health` 不会续命
5. 持续查看页面时 viewer 不会被误关停
6. 无人访问时 viewer 不再常驻
7. 若启动后从未被页面访问，也会在 30 分钟空闲窗口后自动退出
8. 新增测试能覆盖超时与续命行为

---

## 结论

viewer 应被进一步收敛为：

- **任务运行期间按需打开**
- **页面请求续命**
- **连续 30 分钟无页面请求自动退出**

这保持了 viewer 的正确定位：

- 它是临时只读观察窗
- 不是常驻服务
- 不是控制台
- 不是新的 source of truth
