# 按时间范围抓取微博（searchProfile）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 BlogCrawler 上新增按时间范围抓取微博的能力（searchProfile 接口），用于补全历史缺口。

**Architecture:** BlogCrawler 类新增 `fetch_searchprofile`（调一次接口拿一页）+ `crawl_blog_by_range`（翻页编排）两个方法，与现有 `fetch_mymblog` / `crawl_blog_backfill` 结构对齐。CLI 新增 `--start`/`--end` 参数。复用现有 parse_post / fetch_longtext / save_post 去重 / _jitter_sleep 节奏。数据入同一张 weibo_posts 表，靠 mblogid 去重。

**Tech Stack:** Python 3.11+ / requests / sqlite3 / pytest

**Spec:** `docs/superpowers/specs/2026-06-22-searchprofile-range-crawl-design.md`

---

## 文件结构

| 文件 | 动作 | 职责 |
|------|------|------|
| `weibo_blog/crawler.py` | 修改 | 新增 `_date_to_timestamp` 模块函数 + `BlogCrawler.fetch_searchprofile` + `BlogCrawler.crawl_blog_by_range` |
| `crawl_blog.py` | 修改 | 新增 `--start`/`--end` CLI 参数 + 互斥校验 + 分支调度 |
| `tests/test_crawler.py` | 修改 | 新增 7 个测试（fetch 2 + by_range 3 + 时间转换 2） |
| `API.md` | 修改 | §1 接口清单加第 4 行 + 新增 §2.4 |
| `ARCHITECTURE.md` | 修改 | §2.2 调用关系图 + §3.2 方法表 + §5.3 算法 |
| `README.md` | 修改 | §4.4 按时间范围补抓 |

不改：`weibo_blog/parser.py`、`weibo_blog/db.py`、`server.py`、`web/`、`tests/fixtures/`。

---

### Task 1: 时间转换函数 `_date_to_timestamp`

**Files:**
- Modify: `weibo_blog/crawler.py`（顶部 import 区 + `_jitter_sleep` 之前）
- Test: `tests/test_crawler.py`（文件末尾追加）

- [ ] **Step 1: 写失败测试**

在 `tests/test_crawler.py` 末尾追加：

```python
from weibo_blog.crawler import _date_to_timestamp


def test_date_to_timestamp_start():
    """'2012-01-01' → 1325347200（当日 00:00:00 +0800）"""
    assert _date_to_timestamp("2012-01-01") == 1325347200


def test_date_to_timestamp_end():
    """'2012-12-31' end_of_day=True → 1356969599（当日 23:59:59 +0800）"""
    assert _date_to_timestamp("2012-12-31", end_of_day=True) == 1356969599
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_crawler.py::test_date_to_timestamp_start tests/test_crawler.py::test_date_to_timestamp_end -v`
Expected: FAIL with `ImportError: cannot import name '_date_to_timestamp'`

- [ ] **Step 3: 实现 `_date_to_timestamp`**

在 `weibo_blog/crawler.py` 顶部 import 区（`import random` 之后）加：

```python
from datetime import datetime, timezone, timedelta
```

在 `API_BASE = "https://weibo.com"` 之后、`_jitter_sleep` 之前加：

```python
CST = timezone(timedelta(hours=8))


def _date_to_timestamp(date_str: str, end_of_day: bool = False) -> int:
    """'2012-01-01' → 1325347200（当日 00:00:00 +0800）
    end_of_day=True → 当日 23:59:59 +0800（如 '2012-12-31' → 1356969599）
    """
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    if end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59)
    return int(dt.replace(tzinfo=CST).timestamp())
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_crawler.py::test_date_to_timestamp_start tests/test_crawler.py::test_date_to_timestamp_end -v`
Expected: PASS（2 个测试）

- [ ] **Step 5: 提交**

```bash
git add weibo_blog/crawler.py tests/test_crawler.py
git commit -m "feat(crawler): 新增 _date_to_timestamp 日期→+0800 秒级时间戳转换"
```

---

### Task 2: `fetch_searchprofile` 方法

