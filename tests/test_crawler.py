"""抓取层测试"""
import json
import os
import requests
from unittest.mock import patch, MagicMock
import pytest
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
