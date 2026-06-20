# weiboblog 抓取子系统设计

日期：2026-06-20
状态：已确认，待编写实现计划

## 背景

新建独立项目 `D:\weiboblog`，用于抓取指定微博博主的全部微博并存入本地 SQLite 数据库。项目与 `weibogroup`（微博群聊抓取器）平级，代码全新编写，技术栈沿用 weibogroup：Python + requests + SQLite + stdlib，CLI 入口。

**本次范围仅抓取子系统**（接口调用 + 翻页 + 解析 + 存储）。查看器（前端浏览）为下一阶段独立需求，本次不涉及。

**数据来源**：weibo.txt 样本确认的两个接口：
- `GET https://weibo.com/ajax/statuses/mymblog?uid={uid}&page={page}&feature=0&since_id={since_id}` — 微博列表，分页用 page + since_id，返回 `data.since_id`（下页游标）和 `data.list[]`（微博数组，新→旧）。
- `GET https://weibo.com/ajax/statuses/longtext?id={mblogid}` — 长文全文，`isLongText=true` 时调用，返回 `data.longTextContent`。

**Cookie**：复用微博账号 cookie，存 config 表 `weibo_cookie` 键（独立 DB，与 weibogroup 互不影响）。提供 `--renew-cookie` 用 Playwright 打开 `weibo.com` 主站扫码登录提取 cookie（与 weibogroup 同模式，扫码入口改为主站）。

## 架构方案

方案 A（已确认）：单模块 Crawler 类 + db + parser 三件套，照搬 weibogroup 模式。

## §1 项目结构与文件职责

```
D:\weiboblog\
├── weibo_blog/              # Python 包
│   ├── __init__.py
│   ├── crawler.py           # BlogCrawler：HTTP 调用、翻页、长文补全、抓取编排
│   ├── parser.py            # mymblog JSON → 扁平 dict 字段映射（纯函数，无 IO）
│   └── db.py                # SQLite 建表、bloggers/weibo_posts 存取、cookie 读写
├── crawl_blog.py            # CLI 入口（--uid / --set-cookie / --renew-cookie / --all / --full）
├── weibo_blog.db            # SQLite 数据库（运行时生成）
├── pyproject.toml           # 项目元数据 + requests 依赖
└── tests/
    ├── conftest.py          # 测试 DB 夹具
    ├── test_crawler.py      # 抓取/存储单测
    └── fixtures/            # 真实 JSON 样本（从 weibo.txt 提取）
```

**职责边界**：
- `parser.py`：纯函数 `parse_post(raw: dict) -> dict`，输入 mymblog 单条原始 dict，输出扁平 dict。无 IO、无 DB，可独立单测。
- `db.py`：表 DDL + 存取函数。crawler 只调函数不写 SQL。
- `crawler.py`：`BlogCrawler` 类持有 session，调 mymblog/longtext 接口，调 parser 解析，调 db 存储，负责翻页停止条件与增量/全量判断。

## §2 数据库表设计

### bloggers 表（博主信息）

```sql
CREATE TABLE IF NOT EXISTS bloggers (
    uid           INTEGER PRIMARY KEY,        -- 博主 uid（如 1401527553）
    screen_name   TEXT NOT NULL DEFAULT '',   -- 昵称（如 tombkeeper）
    avatar        TEXT DEFAULT '',            -- 头像 URL
    profile_url   TEXT DEFAULT '',            -- /u/uid
    verified      INTEGER DEFAULT 0,          -- 是否认证
    post_count    INTEGER DEFAULT 0,          -- 抓取到的微博数（运行时统计）
    raw_json      TEXT DEFAULT '',            -- user 原始 JSON
    created_at    INTEGER DEFAULT 0,          -- ms
    updated_at    INTEGER DEFAULT 0           -- ms
);
```