**Files:**
- Modify: `weibo_blog/crawler.py`（`BlogCrawler` 类，`fetch_longtext` 之后）
- Test: `tests/test_crawler.py`（末尾追加）

- [ ] **Step 1: 写失败测试**

在 `tests/test_crawler.py` 末尾追加：

```python
def test_fetch_searchprofile_parses_response(monkeypatch):
    """fetch_searchprofile 返回 (list, total)，params 含 starttime/endtime/has*，无 since_id"""
    cr, conn = make_crawler(monkeypatch)
    sample = load_fixture("post_plain.json")
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {
        "ok": 1,
        "data": {"total": "934", "list": [sample]},
    }
    fake_resp.raise_for_status = MagicMock()
    with patch.object(cr.session, "get", return_value=fake_resp) as mock_get:
        posts, total = cr.fetch_searchprofile(
            uid=1401527553, page=1, starttime=1325347200, endtime=1356969599)
    assert total == 934
    assert len(posts) == 1
    assert posts[0]["mblogid"] == "PrP6QqqEQ"
    kwargs = mock_get.call_args.kwargs
    assert kwargs["params"]["starttime"] == 1325347200
    assert kwargs["params"]["endtime"] == 1356969599
    assert kwargs["params"]["hasori"] == 1
    assert kwargs["params"]["hasret"] == 1
    assert kwargs["params"]["hastext"] == 1
    assert kwargs["params"]["haspic"] == 1
    assert kwargs["params"]["hasvideo"] == 1
    assert kwargs["params"]["hasmusic"] == 1
    assert "since_id" not in kwargs["params"]


def test_fetch_searchprofile_total_string(monkeypatch):
    """total 是字符串 '934'，应转成 int 934"""
    cr, conn = make_crawler(monkeypatch)
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {
        "ok": 1,
        "data": {"total": "934", "list": []},
    }
    fake_resp.raise_for_status = MagicMock()
    with patch.object(cr.session, "get", return_value=fake_resp):
        posts, total = cr.fetch_searchprofile(
            uid=1401527553, page=1, starttime=1325347200, endtime=1356969599)
    assert total == 934
    assert posts == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_crawler.py::test_fetch_searchprofile_parses_response tests/test_crawler.py::test_fetch_searchprofile_total_string -v`
Expected: FAIL with `AttributeError: 'BlogCrawler' object has no attribute 'fetch_searchprofile'`

- [ ] **Step 3: 实现 `fetch_searchprofile`**

在 `weibo_blog/crawler.py` 的 `BlogCrawler` 类里，`fetch_longtext` 方法之后（`# ── 编排：全量回填 ──` 注释之前）加：

```python
    def fetch_searchprofile(self, uid: int, page: int,
                            starttime: int, endtime: int) -> tuple[list[dict], int]:
        """调用 searchProfile 接口，返回 (posts_raw_list, total)

        返回的 list 内部是「新→旧」排列（首条最新），与 mymblog 相反。
        total 是该时间范围内的微博总数（字符串转 int），仅用于日志展示；
        翻页终止以 list 为空为准。
        """
        params = {
            "uid": uid, "page": page,
            "starttime": starttime, "endtime": endtime,
            "hasori": 1, "hasret": 1, "hastext": 1,
            "haspic": 1, "hasvideo": 1, "hasmusic": 1,
        }
        self.session.headers["referer"] = f"{API_BASE}/u/{uid}"
        resp = _request_with_retry(
            self.session, "GET", f"{API_BASE}/ajax/statuses/searchProfile",
            params=params, timeout=15,
        )
        data = resp.json().get("data", {}) or {}
        total = int(data.get("total", 0) or 0)  # "934" → 934
        return data.get("list", []) or [], total
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_crawler.py::test_fetch_searchprofile_parses_response tests/test_crawler.py::test_fetch_searchprofile_total_string -v`
Expected: PASS（2 个测试）

- [ ] **Step 5: 提交**

```bash
git add weibo_blog/crawler.py tests/test_crawler.py
git commit -m "feat(crawler): 新增 fetch_searchprofile 调 searchProfile 接口拿一页"
```

