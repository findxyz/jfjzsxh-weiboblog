# WeiboBlog 消息查看器（web server）设计

> 日期：2026-06-20
> 状态：待实现
> 关联：与 weibogroup 的 `server.py` / `web/` 同构，但视觉风格与交互刻意区分

## 1. 背景与目标

weiboblog 已能抓取博主微博到本地 SQLite（`weibo_blog.db`）。现需一个本地只读
web 查看器，浏览已抓取的微博。

布局参考 weibogroup 的消息查看器（顶栏 + 左侧列表 + 右侧内容），但：
- **顶部是博主昵称**（非群选择器）
- **左侧是单层月份列表**（年月 `YYYY-MM`，点开展开当月各日）
- **右侧是卡片流**，点开某日 → 一次性展示当日全部微博，倒序（最新在上）
- **高级搜索只搜内容**（关键词 + 时间范围，无发送者筛选）
- **视觉用微博橙 + 卡片流**，与 weibogroup 的 Google 蓝 + 聊天气泡明显区分

### 关键简化（相比 weibogroup）

| 维度 | weibogroup | weiboblog |
|------|-----------|-----------|
| 分页 | 游标分页 + 触顶触底加载 | 无，点开某日一次查全部 |
| 发送者筛选 | 有 | 无（单博主） |
| 媒体 | server 代理下载 + cookie 注入 | 直接用 sinaimg.cn 原始 URL |
| 排列 | 升序（新在底） | 倒序（新在上） |

单日微博实测最多 71 条（2018-10-21），绝大多数个位数，一次查全部无性能压力。

## 2. 整体架构

```
weiboblog/
├── server.py              # HTTP server（纯 stdlib，ThreadingHTTPServer）
├── web/
│   ├── index.html         # 页面骨架
│   ├── app.js             # 前端逻辑（原生 JS）
│   └── style.css          # 样式（微博橙 + 卡片流）
├── crawl_blog.py          # 既有 CLI
└── weibo_blog/            # 既有包
```

### server.py

- `ThreadingHTTPServer` + `BaseHTTPRequestHandler`，纯标准库，零新依赖
- DB 连接以只读模式 `file:...?mode=ro` 注入 Handler 类属性，
  `check_same_thread=False`，与 weibogroup 一致
- 路由：`/` → index.html；`/web/<file>` → 静态资源；`/api/*` → JSON；其余 404
- CST(+8) 时间边界：`calendar.timegm` 算 UTC 边界后减 8h，DB 存 UTC ms、UI 按 CST 日聚合
- CLI 参数：`--db`（默认 `weibo_blog.db`）、`--host`（默认 `127.0.0.1`）、
  `--port`（默认 **8766**，避开 weibogroup 的 8765，两个查看器可同时开）
- 静态资源服务含路径穿越防护（`os.path.normpath` + `startswith(WEB_DIR)`）

## 3. API 接口

全部 GET，全部 `/api/` 前缀，返回 JSON（`application/json; charset=utf-8`）。
出错返回 `{"error": "..."}` + HTTP 码（400/404/500）。

| 接口 | 参数 | 返回 | 用途 |
|------|------|------|------|
| `/api/blogger` | 无 | `{uid, screen_name, profile_url, verified}` | 顶部博主信息 |
| `/api/months` | 无 | `[{month, count}]` 降序 | 左侧月份列表 |
| `/api/dates` | `month`（`YYYY-MM`，必填） | `[{date, count}]` 降序 | 展开某月时取该月各日 |
| `/api/posts` | `date`（`YYYY-MM-DD`，必填） | `{date, posts:[...]}` | 当天全部微博，倒序 |
| `/api/search` | `q`（可选）、`start`/`end`（`YYYY-MM-DD`，可选）、`limit`（默认 1000） | `{results:[...], total}` | 内容搜索 |

### 3.1 `/api/blogger`

从 `bloggers` 表取一条（单博主场景）。空库返回 404 `{"error":"no blogger"}`。

返回：
```json
{"uid": 1401527553, "screen_name": "tombkeeper", "profile_url": "/u/1401527553", "verified": 1}
```

### 3.2 `/api/months`

```sql
SELECT strftime('%Y-%m', datetime(created_at/1000,'unixepoch','+8 hours')) AS month,
       COUNT(*) AS count
FROM weibo_posts GROUP BY month ORDER BY month DESC
```