**信息来源**：mymblog 接口返回的每条微博都嵌套 `user` 对象（含 id/screen_name/profile_image_url/profile_url/verified），从首页 `data.list[0].user` 提取即可，无需单独调"获取博主信息"接口。CLI 只需传 uid 启动，昵称等从首条微博自动填充。边界：0 条微博时无法提取 user，日志提示即可。

### weibo_posts 表（微博正文）

```sql
CREATE TABLE IF NOT EXISTS weibo_posts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    mblogid         TEXT NOT NULL UNIQUE,       -- 短链 ID（如 PrPsZ0f8o），唯一去重键
    post_id         INTEGER NOT NULL,           -- 微博数字 ID（如 5166326970058180）
    uid             INTEGER NOT NULL,           -- 博主 uid
    text            TEXT DEFAULT '',            -- 正文（含 HTML 标签，如 <a>/<br>）
    text_raw        TEXT DEFAULT '',            -- 纯文本正文（text_raw）
    long_text       TEXT DEFAULT '',            -- 长文全文（isLongText 时从 longtext 接口取）
    is_long_text    INTEGER DEFAULT 0,          -- 是否长文
    source          TEXT DEFAULT '',            -- 发布来源（清洗后纯文本）
    region          TEXT DEFAULT '',            -- 发布地域（region_name，如"发布于 北京"）
    pics_json       TEXT DEFAULT '[]',          -- JSON: [{pid, url_large, url_bmiddle, w, h}]
    video_url       TEXT DEFAULT '',            -- 视频直链（page_info.media_info.stream_url）
    retweeted_json  TEXT DEFAULT '',            -- JSON: 转发原微博精简信息
    reposts_count   INTEGER DEFAULT 0,          -- 转发数
    comments_count  INTEGER DEFAULT 0,          -- 评论数
    attitudes_count INTEGER DEFAULT 0,          -- 点赞数
    created_at      INTEGER NOT NULL,           -- 微博发布时间（ms）
    saved_at        INTEGER NOT NULL,           -- 入库时间（ms）
    raw_json        TEXT DEFAULT ''             -- 原始 mymblog 单条 JSON
);

CREATE INDEX IF NOT EXISTS idx_wp_uid    ON weibo_posts(uid);
CREATE INDEX IF NOT EXISTS idx_wp_ctime  ON weibo_posts(created_at);
CREATE INDEX IF NOT EXISTS idx_wp_pid    ON weibo_posts(post_id);
```

### config 表

```sql
CREATE TABLE IF NOT EXISTS config (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL DEFAULT '',
    updated_at INTEGER NOT NULL DEFAULT 0
);
```

存 `weibo_cookie` 键。

**关键设计决策**：
- `mblogid`（短链 ID）作唯一去重键——稳定对外标识，URL 形如 `weibo.com/u/uid/PrPsZ0f8o`。
- `pics_json` 只存精简结构（pid + 大图/中图 URL + 宽高），不存全部 7 种尺寸——查看器只需大图，中图做缩略。
- `retweeted_json` 存转发原微博的关键字段而非全量——转发链通常一层，扁平存即可，避免嵌套表。
- 不建 FTS5（本次只做抓取，搜索是查看器的事，YAGNI）。

## §3 抓取流程与翻页状态机

### mymblog 接口

```
GET https://weibo.com/ajax/statuses/mymblog?uid={uid}&page={page}&feature=0&since_id={since_id}
```

- `feature=0`：全部微博
- `page`：页码，从 1 开始，递增取更旧的历史
- `since_id`：游标，首次空，后续用上一页 `data.since_id`（实测可带可不带，带上与浏览器行为一致更保险）
- 响应：`data.since_id`（下页游标）、`data.list[]`（微博数组）

**翻页方向（实测确认）**：page 递增 = 取更旧历史。list 内部排列为**旧→新**（首条最旧、末条最新）。页间衔接：page2 首条比 page1 末条更旧。无需 since_id 即可正确翻页，但带上作为下一页游标提示。停止条件：list 为空即到底（微博多的博主可能翻几百页）。