---

### Task 3: `crawl_blog_by_range` 编排方法

**Files:**
- Modify: `weibo_blog/crawler.py`（`BlogCrawler` 类，`crawl_blog_incremental` 之后、`crawl_blog` 之前）
- Test: `tests/test_crawler.py`（末尾追加）

- [ ] **Step 1: 写失败测试**

在 `tests/test_crawler.py` 末尾追加：

```python
def test_crawl_blog_by_range(monkeypatch):
    """范围抓取：两页后空，入库正确，首页提取博主，长文补全被调用"""
    cr, conn = make_crawler(monkeypatch)
    plain = load_fixture("post_plain.json")
    longp = load_fixture("post_longtext.json")
    page1 = ([plain, longp], 934)
    page2 = ([], 934)
    pages = iter([page1, page2])
    with patch.object(cr, "fetch_searchprofile",
                      side_effect=lambda *a, **k: next(pages)), \
         patch.object(cr, "fetch_longtext", return_value="长文全文") as mock_lt:
        result = cr.crawl_blog_by_range(uid=1401527553,
                                        start_date="2012-01-01",
                                        end_date="2012-12-31")
    assert result["new"] == 2
    assert mock_lt.call_count == 1  # 只有 longp 是长文
    row = conn.execute("SELECT screen_name FROM bloggers WHERE uid=1401527553").fetchone()
    assert row["screen_name"] == "tombkeeper"


def test_crawl_blog_by_range_empty(monkeypatch):
    """page=1 就空 list：返回 new=0，不抛错（可能是范围无微博或 cookie 失效）"""
    cr, conn = make_crawler(monkeypatch)
    with patch.object(cr, "fetch_searchprofile", return_value=([], 0)):
        result = cr.crawl_blog_by_range(uid=1401527553,
                                        start_date="2012-01-01",
                                        end_date="2012-12-31")
    assert result["new"] == 0


def test_crawl_blog_by_range_dedup(monkeypatch):
    """范围内微博已部分存在：mblogid 去重，只入库新的"""
    cr, conn = make_crawler(monkeypatch)
    plain = load_fixture("post_plain.json")        # post_id=5166313246299004
    longp = load_fixture("post_longtext.json")     # post_id=5165832909360655
    # 预存 plain（视为已知）
    save_post(conn, parse_post(plain))
    # page1: [plain(已存), longp(新)]，page2 空
    page1 = ([plain, longp], 934)
    page2 = ([], 934)
    pages = iter([page1, page2])
    with patch.object(cr, "fetch_searchprofile",
                      side_effect=lambda *a, **k: next(pages)), \
         patch.object(cr, "fetch_longtext", return_value=""):
        result = cr.crawl_blog_by_range(uid=1401527553,
                                        start_date="2012-01-01",
                                        end_date="2012-12-31")
    assert result["new"] == 1  # 只 longp 新增
    cnt = conn.execute("SELECT COUNT(*) FROM weibo_posts").fetchone()[0]
    assert cnt == 2
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_crawler.py::test_crawl_blog_by_range tests/test_crawler.py::test_crawl_blog_by_range_empty tests/test_crawler.py::test_crawl_blog_by_range_dedup -v`
Expected: FAIL with `AttributeError: 'BlogCrawler' object has no attribute 'crawl_blog_by_range'`

- [ ] **Step 3: 实现 `crawl_blog_by_range`**

在 `weibo_blog/crawler.py` 的 `BlogCrawler` 类里，`crawl_blog_incremental` 方法之后、`crawl_blog` 方法之前加：

