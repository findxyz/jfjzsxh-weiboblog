"""数据库 — SQLite 建表 + 存取"""
from __future__ import annotations

import json
import time
import sqlite3


def init_db(conn: sqlite3.Connection):
    """创建所有表与索引（幂等）"""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS config (
            key        TEXT PRIMARY KEY,
            value      TEXT NOT NULL DEFAULT '',
            updated_at INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS bloggers (
            uid           INTEGER PRIMARY KEY,
            screen_name   TEXT NOT NULL DEFAULT '',
            avatar        TEXT DEFAULT '',
            profile_url   TEXT DEFAULT '',
            verified      INTEGER DEFAULT 0,
            post_count    INTEGER DEFAULT 0,
            raw_json      TEXT DEFAULT '',
            created_at    INTEGER DEFAULT 0,
            updated_at    INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS weibo_posts (
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
        );

        CREATE INDEX IF NOT EXISTS idx_wp_uid   ON weibo_posts(uid);
        CREATE INDEX IF NOT EXISTS idx_wp_ctime ON weibo_posts(created_at);
        CREATE INDEX IF NOT EXISTS idx_wp_pid   ON weibo_posts(post_id);
    """)
    conn.commit()


# ── config / cookie ──────────────────────────────


def get_config(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_config(conn: sqlite3.Connection, key: str, value: str):
    now = int(time.time() * 1000)
    conn.execute("""
        INSERT INTO config (key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
    """, (key, value, now))
    conn.commit()


def get_cookie(conn: sqlite3.Connection) -> str:
    return get_config(conn, "weibo_cookie", "")


def set_cookie(conn: sqlite3.Connection, cookie: str):
    set_config(conn, "weibo_cookie", cookie)
