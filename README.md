# WeiboBlogCrawler — 微博博主微博本地爬虫

把指定微博博主的全部微博抓取到本地 SQLite 数据库的工具。与同源的
[weibogroup](../weibogroup)（群聊爬虫）技术栈一致、互不依赖，各自独立数据库。

- 全量回填博主历史微博（page=1 翻到空为止，无年限限制）
- 增量更新：只拉比已存最新一条更新的微博，末条已存则整页停止
- 长文自动补全（`isLongText` 时调 longtext 接口取全文）
- 图片 / 视频 / 转发原微博精简存储（只留必要字段 + 原始 JSON 备份）
- Playwright 扫码登录续期 cookie（有头弹窗 / 无头截图二选一）
- 复用微博账号 cookie，无需 x-xsrf-token

> 面向 **Windows / macOS / Linux 桌面**，全部交互通过控制台完成。数据全部落在本地。

---

## 1. 环境要求

| 项 | 要求 |
|----|------|
| 操作系统 | Windows 10+ / macOS / Linux（有桌面或无头均可） |
| Python | ≥ 3.11 |
| 包管理 | 推荐 `uv`；也可用标准 `pip` |
| 浏览器 | Playwright + Chromium（**仅** `--renew-cookie` 扫码需要） |
| 账号 | 一个能正常登录微博的账号（用于扫码取 cookie） |

依赖包（见 `pyproject.toml`）：

```
requests
urllib3
playwright    # 可选依赖，仅扫码续期需要
```

---

## 2. 项目结构

```
weiboblog/
├── crawl_blog.py          # CLI 入口（唯一可执行脚本）
├── pyproject.toml         # 项目配置 + 依赖
├── README.md              # 本文档
├── API.md                 # 接口契约（URL/参数/响应，与实现语言无关）
├── ARCHITECTURE.md        # 架构 / 数据流 / 跨语言迁移
├── weibo_blog.db          # SQLite 数据库（运行时生成，不入库）
├── qrcode.png             # 扫码二维码截图（--renew-cookie 生成，可删）
└── weibo_blog/            # 核心包
    ├── __init__.py
    ├── parser.py          # mymblog 单条 JSON → 扁平 dict（纯函数）
    ├── db.py              # SQLite 建表 + 存取
    └── crawler.py         # HTTP 客户端 + 翻页 + 长文补全 + cookie 续期
```

**结构约定：**

- `crawl_blog.py` 是唯一入口，**不放置业务逻辑**，只做参数解析与分支调度。
- `weibo_blog/` 包对外暴露 `BlogCrawler` 类与若干 `db.*` 函数，其余为内部实现。
- 所有可变状态（数据库、二维码）都生成在项目根目录下，方便备份/迁移。
- 运行时产物（`weibo_blog.db`、`qrcode.png`、`__pycache__/`）不应纳入版本管理。

---

## 3. 首次部署

```bash
# 1. 安装依赖
uv sync

# 2. 安装浏览器（仅扫码登录需要）
uv run playwright install chromium

# 3. 验证环境
uv run crawl_blog.py --check-playwright
# 期望：✅ playwright Python 包可导入  /  ✅ Chromium 启动正常

# 4. 扫码登录（默认有头弹窗；无桌面环境加 --headless）
uv run crawl_blog.py --renew-cookie
# 弹出浏览器 → 用微博 APP 扫码 → 程序自动提取 cookie 入库

# 5. 首次抓取（首次无已存数据 → 自动全量回填）
uv run crawl_blog.py --uid 1401527553
```

扫码失败的退路：

- 浏览器打开 `https://weibo.com` 手动登录，从 DevTools → Application → Cookies
  复制 `.weibo.com` 域下所有键值，拼成 `k1=v1; k2=v2` 形式，运行：
  ```
  uv run crawl_blog.py --set-cookie "SUB=xxx; SUBP=yyy; ..."
  ```
- 如果已有 weibogroup 的 cookie 想复用，直接把那个 cookie 字符串用 `--set-cookie`
  写进 weiboblog 的库即可（两个项目库独立，互不影响，详见 §6）。

---

## 4. 命令清单

`crawl_blog.py` 的全部子功能。除注明外，均作用于默认数据库 `weibo_blog.db`。

### 4.1 登录与 cookie

