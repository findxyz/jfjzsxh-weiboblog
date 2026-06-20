# weiboblog 抓取子系统 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在新项目 `D:\weiboblog` 中实现微博博主微博抓取子系统：调用 mymblog/longtext 接口翻页抓取，解析后存入 SQLite。

**Architecture:** 三件套结构照搬 weibogroup——`parser.py`（纯函数 JSON→扁平 dict）、`db.py`（建表+存取）、`crawler.py`（BlogCrawler 类：HTTP+翻页+长文补全+编排）。翻页方向实测确认：page 递增取更旧，list 内部旧→新。复用微博账号 cookie，无需 x-xsrf-token。

**Tech Stack:** Python 3.13 + requests + SQLite（stdlib）。TDD，pytest。

**Spec:** `docs/superpowers/specs/2026-06-20-weiboblog-crawler-design.md`

**前置条件：** 已在 `D:\weiboblog` 的 master 分支，spec + fixtures 已提交。工作区干净。测试 fixture 已在 `tests/fixtures/`（post_plain/post_with_pics/post_with_video/post_with_retweet/post_longtext/longtext_response）。

**venv 与命令约定：** 项目用 uv 管理 venv，位于 `.venv/`。本计划所有 `pytest` 运行命令统一为 `D:/weiboblog/.venv/Scripts/python.exe -m pytest <args>`。首次运行前需建 venv 并装依赖（Task 0）。

---

### Task 0: 项目脚手架

**Files:**
- Create: `D:\weiboblog\pyproject.toml`
- Create: `D:\weiboblog\weibo_blog\__init__.py`
- Create: `D:\weiboblog\tests\__init__.py`
- Create: `D:\weiboblog\tests\conftest.py`
- Create: `D:\weiboblog\.gitignore`

- [ ] **Step 1: 创建 pyproject.toml**

```toml
[project]
name = "weiboblog"
version = "0.1.0"
description = "微博博主微博抓取器"
requires-python = ">=3.11"
dependencies = [
    "requests>=2.31",
    "urllib3>=2.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0"]
playwright = ["playwright>=1.40"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["weibo_blog"]
```

- [ ] **Step 2: 创建 .gitignore**

```
.venv/
__pycache__/
*.pyc
weibo_blog.db
*.db-journal
.pytest_cache/
```

- [ ] **Step 3: 创建包与测试 __init__.py**

`D:\weiboblog\weibo_blog\__init__.py`:
```python
"""微博博主微博抓取"""
```

`D:\weiboblog\tests\__init__.py`:
```python
```

- [ ] **Step 4: 创建 conftest.py（测试 DB 夹具，先放最小骨架，Task 1 补全 DDL）**

`D:\weiboblog\tests\conftest.py`:
```python
"""测试夹具"""
import sqlite3
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
```

- [ ] **Step 5: 建 venv 并安装依赖**

Run:
```bash
uv venv D:\weiboblog\.venv --python 3.13
uv pip install -e ".[dev]" --python D:/weiboblog/.venv/Scripts/python.exe
```
Expected: 安装成功，`.venv/Scripts/python.exe` 可用。

- [ ] **Step 6: 验证 pytest 可运行**

Run: `D:/weiboblog/.venv/Scripts/python.exe -m pytest tests/ -v`
Expected: `no tests ran`（0 错误，pytest 能启动）。

- [ ] **Step 7: 提交**

```bash
cd D:/weiboblog
git add pyproject.toml .gitignore weibo_blog/__init__.py tests/__init__.py tests/conftest.py
git commit -m "chore: 项目脚手架（pyproject + 包结构 + 测试夹具骨架）"
```

---

### Task 1: 数据库层 — DDL 与表结构

**Files:**
- Create: `D:\weiboblog\weibo_blog\db.py`
- Modify: `D:\weiboblog\tests\conftest.py`（补 DDL 常量与 make_test_db）
- Test: `D:\weiboblog\tests\test_db.py`

- [ ] **Step 1: 写失败测试 — 建表与 cookie 存取**

`D:\weiboblog\tests\test_db.py`:
```python
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
```

- [ ] **Step 2: 运行测试验证失败**

Run: `D:/weiboblog/.venv/Scripts/python.exe -m pytest tests/test_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'weibo_blog.db'`

- [ ] **Step 3: 实现 db.py**

`D:\weiboblog\weibo_blog\db.py`:
```python
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
```

- [ ] **Step 4: 运行测试验证通过**

Run: `D:/weiboblog/.venv/Scripts/python.exe -m pytest tests/test_db.py -v`
Expected: 2 passed

- [ ] **Step 5: 提交**

```bash
cd D:/weiboblog
git add weibo_blog/db.py tests/test_db.py
git commit -m "feat(db): 建表 DDL + cookie 存取"
```

---

### Task 2: 数据库层 — 博主与微博存取

**Files:**
- Modify: `D:\weiboblog\weibo_blog\db.py`（追加 save_blogger / save_post / get_latest_post_id）
- Test: `D:\weiboblog\tests\test_db.py`（追加用例）

- [ ] **Step 1: 写失败测试 — save_blogger**

追加到 `tests/test_db.py`:
```python
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
```

- [ ] **Step 2: 运行验证失败**

Run: `D:/weiboblog/.venv/Scripts/python.exe -m pytest tests/test_db.py::test_save_blogger -v`
Expected: FAIL — `ImportError: cannot import name 'save_blogger'`

- [ ] **Step 3: 实现 save_blogger**

追加到 `weibo_blog/db.py`:
```python
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
```