**Cookie 要求（实测确认）**：mymblog/longtext 接口复用微博账号 cookie 即可，**不需要 x-xsrf-token header**（即便 cookie 中无 XSRF-TOKEN 也能返回 200）。直接复用 weibogroup 的 weibo_cookie。

### 全量回填 `crawl_blog_backfill(uid)`（首次或 --full）

```
page=1, since_id=""
loop:
    resp = fetch_mymblog(uid, page, since_id)
    if data.list 为空: break
    首页提取 user → save_blogger
    for post in data.list:           # list 旧→新，逐条处理
        parsed = parse_post(post)
        if parsed.is_long_text: parsed.long_text = fetch_longtext(mblogid)
        save_post(parsed)
    since_id = resp.data.since_id
    page += 1
    jitter_sleep(0.5)
```

无上限翻页直到 list 为空。

### 增量更新 `crawl_blog_incremental(uid)`（默认模式）

list 旧→新，末条为当页最新。增量从 page=1（最新一屏）开始往旧翻，检查每条是否已存：

```
page=1, since_id=""
latest_post_id = get_latest_post_id(uid)   # DB 里已存的最新 post_id
loop:
    resp = fetch_mymblog(uid, page, since_id)
    if data.list 为空: break
    for post in data.list:                   # 旧→新遍历
        if post.id <= latest_post_id:        # 命中已存（更旧），后续都更旧，可跳过
            continue
        parsed = parse_post(post)
        if parsed.is_long_text: parsed.long_text = fetch_longtext(mblogid)
        save_post(parsed)
    # 当页末条（最新）<= latest_post_id → 整页都已知，增量结束
    if data.list[-1].id <= latest_post_id: return
    since_id = resp.data.since_id
    page += 1
    jitter_sleep(0.5)
```

注意：增量方向与全量相同（都从 page=1 往旧翻），因为 mymblog 没有"往新翻"的参数——page=1 永远是最新一屏。增量靠"跳过已存 + 命中整页已知即停"。

### 模式判断

`BlogCrawler.crawl_blog(uid, full=False)`：
- `full=True` 或 DB 中该 uid 无任何微博 → backfill
- 否则 → incremental

### longtext 补全

```
GET https://weibo.com/ajax/statuses/longtext?id={mblogid}
响应: data.longTextContent
```

`isLongText=true` 时解析后立即调用，取 `longTextContent` 填入 `long_text`。失败则留空、记日志，不中断整页抓取。

### 重试与限流

复用 weibogroup 的 `_request_with_retry` 模式：5xx 指数退避、429 额外等待、4xx 非 429 不重试。jitter sleep 翻页间隔 0.5s。

## §4 解析逻辑（parser.py）

`parse_post(raw: dict) -> dict` 字段映射：

| 输出字段 | 来源路径 | 说明 |
|---|---|---|
| `mblogid` | `raw["mblogid"]` | 短链 ID，唯一键 |
| `post_id` | `raw["id"]` | 数字 ID |
| `uid` | `raw["user"]["id"]` | 博主 uid |
| `text` | `raw["text"]` | 含 HTML 的正文 |
| `text_raw` | `raw["text_raw"]` | 纯文本正文 |
| `is_long_text` | `raw.get("isLongText", False)` | bool→int |
| `source` | `raw.get("source", "")` | 去标签后纯文本 |
| `region` | `raw.get("region_name", "")` | 如"发布于 北京" |
| `reposts_count` | `raw.get("reposts_count", 0)` | 转发数 |
| `comments_count` | `raw.get("comments_count", 0)` | 评论数 |
| `attitudes_count` | `raw.get("attitudes_count", 0)` | 点赞数 |
| `created_at` | `raw["created_at"]` | `"Wed May 14 22:09:58 +0800 2025"` → ms |
| `pics_json` | `raw.get("pic_infos", {})` | 精简为 `[{pid, url_large, url_bmiddle, w, h}]` |
| `video_url` | `raw.page_info.media_info.stream_url` | 视频直链（多层 .get 防缺字段） |
| `retweeted_json` | `raw.get("retweeted_status")` | 精简为 `{post_id, mblogid, text_raw, uid, screen_name, created_at}` |
| `raw_json` | `raw` | 原始 JSON |