| 命令 | 作用 |
|------|------|
| `uv run crawl_blog.py --renew-cookie` | Playwright 打开 api.weibo.com/chat 扫码页，扫码后自动存 cookie。默认**有头弹窗**。 |
| `uv run crawl_blog.py --renew-cookie --headless` | 无头模式：二维码截图存到 `qrcode.png` 并尝试用系统默认程序打开。适合无桌面 Linux。 |
| `uv run crawl_blog.py --check-playwright` | 检查 Playwright + Chromium 是否就绪，返回 exit code 0/1。 |
| `uv run crawl_blog.py --set-cookie 'SUB=xxx; SUBP=yyy'` | 手动写入 cookie，不依赖 Playwright。 |
| `uv run crawl_blog.py --db D:\path\to.db ...` | 任何命令都可加 `--db` 指定数据库路径。 |

### 4.2 抓取

| 命令 | 作用 |
|------|------|
| `uv run crawl_blog.py --uid 1401527553` | 抓取指定博主。有已存数据走**增量**，无则自动**全量回填**。 |
| `uv run crawl_blog.py --uid 1401527553 --full` | 强制**全量回填**（从 page=1 翻到空为止）。 |
| `uv run crawl_blog.py --all` | 增量抓取数据库中所有已存博主。 |

**两种抓取模式：**

| 模式 | 触发条件 | 行为 | 停止条件 |
|------|---------|------|---------|
| 全量回填 `--full` | 首次无数据 / 显式 `--full` | page=1 → 递增翻到空 | list 为空 |
| 增量 | 有已存数据且不带 `--full` | page=1 往旧翻，跳过 `post_id <= latest` | 当页末条 `post_id <= latest` |

**抓取行为要点：**

- `mymblog` 接口 list 内部是**旧 → 新**排列（第一条最旧，末条最新）；**page 递增 = 取更旧**。
- 比较新旧用 `post_id`（数字 id，单调递增），不用时间戳。
- 去重双保险：`post_id <= latest` 跳过（省请求）+ `INSERT OR IGNORE`（mblogid 唯一键兜底）。
- 长文（`isLongText=true`）逐条调 longtext 接口补全 `long_text` 字段。
- 首页第一条的 `user` 字段用于提取博主信息入库 `bloggers` 表。
- 请求间带抖动 `_jitter_sleep(0.5s ± 20%)`，规避固定间隔频控。

### 4.3 关于全量回填的耗时

全量回填会翻遍博主全部历史微博。对于 prolific 博主动辄上万条，按实测约
**400 页 / 9 分钟**（含长文补全），总量大的博主可能需要 **30-40 分钟**。

- 数据逐条 commit，**中途 Ctrl+C 不丢数据**，已写入的都保留。
- 重跑 `--full` 时已存微博靠 mblogid 去重跳过入库，但 longtext 会重新请求一遍
  （当前未做"整页已存则跳过"优化），所以重跑较慢但结果正确。

---

## 5. 数据存储

所有数据均在项目目录下，不依赖任何外部服务。

| 类型 | 位置 | 说明 |
|------|------|------|
| 数据库 | `weibo_blog.db` | SQLite。微博、博主、cookie、配置 |
| 二维码截图 | `qrcode.png` | `--renew-cookie` 生成，可随时删 |

### 5.1 数据库表结构

| 表 | 用途 |
|----|------|
| `config` | key-value：`weibo_cookie` 等 |
| `bloggers` | 博主信息（uid / 昵称 / 头像 / 认证 / raw_json） |
| `weibo_posts` | 微博主表，`mblogid` 唯一，含正文/长文/图片/视频/转发/计数/created_at(ms)/raw_json |

`weibo_posts` 关键字段：

| 字段 | 说明 |
|------|------|
| `mblogid` | 短链 ID（如 `PrP6QqqEQ`），UNIQUE 去重键 |
| `post_id` | 数字 ID，单调递增，增量比较用 |
| `text` / `text_raw` | 带标签原文 / 纯文本 |
| `long_text` | 长文全文（非长文为空） |
| `pics_json` | `[{pid, url_large, url_bmiddle, w, h}]` 精简数组 |
| `video_url` | 视频直链（stream_url） |
| `retweeted_json` | 转发原微博精简 `{post_id, mblogid, text_raw, uid, screen_name, created_at}` |
| `created_at` | 毫秒时间戳 |
| `raw_json` | 原始 JSON 永久保留，便于重新解析 |

### 5.2 时区约定

- `created_at` 字段是 **毫秒时间戳**（由 `"Wed May 14 21:15:26 +0800 2025"` 解析而来，
  含时区，按服务器返回的 +0800 计算）。
- 不依赖系统时区。

---

## 6. 与 weibogroup 的关系