- [ ] **Step 4: 运行验证通过**

Run: `D:/weiboblog/.venv/Scripts/python.exe -m pytest tests/test_db.py::test_save_blogger -v`
Expected: 1 passed

- [ ] **Step 5: 写失败测试 — save_post 去重 + get_latest_post_id**

追加到 `tests/test_db.py`:
```python
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
```

- [ ] **Step 6: 运行验证失败**

Run: `D:/weiboblog/.venv/Scripts/python.exe -m pytest tests/test_db.py::test_save_post_dedup tests/test_db.py::test_get_latest_post_id -v`
Expected: FAIL — `ImportError: cannot import name 'save_post'`

- [ ] **Step 7: 实现 save_post + get_latest_post_id**

追加到 `weibo_blog/db.py`:
```python
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
```

- [ ] **Step 8: 运行验证通过**

Run: `D:/weiboblog/.venv/Scripts/python.exe -m pytest tests/test_db.py -v`
Expected: 4 passed

- [ ] **Step 9: 提交**

```bash
cd D:/weiboblog
git add weibo_blog/db.py tests/test_db.py
git commit -m "feat(db): save_blogger + save_post 去重 + get_latest_post_id"
```

---

### Task 3: 解析层 — created_at 与基础字段

**Files:**
- Create: `D:\weiboblog\weibo_blog\parser.py`
- Test: `D:\weiboblog\tests\test_parser.py`

- [ ] **Step 1: 写失败测试 — parse_post 纯文本微博**

`D:\weiboblog\tests\test_parser.py`:
```python
"""解析层测试"""
import json
import os
import pytest
from weibo_blog.parser import parse_post


def load_fixture(name):
    path = os.path.join(os.path.dirname(__file__), "fixtures", name)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def test_parse_post_plain():
    """纯文本微博字段映射"""
    raw = load_fixture("post_plain.json")
    p = parse_post(raw)
    assert p["mblogid"] == "PrP6QqqEQ"
    assert p["post_id"] == 5166313246299004
    assert p["uid"] == 1401527553
    assert "古埃及文字" in p["text_raw"]
    assert p["is_long_text"] == 0
    assert p["region"] == "发布于 北京"
    assert p["reposts_count"] == 5
    assert p["comments_count"] == 20
    assert p["attitudes_count"] == 393
    assert p["created_at"] == 1747226126000  # Wed May 14 21:15:26 +0800 2025 → ms
    assert p["pics_json"] == "[]"
    assert p["video_url"] == ""
    assert p["retweeted_json"] == ""
```

- [ ] **Step 2: 运行验证失败**

Run: `D:/weiboblog/.venv/Scripts/python.exe -m pytest tests/test_parser.py::test_parse_post_plain -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'weibo_blog.parser'`

- [ ] **Step 3: 实现 parser.py 基础版**

`D:\weiboblog\weibo_blog\parser.py`:
```python
"""解析 — mymblog 单条 JSON → 扁平 dict"""
from __future__ import annotations

import json
import re
from datetime import datetime


def _parse_created_at(s: str) -> int:
    """'Wed May 14 21:15:26 +0800 2025' → 毫秒时间戳"""
    dt = datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y")
    return int(dt.timestamp() * 1000)


def _clean_source(s: str) -> str:
    """去掉 <a> 标签，取纯文本来源"""
    if not s:
        return ""
    text = re.sub(r"<[^>]+>", "", s).strip()
    return text


def parse_post(raw: dict) -> dict:
    """把 mymblog 单条微博映射为扁平 dict"""
    user = raw.get("user", {}) or {}
    created_at = _parse_created_at(raw["created_at"])

    # 图片精简
    pics = []
    pic_infos = raw.get("pic_infos") or {}
    for pid, info in pic_infos.items():
        large = info.get("large", {}) or {}
        bmiddle = info.get("bmiddle", {}) or {}
        pics.append({
            "pid": pid,
            "url_large": large.get("url", ""),
            "url_bmiddle": bmiddle.get("url", ""),
            "w": large.get("width", 0),
            "h": large.get("height", 0),
        })

    # 视频直链
    page_info = raw.get("page_info") or {}
    media_info = page_info.get("media_info") or {}
    video_url = media_info.get("stream_url", "")

    # 转发原微博精简
    retweeted_json = ""
    rt = raw.get("retweeted_status")
    if rt:
        rt_user = rt.get("user", {}) or {}
        retweeted_json = json.dumps({
            "post_id": rt.get("id", 0),
            "mblogid": rt.get("mblogid", ""),
            "text_raw": rt.get("text_raw", ""),
            "uid": rt_user.get("id", 0),
            "screen_name": rt_user.get("screen_name", ""),
            "created_at": rt.get("created_at", ""),
        }, ensure_ascii=False)

    return {
        "mblogid": raw.get("mblogid", ""),
        "post_id": raw.get("id", 0),
        "uid": user.get("id", 0),
        "text": raw.get("text", ""),
        "text_raw": raw.get("text_raw", ""),
        "long_text": "",
        "is_long_text": 1 if raw.get("isLongText") else 0,
        "source": _clean_source(raw.get("source", "")),
        "region": raw.get("region_name", ""),
        "pics_json": json.dumps(pics, ensure_ascii=False),
        "video_url": video_url,
        "retweeted_json": retweeted_json,
        "reposts_count": raw.get("reposts_count", 0),
        "comments_count": raw.get("comments_count", 0),
        "attitudes_count": raw.get("attitudes_count", 0),
        "created_at": created_at,
        "raw_json": json.dumps(raw, ensure_ascii=False),
    }
```