返回：`[{"month":"2025-06","count":40}, {"month":"2025-05","count":12}, ...]`

### 3.3 `/api/dates?month=YYYY-MM`

`month` 缺失 → 400。CST 月边界 `[start_ms, end_ms)` 过滤，按 CST 日聚合。

```sql
SELECT strftime('%Y-%m-%d', datetime(created_at/1000,'unixepoch','+8 hours')) AS date,
       COUNT(*) AS count
FROM weibo_posts
WHERE created_at >= ? AND created_at < ?
GROUP BY date ORDER BY date DESC
```

返回：`[{"date":"2025-06-17","count":3}, {"date":"2025-06-07","count":37}, ...]`

懒加载：首次展开某月才请求，结果缓存在前端。

### 3.4 `/api/posts?date=YYYY-MM-DD`

`date` 缺失 → 400。CST 日边界 `[start_ms, end_ms)` 过滤 `created_at`，倒序。

```sql
SELECT mblogid, text_raw, long_text, is_long_text, pics_json, source,
       reposts_count, comments_count, attitudes_count, created_at
FROM weibo_posts
WHERE created_at >= ? AND created_at < ?
ORDER BY created_at DESC
```

`pics_json` 在 server 端解析成数组再返回（前端不二次解析）。

返回的 post 对象：
```json
{
  "mblogid": "PrP6QqqEQ",
  "text_raw": "正文纯文本...",
  "long_text": "",
  "is_long_text": 0,
  "pics": [{"pid":"...","url_bmiddle":"https://wx2.sinaimg.cn/wap360/...jpg","url_large":"https://wx2.sinaimg.cn/orj960/...jpg","w":668,"h":347}],
  "source": "微博 weibo.com",
  "reposts_count": 12, "comments_count": 5, "attitudes_count": 88,
  "created_at": 1747228526000
}
```

无数据返回 `{"date":"2025-06-17","posts":[]}`（非 404）。`text`（HTML 版）不返回。

### 3.5 `/api/search`

匹配 `text_raw` 和 `long_text`（OR），时间范围用 CST 日边界转 ms。
LIKE 通配符 `% _ \` 转义。结果按 `created_at DESC`，limit 截断返回 total。

结果对象：
```json
{
  "mblogid": "PrP6QqqEQ",
  "date": "2025-06-17",
  "created_at": 1750197180000,
  "snippet": "...命中前后文...（命中处用 \x00 \x01 包裹，前端转 <mark>）"
}
```

返回：`{"results":[...], "total": 42}`

### 3.6 搜索跳转定位

点击搜索结果 → 关闭浮层 → 调 `/api/posts?date=...` 加载当天 → 渲染 →
`document.getElementById('post-'+mblogid).scrollIntoView({block:'center'})` →
加 `.post-highlight` 类闪烁高亮。无需额外接口（当天最多 71 条，前端直接定位）。

## 4. 前端布局与视觉风格

三栏式：顶栏 + 左侧月份列表 + 右侧卡片流。

```
┌─────────────────────────────────────────────────────┐
│  tombkeeper                          🔍 高级搜索     │  顶栏
├──────────┬──────────────────────────────────────────┤
│          │  2025-06-17  共 3 条                      │  日期标题条
│ 2025-06  │ ┌────────────────────────────────────┐  │
│   06-17 3│ │  17:53                             │  │  微博卡片
│   06-07 37│ │  正文文本内容...                    │  │
│ 2025-05  │ │  [缩略图] [缩略图]                  │  │
│   05-20 2│ │  转发 12  评论 5  赞 88  · weibo.com│  │
│          │ └────────────────────────────────────┘  │
└──────────┴──────────────────────────────────────────┘
```

### 4.1 顶栏

- 左侧博主昵称纯文字（加粗，微博橙 `#ff8200`）
- 右侧"🔍 高级搜索"按钮
- 背景 `#fff`，底部微博橙细线 `1px solid #ff8200`
- （weibogroup 是灰底 `#f5f5f5` + 群选择器 + 灰线 `#ddd`）

### 4.2 左侧月份列表

- 宽 200px，背景 `#fffaf5`（极浅橙）
- 月份项 `2025-06 (40)`，降序
- 点击月份 → 展开/收起该月日期列表（`06-17 (3)`…），懒加载（首次展开才请求）
- 选中日期：微博橙底 `#ff8200` + 白字

### 4.3 右侧卡片流

