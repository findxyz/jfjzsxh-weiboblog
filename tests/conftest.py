"""测试夹具"""
import os
import sqlite3
import tempfile
import pytest


@pytest.fixture
def mem_db():
    """内存 SQLite 连接，测试用"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


@pytest.fixture
def fixture_dir():
    """测试 fixture 目录路径"""
    import os
    return os.path.join(os.path.dirname(__file__), "fixtures")


# ── server 测试夹具：复刻生产 schema 的临时库 ──────────────

WEIBO_POSTS_DDL = """
CREATE TABLE weibo_posts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    mblogid         TEXT NOT NULL UNIQUE,
    post_id         INTEGER NOT NULL,
    uid             INTEGER NOT NULL,
    text            TEXT DEFAULT '',
    text_raw        TEXT DEFAULT '',
    long_text       TEXT DEFAULT '',
    is_long_text    INTEGER DEFAULT 0,
    source          TEXT DEFAULT '',
    region          TEXT DEFAULT '',
    pics_json       TEXT DEFAULT '[]',
    video_url       TEXT DEFAULT '',
    retweeted_json  TEXT DEFAULT '',
    reposts_count   INTEGER DEFAULT 0,
    comments_count  INTEGER DEFAULT 0,
    attitudes_count INTEGER DEFAULT 0,
    created_at      INTEGER NOT NULL,
    saved_at        INTEGER NOT NULL,
    raw_json        TEXT DEFAULT ''
)
"""

BLOGGERS_DDL = """
CREATE TABLE bloggers (
    uid           INTEGER PRIMARY KEY,
    screen_name   TEXT NOT NULL DEFAULT '',
    avatar        TEXT DEFAULT '',
    profile_url   TEXT DEFAULT '',
    verified      INTEGER DEFAULT 0,
    post_count    INTEGER DEFAULT 0,
    raw_json      TEXT DEFAULT '',
    created_at    INTEGER DEFAULT 0,
    updated_at    INTEGER DEFAULT 0
)
"""

CONFIG_DDL = """
CREATE TABLE config (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL DEFAULT '',
    updated_at INTEGER NOT NULL DEFAULT 0
)
"""

SERVER_INDEXES_DDL = [
    "CREATE INDEX idx_wp_uid   ON weibo_posts(uid)",
    "CREATE INDEX idx_wp_ctime ON weibo_posts(created_at)",
    "CREATE INDEX idx_wp_pid   ON weibo_posts(post_id)",
    "CREATE INDEX idx_wp_uid_ctime ON weibo_posts(uid, created_at)",
]


def make_test_db():
    """建临时文件 SQLite（生产 schema），返回 db 路径。调用方负责删除。"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.executescript(WEIBO_POSTS_DDL)
    conn.executescript(BLOGGERS_DDL)
    conn.executescript(CONFIG_DDL)
    for ddl in SERVER_INDEXES_DDL:
        conn.execute(ddl)
    conn.commit()
    conn.close()
    return path


def insert_blogger(conn, uid, screen_name, profile_url="", verified=0):
    conn.execute(
        "INSERT INTO bloggers (uid, screen_name, profile_url, verified, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?)",
        (uid, screen_name, profile_url, verified, 0, 0),
    )
    conn.commit()


def insert_posts(conn, rows):
    """批量插入 weibo_posts。rows 是 list[dict]，缺失字段用默认值。

    必填：mblogid, post_id, uid, created_at。
    """
    cols = [
        "mblogid", "post_id", "uid", "text", "text_raw", "long_text",
        "is_long_text", "source", "region", "pics_json", "video_url",
        "retweeted_json", "reposts_count", "comments_count",
        "attitudes_count", "created_at", "saved_at", "raw_json",
    ]
    defaults = {c: "" for c in cols}
    defaults.update({
        "is_long_text": 0, "pics_json": "[]", "retweeted_json": "",
        "reposts_count": 0, "comments_count": 0, "attitudes_count": 0,
        "saved_at": 0,
    })
    placeholders = ",".join("?" for _ in cols)
    sql = f"INSERT INTO weibo_posts ({','.join(cols)}) VALUES ({placeholders})"
    conn.executemany(sql, [[r.get(c, defaults[c]) for c in cols] for r in rows])
    conn.commit()
