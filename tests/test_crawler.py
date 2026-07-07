"""抓取层测试"""
import json
import os
import requests
from unittest.mock import patch, MagicMock
import pytest
import weibo_blog.crawler as crawler_module
from weibo_blog.crawler import BlogCrawler, _is_logged_in
from weibo_blog.parser import parse_post


def load_fixture(name):
    path = os.path.join(os.path.dirname(__file__), "fixtures", name)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def make_crawler(monkeypatch):
    """构造一个不走真实 HTTP 的 BlogCrawler（cookie 从内存 DB 读）"""
    import sqlite3
    from weibo_blog.db import init_db, set_cookie
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    set_cookie(conn, "SUB=abc; SUBP=xyz")
    monkeypatch.setattr("weibo_blog.crawler.get_conn", lambda: conn)
    return BlogCrawler(db_path=":memory:"), conn


def test_fetch_mymblog_parses_response(monkeypatch):
    """fetch_mymblog 返回 (since_id, list)"""
    cr, conn = make_crawler(monkeypatch)
    sample = load_fixture("post_plain.json")
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {
        "ok": 1,
        "data": {"since_id": "abc_kp2", "list": [sample]},
    }
    fake_resp.raise_for_status = MagicMock()
    with patch.object(cr.session, "get", return_value=fake_resp) as mock_get:
        since_id, posts = cr.fetch_mymblog(uid=1401527553, page=1)
    assert since_id == "abc_kp2"
    assert len(posts) == 1
    assert posts[0]["mblogid"] == "PrP6QqqEQ"
    args, kwargs = mock_get.call_args
    assert kwargs["params"]["uid"] == 1401527553
    assert kwargs["params"]["page"] == 1
    assert kwargs["params"]["feature"] == 0


def test_fetch_mymblog_raises_only_on_explicit_login_redirect(monkeypatch):
    cr, conn = make_crawler(monkeypatch)
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.url = "https://login.sina.com.cn/sso/login.php"
    fake_resp.json.return_value = {}
    fake_resp.raise_for_status = MagicMock()

    with patch.object(cr.session, "get", return_value=fake_resp):
        with pytest.raises(crawler_module.CookieExpiredError):
            cr.fetch_mymblog(uid=1401527553, page=1)


def test_fetch_mymblog_empty_list_is_not_cookie_expiry(monkeypatch):
    cr, conn = make_crawler(monkeypatch)
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.url = "https://weibo.com/ajax/statuses/mymblog"
    fake_resp.json.return_value = {
        "ok": 1,
        "data": {"since_id": "", "list": []},
    }
    fake_resp.raise_for_status = MagicMock()

    with patch.object(cr.session, "get", return_value=fake_resp):
        since_id, posts = cr.fetch_mymblog(uid=1401527553, page=2)

    assert since_id == ""
    assert posts == []


def test_fetch_mymblog_sends_since_id_param(monkeypatch):
    """正常翻页必须回传服务端下发的 since_id（微博分页游标，不传会被风控/漏数据）"""
    cr, conn = make_crawler(monkeypatch)
    sample = load_fixture("post_plain.json")
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {
        "ok": 1,
        "data": {"since_id": "abc_kp2", "list": [sample]},
    }
    fake_resp.raise_for_status = MagicMock()
    with patch.object(cr.session, "get", return_value=fake_resp) as mock_get:
        cr.fetch_mymblog(uid=1401527553, page=5, since_id="abc_kp2")
    args, kwargs = mock_get.call_args
    assert kwargs["params"]["since_id"] == "abc_kp2"


def test_fetch_mymblog_414_falls_back_without_since_id(monkeypatch):
    """414 时降级重试一次：去掉 since_id 仅用 page，仍按微博要求先尝试带游标。

    回归：深翻（数百页）偶发 414 Request-URI Too Large。原逻辑会直接崩溃丢失
    整轮进度。期望：第一次带 since_id 抛 414 → 第二次不带 since_id 重试成功。
    """
    cr, conn = make_crawler(monkeypatch)
    sample = load_fixture("post_plain.json")

    ok_resp = MagicMock()
    ok_resp.status_code = 200
    ok_resp.json.return_value = {"ok": 1, "data": {"since_id": "next", "list": [sample]}}
    ok_resp.raise_for_status = MagicMock()

    err_resp = MagicMock(status_code=414)
    err = requests.HTTPError("414 Client Error", response=err_resp)

    # 第一次 GET 抛 414，第二次 GET 返回正常
    with patch.object(cr.session, "get", side_effect=[err, ok_resp]) as mock_get:
        since_id, posts = cr.fetch_mymblog(uid=1401527553, page=963, since_id="abc_kp963")

    assert mock_get.call_count == 2
    # 第一次带 since_id
    first_params = mock_get.call_args_list[0].kwargs["params"]
    assert first_params["since_id"] == "abc_kp963"
    # 第二次降级，不带 since_id
    second_params = mock_get.call_args_list[1].kwargs["params"]
    assert "since_id" not in second_params
    assert second_params["page"] == 963
    assert since_id == "next"
    assert len(posts) == 1


