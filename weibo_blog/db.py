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


# ── blogger ──────────────────────────────────────


def save_blogger(conn: sqlite3.Connection, blogger: dict):
    """写入/更新博主信息"""
    now = int(time.time() * 1000)
    conn.execute("""
        INSERT INTO bloggers (uid, screen_name, avatar, profile_url, verified, raw_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(uid) DO UPDATE SET
            screen_name=excluded.screen_name,
            avatar=COALESCE(NULLIF(excluded.avatar,''), bloggers.avatar),
            profile_url=excluded.profile_url,
            verified=excluded.verified,
            raw_json=excluded.raw_json,
            updated_at=excluded.updated_at
    """, (
        blogger["uid"],
        blogger.get("screen_name", ""),
        blogger.get("avatar", ""),
        blogger.get("profile_url", ""),
        blogger.get("verified", 0),
        blogger.get("raw_json", ""),
        now, now,
    ))
    conn.commit()


# ── weibo_posts ──────────────────────────────────


def save_post(conn: sqlite3.Connection, post: dict) -> bool:
    """保存一条微博，已存在（mblogid 冲突）则忽略，返回是否新增"""
    now = int(time.time() * 1000)
    try:
        cursor = conn.execute("""
            INSERT OR IGNORE INTO weibo_posts
                (mblogid, post_id, uid, text, text_raw, long_text, is_long_text,
                 source, region, pics_json, video_url, retweeted_json,
                 reposts_count, comments_count, attitudes_count,
                 created_at, saved_at, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            post["mblogid"],
            post["post_id"],
            post["uid"],
            post.get("text", ""),
            post.get("text_raw", ""),
            post.get("long_text", ""),
            post.get("is_long_text", 0),
            post.get("source", ""),
            post.get("region", ""),
            post.get("pics_json", "[]"),
            post.get("video_url", ""),
            post.get("retweeted_json", ""),
            post.get("reposts_count", 0),
            post.get("comments_count", 0),
            post.get("attitudes_count", 0),
            post["created_at"],
            now,
            post.get("raw_json", ""),
        ))
        conn.commit()
        return cursor.rowcount > 0
    except Exception:
        return False


def get_latest_post_id(conn: sqlite3.Connection, uid: int) -> int | None:
    """返回该 uid 已存微博中最大的 post_id，无则 None"""
    row = conn.execute(
        "SELECT MAX(post_id) FROM weibo_posts WHERE uid=?", (uid,)
    ).fetchone()
    return row[0] if row and row[0] is not None else None


def get_blogger_list(conn: sqlite3.Connection) -> list[dict]:
    """返回所有博主"""
    rows = conn.execute("SELECT * FROM bloggers ORDER BY screen_name").fetchall()
    return [dict(r) for r in rows]
