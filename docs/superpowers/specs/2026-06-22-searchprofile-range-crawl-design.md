# 设计：按时间范围抓取微博（searchProfile 接口）

- 日期：2026-06-22
- 范围：`weibo_blog/crawler.py` + `crawl_blog.py` + `tests/test_crawler.py` + `API.md` + `ARCHITECTURE.md` + `README.md`
- 目标：新增按时间范围抓取微博的能力，用于补全历史缺口（`--full` 全量回填因 414/风控中断后，针对性补某段时间）。

## 背景

现有抓取基于 `mymblog` 接口（`weibo.com/ajax/statuses/mymblog`），按 `page` 翻页拉博主全部微博。全量回填从 page=1 翻到空，深翻（数百页）偶发 414 或风控中断，某些时间段可能没抓全。重跑 `--full` 能补，但要重新翻遍全部历史，慢且浪费 API。

微博另有 `searchProfile` 接口（`weibo.com/ajax/statuses/searchProfile`），支持 `starttime`/`endtime` 限定时间范围，可精准补某段时间，比重跑全量快得多。

## 接口探测结论

实测 `searchProfile`（uid=1401527553，2012 全年）确认：

| 维度 | mymblog（现有） | searchProfile（新） |
|------|----------------|---------------------|
| list 内部排序 | 旧→新（首条最旧） | **新→旧**（首条最新） |
| page 递增方向 | 取更旧 | 取更旧（一致） |
| 分页游标 | since_id（必须回传） | **无**，纯 page 翻页 |
| 时间范围 | 无 | **starttime/endtime**（秒级时间戳，+0800） |
| 内容类型筛选 | 无 | hasori/hasret/hastext/haspic/hasvideo/hasmusic |
| total 字段 | 无 | **有**（字符串，如 "934"） |
| 翻页终止 | list 为空 | list 为空（一致） |
| 响应结构 | `{ok, data:{since_id, list}}` | `{ok, data:{total, absstr, list}}` |
| mblog 结构 | 标准 | 标准（与 mymblog 一致，可复用 `parse_post`） |

关键差异：**list 方向相反**（新→旧）、**无 since_id**、**有 total**。

## 方案

在现有 `BlogCrawler` 类上新增两个方法，与现有 `fetch_mymblog` / `crawl_blog_backfill` 结构对齐：

- `fetch_searchprofile(uid, page, starttime, endtime)` —— 调一次 searchProfile 拿一页，返回 `(list, total)`。对应 `fetch_mymblog`。
- `crawl_blog_by_range(uid, start_date, end_date)` —— 编排：循环调 `fetch_searchprofile` 翻页 + 解析 + 长文补全 + 入库。对应 `crawl_blog_backfill`。

CLI 新增 `--start`/`--end` 参数，与 `--uid` 配合。

**为什么不新建 `RangeCrawler` 类？** searchProfile 与 mymblog 共用 cookie/session/DB/parse_post/longtext/去重/节奏，拆开会有大量重复或频繁跨类调用。现有项目风格是「一个 `BlogCrawler` 包揽所有抓取模式」（backfill/incremental 都在里面），新建类偏离风格。YAGNI——当前只有这一个新接口。

**为什么不把 searchProfile 作为 mymblog 的降级备选？** 两个接口语义不同（mymblog 是全量时间线，searchProfile 需要时间范围），强行耦合会让逻辑复杂化。用户要的是「指定时间范围补缺口」这个明确能力，不是隐式降级。

## 改动明细

### 1. `weibo_blog/crawler.py`

#### 1.1 新增模块级函数 `_date_to_timestamp`