```python
    # ── 编排：按时间范围抓取 ───────────────────────

    def crawl_blog_by_range(self, uid: int, start_date: str, end_date: str) -> dict:
        """按时间范围抓取（补全历史缺口）。

        用 searchProfile 接口，page=1 翻到 list 空。list 新→旧，逐条 parse_post
        → 长文补全 → save_post（mblogid 去重）。数据入 weibo_posts 表，与
        mymblog 抓取的数据混存，靠 mblogid UNIQUE 去重。
        """
        starttime = _date_to_timestamp(start_date, end_of_day=False)
        endtime = _date_to_timestamp(end_date, end_of_day=True)

        new_count = 0
        page = 1
        blogger_saved = False
        total = 0

        while True:
            try:
                posts, total = self.fetch_searchprofile(uid, page, starttime, endtime)
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 414:
                    log.warning("  page %d 触发 414，停止，已抓 %d 条", page, new_count)
                    break
                raise
            if not posts:
                break

            # 首页提取博主信息（复用 parse_blogger）
            if not blogger_saved and posts[0].get("user"):
                save_blogger(self.conn, parse_blogger(posts[0]["user"]))
                blogger_saved = True
            if page == 1 and total:
                log.info("  范围 %s~%s 预计 %d 条", start_date, end_date, total)

            for raw in posts:  # list 新→旧，逐条处理
                parsed = parse_post(raw)
                if parsed["is_long_text"]:
                    try:
                        parsed["long_text"] = self.fetch_longtext(parsed["mblogid"])
                    except Exception as e:
                        log.warning("  长文补全失败 mblogid=%s: %s", parsed["mblogid"], e)
                try:
                    self._fill_retweet_longtext(parsed)
                except Exception as e:
                    log.warning("  转发长文补全失败 mblogid=%s: %s", parsed["mblogid"], e)
                if save_post(self.conn, parsed):
                    new_count += 1

            log.info("  page %d: +%d (累计 %d/%s)", page, len(posts), new_count,
                     total or "?")
            page += 1
            _jitter_sleep(0.5)

        log.info("  范围抓取完成 uid=%d %s~%s: %d 条", uid, start_date, end_date, new_count)
        return {"new": new_count, "total": new_count}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_crawler.py::test_crawl_blog_by_range tests/test_crawler.py::test_crawl_blog_by_range_empty tests/test_crawler.py::test_crawl_blog_by_range_dedup -v`
Expected: PASS（3 个测试）

- [ ] **Step 5: 跑全量测试确认无回归**

Run: `uv run pytest tests/ -v`
Expected: 全部 PASS（原有测试 + 新增 7 个）

- [ ] **Step 6: 提交**

```bash
git add weibo_blog/crawler.py tests/test_crawler.py
git commit -m "feat(crawler): 新增 crawl_blog_by_range 按时间范围抓取编排"
```

---

### Task 4: CLI `--start`/`--end` 参数与分支

**Files:**
- Modify: `crawl_blog.py`（argparse 参数 + main() 分支）

- [ ] **Step 1: 新增 CLI 参数**

在 `crawl_blog.py` 的 `argparse` 参数区，`--start-page` 参数之后加：

```python
    parser.add_argument("--start", default="",
                        help="起始日期 YYYY-MM-DD（与 --end 配合，按时间范围抓取）")
    parser.add_argument("--end", default="",
                        help="结束日期 YYYY-MM-DD（含当天，与 --start 配合）")
```

- [ ] **Step 2: 新增 CLI 分支**

在 `crawl_blog.py` 的 `main()` 函数里，`# 抓取模式` 注释之前（`--renew-cookie` 分支之后）加：

```python
    # --start/--end：按时间范围抓取
    if args.start or args.end:
        if not (args.start and args.end):
            parser.error("--start 和 --end 必须同时指定")
        if args.full:
            parser.error("--start/--end 与 --full 互斥")
        if args.all:
            parser.error("--start/--end 与 --all 互斥")
        if not args.uid:
            parser.error("--start/--end 需配合 --uid")
        from datetime import datetime as _dt
        d1 = _dt.strptime(args.start, "%Y-%m-%d")
        d2 = _dt.strptime(args.end, "%Y-%m-%d")
        if d1 > d2:
            parser.error("--start 不能晚于 --end")
        crawler = BlogCrawler(db_path=args.db)
        result = crawler.crawl_blog_by_range(args.uid, args.start, args.end)
        print(f"uid={args.uid} {args.start}~{args.end}: 新增 {result['new']} 条")
        return
```

