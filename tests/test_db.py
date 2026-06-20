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