- [ ] **Step 4: 运行验证通过**

Run: `D:/weiboblog/.venv/Scripts/python.exe -m pytest tests/test_parser.py::test_parse_post_plain -v`
Expected: 1 passed

- [ ] **Step 5: 提交**

```bash
cd D:/weiboblog
git add weibo_blog/parser.py tests/test_parser.py
git commit -m "feat(parser): parse_post 基础字段 + created_at 解析"
```

---

### Task 4: 解析层 — 图片/视频/转发/长文标记

**Files:**
- Modify: `D:\weiboblog\tests\test_parser.py`（追加用例，parser 实现已在上个任务完成）

- [ ] **Step 1: 写测试 — 图片精简**

追加到 `tests/test_parser.py`:
```python
def test_parse_post_with_pics():
    """带图微博：pics_json 精简为 pid+large+bmiddle+宽高"""
    raw = load_fixture("post_with_pics.json")
    p = parse_post(raw)
    pics = json.loads(p["pics_json"])
    assert len(pics) == 1
    assert pics[0]["pid"] == "53899d01ly1i1f0qezg9nj20mj0oytjr"
    assert pics[0]["url_large"] == "https://wx3.sinaimg.cn/orj960/53899d01ly1i1f0qezg9nj20mj0oytjr.jpg"
    assert pics[0]["url_bmiddle"] == "https://wx3.sinaimg.cn/wap360/53899d01ly1i1f0qezg9nj20mj0oytjr.jpg"
    assert pics[0]["w"] == 811
    assert pics[0]["h"] == 898
```

- [ ] **Step 2: 写测试 — 视频直链**

追加到 `tests/test_parser.py`:
```python
def test_parse_post_with_video():
    """带视频微博：video_url 提取 stream_url"""
    raw = load_fixture("post_with_video.json")
    p = parse_post(raw)
    assert "f.video.weibocdn.com" in p["video_url"]
    assert p["video_url"].endswith(".mp4") or ".mp4?" in p["video_url"]
```

- [ ] **Step 3: 写测试 — 转发精简**

追加到 `tests/test_parser.py`:
```python
def test_parse_post_with_retweet():
    """转发微博：retweeted_json 精简为 id/mblogid/text_raw/uid/screen_name/created_at"""
    raw = load_fixture("post_with_retweet.json")
    p = parse_post(raw)
    assert p["retweeted_json"]
    rt = json.loads(p["retweeted_json"])
    assert rt["post_id"] == 4636881109388307
    assert rt["mblogid"] == "KftrEDokj"
    assert "失去人性" in rt["text_raw"]
    assert rt["uid"] == 1401527553
    assert rt["screen_name"] == "tombkeeper"
    assert rt["created_at"] == "Fri May 14 22:21:08 +0800 2021"
```

- [ ] **Step 4: 写测试 — 长文标记 + source 清洗**

追加到 `tests/test_parser.py`:
```python
def test_parse_post_longtext_flag():
    """isLongText=True 标记为 1"""
    raw = load_fixture("post_longtext.json")
    p = parse_post(raw)
    assert p["is_long_text"] == 1
    assert p["long_text"] == ""  # long_text 由 crawler 补全，parser 只置空


def test_parse_post_source_clean():
    """source 去标签（post_with_pics 的 source='微博视频号'）"""
    raw = load_fixture("post_with_pics.json")
    p = parse_post(raw)
    assert p["source"] == "微博视频号"
```

- [ ] **Step 5: 运行全部解析测试**

Run: `D:/weiboblog/.venv/Scripts/python.exe -m pytest tests/test_parser.py -v`
Expected: 5 passed

- [ ] **Step 6: 提交**

```bash
cd D:/weiboblog
git add tests/test_parser.py
git commit -m "test(parser): 图片/视频/转发/长文标记/source 清洗用例"
```

---

### Task 5: 解析层 — 博主信息提取

**Files:**
- Modify: `D:\weibogroup\weibo_blog\parser.py`（追加 parse_blogger）—— 注：路径为 `D:\weiboblog\weibo_blog\parser.py`
- Test: `D:\weiboblog\tests\test_parser.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_parser.py`:
```python
from weibo_blog.parser import parse_blogger


def test_parse_blogger_from_user():
    """从微博 user 字段提取博主信息"""
    raw = load_fixture("post_plain.json")
    user = raw["user"]
    b = parse_blogger(user)
    assert b["uid"] == 1401527553
    assert b["screen_name"] == "tombkeeper"
    assert b["profile_url"] == "/u/1401527553"
    assert b["verified"] == 1
    assert "sinaimg.cn" in b["avatar"]
```

- [ ] **Step 2: 运行验证失败**

Run: `D:/weiboblog/.venv/Scripts/python.exe -m pytest tests/test_parser.py::test_parse_blogger_from_user -v`
Expected: FAIL — `ImportError: cannot import name 'parse_blogger'`

- [ ] **Step 3: 实现 parse_blogger**

追加到 `weibo_blog/parser.py`:
```python
def parse_blogger(user: dict) -> dict:
    """从 mymblog 的 user 字段提取博主信息"""
    return {
        "uid": user.get("id", 0),
        "screen_name": user.get("screen_name", ""),
        "avatar": user.get("avatar_large", "") or user.get("profile_image_url", ""),
        "profile_url": user.get("profile_url", ""),
        "verified": 1 if user.get("verified") else 0,
        "raw_json": json.dumps(user, ensure_ascii=False),
    }
```