- [ ] **Step 3: 手动验证互斥校验**

Run: `uv run crawl_blog.py --uid 1401527553 --start 2012-01-01`
Expected: 报错 `--start 和 --end 必须同时指定` 并退出

Run: `uv run crawl_blog.py --uid 1401527553 --start 2012-01-01 --end 2012-12-31 --full`
Expected: 报错 `--start/--end 与 --full 互斥` 并退出

Run: `uv run crawl_blog.py --start 2012-01-01 --end 2012-12-31`
Expected: 报错 `--start/--end 需配合 --uid` 并退出

Run: `uv run crawl_blog.py --uid 1401527553 --start 2012-12-31 --end 2012-01-01`
Expected: 报错 `--start 不能晚于 --end` 并退出

- [ ] **Step 4: 提交**

```bash
git add crawl_blog.py
git commit -m "feat(cli): 新增 --start/--end 按时间范围抓取参数与互斥校验"
```

---

### Task 5: 更新 API.md

**Files:**
- Modify: `API.md`

- [ ] **Step 1: §1 接口清单表加第 4 行**

找到 `API.md` §1 的接口清单表（`| # | 名称 | 方法 | URL | 鉴权 | 用途 |` 开头那块），在 longtext 行之后加：

```
| 4 | 按时间范围搜索博主微博 | GET | `https://weibo.com/ajax/statuses/searchProfile` | Cookie | 时间范围抓取 |
```

- [ ] **Step 2: 新增 §2.4**

找到 `API.md` §2.3 长文全文的结尾（`---` 分隔线之前），在其后加新的 §2.4：

```markdown
### 2.4 按时间范围搜索博主微博（searchProfile）

| 项 | 值 |
|----|-----|
| URL | `https://weibo.com/ajax/statuses/searchProfile` |
| 方法 | `GET` |
| 鉴权 | Cookie（需含 `SUB`） |
| 返回 | JSON |

**Query 参数**

| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `uid` | int | 是 | 博主 uid |
| `page` | int | 是 | 页码，递增取更旧 |
| `starttime` | int | 是 | 起始秒级时间戳（+0800） |
| `endtime` | int | 是 | 结束秒级时间戳（+0800） |
| `hasori` | int | 是 | 含原创，固定 1 |
| `hasret` | int | 是 | 含转发，固定 1 |
| `hastext` | int | 是 | 含文本，固定 1 |
| `haspic` | int | 是 | 含图片，固定 1 |
| `hasvideo` | int | 是 | 含视频，固定 1 |
| `hasmusic` | int | 是 | 含音乐，固定 1 |

**成功响应（200）**

```json
{
  "ok": 1,
  "data": {
    "total": "934",
    "absstr": "",
    "list": [ {mblog}, ... ]
  }
}
```

**字段语义**

| 字段 | 类型 | 含义 | 备注 |
|------|------|------|------|
| `total` | string | 时间范围内微博总数 | 字符串需转 int；仅日志参考，不精确 |
| `absstr` | string | 摘要 | 实测为空 |
| `list` | array | mblog 数组 | 结构与 §2.2 mymblog 一致，可复用 `parse_post` |

**关键差异（与 §2.2 mymblog 对比）**

| 维度 | mymblog | searchProfile |
|------|---------|---------------|
| list 内部排序 | 旧→新（首条最旧） | **新→旧**（首条最新） |
| 分页游标 | since_id（必须回传） | **无**，纯 page 翻页 |
| 时间范围 | 无 | starttime/endtime |
| 414 降级 | 有（since_id 导致 URI 过长） | **不需要**（无 since_id，URL 短） |

**前置条件**

- Cookie 有效。
- `uid` 是公开可见的博主。
- `starttime`/`endtime` 用秒级时间戳（非毫秒），时区 +0800。

**注意事项**