**关键处理**：
- **created_at 解析**：`datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y")` → 转 ms。
- **pics_json 精简**：遍历 `pic_infos` dict（key 是 pid），每张取 `large.url` / `bmiddle.url` / `large.width` / `large.height`，丢弃 thumbnail/original/largest/mw2000/largecover 等。
- **retweeted_json 精简**：只保留原微博的 id/mblogid/text_raw/user(id+screen_name)/created_at，丢弃嵌套 pics/page_info。
- **video_url**：`raw.get("page_info", {}).get("media_info", {}).get("stream_url", "")`。
- **source 清洗**：原始形如 `<a href="..." rel="...">iPhone客户端</a>` 或纯文本，正则去标签取文本。

## §5 CLI 入口与测试策略

### CLI（`crawl_blog.py`）

```python
python crawl_blog.py --set-cookie 'SUB=xxx; ...'      # 手动设置 cookie
python crawl_blog.py --renew-cookie                    # Playwright 打开 weibo.com 扫码续期
python crawl_blog.py --uid 1401527553                  # 增量抓取该博主
python crawl_blog.py --uid 1401527553 --full           # 全量回填
python crawl_blog.py --all                             # 增量抓取 bloggers 表所有博主
```

无参数打印用法。

### --renew-cookie 实现

复用 weibogroup `_renew_cookie` 的 Playwright 扫码模式，扫码入口改为 `https://weibo.com`（主站，与 mymblog 同域）。登录成功后提取 `.weibo.com` 域 cookie，存入 config 表 `weibo_cookie` 键。支持 `--headless`（仅截图二维码）与有头模式（弹窗）。

### 测试策略（TDD）

`tests/conftest.py`：`make_test_db()` 建内存 SQLite + bloggers/weibo_posts/config 表，提供插入夹具。

`tests/test_crawler.py`：
1. `test_parse_post_basic`：纯文本微博样本，断言各字段正确。
2. `test_parse_post_with_pics`：带 pic_infos 样本，断言 pics_json 精简后只含 large/bmiddle。
3. `test_parse_post_with_video`：带 page_info 样本，断言 video_url 提取正确。
4. `test_parse_post_with_retweet`：带 retweeted_status 样本，断言 retweeted_json 精简正确。
5. `test_parse_post_longtext_flag`：isLongText=true 样本，断言 is_long_text=1。
6. `test_parse_created_at`：断言 `"Wed May 14 22:09:58 +0800 2025"` → 正确 ms。
7. `test_save_post_dedup`：同 mblogid 存两次，只入库一条。
8. `test_crawl_blog_backfill`：mock requests，mymblog 返回多页（list 旧→新）+ 空页，断言翻页停止、微博数正确、longtext 补全被调用。
9. `test_crawl_blog_incremental`：mock mymblog 返回新微博 + 已存微博（list 旧→新，末条已存触发整页停止），断言跳过已存、命中即停。
10. `test_save_blogger_from_first_post`：断言首页 user 字段提取并入库。
11. `test_cookie_set_get`：断言 cookie 存取 config 表。

**测试数据**：从 weibo.txt 提取真实 JSON 片段作为 fixture（纯文本/带图/带视频/转发/长文标记各一条），存为 `tests/fixtures/*.json`。`--renew-cookie` 涉及 Playwright 浏览器交互，不做自动化测试，手动验证。

## 非目标

- 查看器（前端浏览、搜索）——下一阶段独立需求。
- 媒体下载——只存 URL，查看阶段按需下载。
- FTS5 全文搜索——查看器阶段再加。
- 评论/转发链抓取——只抓博主本人微博正文。