- [ ] **Step 4: 运行验证通过**

Run: `D:/weiboblog/.venv/Scripts/python.exe -m pytest tests/test_parser.py -v`
Expected: 6 passed

- [ ] **Step 5: 提交**

```bash
cd D:/weiboblog
git add weibo_blog/parser.py tests/test_parser.py
git commit -m "feat(parser): parse_blogger 从 user 字段提取博主信息"
```

---

### Task 6: 抓取层 — HTTP 客户端与 mymblog/longtext 调用

**Files:**
- Create: `D:\weiboblog\weibo_blog\crawler.py`
- Test: `D:\weiboblog\tests\test_crawler.py`

- [ ] **Step 1: 写失败测试 — fetch_mymblog mock**

`D:\weiboblog\tests\test_crawler.py`:
```python
"""抓取层测试"""
import json
import os
from unittest.mock import patch, MagicMock
import pytest
from weibo_blog.crawler import BlogCrawler


def load_fixture(name):
    path = os.path.join(os.path.dirname(__file__), "fixtures", name)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def make_crawler(monkeypatch):
    """构造一个不走真实 HTTP 的 BlogCrawler（cookie 从 config 表读）"""
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
    # 验证请求参数
    args, kwargs = mock_get.call_args
    assert kwargs["params"]["uid"] == 1401527553
    assert kwargs["params"]["page"] == 1
    assert kwargs["params"]["feature"] == 0
```

- [ ] **Step 2: 运行验证失败**

Run: `D:/weiboblog/.venv/Scripts/python.exe -m pytest tests/test_crawler.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'weibo_blog.crawler'`

- [ ] **Step 3: 实现 crawler.py 骨架 + fetch_mymblog**

`D:\weiboblog\weibo_blog\crawler.py`:
```python
"""爬虫核心 — mymblog/longtext 接口调用 + 翻页 + 长文补全 + 编排"""
from __future__ import annotations

import json
import time
import random
import logging

import requests
import urllib3

from .parser import parse_post, parse_blogger
from .db import (
    init_db, get_cookie, set_cookie,
    save_blogger, save_post, get_latest_post_id, get_conn,
)

urllib3.disable_warnings()
log = logging.getLogger("weibo_blog.crawler")

API_BASE = "https://weibo.com"


def _jitter_sleep(base: float, jitter: float = 0.2):
    actual = base * (1 + random.uniform(-jitter, jitter))
    time.sleep(max(actual, 0.05))


def _request_with_retry(session, method, url, max_retries=3, **kwargs):
    last_err = None
    for attempt in range(max_retries + 1):
        try:
            resp = session.request(method, url, **kwargs)
            if resp.status_code >= 500 and attempt < max_retries:
                wait = (2 ** attempt) * (1 + random.uniform(0, 0.5))
                log.warning("  ↻ 5xx(%d) 重试 %d/%d", resp.status_code, attempt + 1, max_retries)
                time.sleep(wait)
                continue
            if resp.status_code == 429 and attempt < max_retries:
                wait = (4 ** attempt) * (1 + random.uniform(0, 0.5))
                log.warning("  ↻ 429 限流 重试 %d/%d", attempt + 1, max_retries)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        except (requests.ConnectionError, requests.Timeout) as e:
            last_err = e
            if attempt < max_retries:
                wait = (2 ** attempt) * (1 + random.uniform(0, 0.5))
                log.warning("  ↻ 连接错误 重试 %d/%d", attempt + 1, max_retries)
                time.sleep(wait)
                continue
            raise
    raise last_err or RuntimeError("请求失败（重试耗尽）")


class BlogCrawler:
    """微博博主微博抓取器"""

    def __init__(self, db_path: str, cookie: str = ""):
        self.conn = get_conn()
        if cookie:
            set_cookie(self.conn, cookie)
        self.cookie = cookie or get_cookie(self.conn)
        if not self.cookie:
            raise RuntimeError("Cookie 未设置。请先运行: python crawl_blog.py --set-cookie '...'")
        self.session = self._make_session(self.cookie)

    def _make_session(self, cookie: str) -> requests.Session:
        s = requests.Session()
        s.verify = False
        s.headers.update({
            "accept": "application/json, text/plain, */*",
            "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36 Edg/149.0.0.0"
            ),
            "x-requested-with": "XMLHttpRequest",
        })
        s.headers["Cookie"] = cookie
        return s

    def fetch_mymblog(self, uid: int, page: int, since_id: str = "") -> tuple[str, list[dict]]:
        """调用 mymblog 接口，返回 (next_since_id, posts_raw_list)"""
        params = {"uid": uid, "page": page, "feature": 0}
        if since_id:
            params["since_id"] = since_id
        self.session.headers["referer"] = f"{API_BASE}/u/{uid}"
        resp = _request_with_retry(
            self.session, "GET", f"{API_BASE}/ajax/statuses/mymblog",
            params=params, timeout=15,
        )
        data = resp.json().get("data", {})
        return data.get("since_id", ""), data.get("list", []) or []

    def fetch_longtext(self, mblogid: str) -> str:
        """调用 longtext 接口，返回长文全文"""
        resp = _request_with_retry(
            self.session, "GET", f"{API_BASE}/ajax/statuses/longtext",
            params={"id": mblogid}, timeout=15,
        )
        return resp.json().get("data", {}).get("longTextContent", "") or ""
```

- [ ] **Step 4: 运行验证通过**

Run: `D:/weiboblog/.venv/Scripts/python.exe -m pytest tests/test_crawler.py -v`
Expected: 1 passed