```python
from datetime import datetime, timezone, timedelta

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

- 时区固定 +0800（与现有 `created_at` 解析一致，不依赖系统时区）。
- `--start` 用 `end_of_day=False`（当日 00:00:00），`--end` 用 `end_of_day=True`（当日 23:59:59，含当天）。
- end 用 23:59:59 而非次日 00:00:00：更精确表达「含当天」，避免边界歧义。

#### 1.2 `BlogCrawler` 新增 `fetch_searchprofile`

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

- 六个内容类型筛选参数固定全 1（抓全，补缺口不需要按类型筛选，可配置是 YAGNI）。
- 无 since_id，URL 短，414 概率极低；若真遇到，交给编排层优雅停止（见 1.3）。
- total 是字符串（如 "934"），转 int；仅日志展示，不参与逻辑。

#### 1.3 `BlogCrawler` 新增 `crawl_blog_by_range`

```python
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

#### 1.4 与 `crawl_blog_backfill` 的对比

| 维度 | backfill | by_range |
|------|----------|----------|
| 接口 | fetch_mymblog | fetch_searchprofile |
| 翻页终止 | list 空 / 414 | list 空 / 414（同） |
| list 方向 | 旧→新 | 新→旧（不影响逐条处理） |
| 去重 | mblogid UNIQUE + INSERT OR IGNORE | 同 |
| 长文补全 | fetch_longtext + _fill_retweet_longtext | 同 |
| 博主提取 | 首页 posts[0].user | 同 |
| since_id | 必须回传 + 414 降级 | 无（更简单） |
| post_id 比较 | 有（增量模式用） | **无**（范围已由时间限定） |
| start_page | 支持（断点续抓） | **不支持**（重跑靠 mblogid 去重） |

#### 1.5 关键设计决策

1. **不用 post_id 做停止判断**。范围已由 starttime/endtime 限定，翻到 list 空就是到底。比 backfill/incremental 用 post_id 比较更简单，符合「补某段时间」语义。
2. **不支持 start_page 断点续抓**。范围抓取通常比全量回填快得多（单年 934 条 vs 全量上万条），中途断了直接重跑，靠 mblogid 去重不会重复入库。加 start_page 是 YAGNI。
3. **list 方向新→旧不影响逐条处理**。不依赖 post_id 比较停止，每条独立 parse + save，顺序无所谓。首页 posts[0] 是最新的，posts[0].user 是博主本人，用于提取博主信息没问题。
4. **total 仅日志展示**。`page %d: +%d (累计 50/934)` 让用户知道进度，不参与任何逻辑判断。
5. **414 优雅停止**。searchProfile 无 since_id、URL 短，414 概率极低，但保留与 backfill 一致的优雅停止：捕获 414，记录已抓数量，正常返回（已抓数据因逐条 commit 不丢）。
6. **返回值 `{"new": new_count, "total": new_count}`**。与 backfill/incremental 一致（那里 total 也等于 new），不改。

### 2. `crawl_blog.py`

#### 2.1 新增 CLI 参数

```python
parser.add_argument("--start", default="", help="起始日期 YYYY-MM-DD（与 --end 配合，按时间范围抓取）")
parser.add_argument("--end", default="", help="结束日期 YYYY-MM-DD（含当天）")
```

#### 2.2 互斥与校验规则

- `--start`/`--end` 必须同时指定。
- 与 `--full` 互斥（一个是按范围，一个是全量，语义冲突）。
- 与 `--all` 互斥（`--all` 是增量所有博主，不带范围）。
- 必须与 `--uid` 配合（范围抓取针对单个博主）。
- 校验 `start <= end`，否则报错退出。

#### 2.3 CLI 分支

在现有抓取分支前加（`--set-cookie`/`--check-playwright`/`--renew-cookie` 之后，抓取模式判断之前）：

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
    # 校验 start <= end
    from datetime import datetime
    d1 = datetime.strptime(args.start, "%Y-%m-%d")
    d2 = datetime.strptime(args.end, "%Y-%m-%d")
    if d1 > d2:
        parser.error("--start 不能晚于 --end")
    crawler = BlogCrawler(db_path=args.db)
    result = crawler.crawl_blog_by_range(args.uid, args.start, args.end)
    print(f"uid={args.uid} {args.start}~{args.end}: 新增 {result['new']} 条")
    return