| 场景 | 表现/处理 |
|------|----------|
| Cookie 失效 | 200 但 `list` 为空（与 mymblog 一致） |
| 时间范围内无微博 | `list` 为空（与 cookie 失效无法区分） |
| 翻页终止 | `list` 为空即到底 |
| 频率建议 | 每页间 ≥ 0.5s（带 ±20% 抖动） |
```

- [ ] **Step 3: 提交**

```bash
git add API.md
git commit -m "docs(api): 新增 §2.4 searchProfile 接口规范"
```

---

### Task 6: 更新 ARCHITECTURE.md

**Files:**
- Modify: `ARCHITECTURE.md`

- [ ] **Step 1: §2.2 调用关系图加分支**

找到 `ARCHITECTURE.md` §2.2 的调用关系图（`crawl_blog.main()` 开头的代码块），在 `└─ 否则 → BlogCrawler(db_path)` 之前加 `--start/--end` 分支：

```text
crawl_blog.main()
  ├─ 若 --renew-cookie → renew_cookie() (Playwright)
  │                        ↓
  │                   db.set_cookie()
  │
  ├─ 若 --check-playwright → check_playwright() → exit 0/1
  │
  ├─ 若 --set-cookie → db.set_cookie() → return
  │
  ├─ 若 --start/--end → BlogCrawler(db_path)
  │                       └─ crawl_blog_by_range(uid, start_date, end_date)
  │                            └─ fetch_searchprofile 翻页
  │
  └─ 否则 → BlogCrawler(db_path)
              └─ crawl_blog(uid, full)
                   ├─ full=True 或无已存 → crawl_blog_backfill()
                   └─ 否则 → crawl_blog_incremental()
```

- [ ] **Step 2: §3.2 BlogCrawler 方法表加两行**

找到 `ARCHITECTURE.md` §3.2 的 `BlogCrawler` 类方法表，在 `crawl_blog` 行之后加：

```text
| `fetch_searchprofile(uid, page, starttime, endtime)` | §2.4 searchProfile → (list, total) |
| `crawl_blog_by_range(uid, start_date, end_date)` | 按时间范围抓取（补全历史缺口） |
```

- [ ] **Step 3: §5 新增 §5.3 算法**

找到 `ARCHITECTURE.md` §5.2 重试退避之后，在 §6 状态机之前加 §5.3：

```markdown
### 5.3 按时间范围抓取（searchProfile）

与 mymblog 的 page 翻页不同点：

```
searchProfile：
   page=1   时间范围内最新的一屏（list 内部新→旧）
   page=2   更旧的一屏
   page=N   list 为空 → 到底
```

**与全量回填的差异：**

| 维度 | 全量回填 | 按时间范围 |
|------|---------|-----------|
| list 方向 | 旧→新 | 新→旧（不影响逐条处理） |
| since_id | 必须回传 + 414 降级 | 无（纯 page） |
| 停止判断 | list 空 | list 空（同） |
| post_id 比较 | 增量模式用 | **不用**（范围已由时间限定） |
| start_page | 支持（断点续抓） | 不支持（重跑靠 mblogid 去重） |
| total 字段 | 无 | 有（仅日志参考） |

**不用 post_id 做停止判断**：范围已由 starttime/endtime 限定，翻到 list 空就是到底。去重仍靠 mblogid UNIQUE + INSERT OR IGNORE 兜底（三层去重的第 3 层）。
```

- [ ] **Step 4: 提交**

```bash
git add ARCHITECTURE.md
git commit -m "docs(arch): 新增 searchProfile 范围抓取的调用关系/方法表/算法"
```

---

### Task 7: 更新 README.md

**Files:**
- Modify: `README.md`

- [ ] **Step 1: §4.2 抓取表后新增 §4.4**

找到 `README.md` §4.2 抓取表之后、§4.3 关于全量回填的耗时 之前，插入 §4.4：

```markdown
### 4.4 按时间范围补抓

| 命令 | 作用 |
|------|------|
| `uv run crawl_blog.py --uid 1401527553 --start 2012-01-01 --end 2012-12-31` | 按 searchProfile 接口抓指定时间范围，补全历史缺口 |

**适用场景**：全量回填因 414/风控中断，某段时间没抓全，针对性补抓。比重跑
`--full` 快得多（单年约 934 条 vs 全量上万条）。

