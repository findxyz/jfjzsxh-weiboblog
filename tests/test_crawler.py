"""抓取层测试"""
import json
import os
from unittest.mock import patch, MagicMock
import pytest
from weibo_blog.crawler import BlogCrawler
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