```

### 3. `tests/test_crawler.py`

新增测试，复用现有 `make_crawler` 与 `tests/fixtures/post_*.json`（searchProfile 的 mblog 结构与 mymblog 一致，无需新增 fixture）。

#### 3.1 fetch_searchprofile 测试

| 测试 | 场景 | 断言 |
|------|------|------|
| `test_fetch_searchprofile_parses_response` | 正常返回 | list/total 正确解析；params 含 starttime/endtime/六个 has*；无 since_id |
| `test_fetch_searchprofile_total_string` | total 是字符串 "934" | 转成 int 934 |

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
    assert "since_id" not in kwargs["params"]
```

#### 3.2 crawl_blog_by_range 测试

| 测试 | 场景 | 断言 |
|------|------|------|
| `test_crawl_blog_by_range` | 两页后 list 空 | 入库正确；首页提取博主；长文补全被调用；返回 new 计数 |
| `test_crawl_blog_by_range_empty` | page=1 就空 list | 返回 new=0，不抛错 |
| `test_crawl_blog_by_range_dedup` | 范围内微博已部分存在 | mblogid 去重，只入库新的 |

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
    assert mock_lt.call_count == 1
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
    plain = load_fixture("post_plain.json")   # post_id=5166313246299004
    longp = load_fixture("post_longtext.json")  # post_id=5165832909360655
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

#### 3.3 `_date_to_timestamp` 测试

| 测试 | 断言 |
|------|------|
| `test_date_to_timestamp_start` | `"2012-01-01"` → 1325347200（当日 00:00:00 +0800） |
| `test_date_to_timestamp_end` | `"2012-12-31"` end_of_day=True → 1356969599（当日 23:59:59 +0800） |

```python
from weibo_blog.crawler import _date_to_timestamp

def test_date_to_timestamp_start():
    assert _date_to_timestamp("2012-01-01") == 1325347200

def test_date_to_timestamp_end():
    assert _date_to_timestamp("2012-12-31", end_of_day=True) == 1356969599
```

### 4. `API.md`

#### 4.1 §1 接口清单表加第 4 行

```
| 4 | 按时间范围搜索博主微博 | GET | https://weibo.com/ajax/statuses/searchProfile | Cookie | 时间范围抓取 |
```

#### 4.2 新增 §2.4

```
### 2.4 按时间范围搜索博主微博（searchProfile）

| 项 | 值 |
|----|-----|
| URL | https://weibo.com/ajax/statuses/searchProfile |
| 方法 | GET |
| 鉴权 | Cookie |
| 返回 | JSON |

Query 参数：
| 参数 | 类型 | 必需 | 说明 |
| uid | int | 是 | 博主 uid |
| page | int | 是 | 页码，递增取更旧 |
| starttime | int | 是 | 起始秒级时间戳（+0800） |
| endtime | int | 是 | 结束秒级时间戳（+0800） |
| hasori/hasret/hastext/haspic/hasvideo/hasmusic | int | 是 | 内容类型筛选，固定全 1 |

成功响应：
{ "ok":1, "data":{ "total":"934", "absstr":"", "list":[mblog...] } }

关键差异（与 §2.2 mymblog 对比）：
- list 内部「新→旧」排列（首条最新），与 mymblog 相反
- 无 since_id 分页游标，纯 page 翻页（无 414 降级问题）
- 有 total 字段（字符串），仅日志参考
- mblog 结构与 mymblog 一致，可复用 parse_post
```

### 5. `ARCHITECTURE.md`

- §2.2 调用关系图加 `--start/--end` 分支 → `crawl_blog_by_range`。
- §3.2 `BlogCrawler` 方法表加两行：`fetch_searchprofile` / `crawl_blog_by_range`。
- §5 关键算法新增 §5.3「按时间范围抓取」：说明 list 新→旧、无 since_id、不用 post_id 停止、total 仅参考。