def test_crawl_blog_backfill(monkeypatch):
    """全量回填：翻多页直到 list 空，首页提取博主，长文补全被调用"""
    cr, conn = make_crawler(monkeypatch)
    plain = load_fixture("post_plain.json")
    longp = load_fixture("post_longtext.json")

    page1 = {"since_id": "kp2", "list": [longp, plain]}
    page2 = {"since_id": "", "list": []}

    # 用 side_effect 逐页返回 (since_id, list)
    pages = iter([(page1["since_id"], page1["list"]), (page2["since_id"], page2["list"])])

    with patch.object(cr, "fetch_mymblog", side_effect=lambda *a, **k: next(pages)), \
         patch.object(cr, "fetch_longtext", return_value="这是长文全文内容") as mock_lt:
        result = cr.crawl_blog_backfill(uid=1401527553)

    assert result["new"] == 2
    assert mock_lt.call_count == 1  # 只有 longp 是长文
    row = conn.execute("SELECT * FROM bloggers WHERE uid=1401527553").fetchone()
    assert row["screen_name"] == "tombkeeper"
    lt_row = conn.execute(
        "SELECT long_text FROM weibo_posts WHERE mblogid='PrCC6Dh8j'"
    ).fetchone()
    assert lt_row["long_text"] == "这是长文全文内容"


def test_crawl_blog_backfill_start_page(monkeypatch):
    """start_page=963：从指定页开始，跳过前面的页，不提取博主（已存）。

    场景：之前回填到 page 962 撞 414，想从 963 继续。
    """
    cr, conn = make_crawler(monkeypatch)
    plain = load_fixture("post_plain.json")

    # page 963 有数据，page 964 为空（到底）
    pages = iter([("kp964", [plain]), ("", [])])
    fetched_pages = []

    def fake_fetch(uid, page, since_id=""):
        fetched_pages.append(page)
        return next(pages)

    with patch.object(cr, "fetch_mymblog", side_effect=fake_fetch), \
         patch.object(cr, "fetch_longtext", return_value=""):
        result = cr.crawl_blog_backfill(uid=1401527553, start_page=963)

    assert fetched_pages == [963, 964]  # 从 963 开始，没碰 1-962
    assert result["new"] == 1
    # start_page>1 时跳过博主提取（前面页已存过）
    row = conn.execute("SELECT COUNT(*) FROM bloggers WHERE uid=1401527553").fetchone()
    assert row[0] == 0


def test_crawl_blog_backfill_stops_gracefully_on_414(monkeypatch):
    """降级重试仍 414 时优雅停止并保留已抓数据，而非整轮崩溃。

    场景：fetch_mymblog 内部 414 降级重试一次（去 since_id），仍 414 → 冒到
    编排层。编排层捕获，记录告警，正常返回已抓计数。已抓数据因 save_post 逐条
    commit 已落库，不会丢。
    """
    cr, conn = make_crawler(monkeypatch)
    plain = load_fixture("post_plain.json")

    page1 = ("kp2", [plain])
    # page2：fetch 降级重试仍 414，冒到编排层（带 .response）
    err_resp = MagicMock(status_code=414)
    err = requests.HTTPError("414 Client Error: Request-URI Too Large", response=err_resp)
    pages = iter([page1, err])

    def fake_fetch(*a, **k):
        item = next(pages)
        if isinstance(item, Exception):
            raise item
        return item

    with patch.object(cr, "fetch_mymblog", side_effect=fake_fetch), \
         patch.object(cr, "fetch_longtext", return_value=""):
        result = cr.crawl_blog_backfill(uid=1401527553)

    assert result["new"] == 1  # page1 的 plain 已入库
    cnt = conn.execute("SELECT COUNT(*) FROM weibo_posts").fetchone()[0]
    assert cnt == 1


