# 设计：同步后静默刷新 + 自动刷新

- 日期：2026-06-21
- 范围：`web/app.js` + `web/index.html` + `web/style.css`
- 目标：同步完成后不再整页 `location.reload()`，改为局部 diff 更新当前视图，不打断阅读；并增加每分钟一次的自动刷新。

## 现状与问题

当前同步流程（`web/app.js:462-500`）：

1. 点"🔄 同步" → POST `/api/sync`，后台子进程跑 `crawl_blog.py --all` 增量抓取
2. 前端每 2 秒轮询 `/api/sync/status`
3. `running=false && exit_code=0` 时执行 **`location.reload()`** 整页刷新

问题：整页刷新丢失当前选中日期、搜索状态、滚动位置，打断阅读体验。且无自动刷新，需手动点同步才能看到新微博。

## 决策

| 项 | 决策 |
| --- | --- |
| 同步后页面更新 | 不再 `location.reload()`，改为局部 diff 更新（方案 A） |
| 新增条目呈现 | prepend 到列表顶部 + 高亮淡出 + "新增 N 条"提示条（手动关闭） |
| 自动刷新 | 默认开启，每 60 秒触发一次同步 |
| 自动刷新开关 | 同步按钮旁独立 toggle，可关 |
| 开关语义 | 关闭=立即停定时器；开启=立即同步一次 + 启动定时器 |
| 搜索视图 | 同步后不更新（保持当前搜索结果不变） |
| 页面可见性 | 不检测，60 秒一次开销可接受 |

## 架构：同步与更新解耦

两种触发入口共用同一流程：

- **手动同步**：点"🔄 同步"按钮
- **自动刷新**：60 秒定时器

两者都调 `runSync()` → 轮询等完成 → 成功后调 `silentRefresh()`。失败时只在状态栏提示，不更新页面。

### 触发前检查

自动刷新定时器触发时，先 GET `/api/sync/status`，若 `running=true`（已有同步在跑），跳过本次，避免并发同步。

## 静默更新流程（`silentRefresh`）

同步 `exit_code=0` 后执行：

1. 记录当前视图状态：`currentDay`、当前 `postList` 内卡片 mblogid 集合（`Set`）、`postList.scrollTop`
2. 并行拉取三部分：当前视图帖子、左侧日期树（`/api/months`）、博主列表（`/api/bloggers`）
3. diff 当前视图帖子与旧 mblogid 集合，找出新增项
4. 局部更新 DOM
5. 恢复 `postList.scrollTop`
6. 有新增则显示提示条

### 各视图更新策略

**① 选中某天视图（最常见）**
- 重新 GET `/api/posts?date=${currentDay}&uid=...`
- diff：新数据里 mblogid 不在旧集合中的 = 新增项
- 新增卡片按数据顺序 prepend 到 `postList` 顶部
- 已有卡片完全不动（不重渲染、不挪位）
- 更新 `dayIndicator` 文本 `${date} 共 N 条`（N 取新数据总数）
- 恢复 `postList.scrollTop`

**② 搜索结果视图**
- 同步后不做任何更新，保持当前搜索结果不变

**③ 日期树（左侧）**
- 重新 GET `/api/months?uid=...`
- 局部更新：已存在月份/日期更新计数文本；新月份/新日期按现有结构插入 DOM；已展开月份保持展开态
- 不重建整棵树（避免丢失展开状态）

**④ 博主列表**
- 重新 GET `/api/bloggers`，更新 `bloggerMap`
- 不重渲染博主选择器 DOM，只更新数据

### 失败处理

同步 `exit_code≠0` 时不静默更新，只在状态栏提示"同步失败"，不影响当前页面。

## 自动刷新机制

**默认开启**：`init` 末尾自动 `startAutoRefresh()`，启动 60 秒定时器。

**定时器行为**：
- 触发时先检查 `/api/sync/status`，`running=true` 则跳过
- 空闲则 POST `/api/sync`，复用 `runSync()` 流程
- 同步按钮显示"🔄 同步中..."（与手动一致）

**开关 UI**：同步按钮旁加独立 toggle，文字"自动刷新"，默认开启。

**开启/关闭语义**：
- 关闭：`clearInterval`，停定时器
- 开启：立即触发一次 `runSync()` + `silentRefresh()`，然后启动 60 秒定时器

**与手动同步的关系**：手动点"🔄 同步"始终可用，与自动刷新独立。两者共用 `runSync()` + `silentRefresh()`。

## 新增条目提示条

**出现条件**：选中某天视图下，同步后 diff 出新增帖子（数量 > 0）时显示。搜索视图/空视图不显示。

**位置与样式**：
- 浮在 `postList` 顶部，贴列表上沿（`position: sticky`）
- 文本 `新增 N 条微博` + 关闭按钮 `✕`
- 橙色系（与查看器主题一致），浅底深字

**交互**：
- 手动关闭：点 `✕` 或点提示条本身，立即移除
- 关闭后不再自动出现，直到下一次同步又有新增时重新出现
- 点击提示条（非关闭按钮区域）：滚动到第一条新增帖子位置并高亮

**与卡片高亮的关系**：新增卡片本身也有高亮淡出（复用 `.post-highlight`，2 秒后移除）。提示条是额外的明确告知层。

**无新增时**：不显示提示条；上一次的提示条还在时不主动移除（用户自己关）。

## 组件改动清单

### web/app.js

1. **抽取 `runSync()`**：现有 `syncBtn` 点击 handler + `pollSync` 核心逻辑合并——POST `/api/sync` → 轮询 → 成功后 `silentRefresh()`。手动按钮和自动刷新都调它。
2. **新增 `silentRefresh()`**：按上述策略执行静默更新。
3. **新增 `refreshDateTree(monthsData)`**：局部更新左侧日期树（保留展开态）。
4. **新增 `refreshBloggerMap(bloggersData)`**：更新 `bloggerMap`。
5. **新增自动刷新控制**：`autoRefreshTimer` + `startAutoRefresh()` / `stopAutoRefresh()` + toggle 事件。`init` 末尾默认启动。
6. **新增提示条操作**：`showNewPostsBanner(count)` / `dismissNewPostsBanner()`。
7. **移除 `pollSync` 里 `location.reload()`**：替换为 `silentRefresh()`。

### web/index.html

- 同步按钮旁加自动刷新 toggle 开关
- `postList` 容器内预置提示条占位元素

### web/style.css

- 自动刷新 toggle 样式
- 提示条样式（sticky 顶部、橙色系、关闭按钮）
- 复用 `.post-highlight`

### server.py

无改动。现有 `/api/sync`、`/api/sync/status`、`/api/posts`、`/api/months`、`/api/bloggers` 接口已够用。

## 测试

- 现有 63 个测试不受影响（不碰 server.py 逻辑）
- 自动刷新/静默更新是纯前端逻辑，项目无前端测试框架，靠手动验收
- 验收点：同步后无整页刷新、滚动位置保留、新增卡片 prepend + 高亮、提示条手动关闭、自动刷新开关开/关、开启立即同步