- [ ] **Step 5: 提交**

```bash
cd D:/weiboblog
git add weibo_blog/crawler.py tests/test_crawler.py
git commit -m "feat(crawler): BlogCrawler + fetch_mymblog + fetch_longtext"
```

---

### Task 7: 抓取层 — 全量回填与长文补全编排

**Files:**
- Modify: `D:\weiboblog\weibo_blog\crawler.py`（追加 crawl_blog_backfill）
- Test: `D:\weiboblog\tests\test_crawler.py`

- [ ] **Step 1: 写失败测试 — backfill 翻页停止 + 长文补全 + 博主入库**

追加到 `tests/test_crawler.py`:
```python
def test_crawl_blog_backfill(monkeypatch):
    """全量回填：翻多页直到 list 空，首页提取博主，长文补全被调用"""
    cr, conn = make_crawler(monkeypatch)
    plain = load_fixture("post_plain.json")
    longp = load_fixture("post_longtext.json")

    # page1: 两条（一条长文），page2: 空
    page1 = {"since_id": "kp2", "list": [longp, plain]}
    page2 = {"since_id": "", "list": []}
    responses = iter([page1, page2])

    longtext_resp = {"data": {"longTextContent": "这是长文全文内容"}}

    def fake_mymblog(uid, page, since_id=""):
        return next(responses)

    with patch.object(cr, "fetch_mymblog", side_effect=fake_mymblog), \
         patch.object(cr, "fetch_longtext", return_value="这是长文全文内容") as mock_lt:
        result = cr.crawl_blog_backfill(uid=1401527553)

    assert result["new"] == 2
    assert mock_lt.call_count == 1  # 只有 longp 是长文
    # 博主已入库
    row = conn.execute("SELECT * FROM bloggers WHERE uid=1401527553").fetchone()
    assert row["screen_name"] == "tombkeeper"
    # 长文 long_text 已补全
    lt_row = conn.execute(
        "SELECT long_text FROM weibo_posts WHERE mblogid='PrCC6Dh8j'"
    ).fetchone()
    assert lt_row["long_text"] == "这是长文全文内容"
```

- [ ] **Step 2: 运行验证失败**

Run: `D:/weiboblog/.venv/Scripts/python.exe -m pytest tests/test_crawler.py::test_crawl_blog_backfill -v`
Expected: FAIL — `AttributeError: 'BlogCrawler' object has no attribute 'crawl_blog_backfill'`

- [ ] **Step 3: 实现 crawl_blog_backfill**

追加到 `weibo_blog/crawler.py`（BlogCrawler 类内）:
```python
    def crawl_blog_backfill(self, uid: int) -> dict:
        """全量回填：从 page=1 翻到 list 为空"""
        new_count = 0
        page = 1
        since_id = ""
        blogger_saved = False

        while True:
            since_id, posts = self.fetch_mymblog(uid, page, since_id)
            if not posts:
                break

            # 首页提取博主信息
            if not blogger_saved and posts[0].get("user"):
                save_blogger(self.conn, parse_blogger(posts[0]["user"]))
                blogger_saved = True

            for raw in posts:  # list 旧→新，逐条处理
                parsed = parse_post(raw)
                if parsed["is_long_text"]:
                    try:
                        parsed["long_text"] = self.fetch_longtext(parsed["mblogid"])
                    except Exception as e:
                        log.warning("  长文补全失败 mblogid=%s: %s", parsed["mblogid"], e)
                if save_post(self.conn, parsed):
                    new_count += 1

            log.info("  page %d: +%d (累计 %d)", page, len(posts), new_count)
            page += 1
            _jitter_sleep(0.5)

        log.info("  回填完成 uid=%d: %d 条", uid, new_count)
        return {"new": new_count, "total": new_count}
```

- [ ] **Step 4: 运行验证通过**

Run: `D:/weiboblog/.venv/Scripts/python.exe -m pytest tests/test_crawler.py::test_crawl_blog_backfill -v`
Expected: 1 passed

- [ ] **Step 5: 提交**

```bash
cd D:/weiboblog
git add weibo_blog/crawler.py tests/test_crawler.py
git commit -m "feat(crawler): crawl_blog_backfill 全量回填 + 长文补全 + 博主入库"
```

---

### Task 8: 抓取层 — 增量更新与模式判断

**Files:**
- Modify: `D:\weiboblog\weibo_blog\crawler.py`（追加 crawl_blog_incremental + crawl_blog 入口）
- Test: `D:\weiboblog\tests\test_crawler.py`

- [ ] **Step 1: 写失败测试 — 增量跳过已存、整页已知即停**