from weibo_blog.db import save_post


def test_crawl_blog_incremental(monkeypatch):
    """增量：list 旧→新，跳过已存 post_id，末条已存则整页停止"""
    cr, conn = make_crawler(monkeypatch)
    plain = load_fixture("post_plain.json")        # post_id=5166313246299004
    longp = load_fixture("post_longtext.json")     # post_id=5165832909360655

    # 预存 plain（视为已知最新），latest_post_id=5166313246299004
    save_post(conn, parse_post(plain))

    # page1: list 旧→新 [longp(更旧, post_id<latest), plain(已存)]
    # longp 更旧跳过，plain 末条已存 → 整页停止
    page1_list = [longp, plain]

    with patch.object(cr, "fetch_mymblog", return_value=("kp2", page1_list)):
        result = cr.crawl_blog_incremental(uid=1401527553)

    assert result["new"] == 0
    cnt = conn.execute("SELECT COUNT(*) FROM weibo_posts").fetchone()[0]
    assert cnt == 1  # 只有预存的 plain


def test_crawl_blog_incremental_adds_new(monkeypatch):
    """增量：有新微博时入库"""
    cr, conn = make_crawler(monkeypatch)
    plain = load_fixture("post_plain.json")        # post_id=5166313246299004（最新）
    longp = load_fixture("post_longtext.json")     # post_id=5165832909360655（更旧）

    # 预存 longp（已知），latest=5165832909360655
    save_post(conn, parse_post(longp))

    # page1: list 旧→新 [longp(已存,更旧), plain(新,更新)]
    # longp 跳过，plain 入库；末条 plain>latest 不停，但只有一页，list 空则停
    page1_list = [longp, plain]
    page2_list = []
    pages = iter([("kp2", page1_list), ("", page2_list)])

    with patch.object(cr, "fetch_mymblog", side_effect=lambda *a, **k: next(pages)):
        result = cr.crawl_blog_incremental(uid=1401527553)

    assert result["new"] == 1  # plain 新增
    # 增量模式首页也刷新博主信息
    row = conn.execute("SELECT screen_name FROM bloggers WHERE uid=1401527553").fetchone()
    assert row["screen_name"] == "tombkeeper"


def _fake_page(url: str):
    """构造一个 page mock，evaluate("window.location.href") 返回给定 url"""
    page = MagicMock()
    page.evaluate.return_value = url
    return page


def test_is_logged_in_rejects_login_page():
    """api.weibo.com/chat 未登录态 URL 为 #/ → 未登录"""
    page = _fake_page("https://api.weibo.com/chat#/")
    assert _is_logged_in(page) is False


def test_is_logged_in_accepts_after_login():
    """扫码登录成功后 hash 路由变为 #/chat → 已登录"""
    page = _fake_page("https://api.weibo.com/chat#/chat")
    assert _is_logged_in(page) is True


def test_is_logged_in_evaluate_raises():
    """page.evaluate 抛异常（页面已关闭等）→ 视为未登录，不抛出"""
    page = MagicMock()
    page.evaluate.side_effect = Exception("page closed")
    assert _is_logged_in(page) is False


from weibo_blog.crawler import _date_to_timestamp


def test_date_to_timestamp_start():
    """'2012-01-01' → 1325347200（当日 00:00:00 +0800）"""
    assert _date_to_timestamp("2012-01-01") == 1325347200


def test_date_to_timestamp_end():
    """'2012-12-31' end_of_day=True → 1356969599（当日 23:59:59 +0800）"""
    assert _date_to_timestamp("2012-12-31", end_of_day=True) == 1356969599


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


