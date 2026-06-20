"""数据库层测试"""
import sqlite3
from weibo_blog.db import init_db, set_cookie, get_cookie


def test_init_db_creates_tables(mem_db):
    """init_db 应创建 bloggers / weibo_posts / config 三表"""
    init_db(mem_db)
    tables = {r[0] for r in mem_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert "bloggers" in tables
    assert "weibo_posts" in tables
    assert "config" in tables


def test_cookie_roundtrip(mem_db):
    """cookie 存取 config 表"""
    init_db(mem_db)
    set_cookie(mem_db, "SUB=abc; SUBP=xyz")
    assert get_cookie(mem_db) == "SUB=abc; SUBP=xyz"


from weibo_blog.db import save_blogger


def test_save_blogger(mem_db):
    """save_blogger 写入博主信息，重复写则更新"""
    init_db(mem_db)
    save_blogger(mem_db, {
        "uid": 1401527553,
        "screen_name": "tombkeeper",
        "avatar": "https://example.com/a.jpg",
        "profile_url": "/u/1401527553",
        "verified": 1,
        "raw_json": "{}",
    })
    row = mem_db.execute("SELECT * FROM bloggers WHERE uid=1401527553").fetchone()
    assert row["screen_name"] == "tombkeeper"
    assert row["verified"] == 1

    # 更新昵称
    save_blogger(mem_db, {
        "uid": 1401527553,
        "screen_name": "TK",
        "avatar": "",
        "profile_url": "/u/1401527553",
        "verified": 1,
        "raw_json": "{}",
    })
    row = mem_db.execute("SELECT * FROM bloggers WHERE uid=1401527553").fetchone()
    assert row["screen_name"] == "TK"


from weibo_blog.db import save_post, get_latest_post_id


def test_save_post_dedup(mem_db):
    """同 mblogid 存两次只入库一条"""
    init_db(mem_db)
    post = {
        "mblogid": "PrP6QqqEQ",
        "post_id": 5166313246299004,
        "uid": 1401527553,
        "text": "hello",
        "text_raw": "hello",
        "long_text": "",
        "is_long_text": 0,
        "source": "",
        "region": "发布于 北京",
        "pics_json": "[]",
        "video_url": "",
        "retweeted_json": "",
        "reposts_count": 0,
        "comments_count": 0,
        "attitudes_count": 0,
        "created_at": 1747226126000,
        "raw_json": "{}",
    }
    assert save_post(mem_db, post) is True
    assert save_post(mem_db, post) is False  # 重复，忽略
    cnt = mem_db.execute("SELECT COUNT(*) FROM weibo_posts WHERE mblogid='PrP6QqqEQ'").fetchone()[0]
    assert cnt == 1


def test_get_latest_post_id(mem_db):
    """get_latest_post_id 返回已存最新微博的 post_id"""
    init_db(mem_db)
    assert get_latest_post_id(mem_db, 1401527553) is None
    save_post(mem_db, {
        "mblogid": "A", "post_id": 100, "uid": 1401527553,
        "text": "", "text_raw": "", "long_text": "", "is_long_text": 0,
        "source": "", "region": "", "pics_json": "[]", "video_url": "",
        "retweeted_json": "", "reposts_count": 0, "comments_count": 0,
        "attitudes_count": 0, "created_at": 1000, "raw_json": "{}",
    })
    save_post(mem_db, {
        "mblogid": "B", "post_id": 200, "uid": 1401527553,
        "text": "", "text_raw": "", "long_text": "", "is_long_text": 0,
        "source": "", "region": "", "pics_json": "[]", "video_url": "",
        "retweeted_json": "", "reposts_count": 0, "comments_count": 0,
        "attitudes_count": 0, "created_at": 2000, "raw_json": "{}",
    })
    assert get_latest_post_id(mem_db, 1401527553) == 200


def test_composite_index_uid_ctime_exists(mem_db):
    """init_db 应建 (uid, created_at) 复合索引，供按日范围查询走索引。"""
    init_db(mem_db)
    rows = mem_db.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='weibo_posts'"
    ).fetchall()
    names = {r[0] for r in rows}
    assert "idx_wp_uid_ctime" in names