追加到 `tests/test_crawler.py`:
```python
from weibo_blog.db import save_post


def test_crawl_blog_incremental(monkeypatch):
    """增量：list 旧→新，跳过已存 post_id，末条已存则整页停止"""
    cr, conn = make_crawler(monkeypatch)
    plain = load_fixture("post_plain.json")        # post_id=5166313246299004
    longp = load_fixture("post_longtext.json")     # post_id=5165832909360655

    # 预存 plain（视为已知最新），latest_post_id=5166313246299004
    save_post(conn, parse_post(plain))

    # page1 返回: longp(更旧, post_id=5165832909360655 < latest) + plain(已存)
    # list 旧→新: [longp, plain] —— longp 更旧，plain 是当页末条且已存 → 跳过 longp，整页停止
    page1 = {"since_id": "kp2", "list": [longp, plain]}

    with patch.object(cr, "fetch_mymblog", return_value=("kp2", page1["list"])):
        result = cr.crawl_blog_incremental(uid=1401527553)

    # longp 更旧（post_id < latest）应被跳过，plain 已存，无新增
    assert result["new"] == 0
    # 不应再翻 page2
    cnt = conn.execute("SELECT COUNT(*) FROM weibo_posts").fetchone()[0]
    assert cnt == 1  # 只有预存的 plain


def test_crawl_blog_incremental_adds_new(monkeypatch):
    """增量：有新微博时入库"""
    cr, conn = make_crawler(monkeypatch)
    plain = load_fixture("post_plain.json")        # post_id=5166313246299004（最新）
    longp = load_fixture("post_longtext.json")     # post_id=5165832909360655（更旧）

    # 预存 longp（已知），latest=5165832909360655
    save_post(conn, parse_post(longp))

    # page1: [longp(已存, 更旧), plain(新, 更新)] —— longp 跳过，plain 入库
    # 末条 plain > latest → 不停，继续；但只有一页，list 空则停
    page1 = {"since_id": "", "list": [longp, plain]}
    page2 = {"since_id": "", "list": []}
    responses = iter([("kp2", page1["list"]), ("", page2["list"])])

    with patch.object(cr, "fetch_mymblog", side_effect=lambda *a, **k: next(responses)):
        result = cr.crawl_blog_incremental(uid=1401527553)

    assert result["new"] == 1  # plain 新增
```

- [ ] **Step 2: 运行验证失败**

Run: `D:/weiboblog/.venv/Scripts/python.exe -m pytest tests/test_crawler.py::test_crawl_blog_incremental tests/test_crawler.py::test_crawl_blog_incremental_adds_new -v`
Expected: FAIL — `AttributeError: 'BlogCrawler' object has no attribute 'crawl_blog_incremental'`

- [ ] **Step 3: 实现 crawl_blog_incremental + crawl_blog**

追加到 `weibo_blog/crawler.py`（BlogCrawler 类内）:
```python
    def crawl_blog_incremental(self, uid: int) -> dict:
        """增量更新：从 page=1（最新一屏）往旧翻，跳过已存，末条已存则整页停止"""
        latest = get_latest_post_id(self.conn, uid)
        if latest is None:
            # 无已存数据，走全量
            return self.crawl_blog_backfill(uid)

        new_count = 0
        page = 1
        since_id = ""

        while True:
            since_id, posts = self.fetch_mymblog(uid, page, since_id)
            if not posts:
                break

            for raw in posts:  # list 旧→新
                post_id = raw.get("id", 0)
                if post_id <= latest:
                    continue  # 已存（更旧），跳过
                parsed = parse_post(raw)
                if parsed["is_long_text"]:
                    try:
                        parsed["long_text"] = self.fetch_longtext(parsed["mblogid"])
                    except Exception as e:
                        log.warning("  长文补全失败 mblogid=%s: %s", parsed["mblogid"], e)
                if save_post(self.conn, parsed):
                    new_count += 1

            # 末条（当页最新）<= latest → 整页都已知，停止
            last_id = posts[-1].get("id", 0)
            if last_id <= latest:
                break

            page += 1
            _jitter_sleep(0.5)

        if new_count:
            log.info("  增量完成 uid=%d: +%d 条", uid, new_count)
        return {"new": new_count, "total": new_count}

    def crawl_blog(self, uid: int, full: bool = False) -> dict:
        """抓取博主微博。full=True 或无已存数据 → 全量回填，否则增量"""
        if full or get_latest_post_id(self.conn, uid) is None:
            return self.crawl_blog_backfill(uid)
        return self.crawl_blog_incremental(uid)
```

- [ ] **Step 4: 运行验证通过**

Run: `D:/weiboblog/.venv/Scripts/python.exe -m pytest tests/test_crawler.py -v`
Expected: 4 passed

- [ ] **Step 5: 提交**

```bash
cd D:/weiboblog
git add weibo_blog/crawler.py tests/test_crawler.py
git commit -m "feat(crawler): crawl_blog_incremental 增量更新 + crawl_blog 模式判断"
```

---

### Task 9: CLI 入口

**Files:**
- Create: `D:\weiboblog\crawl_blog.py`

- [ ] **Step 1: 实现 CLI**

`D:\weiboblog\crawl_blog.py`:
```python
#!/usr/bin/env python3
"""微博博主微博抓取 CLI

用法:
    python crawl_blog.py --set-cookie 'SUB=xxx; ...'   # 设置 cookie
    python crawl_blog.py --uid 1401527553               # 增量抓取
    python crawl_blog.py --uid 1401527553 --full        # 全量回填
    python crawl_blog.py --all                          # 增量抓取所有已存博主
"""
import argparse
import logging
import sys

from weibo_blog.crawler import BlogCrawler
from weibo_blog.db import init_db, get_cookie, set_cookie, get_conn


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="微博博主微博抓取")
    parser.add_argument("--db", default="weibo_blog.db", help="数据库路径")
    parser.add_argument("--set-cookie", default="", help="设置 cookie 到数据库后退出")
    parser.add_argument("--uid", type=int, default=0, help="抓取指定博主 uid")
    parser.add_argument("--full", action="store_true", help="全量回填（而非增量）")
    parser.add_argument("--all", action="store_true", help="增量抓取所有已存博主")
    args = parser.parse_args()

    # --set-cookie: 只存 cookie 退出
    if args.set_cookie:
        import sqlite3
        conn = sqlite3.connect(args.db)
        conn.row_factory = sqlite3.Row
        init_db(conn)
        set_cookie(conn, args.set_cookie)
        print(f"cookie 已保存到 {args.db}")
        return

    # 抓取模式
    if not args.uid and not args.all:
        parser.print_help()
        sys.exit(1)

    crawler = BlogCrawler(db_path=args.db)

    if args.uid:
        result = crawler.crawl_blog(args.uid, full=args.full)
        print(f"uid={args.uid}: 新增 {result['new']} 条")
    elif args.all:
        from weibo_blog.db import get_blogger_list
        for b in get_blogger_list(crawler.conn):
            try:
                result = crawler.crawl_blog(b["uid"])
                print(f"[{b['screen_name']}] +{result['new']}")
            except Exception as e:
                logging.warning("uid=%s 抓取失败: %s", b["uid"], e)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 补 db.py 的 get_blogger_list**

追加到 `weibo_blog/db.py`:
```python
def get_blogger_list(conn: sqlite3.Connection) -> list[dict]:
    """返回所有博主"""
    rows = conn.execute("SELECT * FROM bloggers ORDER BY screen_name").fetchall()
    return [dict(r) for r in rows]