def test_crawl_blog_by_range(monkeypatch):
    """范围抓取：按日拆分，逐日翻页到空，入库正确，首页提取博主，长文补全被调用。

    用 2 天范围（2012-01-01 ~ 2012-01-02）验证：每天独立调 searchProfile，
    每天返回一页有数据 + 一页空。两天返回相同微博，靠 mblogid 去重，第二天
    不重复入库。
    """
    cr, conn = make_crawler(monkeypatch)
    plain = load_fixture("post_plain.json")
    longp = load_fixture("post_longtext.json")
    # 每天两页：第一页有数据，第二页空
    day_pages = [([plain, longp], 934), ([], 934)]
    # 2 天 × 2 页 = 4 次调用
    pages = iter(day_pages * 2)
    with patch.object(cr, "fetch_searchprofile",
                      side_effect=lambda *a, **k: next(pages)), \
         patch.object(cr, "fetch_longtext", return_value="长文全文") as mock_lt:
        result = cr.crawl_blog_by_range(uid=1401527553,
                                        start_date="2012-01-01",
                                        end_date="2012-01-02")
    # 两天返回相同微博，mblogid 去重后只入库 2 条（第一天全部，第二天全重复）
    assert result["new"] == 2
    assert mock_lt.call_count == 2  # 每天都会尝试补全 longp 长文（fetch 在 save 前）
    row = conn.execute("SELECT screen_name FROM bloggers WHERE uid=1401527553").fetchone()
    assert row["screen_name"] == "tombkeeper"


def test_crawl_blog_by_range_single_day(monkeypatch):
    """单日范围：start==end，只抓一天，翻页到空停止。"""
    cr, conn = make_crawler(monkeypatch)
    plain = load_fixture("post_plain.json")
    pages = iter([([plain], 1), ([], 1)])
    with patch.object(cr, "fetch_searchprofile",
                      side_effect=lambda *a, **k: next(pages)), \
         patch.object(cr, "fetch_longtext", return_value=""):
        result = cr.crawl_blog_by_range(uid=1401527553,
                                        start_date="2012-01-01",
                                        end_date="2012-01-01")
    assert result["new"] == 1


def test_crawl_blog_by_range_passes_day_bounds(monkeypatch):
    """按日拆分：每天传给 fetch_searchprofile 的是当天 00:00:00~23:59:59 时间戳，
    而非整个范围的起止。验证 starttime/endtime 与日期对应。"""
    cr, conn = make_crawler(monkeypatch)
    captured = []
    pages = iter([([], 0), ([], 0)])  # 两天都空

    def fake_fetch(uid, page, starttime, endtime):
        captured.append((starttime, endtime))
        return next(pages)

    with patch.object(cr, "fetch_searchprofile", side_effect=fake_fetch):
        cr.crawl_blog_by_range(uid=1401527553,
                               start_date="2012-01-01",
                               end_date="2012-01-02")
    # 2012-01-01: 00:00:00 +0800 = 1325347200, 23:59:59 = 1325433599
    # 2012-01-02: 00:00:00 +0800 = 1325433600, 23:59:59 = 1325519999
    assert captured[0] == (1325347200, 1325433599)
    assert captured[1] == (1325433600, 1325519999)


def test_crawl_blog_by_range_empty(monkeypatch):
    """每一天都空 list：返回 new=0，不抛错（范围无微博或 cookie 失效）"""
    cr, conn = make_crawler(monkeypatch)
    with patch.object(cr, "fetch_searchprofile", return_value=([], 0)):
        result = cr.crawl_blog_by_range(uid=1401527553,
                                        start_date="2012-01-01",
                                        end_date="2012-01-03")
    assert result["new"] == 0


def test_crawl_blog_by_range_does_not_swallow_cookie_expiry(monkeypatch):
    cr, conn = make_crawler(monkeypatch)
    with patch.object(
        cr,
        "fetch_searchprofile",
        side_effect=crawler_module.CookieExpiredError("expired"),
    ):
        with pytest.raises(crawler_module.CookieExpiredError):
            cr.crawl_blog_by_range(
                uid=1401527553,
                start_date="2012-01-01",
                end_date="2012-01-01",
            )


def test_backfill_does_not_swallow_cookie_expiry_from_longtext(monkeypatch):
    cr, conn = make_crawler(monkeypatch)
    longp = load_fixture("post_longtext.json")
    pages = iter([("", [longp]), ("", [])])
    with patch.object(cr, "fetch_mymblog", side_effect=lambda *a, **k: next(pages)), \
         patch.object(
             cr,
             "fetch_longtext",
             side_effect=crawler_module.CookieExpiredError("expired"),
         ):
        with pytest.raises(crawler_module.CookieExpiredError):
            cr.crawl_blog_backfill(uid=1401527553)


