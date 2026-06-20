# 微博博主微博爬虫架构设计文档

> 本文档描述 `D:\weiboblog` 项目的架构设计、模块职责、数据流、状态机和跨语言迁移要点。**与实现语言无关**——目的是让任何人读完之后能用 Java/Go/Rust 重新实现一遍。
>
> 接口层细节见配套文档 [`API.md`](./API.md)。本文档关注「怎么组织代码」「数据怎么流」「核心算法怎么设计」。

---

## 目录

- [1. 设计目标与原则](#1-设计目标与原则)
- [2. 总体架构](#2-总体架构)
- [3. 模块职责](#3-模块职责)
- [4. 核心数据流](#4-核心数据流)
- [5. 关键算法](#5-关键算法)
- [6. 状态机](#6-状态机)
- [7. 数据模型](#7-数据模型)
- [8. 并发与一致性](#8-并发与一致性)
- [9. 已知局限与演进方向](#9-已知局限与演进方向)

---

## 1. 设计目标与原则

### 1.1 目标

1. **数据完整性**：不漏微博（按 mblogid 去重，全量覆盖到 list 空）。
2. **幂等性**：任何命令重复跑都不产生脏数据。
3. **断点续传**：全量回填中途打断，已写入的都保留；重跑 `--full` 靠去重跳过。
4. **抗风控**：节奏抖动 + 重试退避。
5. **零外部服务依赖**：单机即可跑。
6. **可移植**：核心是 HTTP + JSON + DB，方便用其他语言重写。

### 1.2 设计原则

| 原则 | 体现 |
|------|------|
| **逻辑/入口分离** | `crawl_blog.py` 只做参数解析；业务全在 `weibo_blog/` 包 |
| **解析与 IO 分离** | `parser.py` 是纯函数，不碰网络/数据库 |
| **去重靠数据库约束** | `UNIQUE(mblogid)` + `INSERT OR IGNORE` 兜底 |
| **节奏抖动** | 所有 sleep 都带随机偏移 |
| **原始数据留存** | `raw_json` 字段永久保留，便于重新解析 |
| **Cookie 持久化在 DB** | 不靠文件、不靠环境变量，单一数据源 |

---

## 2. 总体架构

### 2.1 分层架构图

```
┌──────────────────────────────────────────────────────────┐
│  CLI 入口层  (crawl_blog.py)                              │
│  - 参数解析 / 日志初始化 / 流程编排                         │
│  - 调用 weibo_blog.* 完成具体业务                          │
└─────────────────────────┬────────────────────────────────┘
                          │
┌─────────────────────────▼────────────────────────────────┐
│  业务层  (weibo_blog.crawler.BlogCrawler)                 │
│  - fetch_mymblog / fetch_longtext                         │
│  - crawl_blog_backfill（全量回填）                         │
│  - crawl_blog_incremental（增量）                          │
│  - crawl_blog（模式判断入口）                              │
│  - renew_cookie / check_playwright                        │
└────────┬──────────────────────────────────┬───────────────┘
         │ HTTP                             │ DB
┌────────▼─────────────┐  ┌─────────────────▼──────────────┐
│ API 客户端层          │  │  持久层 db.py                    │
│ - requests.Session   │  │  - SQLite 连接 (get_conn)        │
│ - _request_with_retry│  │  - init_db 建表                  │
│ - _jitter_sleep      │  │  - save_blogger / save_post      │
│  crawler.py 上半     │  │  - get_latest_post_id            │
└────────┬─────────────┘  │  - get_cookie / set_cookie       │
         │                │  - get_blogger_list              │
┌────────▼──────────────┐ └─────────────────────────────────┘
│  解析层  (parser.py) — 纯函数，无副作用                    │
│  parse_post(raw) → 扁平 dict                              │
│  parse_blogger(user) → dict                              │
└───────────────────────────────────────────────────────────┘
```

### 2.2 调用关系（运行时）

```
crawl_blog.main()
  ├─ 若 --renew-cookie → renew_cookie() (Playwright)
  │                        ↓
  │                   db.set_cookie()
  │
  ├─ 若 --check-playwright → check_playwright() → exit 0/1
  │
  ├─ 若 --set-cookie → db.set_cookie() → return
  │
  └─ 否则 → BlogCrawler(db_path)
              └─ crawl_blog(uid, full)
                   ├─ full=True 或无已存 → crawl_blog_backfill()
                   └─ 否则 → crawl_blog_incremental()
```

---

## 3. 模块职责

### 3.1 模块清单

| 模块 | 职责 | 对外接口 |
|------|------|---------|
| `crawl_blog.py` | CLI 入口、参数解析、流程编排 | `main()` |
| `weibo_blog/crawler.py` | API 客户端 + 爬取业务 + cookie 续期 | `BlogCrawler` 类 / `renew_cookie` / `check_playwright` |
| `weibo_blog/parser.py` | 原始 JSON → 扁平字典（纯函数） | `parse_post` / `parse_blogger` |
| `weibo_blog/db.py` | SQLite 建表 + 存取 | `init_db` / `get_conn` / `save_*` / `get_*` |

### 3.2 各模块详解

#### `crawl_blog.py` — 入口层

**只做三件事：**
1. 解析命令行参数。
2. 初始化日志。
3. 根据 `--xxx` 分支调对应业务函数。

**不做：** 任何业务逻辑、HTTP 请求、SQL。所有「干活的代码」都在 `weibo_blog/`。

**特殊处理：**
- `--renew-cookie` / `--check-playwright` 与其他分支互斥，走 Playwright。
- `--set-cookie` 只初始化 DB 写 cookie 退出。
- 抓取命令先构造 `BlogCrawler`（这步会校验 cookie 是否存在）。

#### `weibo_blog/crawler.py` — 业务层

**上半部分（模块级函数）：HTTP 客户端 + 续期**

- `_make_session(cookie)` — 构造带 Cookie 的 requests.Session
- `_request_with_retry(...)` — 带重试退避的请求封装
- `_jitter_sleep(base, jitter)` — 抖动 sleep
- `check_playwright()` — 环境检查
- `renew_cookie(db_path, headless)` — Playwright 扫码续期

**`BlogCrawler` 类**

| 方法 | 职责 |
|------|------|
| `__init__(db_path, cookie)` | 初始化 DB + Cookie + Session |
| `fetch_mymblog(uid, page, since_id)` | §2.2 微博列表 → (since_id, list) |
| `fetch_longtext(mblogid)` | §2.3 长文全文 |
| `crawl_blog_backfill(uid)` | 全量回填（page=1→空） |
| `crawl_blog_incremental(uid)` | 增量（跳过已存，末条已存整页停） |
| `crawl_blog(uid, full)` | 模式判断入口 |

#### `weibo_blog/parser.py` — 解析层

**核心是 `parse_post(raw)` 纯函数**：输入 mymblog 单条原始字典，输出扁平字典（见 API.md §3.1）。

设计要点：
- **纯函数**：不读 DB、不发请求、无全局状态。
- **图片精简**：`pic_infos` → `[{pid, url_large, url_bmiddle, w, h}]`，丢弃冗余尺寸。
- **转发精简**：`retweeted_status` → 6 字段字典。
- **source 清洗**：正则去 `<a>` 标签取纯文本。
- **created_at 解析**：`strptime("%a %b %d %H:%M:%S %z %Y")` → 毫秒时间戳。
- `parse_blogger(user)` 从 user 字段提取博主信息。

> 跨语言迁移时这个文件**最重要**——是业务知识的载体。建议逐函数翻译。

#### `weibo_blog/db.py` — 持久层

- **连接管理**：模块级 `_DB_PATH` 变量 + `set_db_path()` + `get_conn()`，`get_conn()` 每次打开新连接并建表。
- **自动建表**：`init_db()` 用 `CREATE TABLE IF NOT EXISTS`。
- **幂等写入**：`save_post` 用 `INSERT OR IGNORE`（依赖 mblogid UNIQUE）；`save_blogger` 用 `ON CONFLICT UPDATE`。
- **索引**：`uid` / `created_at` / `post_id` 上有索引。

---

## 4. 核心数据流

### 4.1 爬取主流程

```
[微博 API]
    │
    │  fetch_mymblog(uid, page)   ← JSON: {data:{since_id, list:[...]}}
    ▼
[parser.parse_post(raw)]          ← 每条 raw → 扁平 dict
    │
    │  if is_long_text:
    │    fetch_longtext(mblogid) → 补 long_text
    ▼
[db.save_post(parsed)]            ← INSERT OR IGNORE（mblogid 去重兜底）
    │
    ▼
[sqlite: weibo_posts 表]
```

### 4.2 三层去重（关键设计）

```
┌─────────────────────────────────────────────────────────────┐
│ 第 1 层：翻页层（增量模式，省请求）                           │
│   拉到一页后，如果 list[-1].id <= latest，整页丢弃不再翻       │
├─────────────────────────────────────────────────────────────┤
│ 第 2 层：内存过滤（省入库）                                   │
│   遍历每条，raw.id <= latest 直接 continue                   │
├─────────────────────────────────────────────────────────────┤
│ 第 3 层：DB 约束（兜底保证）                                  │
│   weibo_posts.mblogid 有 UNIQUE 约束                         │
│   INSERT OR IGNORE 撞主键静默忽略                            │
│   ⇒ 任何情况下都不会有重复行                                  │
└─────────────────────────────────────────────────────────────┘
```

> 全量回填模式只有第 3 层（无 latest 可比较），靠 mblogid 去重。重跑 `--full` 时已存微博被忽略，但 longtext 会重新请求。

---

## 5. 关键算法

### 5.1 翻页方向模型

与群聊接口的线段模型不同，博主微博用 **page 翻页**：

```
page=1   最新的一屏（list 内部旧→新）
page=2   更旧的一屏
page=3   更更旧
  ...
page=N   list 为空 → 到底
```

**两种操作：**

```
增量（只补更新）：
   从 page=1（最新）往旧翻
   遇到 post_id <= latest 跳过
   当页末条 <= latest → 整页已知，停

全量回填（补全部历史）：
   从 page=1 一路翻到 list 空
   靠 mblogid 去重，已存的跳过入库
```

> ⚠️ 增量模式**拉不到比已存更早的数据**。它从最新往旧翻，翻到已存就停。更早的历史只能靠 `--full`。

### 5.2 重试退避

见 API.md §4.1。核心：

```
5xx / 网络错：  backoff = 2^attempt × (1 + rand[0, 0.5])
429 限流：      backoff = 4^attempt × (1 + rand[0, 0.5])
4xx 其他：      不重试
```

---

## 6. 状态机

### 6.1 抓取模式选择

```
                     ┌──────────────────┐
                     │  DB 无此博主微博  │
                     └────────┬─────────┘
                              │
                     ┌────────▼─────────┐
                     │ 自动全量回填      │
                     │ (crawl_blog_backfill)│
                     └────────┬─────────┘
                              │
                              ▼
                     ┌──────────────────┐
                     │ DB 已有此博主微博 │
                     └────────┬─────────┘
                              │
                ┌─────────────┴─────────────┐
                │                           │
        --full 强制                  不带 --full
        全量回填                      增量更新
        (backfill)                   (incremental)
```

### 6.2 Cookie 状态机

```
[未登录] ──scan QR──► [已登录] ──SUB 过期──► [失效]
   ▲                                          │
   │                                          │
   └────────── --renew-cookie ────────────────┘
```

失效检测：`fetch_mymblog` 返回空 list。

---

## 7. 数据模型

### 7.1 ER 图

```
┌─────────────┐         ┌──────────────────┐
│  bloggers   │ 1     N │   weibo_posts     │
│─────────────│◄────────│──────────────────│
│ uid (PK)    │         │ id (PK autoinc)   │
│ screen_name │         │ mblogid (UNIQUE)  │
│ avatar      │         │ post_id           │
│ profile_url │         │ uid (FK→bloggers) │
│ verified    │         │ text / text_raw   │
│ raw_json    │         │ long_text         │
│ created_at  │         │ pics_json         │
│ updated_at  │         │ video_url         │
└─────────────┘         │ retweeted_json    │
                        │ created_at (ms)   │
                        │ raw_json          │
┌──────────────┐        └──────────────────┘
│   config     │
│──────────────│
│ key (PK)     │
│ value        │
│  - weibo_cookie │
└──────────────┘
```

### 7.2 表字段语义

> 完整 DDL 见 `db.py:init_db()`。

| 表 | 关键字段 | 说明 |
|----|---------|------|
| `config` | `key` PK | `weibo_cookie` 等 |
| `bloggers` | `uid` PK | `save_blogger` 用 `ON CONFLICT UPDATE`，avatar 用 COALESCE 保留旧值 |
| `weibo_posts` | `mblogid` UNIQUE, `post_id`, `created_at`(ms) | `raw_json` 永久保留 |

### 7.3 设计取舍

| 决策 | 选择 | 原因 |
|------|------|------|
| 去重键 | `mblogid` TEXT UNIQUE | 短链 ID 全局唯一，稳定不变 |
| 新旧比较 | `post_id` int | 单调递增，数字比较可靠 |
| 时间戳 | INTEGER ms | 统一毫秒 |
| 原始 JSON | TEXT 永久保留 | 解析逻辑会演进 |
| Cookie 存储 | DB config 表 | 单一数据源 |
| 图片/视频 | 只存 URL | 节省存储，按需访问 |
| 转发 | 精简 JSON | 6 字段够用，原始在 raw_json |

---

## 8. 并发与一致性

### 8.1 单进程模型

当前实现**单进程单线程**。`BlogCrawler.__init__` 调 `get_conn()` 持有一个连接，整个抓取过程复用。

### 8.2 事务边界

`save_post` / `save_blogger` 每条 commit 一次。换来的是断点安全性：进程挂掉已写的不会丢。

### 8.3 多进程注意事项

| 场景 | 风险 | 建议 |
|------|------|------|
| 同时开两个 `crawl_blog.py` 抓同一 uid | 都走 `INSERT OR IGNORE`，不会脏数据，但可能重复请求 | ❌ 不要并行 |
| 全量回填被打断后重跑 | 已存的靠去重跳过，但 longtext 重复请求 | ✅ 可重跑，慢但正确 |

---

## 9. 已知局限与演进方向

### 9.1 当前局限

| 局限 | 影响 | 临时规避 |
|------|------|---------|
| 单线程 | 上万条全量回填慢（数十分钟） | 后台运行，中途打断不丢数据 |
| 重跑 `--full` 重复请求 longtext | 浪费 API | 当前未做整页已存跳过优化 |
| 无媒体下载 | 只存 URL | 未来阶段可加，参考 weibogroup media.py |
| 无全文搜索 | 不能搜微博内容 | 未来阶段可加 FTS5 |
| 无消息查看器 | 只能 SQL 查 | 未来阶段可加 web 前端 |
| Cookie 续期需手动扫码 | 不能全自动 | 可加 OCR（不推荐） |

### 9.2 演进路线

```
当前（爬取子系统）
    │
    ├─► 媒体下载（图片/视频落本地）
    │     - 参考 weibogroup media.py
    │     - pics_json / video_url 按需下载
    │
    ├─► 全文搜索（FTS5）
    │     - 索引 text_raw / long_text
    │
    └─► 消息查看器（web 前端）
          - 参考 weibogroup server.py
          - 按日期浏览博主微博
```

---

## 附录：架构决策记录（ADR 摘要）

| # | 决策 | 选择 | 备选 | 理由 |
|---|------|------|------|------|
| 1 | 数据库 | SQLite | MySQL / PG | 单机零依赖，迁移容易 |
| 2 | 入口 | 单一 CLI 文件 | Web UI | 简单可靠，cron 友好 |
| 3 | Cookie 来源 | Playwright 扫码 | 抓包手动 | 自动化，体验好 |
| 4 | 去重 | mblogid UNIQUE 兜底 | 应用层 hash set | 永不重复，无需内存 |
| 5 | 翻页 | page 递增取更旧 | since_id 游标 | 实测 since_id 冗余，page 即可 |
| 6 | 节奏 | 抖动 sleep | 固定间隔 | 规避简单频控 |
| 7 | 媒体 | 只存 URL | 抓取时下载 | 节省存储，按需访问 |
| 8 | 转发 | 精简 JSON | 存完整嵌套 | 6 字段够用，原始在 raw_json |
| 9 | 长文 | 抓取时同步补全 | 懒加载 | 一次抓全，后续只读 |
| 10 | x-xsrf-token | 不需要 | 回填 | 实测 mymblog/longtext 仅需 Cookie |

---

## 文档对照

| 想了解什么 | 看哪里 |
|----------|-------|
| 怎么用（用户视角） | `README.md` |
| 接口细节（URL/参数/响应） | `API.md` |
| 架构/数据流/迁移（开发者视角） | **本文档** |
| 表结构 DDL | `db.py:init_db` |
| 算法细节 | 本文档 §5 |