```

- [ ] **Step 3: 验证 CLI 可启动（无 cookie 时报错提示）**

Run: `D:/weiboblog/.venv/Scripts/python.exe D:/weiboblog/crawl_blog.py --help`
Expected: 打印用法说明，退出码 0。

Run: `D:/weiboblog/.venv/Scripts/python.exe D:/weiboblog/crawl_blog.py --uid 1401527553 --db :memory:`
Expected: 报错 `Cookie 未设置`（内存 DB 无 cookie），退出码非 0。

- [ ] **Step 4: 提交**

```bash
cd D:/weiboblog
git add crawl_blog.py weibo_blog/db.py
git commit -m "feat(cli): crawl_blog.py 入口（--set-cookie/--uid/--full/--all）"
```

---

### Task 10: 端到端真实抓取验证

**Files:**
- 无修改，用真实 cookie 抓几条验证

- [ ] **Step 1: 设置 cookie（复用 weibogroup 的）**

Run:
```bash
D:/weibogroup/.venv/Scripts/python.exe -c "import sqlite3; c=sqlite3.connect('D:/weibogroup/weibo_im.db'); print(c.execute(\"SELECT value FROM config WHERE key='weibo_cookie'\").fetchone()[0])" > /tmp/ck.txt
D:/weiboblog/.venv/Scripts/python.exe D:/weiboblog/crawl_blog.py --set-cookie "$(cat /tmp/ck.txt)"
rm /tmp/ck.txt
```
Expected: 打印 `cookie 已保存到 weibo_blog.db`。

- [ ] **Step 2: 真实增量抓取（默认抓最新一屏）**

Run: `D:/weiboblog/.venv/Scripts/python.exe D:/weiboblog/crawl_blog.py --uid 1401527553`
Expected: 日志显示回填若干页（首次无已存→自动全量），打印 `新增 N 条`。注意：首次会全量翻页，可能耗时较长。如想快速验证，Ctrl+C 中断后查 DB 确认有数据即可。

- [ ] **Step 3: 验证 DB 数据**

Run:
```bash
D:/weiboblog/.venv/Scripts/python.exe -c "import sqlite3; c=sqlite3.connect('D:/weiboblog/weibo_blog.db'); print('微博数:', c.execute('SELECT COUNT(*) FROM weibo_posts').fetchone()[0]); print('博主:', c.execute('SELECT screen_name,post_count FROM bloggers').fetchall()); print('长文:', c.execute('SELECT COUNT(*) FROM weibo_posts WHERE long_text!=\"\"').fetchone()[0]); print('带图:', c.execute('SELECT COUNT(*) FROM weibo_posts WHERE pics_json!=\"[]\"').fetchone()[0]); print('带视频:', c.execute('SELECT COUNT(*) FROM weibo_posts WHERE video_url!=\"\"').fetchone()[0])"
```
Expected: 微博数 > 0，博主为 tombkeeper，长文/带图/带视频各有若干条。

- [ ] **Step 4: 再次运行验证增量（无新增）**

Run: `D:/weiboblog/.venv/Scripts/python.exe D:/weiboblog/crawl_blog.py --uid 1401527553`
Expected: 打印 `新增 0 条`（增量模式，已存最新，无新微博）。

- [ ] **Step 5: 运行全部测试确认无回归**

Run: `D:/weiboblog/.venv/Scripts/python.exe -m pytest tests/ -v`
Expected: 全部 passed。

- [ ] **Step 6: 提交（DB 文件不入库，已被 .gitignore）**

```bash
cd D:/weiboblog
git status  # 确认 weibo_blog.db 不在列表
git add -A
git commit -m "chore: 端到端验证通过" --allow-empty
```

---

### Task 11: renew-cookie（Playwright 扫码续期）

**Files:**
- Modify: `D:\weiboblog\crawl_blog.py`（追加 --renew-cookie）
- Modify: `D:\weiboblog\weibo_blog\crawler.py`（追加 renew_cookie 函数）

**说明：** Playwright 浏览器交互无法自动化测试，本任务手动验证。参考 weibogroup `crawl.py` 的 `_renew_cookie` 实现，扫码入口改为 `https://weibo.com`（主站，与 mymblog 同域）。

- [ ] **Step 1: 安装 playwright 依赖**

Run:
```bash
uv pip install -e ".[playwright]" --python D:/weiboblog/.venv/Scripts/python.exe
D:/weiboblog/.venv/Scripts/python.exe -m playwright install chromium
```
Expected: chromium 浏览器下载完成。