| 方面 | weibogroup | weiboblog |
|------|-----------|-----------|
| 抓取对象 | 微博群聊消息 | 博主个人微博 |
| 数据库 | `weibo_im.db` | `weibo_blog.db`（独立） |
| cookie 存储 | 各自库的 config 表 | 各自库的 config 表 |
| 接口域 | `api.weibo.com`（WebIM） | `weibo.com`（主站 ajax） |
| 媒体下载 | 有（图片/视频/文件） | 无（只存 URL） |
| 全文搜索 | FTS5 | 无 |
| 消息查看器 | 有（web 前端） | 暂无（未来阶段） |

**cookie 是否互相影响？** 不影响。两个项目用各自的 SQLite 数据库，`renew_cookie`
只写 weiboblog 的库，完全不碰 weibogroup 的库。微博账号通常允许多端在线，
扫码续期不会让另一边的 cookie 失效。两边 cookie 字符串也可以互相复用
（同一个账号登录态），用 `--set-cookie` 写过去即可。

---

## 7. 注意事项与已知限制

1. **cookie 会过期**。微博 cookie 有效期有限（几天到几周），过期后 mymblog 会返回
   空 list 或 4xx。届时重新跑 `--renew-cookie` 或 `--set-cookie`。
2. **全量回填耗时**。上万条微博需数十分钟，建议后台运行；中途打断不丢数据。
3. **重跑 `--full` 会重复请求 longtext**。已存微博靠 mblogid 去重不入库，但
   `isLongText` 的仍会再调一次 longtext 接口（未做整页跳过优化）。
4. **媒体只存 URL**。图片/视频只把 URL 存进 `pics_json`/`video_url`，访问需带 cookie，
   不在抓取时下载。
5. **增量补不到更早的历史**。增量只从 page=1（最新）往旧翻到已存就停，拉不到比
   已存更早的数据——那些得靠 `--full` 全量回填。
6. **SSL 警告**。`urllib3.disable_warnings()` 已关闭证书告警，控制台干净。

---

## 8. 定时抓取（可选）

本工具不带调度器，用系统原生计划任务即可。

**Windows 任务计划（每 10 分钟增量）：**

```powershell
schtasks /create /tn "WeiboBlogCrawl" /tr "cmd /c cd /d D:\weiboblog && uv run crawl_blog.py --uid 1401527553 >> crawl.log 2>&1" /sc minute /mo 10
```

**Linux/macOS cron：**

```cron
*/10 * * * * cd /path/to/weiboblog && uv run crawl_blog.py --uid 1401527553 >> crawl.log 2>&1
```

> 定时任务建议只跑增量（不带 `--full`），首次全量回填手动跑一次即可。
> ⚠️ 计划任务环境下需确保 `uv` 在系统 PATH 中，否则用 venv 绝对路径：
> `D:\weiboblog\.venv\Scripts\python.exe crawl_blog.py ...`

---

## 9. 快速自检

部署完成后，按顺序跑这几条确认一切就绪：

```bash
uv run crawl_blog.py --check-playwright          # ① 浏览器环境
uv run crawl_blog.py --renew-cookie              # ② 扫码登录
uv run crawl_blog.py --uid 1401527553            # ③ 首次全量抓取
# 查数据库确认有数据：
uv run python -c "import sqlite3; c=sqlite3.connect('weibo_blog.db'); print('微博数:', c.execute('SELECT COUNT(*) FROM weibo_posts').fetchone()[0]); print('博主:', c.execute('SELECT screen_name FROM bloggers').fetchall())"
uv run crawl_blog.py --uid 1401527553            # ④ 再跑一次，应「新增 0 条」（增量）
```

任何一步报错，对照第 7 节排查。

---

## 10. 测试

```bash
uv run pytest tests/ -v
```

覆盖：建表 + cookie 存取、save_blogger/save_post 去重/get_latest_post_id、
parse_post 各类型字段映射（纯文本/图片/视频/转发/长文标记/source 清洗）、
BlogCrawler 的 fetch_mymblog/backfill/incremental（mock HTTP）。

测试 fixture 来自真实微博 JSON（`tests/fixtures/`）。

---

## 11. 设计与实现文档

- 设计规格：`docs/superpowers/specs/2026-06-20-weiboblog-crawler-design.md`
- 实现计划：`docs/superpowers/plans/2026-06-20-weiboblog-crawler.md`
- 接口契约：[`API.md`](./API.md)
- 架构与迁移：[`ARCHITECTURE.md`](./ARCHITECTURE.md)