### 6. `README.md`

在 §4.2 抓取表后新增 §4.4：

```
### 4.4 按时间范围补抓

| 命令 | 作用 |
| uv run crawl_blog.py --uid 1401527553 --start 2012-01-01 --end 2012-12-31 | 按 searchProfile 接口抓指定时间范围，补全历史缺口 |

适用场景：全量回填因 414/风控中断，某段时间没抓全，针对性补抓。
数据入同一张 weibo_posts 表，靠 mblogid 去重。
```

## 错误处理

| 场景 | 检测 | 处理 | 与现有一致 |
|------|------|------|-------------|
| Cookie 失效 | searchProfile 返回空 list | list 空 → 当到底停止，日志提示 0 条 | ✅ 与 backfill 一致 |
| 429 限流 | HTTP 429 | `_request_with_retry` 退避重试（4^n × 抖动） | ✅ 复用 |
| 5xx 服务端错 | HTTP 5xx | `_request_with_retry` 退避重试（2^n × 抖动） | ✅ 复用 |
| 414 URI 过长 | HTTP 414 | 编排层捕获，优雅停止保留已抓数据 | ✅ 与 backfill 一致（概率极低） |
| 4xx 其他 | HTTP 4xx | 不重试，直接抛 → CLI 打印 traceback 退出 | ✅ 复用 |
| 网络错/超时 | ConnectionError/Timeout | `_request_with_retry` 退避重试，3 次后抛 | ✅ 复用 |
| 长文补全失败 | fetch_longtext 异常 | 捕获，warning，留空 long_text，继续 | ✅ 与 backfill 一致 |
| 转发长文补全失败 | _fill_retweet_longtext 异常 | 捕获，warning，留空，继续 | ✅ 与 backfill 一致 |
| start > end | CLI 校验 | argparse error 退出 | 新增 |

Cookie 失效的边界情况：searchProfile 在 cookie 失效时返回空 list，与「时间范围内本来就没微博」无法区分。不做特殊检测——与现有 backfill 一致，且实际使用场景是「补已知存在数据的时间段」，空结果大概率是 cookie 问题，用户重跑 `--renew-cookie` 即可验证。加 cookie 有效性预检是 YAGNI。

## 不改的部分

- `weibo_blog/parser.py` 不动（searchProfile 的 mblog 结构与 mymblog 一致，`parse_post` / `parse_blogger` 直接复用）。
- `weibo_blog/db.py` 不动（入同一张 `weibo_posts` 表，`save_post` / `save_blogger` / `get_latest_post_id` 都不需要改）。
- 现有 `fetch_mymblog` / `crawl_blog_backfill` / `crawl_blog_incremental` / `crawl_blog` 不动。
- `server.py` / `web/` 不动（查看器读的是同一张表，新数据自动可见）。
- `tests/fixtures/` 不新增（复用现有 post_*.json）。

## 验证

1. **单元测试** `tests/test_crawler.py`：新增 7 个测试（fetch 2 + by_range 3 + 时间转换 2），`uv run pytest tests/ -v` 全绿。
2. **实跑**：`uv run crawl_blog.py --uid 1401527553 --start 2012-01-01 --end 2012-12-31`，日志显示 `预计 934 条`，逐页抓取，完成后 `新增 N 条`。
3. **去重回归**：重跑同一范围，应 `新增 0 条`（mblogid 去重）。
4. **混存验证**：查 DB 确认 searchProfile 抓的数据与 mymblog 抓的数据在同一张表，无重复。
5. **互斥校验**：`--start x --full`、`--start x`（无 --end）、`--start 2012-12-31 --end 2012-01-01` 均报错退出。
6. **文档**：`API.md` / `ARCHITECTURE.md` / `README.md` 更新到位。