- [ ] **Step 2: 实现 renew_cookie**

追加到 `weibo_blog/crawler.py`:
```python
def renew_cookie(headless: bool = False) -> str:
    """用 Playwright 打开 weibo.com 扫码登录，提取 cookie 返回"""
    from playwright.sync_api import sync_playwright
    import time

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto("https://weibo.com", wait_until="domcontentloaded")

        log.info("请扫码登录微博（超时 120 秒）...")
        # 等待登录成功：URL 稳定在 weibo.com 且页面含登录后元素
        for _ in range(240):  # 120 秒，每 0.5s 检查
            time.sleep(0.5)
            try:
                # 登录后会出现导航栏用户区
                if page.query_selector('a[href*="/u/"]') or "登录" not in page.title():
                    # 进一步确认 cookie 里有 SUB
                    cookies = ctx.cookies()
                    if any(c["name"] == "SUB" for c in cookies):
                        break
            except Exception:
                continue
        else:
            browser.close()
            raise RuntimeError("扫码超时（120秒）")

        raw_cookies = ctx.cookies()
        browser.close()

    deduped = {}
    for c in raw_cookies:
        if ".weibo.com" in c.get("domain", ""):
            deduped[c["name"]] = c["value"]
    cookie_str = "; ".join(f"{k}={v}" for k, v in sorted(deduped.items()))
    if "SUB" not in deduped:
        raise RuntimeError("未提取到 SUB cookie，登录可能未成功")
    return cookie_str
```

- [ ] **Step 3: 在 CLI 接入 --renew-cookie**

在 `crawl_blog.py` 的 main() 中，`--set-cookie` 分支后追加：
```python
    parser.add_argument("--renew-cookie", action="store_true", help="浏览器扫码续期 cookie")
    parser.add_argument("--headless", action="store_true", help="renew-cookie 时无头模式")
```
（加在其它 add_argument 之后）

在 `--set-cookie` 分支之后追加：
```python
    # --renew-cookie: 扫码登录提取 cookie
    if args.renew_cookie:
        import sqlite3
        from weibo_blog.crawler import renew_cookie
        cookie = renew_cookie(headless=args.headless)
        conn = sqlite3.connect(args.db)
        conn.row_factory = sqlite3.Row
        init_db(conn)
        set_cookie(conn, cookie)
        print(f"cookie 已续期并保存到 {args.db}")
        return
```

- [ ] **Step 4: 手动验证 renew-cookie**

Run: `D:/weiboblog/.venv/Scripts/python.exe D:/weiboblog/crawl_blog.py --renew-cookie`
Expected: 弹出浏览器打开 weibo.com，手机扫码登录后打印 `cookie 已续期`。

Run: `D:/weiboblog/.venv/Scripts/python.exe D:/weiboblog/crawl_blog.py --uid 1401527553`
Expected: 用新 cookie 成功抓取（证明 renew 的 cookie 有效）。

- [ ] **Step 5: 运行全部测试确认无回归**

Run: `D:/weiboblog/.venv/Scripts/python.exe -m pytest tests/ -v`
Expected: 全部 passed（renew_cookie 未被测试导入，不影响测试）。

- [ ] **Step 6: 提交**

```bash
cd D:/weiboblog
git add crawl_blog.py weibo_blog/crawler.py pyproject.toml
git commit -m "feat(cli): --renew-cookie Playwright 扫码续期"
```

---

## Self-Review

**1. Spec coverage:**
- §1 项目结构 → Task 0 ✓
- §2 bloggers/weibo_posts/config 表 → Task 1 ✓
- §2 save_blogger（信息来自首页 user）→ Task 2 + Task 5（parse_blogger）+ Task 7（backfill 调用）✓
- §2 save_post 去重（mblogid 唯一键）→ Task 2 ✓
- §3 mymblog 接口调用 → Task 6 ✓
- §3 全量回填翻页 → Task 7 ✓
- §3 增量更新（跳过已存、末条已存整页停）→ Task 8 ✓
- §3 longtext 补全 → Task 7 ✓
- §3 重试与限流 → Task 6（_request_with_retry）✓
- §4 parse_post 字段映射 → Task 3 + Task 4 ✓
- §4 created_at 解析 → Task 3 ✓
- §4 pics_json 精简 → Task 4 ✓
- §4 video_url → Task 4 ✓
- §4 retweeted_json 精简 → Task 4 ✓
- §4 source 清洗 → Task 4 ✓
- §5 CLI --set-cookie/--uid/--full/--all → Task 9 ✓
- §5 --renew-cookie → Task 11 ✓
- §5 测试用例 1-11 → Task 1-8 覆盖 ✓

**2. Placeholder scan:** 无 TBD/TODO，每个步骤有具体代码或命令。

**3. Type/名称一致性:**
- `parse_post` / `parse_blogger` 在 Task 3/5 定义，Task 7/8 调用，签名一致 ✓
- `save_post` / `save_blogger` / `get_latest_post_id` 在 Task 2 定义，Task 7/8 调用 ✓
- `fetch_mymblog` / `fetch_longtext` 在 Task 6 定义，Task 7/8 调用 ✓
- `BlogCrawler.__init__(db_path, cookie)` 在 Task 6 定义，Task 9 CLI 调用一致 ✓
- `get_conn` 在 crawler.py import，测试用 monkeypatch 替换 ✓
- `crawl_blog(uid, full)` 入口在 Task 8 定义，Task 9 CLI 调用 ✓

无问题。