- 数据入同一张 `weibo_posts` 表，靠 mblogid 去重，与 mymblog 抓的数据混存。
- `--start`/`--end` 格式 `YYYY-MM-DD`，`--end` 含当天（23:59:59 +0800）。
- 与 `--full`/`--all` 互斥，必须配合 `--uid`。
- 重跑同一范围不会重复入库（mblogid 去重）。
```

- [ ] **Step 2: 提交**

```bash
git add README.md
git commit -m "docs(readme): 新增 §4.4 按时间范围补抓说明"
```

---

### Task 8: 端到端验证

**Files:** 无（验证任务）

- [ ] **Step 1: 跑全量测试**

Run: `uv run pytest tests/ -v`
Expected: 全部 PASS（原有 + 新增 7 个）

- [ ] **Step 2: 实跑小范围抓取**

Run: `uv run crawl_blog.py --uid 1401527553 --start 2012-11-01 --end 2012-11-30`
Expected: 日志显示 `范围 2012-11-01~2012-11-30 预计 N 条`，逐页抓取，完成后 `uid=1401527553 2012-11-01~2012-11-30: 新增 N 条`

- [ ] **Step 3: 去重回归**

Run: `uv run crawl_blog.py --uid 1401527553 --start 2012-11-01 --end 2012-11-30`
Expected: `新增 0 条`（mblogid 去重生效）

- [ ] **Step 4: 混存验证**

Run: `uv run python -c "import sqlite3; c=sqlite3.connect('weibo_blog.db'); print('2012年11月微博数:', c.execute(\"SELECT COUNT(*) FROM weibo_posts WHERE created_at >= 1351728000000 AND created_at < 1354320000000\").fetchone()[0])"`
Expected: 显示非零数字（确认 searchProfile 抓的数据与 mymblog 抓的数据在同一张表）

> 注：`1351728000000` = 2012-11-01 00:00:00 +0800 毫秒，`1354320000000` = 2012-12-01 00:00:00 +0800 毫秒

- [ ] **Step 5: 互斥校验回归**

Run: `uv run crawl_blog.py --uid 1401527553 --start 2012-01-01`
Expected: 报错 `--start 和 --end 必须同时指定`

Run: `uv run crawl_blog.py --uid 1401527553 --start 2012-01-01 --end 2012-12-31 --full`
Expected: 报错 `--start/--end 与 --full 互斥`

Run: `uv run crawl_blog.py --start 2012-01-01 --end 2012-12-31`
Expected: 报错 `--start/--end 需配合 --uid`

Run: `uv run crawl_blog.py --uid 1401527553 --start 2012-12-31 --end 2012-01-01`
Expected: 报错 `--start 不能晚于 --end`

- [ ] **Step 6: 确认无需提交（验证任务无文件改动）**

如有残留产物（如 `__pycache__`），不影响。git status 应为 clean（所有改动已在 Task 1-7 提交）。

---

## 自审

**1. Spec 覆盖：**
- §1 CLI 参数与时间转换 → Task 1 + Task 4 ✅
- §2 fetch_searchprofile → Task 2 ✅
- §3 crawl_blog_by_range → Task 3 ✅
- §4 错误处理 → Task 3（414 优雅停止）+ Task 4（互斥校验）✅
- §5 测试 → Task 1/2/3（共 7 个测试）✅
- §6 文档 → Task 5/6/7 ✅
- 验证 → Task 8 ✅

**2. 占位符扫描：** 无 TBD/TODO，每个 step 都有完整代码或命令。✅

**3. 类型一致性：**
- `_date_to_timestamp(date_str, end_of_day)` 签名在 Task 1 定义，Task 3 调用一致 ✅
- `fetch_searchprofile(uid, page, starttime, endtime) -> (list, total)` 在 Task 2 定义，Task 3 调用一致 ✅
- `crawl_blog_by_range(uid, start_date, end_date) -> {"new", "total"}` 在 Task 3 定义，Task 4 调用一致 ✅
- 测试里 `fetch_searchprofile` 返回 `(list, total)` 元组，与实现一致 ✅