- 每条微博一张白底卡片：`border-radius:8px`，`box-shadow` 轻投影，
  `margin-bottom:12px`，宽度撑满
- 倒序排列（最新在上）
- 卡片内部：
  - 时间 `HH:MM`（灰色 12px，右上角）
  - 正文 `text_raw`（14px，#333，pre-wrap）+ `long_text`（如有）
  - 图片缩略图网格（`url_bmiddle`，max-width 120px）
  - 底部元信息：`转发 N · 评论 N · 赞 N · 来源`（11px，#999）

### 4.4 配色对照（刻意区分 weibogroup）

| 元素 | weibogroup | weiboblog |
|------|-----------|-----------|
| 强调色 | Google 蓝 `#1a73e8` | 微博橙 `#ff8200` |
| 顶栏背景 | `#f5f5f5` 灰 | `#fff` 白 + 橙底线 |
| 侧栏背景 | `#fafafa` 灰 | `#fffaf5` 浅橙 |
| 内容容器 | 聊天气泡 `#f0f0f0` | 白卡片 + 投影 |
| 排列方向 | 升序（新在底） | 倒序（新在上） |
| 高亮闪烁 | `#fff3a0` 黄 | `#ffe0b3` 浅橙 |

### 4.5 图片 lightbox

点击缩略图 → 全屏遮罩 `rgba(0,0,0,.85)` + 大图（`url_large`），点遮罩关闭。
图片直接用 sinaimg.cn 原始 URL，不走 server 代理。
`<img onerror>` 显示"图片加载失败"占位框。

### 4.6 搜索浮层

- 全屏遮罩 + 居中面板（600px 宽）
- 字段：关键词输入框 + 起止日期（默认最近 3 个月）
- 无发送者字段（单博主）
- 结果列表：每项 `日期 时间` + 命中片段（`<mark>` 高亮）
- 点击结果 → 关闭浮层 → 加载该日期 → 定位高亮
- 关闭：× 按钮 / 点遮罩 / Esc

### 4.7 空状态

- 首次进入无选中日期：右侧提示"请从左侧选择日期"
- 某日无微博：显示"该日无微博"

## 5. 数据库变更

在 `weibo_blog/db.py` 的 `init_db` 补一条复合索引：

```sql
CREATE INDEX IF NOT EXISTS idx_wp_uid_ctime ON weibo_posts(uid, created_at)
```

让当天范围查询 `WHERE created_at >= ? AND created_at < ?` 走索引。
（weibogroup 注释声称有复合索引但实际没建，这里实建。）

## 6. 错误处理

- API 出错：`{"error":"..."}` + HTTP 码（400 参数缺失 / 404 资源不存在，如 blogger 空库 / 500 内部错误）
- `/api/posts` 某日无数据返回空数组 `{"posts":[]}`（非 404），由前端显示"该日无微博"
- 前端顶栏右侧状态区显示错误（"加载失败"、空数据提示）
- 搜索浮层："未找到匹配微博"或"已达上限，请缩小范围"
- 图片加载失败：`<img onerror>` 占位，不阻断页面

## 7. 测试

新建 `tests/test_server.py`（pytest），用 `tests/conftest.py` 的 `mem_db` fixture。
启动真实 server 在 127.0.0.1 随机端口，`urllib.request` 发请求断言。

覆盖：
1. `/api/blogger`：返回字段正确；空库 404
2. `/api/months`：跨月数据聚合正确、降序、count 准确
3. `/api/dates?month=`：月内各日聚合正确、降序；month 缺失 400；跨月不串
4. `/api/posts?date=`：倒序（最新在上）；跨天不串；date 缺失 400；无数据空数组
5. `/api/posts` pics 解析：pics_json 字符串在响应里已解析成数组
6. `/api/search`：命中 text_raw 和 long_text；时间范围过滤；LIKE 通配符转义；limit 截断返回 total；无结果空
7. 静态资源：`/` 返回 index.html；`/web/style.css` Content-Type 正确；路径穿越 403

不测前端 JS 渲染逻辑（与 weibogroup 一致）。

## 8. 运行方式

```bash
uv run server.py                       # 默认 127.0.0.1:8766，读 weibo_blog.db
uv run server.py --port 9000           # 自定义端口
uv run server.py --db D:\path\to.db    # 自定义数据库
```

README 第 11 节（或新增章节）补充查看器使用说明，与 weibogroup 的 §11 对齐。