def test_crawl_blog_by_range_dedup(monkeypatch):
    """范围内微博已部分存在：mblogid 去重，只入库新的（跨天重复也去重）"""
    cr, conn = make_crawler(monkeypatch)
    plain = load_fixture("post_plain.json")        # post_id=5166313246299004
    longp = load_fixture("post_longtext.json")     # post_id=5165832909360655
    # 预存 plain（视为已知）
    save_post(conn, parse_post(plain))
    # 两天都返回 [plain(已存), longp(新)]，验证跨天去重
    day_pages = [([plain, longp], 934), ([], 934)]
    pages = iter(day_pages * 2)
    with patch.object(cr, "fetch_searchprofile",
                      side_effect=lambda *a, **k: next(pages)), \
         patch.object(cr, "fetch_longtext", return_value=""):
        result = cr.crawl_blog_by_range(uid=1401527553,
                                        start_date="2012-01-01",
                                        end_date="2012-01-02")
    # 第一天 longp 新增，第二天 longp 重复（mblogid 去重）→ 只入库 1 条
    assert result["new"] == 1
    cnt = conn.execute("SELECT COUNT(*) FROM weibo_posts").fetchone()[0]
    assert cnt == 2  # plain(预存) + longp(新)


def test_crawl_blog_by_range_day_failure_continues(monkeypatch):
    """某天抓取异常不中断整体：记录告警跳过当日，后续天继续。"""
    cr, conn = make_crawler(monkeypatch)
    plain = load_fixture("post_plain.json")
    # Day1 正常（一页有 + 一页空），Day2 抛异常，Day3 正常
    day1 = [([plain], 1), ([], 1)]
    day3 = [([plain], 1)]  # plain 已存，mblogid 去重 → new=0
    pages = iter(day1 + [RuntimeError("network error")] + day3)
    fetched = []

    def fake_fetch(*a, **k):
        item = next(pages)
        fetched.append(item)
        if isinstance(item, Exception):
            raise item
        return item

    with patch.object(cr, "fetch_searchprofile", side_effect=fake_fetch), \
         patch.object(cr, "fetch_longtext", return_value=""):
        result = cr.crawl_blog_by_range(uid=1401527553,
                                        start_date="2012-01-01",
                                        end_date="2012-01-03")
    assert result["new"] == 1  # Day1 的 plain，Day3 的 plain 去重为 0


def test_crawl_blog_by_range_swaps_reversed_dates(monkeypatch):
    """start > end 时容错交换，不报错。"""
    cr, conn = make_crawler(monkeypatch)
    captured = []

    def fake_fetch(uid, page, starttime, endtime):
        captured.append(starttime)
        return [], 0

    with patch.object(cr, "fetch_searchprofile", side_effect=fake_fetch):
        cr.crawl_blog_by_range(uid=1401527553,
                               start_date="2012-01-03",
                               end_date="2012-01-01")
    # 三天，按时间正序：01-01, 01-02, 01-03
    assert captured == [1325347200, 1325433600, 1325520000]


# ── make_session cookie jar + 自动续期测试 ──────────

def test_make_session_uses_cookie_jar_not_headers():
    """_make_session 应把 cookie 写进 session.cookies 而非 headers['Cookie']。"""
    from weibo_blog.crawler import BlogCrawler
    s = BlogCrawler._make_session(None, "SUB=abc123; SUBP=def456")  # type: ignore
    assert s.cookies.get("SUB") == "abc123"
    assert s.cookies.get("SUBP") == "def456"
    assert "Cookie" not in s.headers


def test_make_session_empty_parts_skipped():
    """空片段（如尾部多余的 '; '）不应导致异常"""
    from weibo_blog.crawler import BlogCrawler
    s = BlogCrawler._make_session(None, "SUB=abc; ")  # type: ignore
    assert s.cookies.get("SUB") == "abc"


def test_serialize_cookies_roundtrip():
    from weibo_blog.crawler import BlogCrawler, _serialize_cookies
    s = BlogCrawler._make_session(None, "SUB=abc; SUBP=def")  # type: ignore
    serialized = _serialize_cookies(s)
    s2 = BlogCrawler._make_session(None, serialized)  # type: ignore
    assert s2.cookies.get("SUB") == "abc"
    assert s2.cookies.get("SUBP") == "def"


def test_renew_sub_cookie_success():
    """续期链全通（retcode:0 + arrURL）→ 返回 True"""
    from weibo_blog.crawler import BlogCrawler, _renew_sub_cookie, _serialize_cookies

    crossdomain_text = (
        'cb({"retcode":0,"arrURL":'
        '["https://passport.weibo.com/wbsso/crossdomain?action=login",'
        '"https://passport.weibo.cn/sso/crossdomain?action=login"]})'
    )
    main_session = BlogCrawler._make_session(None, "SUB=old; SUBP=old")  # type: ignore

    fake_make = MagicMock()
    tmp_session = MagicMock()
    tmp_session.cookies = main_session.cookies  # 简化：共享 jar
    calls = {"count": 0}

    def fake_get(url, **kwargs):
        calls["count"] += 1
        r = MagicMock()
        r.text = 'cb({"retcode":0})' if "updatetgt" in url else (
            crossdomain_text if "crossdomain.php" in url else 'cb({"retcode":0})')
        return r
    tmp_session.get = fake_get
    fake_make.return_value = tmp_session

    with patch("weibo_blog.crawler.requests.Session", fake_make):
        result = _renew_sub_cookie(main_session)
    assert result is True


def test_renew_sub_cookie_no_arrurl_returns_false():
    """crossdomain 未返回 arrURL → 返回 False"""
    from weibo_blog.crawler import BlogCrawler, _renew_sub_cookie
    main_session = BlogCrawler._make_session(None, "SUB=old")  # type: ignore

    def fake_get(url, **kwargs):
        r = MagicMock()
        r.text = 'cb({"retcode":500})' if "crossdomain" in url else 'cb({"retcode":0})'
        return r

    fake_make = MagicMock()
    tmp = MagicMock()
    tmp.get = fake_get
    tmp.cookies = main_session.cookies
    fake_make.return_value = tmp

    with patch("weibo_blog.crawler.requests.Session", fake_make):
        result = _renew_sub_cookie(main_session)
    assert result is False


def test_renew_sub_cookie_network_error_returns_false():
    """网络异常 → 返回 False（不抛出）"""
    from weibo_blog.crawler import BlogCrawler, _renew_sub_cookie
    main_session = BlogCrawler._make_session(None, "SUB=old")  # type: ignore

    def raise_error(url, **kwargs):
        raise requests.ConnectionError("network down")

    fake_make = MagicMock()
    tmp = MagicMock()
    tmp.get = raise_error
    tmp.cookies = main_session.cookies
    fake_make.return_value = tmp

    with patch("weibo_blog.crawler.requests.Session", fake_make):
        result = _renew_sub_cookie(main_session)
    assert result is False


def test_maybe_renew_skips_when_cookie_too_new():
    """距登录 < 5 天 → 跳过续期，返回 None"""
    import time as _time
    from weibo_blog.crawler import maybe_renew_cookie
    now = int(_time.time())
    cookie = f"SUB=abc; SSOLoginState={now}"  # 刚登录
    session = MagicMock()
    conn = MagicMock()
    assert maybe_renew_cookie(cookie, session, conn) is None
    conn.execute.assert_not_called()  # 没写 DB


def test_maybe_renew_triggers_when_cookie_old():
    """距登录 > 5 天 → 触发续期"""
    from weibo_blog.crawler import maybe_renew_cookie
    old_ts = 1000000  # 1970 年，远超 5 天
    cookie = f"SUB=abc; SSOLoginState={old_ts}"
    session = MagicMock()
    conn = MagicMock()
    with patch("weibo_blog.crawler._renew_sub_cookie", return_value=True):
        with patch("weibo_blog.crawler._serialize_cookies", return_value="SUB=new"):
            result = maybe_renew_cookie(cookie, session, conn)
    assert result == "SUB=new"
    conn.execute.assert_called()  # 写了 DB


def test_maybe_renew_no_change_returns_none():
    """续期成功但 cookie 没变 → 返回 None，不写 DB"""
    from weibo_blog.crawler import maybe_renew_cookie
    old_ts = 1000000
    cookie = f"SUB=abc; SSOLoginState={old_ts}"
    session = MagicMock()
    conn = MagicMock()
    with patch("weibo_blog.crawler._renew_sub_cookie", return_value=True):
        with patch("weibo_blog.crawler._serialize_cookies", return_value=cookie):
            result = maybe_renew_cookie(cookie, session, conn)
    assert result is None
