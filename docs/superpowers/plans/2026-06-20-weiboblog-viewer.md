# WeiboBlog 消息查看器 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 weiboblog 增加一个本地只读 web 查看器（`server.py` + `web/`），用微博橙 + 卡片流风格浏览已抓取的博主微博，与 weibogroup 的 Google 蓝 + 聊天气泡查看器视觉区分。

**Architecture:** 单文件 `server.py`，纯标准库（`ThreadingHTTPServer` + `BaseHTTPRequestHandler`），DB 以 `mode=ro` 只读连接注入 Handler 类属性。前端 `web/` 为原生 JS（无框架、无构建）。无分页、无触顶触底加载——点开某日一次查全部，倒序展示。端口 8766（避开 weibogroup 的 8765，可同时开）。

**Tech Stack:** Python 3.11+ 标准库（http.server, sqlite3, calendar, json, mimetypes, argparse）、原生 HTML/CSS/JS、pytest（测试）。零新依赖。

**参考实现：** weibogroup 的 `D:\weibogroup\server.py`（605 行）与 `D:\weibogroup\web\`（index.html/app.js/style.css）提供完全同构的模式——`open_db`、`_cst_month_bounds`、`_cst_day_bounds`、`_escape_like`、`_snippet`、`_serve_static`、`Handler`、`make_server`、`main`。weiboblog 版本更简（无 `serve_media`、无游标分页、无 `_has_more`、无 `query_around`、无 `_build_response`）。

**设计规格：** `docs/superpowers/specs/2026-06-20-weiboblog-viewer-design.md`

---

## 文件结构

| 文件 | 职责 | 操作 |
|------|------|------|
| `weibo_blog/db.py` | 补复合索引 `idx_wp_uid_ctime` | 修改 |
| `server.py` | HTTP server + 全部查询函数 + 路由 | 新建 |
| `web/index.html` | 页面骨架（顶栏 + 侧栏 + 卡片流 + 搜索浮层 + lightbox） | 新建 |
| `web/style.css` | 微博橙主题、卡片流样式 | 新建 |
| `web/app.js` | 前端逻辑（加载月份/展开日期/渲染卡片/搜索/定位高亮/lightbox） | 新建 |
| `tests/conftest.py` | 增加 `make_test_db`/`insert_posts`/`insert_blogger` 夹具 | 修改 |
| `tests/test_server.py` | server 端到端测试（真实端口 + urllib） | 新建 |
| `README.md` | §2 项目结构补 `server.py`/`web/`；新增查看器使用说明 | 修改 |

**测试约定（与 weibogroup `tests/test_server.py` 一致）：** unittest 风格，`_ServerTestBase` 启动真实 server 在 127.0.0.1 随机端口，`http.client.HTTPConnection` 发请求断言。子类重写 `make_data(conn)` 写入测试数据。`conftest.py` 提供建表夹具（与 weiboblog 生产 schema 完全一致）。

---

### Task 1: 数据库补复合索引

**Files:**
- Modify: `weibo_blog/db.py:70-73`
- Test: `tests/test_db.py`

- [ ] **Step 1: 写失败测试——验证复合索引存在**

把以下测试追加到 `tests/test_db.py` 末尾：

```python
def test_composite_index_uid_ctime_exists():
    """init_db 应建 (uid, created_at) 复合索引，供按日范围查询走索引。"""
    import sqlite3
    from weibo_blog.db import init_db
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='weibo_posts'"
    ).fetchall()
    names = {r[0] for r in rows}
    assert "idx_wp_uid_ctime" in names
    conn.close()
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/test_db.py::test_composite_index_uid_ctime_exists -v`
Expected: FAIL with AssertionError（索引不存在）

- [ ] **Step 3: 在 init_db 补索引**

修改 `weibo_blog/db.py`，在 `init_db` 的 `executescript` 内、现有三条 `CREATE INDEX` 之后补一条：

```sql
        CREATE INDEX IF NOT EXISTS idx_wp_uid_ctime ON weibo_posts(uid, created_at);
```

修改后该段为：

```python
        CREATE INDEX IF NOT EXISTS idx_wp_uid   ON weibo_posts(uid);
        CREATE INDEX IF NOT EXISTS idx_wp_ctime ON weibo_posts(created_at);
        CREATE INDEX IF NOT EXISTS idx_wp_pid   ON weibo_posts(post_id);
        CREATE INDEX IF NOT EXISTS idx_wp_uid_ctime ON weibo_posts(uid, created_at);
    """)
    conn.commit()
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `uv run pytest tests/test_db.py -v`
Expected: 全部 PASS（含新测试与既有 db 测试）

- [ ] **Step 5: 提交**

```bash
git add weibo_blog/db.py tests/test_db.py
git commit -m "feat(db): 补 weibo_posts(uid, created_at) 复合索引"
```

---

### Task 2: 测试夹具——临时数据库 + 数据插入

**Files:**
- Modify: `tests/conftest.py`
- Test: `tests/test_server.py`（本任务先建夹具，Task 3 起用）

- [ ] **Step 1: 在 conftest.py 追加夹具函数**

在 `tests/conftest.py` 末尾追加。这些夹具复刻 weiboblog 生产 schema（与 `weibo_blog/db.py` 的建表语句一致），供 server 测试构造临时库：

```python
# ── server 测试夹具：复刻生产 schema 的临时库 ──────────────
import os
import tempfile

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
```

- [ ] **Step 2: 验证夹具可导入且能建库**

Run: `uv run python -c "import tests.conftest as c; import sqlite3; p=c.make_test_db(); conn=sqlite3.connect(p); print(conn.execute('SELECT count(*) FROM weibo_posts').fetchone()[0]); import os; os.remove(p)"`
Expected: 输出 `0`（空库建好，表存在）无报错

- [ ] **Step 3: 提交**

```bash
git add tests/conftest.py
git commit -m "test: 增加 server 测试夹具（临时库 + 数据插入）"
```

---

### Task 3: server.py 骨架——HTTP 路由 + 静态资源 + open_db + CST 边界

**Files:**
- Create: `server.py`
- Test: `tests/test_server.py`（本任务建文件并写骨架测试）

- [ ] **Step 1: 写失败测试——server 骨架与静态资源**

新建 `tests/test_server.py`：

```python
"""server.py 端到端测试——启动真实 server，urllib/http.client 发请求断言。"""
import http.client
import json
import os
import socket
import sqlite3
import threading
import unittest

from tests.conftest import make_test_db, insert_posts, insert_blogger
import server


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _ServerTestBase(unittest.TestCase):
    """启动一个 server 供子类测试。子类重写 make_data() 写入测试数据。"""

    def make_data(self, conn):
        pass

    def setUp(self):
        self.db_path = make_test_db()
        conn = sqlite3.connect(self.db_path)
        self.make_data(conn)
        conn.close()
        self.port = _free_port()
        self.httpd = server.make_server("127.0.0.1", self.port, self.db_path)
        self.t = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.t.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.httpd.db_conn.close()
        os.remove(self.db_path)

    def _get(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read()
        self._last_content_type = resp.getheader("Content-Type")
        conn.close()
        return resp.status, body

    def _get_json(self, path):
        status, body = self._get(path)
        return status, json.loads(body.decode("utf-8"))


class ServerSkeletonTest(_ServerTestBase):
    def test_index_html_served(self):
        status, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"<html", body)

    def test_unknown_api_returns_404(self):
        status, _ = self._get("/api/unknown")
        self.assertEqual(status, 404)

    def test_404_for_missing(self):
        status, _ = self._get("/nope")
        self.assertEqual(status, 404)

    def test_static_css_content_type(self):
        status, body = self._get("/web/style.css")
        self.assertEqual(status, 200)
        self.assertIn("text/css", self._last_content_type)

    def test_static_path_traversal_403(self):
        status, _ = self._get("/web/../../../etc/passwd")
        self.assertIn(status, (403, 404))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试，确认失败（server 模块不存在）**

Run: `uv run pytest tests/test_server.py -v`
Expected: FAIL / ERROR——`ModuleNotFoundError: No module named 'server'`

- [ ] **Step 3: 写 server.py 骨架（含路由、静态资源、open_db、CST 边界、make_server、main，但 API 路由先只返回 404）**

新建 `server.py`：

```python
"""WeiboBlog 消息查看器 —— 本地只读 web 服务。

标准库实现，零外部依赖。只读打开 weibo_blog.db，提供 JSON API 与静态前端。
启动：uv run server.py   访问：http://127.0.0.1:8766
"""
import argparse
import calendar
import json
import mimetypes
import os
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


def _cst_month_bounds(month):
    """CST(+8) 某月（YYYY-MM）的 [start_ms, end_ms) 时间戳区间。

    created_at 存的是 UTC 毫秒，CST 整点零分对应 UTC-8h，故月首 CST 00:00
    的 UTC 毫秒 = (calendar.timegm(月首) - 8*3600) * 1000。end 为次月首，开区间。
    """
    y, m = map(int, month.split("-"))
    start_cst = calendar.timegm((y, m, 1, 0, 0, 0, 0, 0, 0))
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    end_cst = calendar.timegm((ny, nm, 1, 0, 0, 0, 0, 0, 0))
    return (start_cst - 8 * 3600) * 1000, (end_cst - 8 * 3600) * 1000


def _cst_day_bounds(date):
    """CST(+8) 某天（YYYY-MM-DD）的 [start_ms, end_ms) 时间戳区间。"""
    y, m, d = map(int, date.split("-"))
    start_cst = calendar.timegm((y, m, d, 0, 0, 0, 0, 0, 0))
    return (start_cst - 8 * 3600) * 1000, (start_cst - 8 * 3600 + 86400) * 1000


# ---------- 数据库 ----------

def open_db(db_path):
    """以只读模式打开 SQLite，返回连接。row_factory 便于按列名取值。"""
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


# ---------- HTTP Handler ----------

class Handler(BaseHTTPRequestHandler):
    # 子类在 make_server 中注入 db_path 与 conn
    db_path = None

    def log_message(self, *args):
        pass  # 静默，避免刷屏

    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, body, status=200, content_type="text/plain; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/":
            self._serve_static("index.html")
            return
        if path.startswith("/web/"):
            self._serve_static(path[len("/web/"):])
            return
        if path.startswith("/api/"):
            self._route_api(path, qs)
            return
        self._send_text("Not Found", status=404)

    def _route_api(self, path, qs):
        # 各 query_* 函数在后续 Task 中接入
        self._send_json({"error": "not found"}, status=404)

    def _serve_static(self, rel):
        # 防目录穿越
        rel = rel.replace("\\", "/").lstrip("/")
        full = os.path.normpath(os.path.join(WEB_DIR, rel))
        if not full.startswith(os.path.normpath(WEB_DIR)):
            self._send_text("Forbidden", status=403)
            return
        if not os.path.isfile(full):
            self._send_text("Not Found", status=404)
            return
        ctype, _ = mimetypes.guess_type(full)
        with open(full, "rb") as f:
            self._send_text(f.read(), content_type=ctype or "application/octet-stream")


# ---------- 工厂 ----------

def make_server(host, port, db_path):
    """构造 ThreadingHTTPServer，把 db_path/conn 绑到 Handler 类上。

    返回的 httpd 附带 .db_conn 属性，便于测试在 shutdown 后关闭连接。
    """
    Handler.db_path = db_path
    Handler.conn = open_db(db_path)
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.db_conn = Handler.conn
    return httpd


def main():
    parser = argparse.ArgumentParser(description="WeiboBlog 消息查看器")
    default_db = os.path.join(os.path.dirname(os.path.abspath(__file__)), "weibo_blog.db")
    parser.add_argument("--db", default=default_db, help="SQLite 数据库路径")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()

    httpd = make_server(args.host, args.port, args.db)
    print(f"查看器已启动：http://{args.host}:{args.port}  (db={args.db})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
        httpd.shutdown()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 运行骨架测试**

`test_static_css_content_type` 与 `test_static_path_traversal_403` 此时因 `web/style.css` 不存在会失败（路径穿越测试返回 404 也算通过，因断言含 404；但 css 测试需要文件）。先创建一个占位 `web/style.css` 让骨架测试全绿，正式样式在 Task 6 写。

新建 `web/style.css`（占位，Task 6 替换）：

```css
/* 占位，Task 6 替换为微博橙主题 */
```

Run: `uv run pytest tests/test_server.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add server.py web/style.css tests/test_server.py
git commit -m "feat(server): HTTP 骨架——路由/静态资源/open_db/CST 边界"
```

---

### Task 4: API——/api/blogger

**Files:**
- Modify: `server.py`
- Test: `tests/test_server.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_server.py` 的 `ServerSkeletonTest` 之后追加：

```python
class BloggerApiTest(_ServerTestBase):
    def make_data(self, conn):
        insert_blogger(conn, 1401527553, "tombkeeper",
                       profile_url="/u/1401527553", verified=1)

    def test_blogger_returns_fields(self):
        status, data = self._get_json("/api/blogger")
        self.assertEqual(status, 200)
        self.assertEqual(data, {
            "uid": 1401527553,
            "screen_name": "tombkeeper",
            "profile_url": "/u/1401527553",
            "verified": 1,
        })


class BloggerEmptyTest(_ServerTestBase):
    # 不重写 make_data → 空库（无 blogger 记录）
    def test_blogger_empty_db_returns_404(self):
        status, data = self._get_json("/api/blogger")
        self.assertEqual(status, 404)
        self.assertIn("error", data)
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/test_server.py::BloggerApiTest tests/test_server.py::BloggerEmptyTest -v`
Expected: FAIL——返回 404（`/api/blogger` 未实现，命中 `_route_api` 兜底 404）

- [ ] **Step 3: 实现 query_blogger 并接入路由**

在 `server.py` 的 `# ---------- 数据库 ----------` 段之后、`# ---------- HTTP Handler ----------` 之前，插入查询函数区：

```python
# ---------- 查询函数 ----------

def query_blogger(conn):
    """取一条博主（单博主场景）。空库返回 None。"""
    row = conn.execute(
        "SELECT uid, screen_name, profile_url, verified FROM bloggers LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return {
        "uid": row["uid"],
        "screen_name": row["screen_name"],
        "profile_url": row["profile_url"],
        "verified": row["verified"],
    }
```

修改 `Handler._route_api`：

```python
    def _route_api(self, path, qs):
        conn = self.conn
        try:
            if path == "/api/blogger":
                b = query_blogger(conn)
                if b is None:
                    self._send_json({"error": "no blogger"}, status=404)
                else:
                    self._send_json(b)
            else:
                self._send_json({"error": "not found"}, status=404)
        except Exception as e:
            self._send_json({"error": str(e)}, status=500)
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `uv run pytest tests/test_server.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add server.py tests/test_server.py
git commit -m "feat(server): /api/blogger 接口"
```

---

### Task 5: API——/api/months + /api/dates

**Files:**
- Modify: `server.py`
- Test: `tests/test_server.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_server.py` 追加。时间戳说明：`1750200000000` → 2025-06-18 10:40 CST，`1750113600000` → 2025-06-17 10:40 CST。

```python
class MonthsDatesApiTest(_ServerTestBase):
    def make_data(self, conn):
        insert_posts(conn, [
            {"mblogid": "p1", "post_id": 1, "uid": 1401527553,
             "text_raw": "hi", "created_at": 1750200000000},  # 2025-06-18
            {"mblogid": "p2", "post_id": 2, "uid": 1401527553,
             "text_raw": "yo", "created_at": 1750113600000},  # 2025-06-17
            {"mblogid": "p3", "post_id": 3, "uid": 1401527553,
             "text_raw": "x", "created_at": 1750113600000},   # 2025-06-17
            {"mblogid": "p4", "post_id": 4, "uid": 1401527553,
             "text_raw": "z", "created_at": 1747430400000},   # 2025-05-17 00:00 CST
        ])

    def test_months_aggregated_desc(self):
        status, data = self._get_json("/api/months")
        self.assertEqual(status, 200)
        self.assertEqual(data, [
            {"month": "2025-06", "count": 3},
            {"month": "2025-05", "count": 1},
        ])

    def test_dates_for_month_desc(self):
        status, data = self._get_json("/api/dates?month=2025-06")
        self.assertEqual(status, 200)
        self.assertEqual(data, [
            {"date": "2025-06-18", "count": 1},
            {"date": "2025-06-17", "count": 2},
        ])

    def test_dates_other_month_no_leak(self):
        status, data = self._get_json("/api/dates?month=2025-05")
        self.assertEqual(status, 200)
        self.assertEqual(data, [{"date": "2025-05-17", "count": 1}])

    def test_dates_missing_month_returns_400(self):
        status, data = self._get_json("/api/dates")
        self.assertEqual(status, 400)
        self.assertIn("error", data)

    def test_dates_empty_month_returns_empty(self):
        status, data = self._get_json("/api/dates?month=2024-01")
        self.assertEqual(status, 200)
        self.assertEqual(data, [])
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/test_server.py::MonthsDatesApiTest -v`
Expected: FAIL（接口未实现，返回 404）

- [ ] **Step 3: 实现 query_months + query_month_days 并接入路由**

在 `server.py` 查询函数区追加：

```python
def query_months(conn):
    """按 CST 月聚合微博数，倒序返回。供左栏初始加载。"""
    rows = conn.execute(
        "SELECT strftime('%Y-%m', datetime(created_at/1000,'unixepoch','+8 hours')) AS m, "
        "COUNT(*) AS c FROM weibo_posts "
        "GROUP BY m ORDER BY m DESC"
    ).fetchall()
    return [{"month": r["m"], "count": r["c"]} for r in rows]


def query_month_days(conn, month):
    """指定月份（YYYY-MM）的每日微博数，倒序返回。

    用 CST 月区间 [start_ms, end_ms) 做 created_at 范围过滤，命中
    (uid, created_at) 复合索引；每日标签由 date() 表达式给出。
    """
    start_ms, end_ms = _cst_month_bounds(month)
    rows = conn.execute(
        "SELECT date(datetime(created_at/1000,'unixepoch','+8 hours')) AS d, "
        "COUNT(*) AS c FROM weibo_posts "
        "WHERE created_at>=? AND created_at<? "
        "GROUP BY d ORDER BY d DESC",
        (start_ms, end_ms),
    ).fetchall()
    return [{"date": r["d"], "count": r["c"]} for r in rows]
```

修改 `Handler._route_api`，在 `/api/blogger` 分支后追加：

```python
            elif path == "/api/months":
                self._send_json(query_months(conn))
            elif path == "/api/dates":
                month = qs.get("month", [None])[0]
                if not month:
                    self._send_json({"error": "missing month"}, status=400)
                else:
                    self._send_json(query_month_days(conn, month))
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `uv run pytest tests/test_server.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add server.py tests/test_server.py
git commit -m "feat(server): /api/months + /api/dates 接口"
```

---

### Task 6: API——/api/posts（含 pics 解析）

**Files:**
- Modify: `server.py`
- Test: `tests/test_server.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_server.py` 追加。BASE=1750113600000 即 2025-06-17 00:00 CST。

```python
class PostsApiTest(_ServerTestBase):
    BASE = 1750113600000  # 2025-06-17 00:00 CST

    def make_data(self, conn):
        base = self.BASE
        insert_posts(conn, [
            {"mblogid": "a1", "post_id": 1, "uid": 1401527553,
             "text_raw": "早", "source": "微博 weibo.com",
             "reposts_count": 1, "comments_count": 2, "attitudes_count": 3,
             "created_at": base + 1000},  # 06-17
            {"mblogid": "a2", "post_id": 2, "uid": 1401527553,
             "text_raw": "午", "long_text": "这是长文",
             "is_long_text": 1,
             "pics_json": '[{"pid":"x","url_bmiddle":"http://img/b.jpg","url_large":"http://img/l.jpg","w":100,"h":80}]',
             "created_at": base + 2000},  # 06-17，更新
            {"mblogid": "a3", "post_id": 3, "uid": 1401527553,
             "text_raw": "次日", "created_at": base + 86400000},  # 06-18
        ])

    def test_posts_desc_newest_first(self):
        status, data = self._get_json("/api/posts?date=2025-06-17")
        self.assertEqual(status, 200)
        self.assertEqual(data["date"], "2025-06-17")
        mids = [p["mblogid"] for p in data["posts"]]
        self.assertEqual(mids, ["a2", "a1"])  # 倒序，最新在上

    def test_posts_no_leak_across_days(self):
        status, data = self._get_json("/api/posts?date=2025-06-18")
        self.assertEqual(status, 200)
        mids = [p["mblogid"] for p in data["posts"]]
        self.assertEqual(mids, ["a3"])

    def test_posts_missing_date_returns_400(self):
        status, data = self._get_json("/api/posts")
        self.assertEqual(status, 400)
        self.assertIn("error", data)

    def test_posts_empty_returns_empty_array_not_404(self):
        status, data = self._get_json("/api/posts?date=2024-01-01")
        self.assertEqual(status, 200)
        self.assertEqual(data["posts"], [])

    def test_posts_pics_parsed_to_array(self):
        status, data = self._get_json("/api/posts?date=2025-06-17")
        self.assertEqual(status, 200)
        a2 = [p for p in data["posts"] if p["mblogid"] == "a2"][0]
        self.assertEqual(a2["pics"], [
            {"pid": "x", "url_bmiddle": "http://img/b.jpg",
             "url_large": "http://img/l.jpg", "w": 100, "h": 80},
        ])
        # 无图微博 pics 为空数组
        a1 = [p for p in data["posts"] if p["mblogid"] == "a1"][0]
        self.assertEqual(a1["pics"], [])

    def test_posts_field_shape(self):
        status, data = self._get_json("/api/posts?date=2025-06-17")
        self.assertEqual(status, 200)
        p = data["posts"][0]
        # 返回字段集合（不含 text/HTML 版、不含 raw_json）
        self.assertEqual(set(p.keys()), {
            "mblogid", "text_raw", "long_text", "is_long_text",
            "pics", "source", "reposts_count", "comments_count",
            "attitudes_count", "created_at",
        })
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/test_server.py::PostsApiTest -v`
Expected: FAIL（接口未实现）

- [ ] **Step 3: 实现 query_posts（含 pics_json 解析）并接入路由**

在 `server.py` 查询函数区追加：

```python
def _parse_pics(pics_json):
    """pics_json 字符串解析为 list[dict]，失败/空返回 []。"""
    if not pics_json:
        return []
    try:
        pics = json.loads(pics_json)
        return pics if isinstance(pics, list) else []
    except (ValueError, TypeError):
        return []


def query_posts(conn, date):
    """某 CST 日期的全部微博，倒序（最新在上）。

    用 CST 当日区间 [start_ms, end_ms) 做 created_at 范围过滤，命中
    (uid, created_at) 复合索引。pics_json 在 server 端解析成数组返回。
    无数据返回空 posts 列表（非 404）。
    """
    start_ms, end_ms = _cst_day_bounds(date)
    rows = conn.execute(
        "SELECT mblogid, text_raw, long_text, is_long_text, pics_json, source, "
        "reposts_count, comments_count, attitudes_count, created_at "
        "FROM weibo_posts "
        "WHERE created_at>=? AND created_at<? "
        "ORDER BY created_at DESC",
        (start_ms, end_ms),
    ).fetchall()
    posts = [{
        "mblogid": r["mblogid"],
        "text_raw": r["text_raw"],
        "long_text": r["long_text"],
        "is_long_text": r["is_long_text"],
        "pics": _parse_pics(r["pics_json"]),
        "source": r["source"],
        "reposts_count": r["reposts_count"],
        "comments_count": r["comments_count"],
        "attitudes_count": r["attitudes_count"],
        "created_at": r["created_at"],
    } for r in rows]
    return {"date": date, "posts": posts}
```

修改 `Handler._route_api`，在 `/api/dates` 分支后追加：

```python
            elif path == "/api/posts":
                date = qs.get("date", [None])[0]
                if not date:
                    self._send_json({"error": "missing date"}, status=400)
                else:
                    self._send_json(query_posts(conn, date))
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `uv run pytest tests/test_server.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add server.py tests/test_server.py
git commit -m "feat(server): /api/posts 接口（倒序 + pics 服务端解析）"
```

---

### Task 7: API——/api/search（内容搜索 + 时间范围 + LIKE 转义 + limit）

**Files:**
- Modify: `server.py`
- Test: `tests/test_server.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_server.py` 追加。注意 `/api/search` 的时间参数是 `YYYY-MM-DD`（由 server 转成 CST 日边界 ms），与 weibogroup 的 `start_ts`/`end_ts` 毫秒参数不同——这是 weiboblog 简化的一部分。

```python
class SearchApiTest(_ServerTestBase):
    BASE = 1750113600000  # 2025-06-17 00:00 CST
    START = "2025-06-16"  # 宽区间，覆盖全部
    END = "2025-06-18"

    def make_data(self, conn):
        base = self.BASE
        insert_posts(conn, [
            {"mblogid": "s1", "post_id": 1, "uid": 1401527553,
             "text_raw": "今天天气不错", "created_at": base},
            {"mblogid": "s2", "post_id": 2, "uid": 1401527553,
             "text_raw": "天气真好啊天气", "created_at": base + 1000},
            {"mblogid": "s3", "post_id": 3, "uid": 1401527553,
             "text_raw": "无关键词", "long_text": "长文里有天气二字",
             "created_at": base + 2000},
            {"mblogid": "s4", "post_id": 4, "uid": 1401527553,
             "text_raw": "含通配符 50% 折扣", "created_at": base + 3000},
        ])

    def test_search_hits_text_raw_and_long_text(self):
        from urllib.parse import quote
        path = f"/api/search?q={quote('天气')}&start={self.START}&end={self.END}&limit=1000"
        status, data = self._get_json(path)
        self.assertEqual(status, 200)
        mids = [r["mblogid"] for r in data["results"]]
        self.assertEqual(mids, ["s3", "s2", "s1"])  # 倒序；s3 命中 long_text

    def test_search_snippet_has_markers(self):
        from urllib.parse import quote
        path = f"/api/search?q={quote('天气')}&start={self.START}&end={self.END}&limit=1000"
        status, data = self._get_json(path)
        self.assertEqual(status, 200)
        # s1 命中 text_raw，snippet 含 \x00 \x01 标记
        s1 = [r for r in data["results"] if r["mblogid"] == "s1"][0]
        self.assertIn("\x00天气\x01", s1["snippet"])

    def test_search_result_has_date_field(self):
        from urllib.parse import quote
        path = f"/api/search?q={quote('天气')}&start={self.START}&end={self.END}&limit=1000"
        status, data = self._get_json(path)
        self.assertEqual(status, 200)
        s1 = [r for r in data["results"] if r["mblogid"] == "s1"][0]
        self.assertEqual(s1["date"], "2025-06-17")
        self.assertIn("created_at", s1)

    def test_search_escapes_like_wildcards(self):
        # 搜 "50%"，% 应被转义为字面量，只匹配 s4
        from urllib.parse import quote
        path = f"/api/search?q={quote('50%')}&start={self.START}&end={self.END}&limit=1000"
        status, data = self._get_json(path)
        self.assertEqual(status, 200)
        mids = [r["mblogid"] for r in data["results"]]
        self.assertEqual(mids, ["s4"])

    def test_search_range_filter(self):
        # 关键词"天气"在 06-17 有命中（s1,s2,s3，见上）；把范围限到 06-18 → 无命中，
        # 证明时间范围过滤生效（不加范围时返回 3 条，加 06-18 范围返回 0 条）
        from urllib.parse import quote
        path = f"/api/search?q={quote('天气')}&start=2025-06-18&end=2025-06-19&limit=1000"
        status, data = self._get_json(path)
        self.assertEqual(status, 200)
        self.assertEqual(data["results"], [])
        self.assertEqual(data["total"], 0)

    def test_search_limit_truncates_returns_total(self):
        # limit=2，命中 3 条（天气），返回 2 条但 total=3
        from urllib.parse import quote
        path = f"/api/search?q={quote('天气')}&start={self.START}&end={self.END}&limit=2"
        status, data = self._get_json(path)
        self.assertEqual(status, 200)
        self.assertEqual(len(data["results"]), 2)
        self.assertEqual(data["total"], 3)

    def test_search_no_match_empty(self):
        from urllib.parse import quote
        path = f"/api/search?q={quote('不存在')}&start={self.START}&end={self.END}&limit=1000"
        status, data = self._get_json(path)
        self.assertEqual(status, 200)
        self.assertEqual(data["results"], [])
        self.assertEqual(data["total"], 0)

    def test_search_no_q_returns_empty(self):
        # spec：搜索只搜内容（关键词 + 时间范围），无 q → 空结果（不做纯范围浏览）
        path = "/api/search?start=2025-06-16&end=2025-06-18&limit=1000"
        status, data = self._get_json(path)
        self.assertEqual(status, 200)
        self.assertEqual(data["results"], [])
        self.assertEqual(data["total"], 0)
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/test_server.py::SearchApiTest -v`
Expected: FAIL（接口未实现）

- [ ] **Step 3: 实现 _escape_like + _snippet + query_search 并接入路由**

在 `server.py` 查询函数区追加：

```python
def _escape_like(s):
    """转义 LIKE 通配符 % _ \\，配合 ESCAPE '\\' 使用。"""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _snippet(text, q, span=30):
    """截取关键词前后各 span 字，关键词用 \\x00/\\x01 包裹供前端转 <mark>。

    q 为空时返回文本前缀，不加高亮标记。
    """
    if not text:
        return ""
    if not q:
        return text[:span * 2]
    idx = text.find(q)
    if idx < 0:
        return text[:span * 2]
    start = max(0, idx - span)
    end = min(len(text), idx + len(q) + span)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return (prefix + text[start:idx] + "\x00" + q + "\x01"
            + text[idx + len(q):end] + suffix)


def query_search(conn, q, start, end, limit):
    """跨日期内容搜索（text_raw 与 long_text OR）。

    - spec：搜索只搜内容（关键词 + 时间范围），故 q 为空 → 直接返回空，
      不做纯范围浏览（避免全表扫描，也与前端「无关键词不发请求」一致）。
    - q 非空：text_raw LIKE OR long_text LIKE。
    - 时间范围 start/end 为 'YYYY-MM-DD'，由 server 转成 CST 日边界 ms
      （[start_ms, end_ms) 开区间）。缺省表示不设该侧边界。
    - LIKE 通配符 % _ \\ 转义。结果按 created_at DESC，limit 截断，
      total 为命中总数（截断前）。
    snippet 用 _snippet 生成（优先 text_raw 命中，否则 long_text 前缀）。
    """
    if not q:
        return {"results": [], "total": 0}

    conds = []
    params = []
    if start is not None:
        conds.append("created_at >= ?")
        params.append(_cst_day_bounds(start)[0])
    if end is not None:
        conds.append("created_at < ?")
        params.append(_cst_day_bounds(end)[1])

    like = "%" + _escape_like(q) + "%"
    conds.append("(text_raw LIKE ? ESCAPE '\\' OR long_text LIKE ? ESCAPE '\\')")
    params += [like, like]

    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    # total：截断前命中数
    total = conn.execute(
        f"SELECT COUNT(*) FROM weibo_posts{where}", params
    ).fetchone()[0]

    rows = conn.execute(
        f"SELECT mblogid, text_raw, long_text, created_at "
        f"FROM weibo_posts{where} "
        f"ORDER BY created_at DESC LIMIT ?",
        params + [limit],
    ).fetchall()
    results = []
    for r in rows:
        text = r["text_raw"] or ""
        long_text = r["long_text"] or ""
        # snippet 优先取命中字段的片段
        if q and q in text:
            snippet = _snippet(text, q)
        elif q and q in long_text:
            snippet = _snippet(long_text, q)
        else:
            snippet = _snippet(text, q)
        results.append({
            "mblogid": r["mblogid"],
            "date": _cst_date_str(r["created_at"]),
            "created_at": r["created_at"],
            "snippet": snippet,
        })
    return {"results": results, "total": total}
```

`_cst_date_str` 是新辅助函数，在 `query_months` 之前定义：

```python
def _cst_date_str(ts_ms):
    """UTC 毫秒 → CST 'YYYY-MM-DD' 字符串。"""
    import datetime
    dt = datetime.datetime.utcfromtimestamp(ts_ms / 1000) + datetime.timedelta(hours=8)
    return dt.strftime("%Y-%m-%d")
```

修改 `Handler._route_api`，在 `/api/posts` 分支后追加：

```python
            elif path == "/api/search":
                q = qs.get("q", [""])[0] or ""
                start = qs.get("start", [None])[0]
                end = qs.get("end", [None])[0]
                limit = int(qs.get("limit", ["1000"])[0])
                self._send_json(query_search(conn, q, start, end, limit))
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `uv run pytest tests/test_server.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add server.py tests/test_server.py
git commit -m "feat(server): /api/search 接口（内容+时间范围+LIKE 转义+total）"
```

---

### Task 8: 前端——web/index.html 页面骨架

**Files:**
- Create: `web/index.html`
- Test: 手动 `uv run server.py` 后访问 127.0.0.1:8766 确认页面加载（无 JS 逻辑，仅结构）

- [ ] **Step 1: 写 index.html**

新建 `web/index.html`。结构参考 weibogroup 但语义对应微博：顶栏（博主昵称 + 搜索按钮 + 状态）、左侧月份列表、右侧卡片流、搜索浮层、图片 lightbox。

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>WeiboBlog 消息查看器</title>
  <link rel="stylesheet" href="/web/style.css">
</head>
<body>
  <div id="topbar">
    <span id="blogger-name">加载中…</span>
    <button id="search-btn" type="button" title="关键词 + 时间范围搜索">🔍 高级搜索</button>
    <span id="status"></span>
  </div>

  <div id="main">
    <aside id="sidebar">
      <div id="date-list"></div>
    </aside>

    <section id="viewer">
      <div id="day-indicator"></div>
      <div id="post-list"></div>
      <div id="empty-hint" hidden></div>
    </section>
  </div>

  <!-- 高级搜索浮层 -->
  <div id="search-overlay" hidden>
    <div id="search-panel">
      <div id="search-panel-head">
        <span>高级搜索</span>
        <button id="search-close" type="button">×</button>
      </div>
      <div class="search-fields">
        <input id="search-keyword" type="search" placeholder="关键词（模糊匹配正文，可选）" autocomplete="off">
      </div>
      <div class="search-fields">
        <label class="field-label">起止日期：
          <input id="search-start" type="date">
          <span class="date-sep-inline">至</span>
          <input id="search-end" type="date">
        </label>
        <button id="search-submit" type="button">搜索</button>
      </div>
      <div id="search-status"></div>
      <div id="search-results"></div>
    </div>
  </div>

  <!-- 图片放大查看 -->
  <div id="lightbox" class="lightbox hidden">
    <div class="lightbox-backdrop"></div>
    <div class="lightbox-content">
      <button class="lightbox-close" type="button" title="关闭">×</button>
      <div class="lightbox-stage"></div>
    </div>
  </div>

  <script src="/web/app.js"></script>
</body>
</html>
```

- [ ] **Step 2: 提交**

```bash
git add web/index.html
git commit -m "feat(web): 页面骨架 index.html"
```

---

### Task 9: 前端——web/style.css 微博橙主题

**Files:**
- Create: `web/style.css`（覆盖 Task 3 的占位文件）
- Test: 手动确认样式加载

- [ ] **Step 1: 写 style.css**

覆盖 `web/style.css`。与 weibogroup 刻意区分：微博橙 `#ff8200`、白底顶栏 + 橙底线、浅橙侧栏 `#fffaf5`、白卡片 + 投影、倒序排列、浅橙高亮闪烁 `#ffe0b3`。

```css
* { box-sizing: border-box; }
/* 兜底：防止 display 规则覆盖 hidden 属性 */
[hidden] { display: none !important; }
html, body { margin: 0; height: 100%; font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif; }
body { display: flex; flex-direction: column; height: 100vh; overflow: hidden; }

#topbar {
  display: flex; gap: 12px; align-items: center; padding: 10px 16px;
  background: #fff; border-bottom: 1px solid #ff8200; flex-shrink: 0;
}
#blogger-name { font-weight: 700; font-size: 16px; color: #ff8200; }
#search-btn {
  padding: 0 14px; height: 28px; cursor: pointer; background: #ff8200; color: #fff;
  border: none; border-radius: 4px; font-size: 13px; line-height: 1;
  display: inline-flex; align-items: center; justify-content: center;
}
#search-btn:hover { background: #e67400; }
#status { margin-left: auto; color: #999; font-size: 13px; }

#main { flex: 1; display: flex; min-height: 0; }

#sidebar {
  width: 200px; flex-shrink: 0; border-right: 1px solid #ffe0b3;
  display: flex; flex-direction: column; min-height: 0; background: #fffaf5;
}
#date-list { overflow-y: auto; flex: 1; }

.month-group { margin: 0; }
.month-header {
  padding: 8px 12px; cursor: pointer; font-weight: 600; font-size: 13px;
  color: #ff8200; user-select: none;
}
.month-header::before { content: "▸ "; }
.month-group.open .month-header::before { content: "▾ "; }
.month-days { display: none; }
.month-group.open .month-days { display: block; }

.date-item {
  padding: 5px 24px; cursor: pointer; font-size: 13px; color: #555;
  display: flex; justify-content: space-between;
}
.date-item:hover { background: #fff0e0; }
.date-item.active { background: #ff8200; color: #fff; }
.date-item .count { color: #bbb; font-size: 11px; }
.date-item.active .count { color: #ffe0b3; }

#viewer { flex: 1; display: flex; flex-direction: column; min-width: 0; min-height: 0; }
#day-indicator {
  padding: 6px 16px; font-size: 12px; color: #888; background: #fffaf5;
  border-bottom: 1px solid #ffe0b3; flex-shrink: 0;
}
#post-list { flex: 1; overflow-y: auto; padding: 12px 16px; }

.post-card {
  background: #fff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,.1);
  margin-bottom: 12px; padding: 14px 16px; transition: box-shadow .2s;
}
.post-card.post-highlight { animation: highlight-flash 1.6s ease-out; }
@keyframes highlight-flash {
  0% { box-shadow: 0 0 0 0 #ff8200; background: #ffe0b3; }
  100% { box-shadow: 0 1px 3px rgba(0,0,0,.1); background: #fff; }
}
.post-time { float: right; font-size: 12px; color: #999; }
.post-text { font-size: 14px; color: #333; white-space: pre-wrap; word-break: break-word; line-height: 1.6; }
.post-text.long-text { margin-top: 4px; }
.post-pics { display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0; }
.post-pics img { width: 120px; height: 120px; object-fit: cover; border-radius: 4px; cursor: pointer; background: #f5f5f5; }
.post-pics img.pic-error { width: 120px; height: 80px; object-fit: contain; background: #f5f5f5; display: flex; align-items: center; justify-content: center; }
.post-meta { font-size: 11px; color: #999; margin-top: 8px; }
.post-meta .count { margin-right: 12px; }
.post-meta .source { color: #bbb; }

#empty-hint { padding: 40px; text-align: center; color: #999; font-size: 14px; }

/* 搜索浮层 */
#search-overlay {
  position: fixed; inset: 0; background: rgba(0,0,0,.4);
  display: flex; align-items: flex-start; justify-content: center; padding-top: 60px; z-index: 100;
}
#search-panel {
  background: #fff; border-radius: 8px; width: 600px; max-width: 92vw;
  box-shadow: 0 4px 20px rgba(0,0,0,.2); overflow: hidden;
}
#search-panel-head {
  display: flex; justify-content: space-between; align-items: center;
  padding: 12px 16px; font-weight: 600; border-bottom: 1px solid #eee; color: #ff8200;
}
#search-close { background: none; border: none; font-size: 22px; cursor: pointer; color: #999; line-height: 1; }
.search-fields { padding: 12px 16px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.search-fields input[type=search] { flex: 1; min-width: 200px; padding: 6px 10px; border: 1px solid #ddd; border-radius: 4px; }
.search-fields input[type=date] { padding: 6px 8px; border: 1px solid #ddd; border-radius: 4px; }
.field-label { font-size: 13px; color: #555; display: inline-flex; align-items: center; gap: 4px; }
.date-sep-inline { color: #999; }
#search-submit {
  padding: 0 16px; height: 30px; cursor: pointer; background: #ff8200; color: #fff;
  border: none; border-radius: 4px; font-size: 13px;
}
#search-status { padding: 0 16px; font-size: 12px; color: #888; min-height: 18px; }
#search-results { max-height: 50vh; overflow-y: auto; }
.search-result-item { padding: 10px 16px; border-top: 1px solid #f5f5f5; cursor: pointer; }
.search-result-item:hover { background: #fff8f0; }
.search-result-item .sr-date { font-size: 12px; color: #ff8200; font-weight: 600; margin-bottom: 4px; }
.search-result-item .sr-snippet { font-size: 13px; color: #333; line-height: 1.5; }
.search-result-item mark { background: #ffe0b3; color: inherit; border-radius: 2px; padding: 0 1px; }

/* lightbox */
.lightbox { position: fixed; inset: 0; z-index: 200; display: flex; align-items: center; justify-content: center; }
.lightbox.hidden { display: none; }
.lightbox-backdrop { position: absolute; inset: 0; background: rgba(0,0,0,.85); cursor: pointer; }
.lightbox-content { position: relative; max-width: 90vw; max-height: 90vh; }
.lightbox-stage img { max-width: 90vw; max-height: 90vh; object-fit: contain; }
.lightbox-close {
  position: absolute; top: -40px; right: 0; background: none; border: none;
  font-size: 32px; color: #fff; cursor: pointer; line-height: 1;
}
```

- [ ] **Step 2: 提交**

```bash
git add web/style.css
git commit -m "feat(web): 微博橙主题样式（卡片流 + 浅橙侧栏）"
```

---

### Task 10: 前端——web/app.js 核心逻辑

**Files:**
- Create: `web/app.js`
- Test: 手动 `uv run server.py` 后访问，验证月份加载、展开日期、渲染卡片、搜索定位、lightbox 全链路

- [ ] **Step 1: 写 app.js**

新建 `web/app.js`。覆盖：加载博主信息、加载月份列表、点击月份懒加载日期、点击日期加载卡片流（倒序）、渲染卡片（时间/正文/长文/图片/互动数/来源）、搜索浮层（关键词+时间范围、结果 snippet 转 `<mark>`、点击定位高亮）、图片 lightbox、空状态。

```javascript
"use strict";
/* WeiboBlog 消息查看器前端 —— 原生 JS，无框架无构建。
 * 与 weibogroup 同构但更简：无游标分页、无触顶触底加载、无发送者筛选。
 * 点开某日一次查全部，倒序（最新在上）。
 */

const $ = (id) => document.getElementById(id);
const bloggerName = $("blogger-name");
const statusEl = $("status");
const dateList = $("date-list");
const dayIndicator = $("day-indicator");
const postList = $("post-list");
const emptyHint = $("empty-hint");

// 月份日期缓存：{ "2025-06": [{date,count}, ...] }
const monthCache = {};
let currentDay = null;

// ── 工具 ──────────────────────────────
function fmtTime(ms) {
  // ms → CST HH:MM（按 +8 计算，不依赖系统时区）
  const d = new Date(ms + 8 * 3600 * 1000);
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mm = String(d.getUTCMinutes()).padStart(2, "0");
  return `${hh}:${mm}`;
}

function setStatus(msg) { statusEl.textContent = msg || ""; }

function escHtml(s) {
  return (s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

// snippet 用 \x00 \x01 包裹命中词，转成 <mark>
function snippetToHtml(snippet) {
  return escHtml(snippet).replace(/\x00/g, "<mark>").replace(/\x01/g, "</mark>");
}

async function getJson(path) {
  const resp = await fetch(path);
  if (!resp.ok) {
    const txt = await resp.text().catch(() => "");
    throw new Error(`${resp.status} ${txt}`);
  }
  return resp.json();
}

// ── 博主信息 ──────────────────────────
async function loadBlogger() {
  try {
    const b = await getJson("/api/blogger");
    bloggerName.textContent = b.screen_name || `uid:${b.uid}`;
    bloggerName.title = b.verified ? "已认证" : "";
  } catch (e) {
    if (String(e).includes("404")) {
      bloggerName.textContent = "（无博主数据，请先抓取）";
    } else {
      bloggerName.textContent = "加载失败";
      setStatus("博主信息加载失败");
    }
  }
}

// ── 月份列表 ──────────────────────────
async function loadMonths() {
  let months;
  try {
    months = await getJson("/api/months");
  } catch (e) {
    setStatus("月份列表加载失败");
    return;
  }
  dateList.innerHTML = "";
  if (!months.length) {
    dateList.innerHTML = '<div style="padding:16px;color:#999;font-size:13px">无微博数据</div>';
    return;
  }
  for (const m of months) {
    const grp = document.createElement("div");
    grp.className = "month-group";
    grp.dataset.month = m.month;
    grp.innerHTML =
      `<div class="month-header">${escHtml(m.month)} <span class="count">(${m.count})</span></div>` +
      `<div class="month-days"></div>`;
    grp.querySelector(".month-header").addEventListener("click", () => toggleMonth(grp, m.month));
    dateList.appendChild(grp);
  }
}

async function toggleMonth(grp, month) {
  const daysEl = grp.querySelector(".month-days");
  const isOpen = grp.classList.toggle("open");
  if (!isOpen) return;
  if (monthCache[month]) {
    renderDays(daysEl, monthCache[month]);
    return;
  }
  daysEl.innerHTML = '<div style="padding:8px 24px;color:#bbb;font-size:12px">加载中…</div>';
  try {
    const days = await getJson(`/api/dates?month=${encodeURIComponent(month)}`);
    monthCache[month] = days;
    renderDays(daysEl, days);
  } catch (e) {
    daysEl.innerHTML = '<div style="padding:8px 24px;color:#c00;font-size:12px">加载失败</div>';
  }
}

function renderDays(daysEl, days) {
  daysEl.innerHTML = "";
  if (!days.length) {
    daysEl.innerHTML = '<div style="padding:8px 24px;color:#bbb;font-size:12px">（无）</div>';
    return;
  }
  for (const d of days) {
    const item = document.createElement("div");
    item.className = "date-item";
    item.dataset.date = d.date;
    // 日期显示为 MM-DD
    const md = d.date.slice(5);
    item.innerHTML = `${escHtml(md)} <span class="count">${d.count}</span>`;
    item.addEventListener("click", () => selectDay(d.date, item));
    daysEl.appendChild(item);
  }
}

// ── 选中日期 → 加载卡片流 ─────────────
async function selectDay(date, itemEl) {
  // 高亮当前选中
  document.querySelectorAll(".date-item.active").forEach(el => el.classList.remove("active"));
  if (itemEl) itemEl.classList.add("active");
  currentDay = date;
  dayIndicator.textContent = `${date}  加载中…`;
  postList.innerHTML = "";
  emptyHint.hidden = true;

  let data;
  try {
    data = await getJson(`/api/posts?date=${encodeURIComponent(date)}`);
  } catch (e) {
    dayIndicator.textContent = date;
    postList.innerHTML = "";
    emptyHint.textContent = "加载失败";
    emptyHint.hidden = false;
    return;
  }
  dayIndicator.textContent = `${date}  共 ${data.posts.length} 条`;
  if (!data.posts.length) {
    emptyHint.textContent = "该日无微博";
    emptyHint.hidden = false;
    return;
  }
  renderPosts(data.posts);
}

function renderPosts(posts) {
  postList.innerHTML = "";
  emptyHint.hidden = true;
  for (const p of posts) {
    postList.appendChild(renderCard(p));
  }
}

function renderCard(p) {
  const card = document.createElement("div");
  card.className = "post-card";
  card.id = "post-" + p.mblogid;

  let html = `<span class="post-time">${fmtTime(p.created_at)}</span>`;

  // 正文
  html += `<div class="post-text">${escHtml(p.text_raw)}</div>`;
  if (p.is_long_text && p.long_text) {
    html += `<div class="post-text long-text">${escHtml(p.long_text)}</div>`;
  }

  // 图片
  if (p.pics && p.pics.length) {
    html += '<div class="post-pics">';
    for (const pic of p.pics) {
      const url = pic.url_bmiddle || pic.url_large || "";
      const large = pic.url_large || pic.url_bmiddle || "";
      if (url) {
        html += `<img src="${escHtml(url)}" data-large="${escHtml(large)}" ` +
          `onerror="this.onerror=null;this.classList.add('pic-error');this.alt='图片加载失败';this.src=''">`;
      }
    }
    html += "</div>";
  }

  // 元信息
  html += `<div class="post-meta">` +
    `<span class="count">转发 ${p.reposts_count}</span>` +
    `<span class="count">评论 ${p.comments_count}</span>` +
    `<span class="count">赞 ${p.attitudes_count}</span>` +
    (p.source ? `<span class="source">· ${escHtml(p.source)}</span>` : "") +
    `</div>`;

  card.innerHTML = html;

  // 图片点击 → lightbox
  card.querySelectorAll(".post-pics img").forEach(img => {
    img.addEventListener("click", () => openLightbox(img.dataset.large));
  });
  return card;
}

// ── lightbox ──────────────────────────
const lightbox = $("lightbox");
function openLightbox(url) {
  const stage = lightbox.querySelector(".lightbox-stage");
  stage.innerHTML = `<img src="${escHtml(url)}" onerror="this.alt='图片加载失败'">`;
  lightbox.classList.remove("hidden");
}
function closeLightbox() {
  lightbox.classList.add("hidden");
  lightbox.querySelector(".lightbox-stage").innerHTML = "";
}
lightbox.querySelector(".lightbox-backdrop").addEventListener("click", closeLightbox);
lightbox.querySelector(".lightbox-close").addEventListener("click", closeLightbox);

// ── 搜索浮层 ──────────────────────────
const searchOverlay = $("search-overlay");
const searchKeyword = $("search-keyword");
const searchStart = $("search-start");
const searchEnd = $("search-end");
const searchStatus = $("search-status");
const searchResults = $("search-results");

function openSearch() {
  // 默认起止：最近 3 个月
  const today = new Date();
  const end = today.toISOString().slice(0, 10);
  const startD = new Date(today.getTime() - 90 * 86400000);
  searchEnd.value = end;
  searchStart.value = startD.toISOString().slice(0, 10);
  searchStatus.textContent = "";
  searchResults.innerHTML = "";
  searchOverlay.hidden = false;
  searchKeyword.focus();
}
function closeSearch() {
  searchOverlay.hidden = true;
}
$("search-btn").addEventListener("click", openSearch);
$("search-close").addEventListener("click", closeSearch);
searchOverlay.addEventListener("click", (e) => {
  if (e.target === searchOverlay) closeSearch();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if (!searchOverlay.hidden) closeSearch();
    if (!lightbox.classList.contains("hidden")) closeLightbox();
  }
});

$("search-submit").addEventListener("click", doSearch);
searchKeyword.addEventListener("keydown", (e) => {
  if (e.key === "Enter") doSearch();
});

async function doSearch() {
  const q = searchKeyword.value.trim();
  const start = searchStart.value || "";
  const end = searchEnd.value || "";
  // spec：搜索只搜内容，关键词必填；时间范围为可选过滤
  if (!q) {
    searchStatus.textContent = "请输入关键词";
    return;
  }
  const params = new URLSearchParams();
  params.set("q", q);
  if (start) params.set("start", start);
  if (end) params.set("end", end);
  params.set("limit", "1000");
  searchStatus.textContent = "搜索中…";
  searchResults.innerHTML = "";
  let data;
  try {
    data = await getJson(`/api/search?${params}`);
  } catch (e) {
    searchStatus.textContent = "搜索失败";
    return;
  }
  if (!data.results.length) {
    searchStatus.textContent = data.total > 0 ? `已达上限（${data.total} 条），请缩小范围` : "未找到匹配微博";
    return;
  }
  searchStatus.textContent = `共 ${data.total} 条结果${data.total > data.results.length ? `（已显示前 ${data.results.length} 条）` : ""}`;
  for (const r of data.results) {
    const item = document.createElement("div");
    item.className = "search-result-item";
    item.innerHTML =
      `<div class="sr-date">${escHtml(r.date)} ${fmtTime(r.created_at)}</div>` +
      `<div class="sr-snippet">${snippetToHtml(r.snippet)}</div>`;
    item.addEventListener("click", () => jumpToPost(r.date, r.mblogid));
    searchResults.appendChild(item);
  }
}

// ── 搜索结果 → 定位高亮 ───────────────
async function jumpToPost(date, mblogid) {
  closeSearch();
  // 若不在该日期，先加载
  if (currentDay !== date) {
    // 展开对应月份
    const month = date.slice(0, 7);
    const grp = dateList.querySelector(`.month-group[data-month="${month}"]`);
    if (grp && !grp.classList.contains("open")) {
      await toggleMonth(grp, month);
    }
    // 选中日期项
    const itemEl = dateList.querySelector(`.date-item[data-date="${date}"]`);
    await selectDay(date, itemEl);
  }
  // 等待渲染后定位
  requestAnimationFrame(() => {
    const el = document.getElementById("post-" + mblogid);
    if (el) {
      el.scrollIntoView({ block: "center", behavior: "smooth" });
      el.classList.add("post-highlight");
      setTimeout(() => el.classList.remove("post-highlight"), 1700);
    }
  });
}

// ── 初始化 ────────────────────────────
(async function init() {
  emptyHint.textContent = "请从左侧选择日期";
  emptyHint.hidden = false;
  await loadBlogger();
  await loadMonths();
})();
```

- [ ] **Step 2: 手动联调验证**

启动 server（需有真实数据库；若没有，至少验证不崩溃）：

```bash
uv run server.py
```

浏览器访问 `http://127.0.0.1:8766`，验证：
1. 顶栏显示博主昵称（tombkeeper，橙色）
2. 左侧月份列表降序展开正常
3. 点击某月展开日期列表，再点某日右侧出卡片流（最新在上）
4. 卡片含时间、正文、图片缩略图、互动数、来源
5. 点缩略图出 lightbox 大图，点遮罩/Esc 关闭
6. 点搜索按钮出浮层，输入关键词搜索出结果，snippet 命中词高亮
7. 点搜索结果 → 跳到对应日期，对应卡片闪烁高亮定位

若没有数据库，至少确认页面无 JS 报错（F12 控制台）——空状态下应显示"无微博数据"或"请从左侧选择日期"。

- [ ] **Step 3: 提交**

```bash
git add web/app.js
git commit -m "feat(web): 前端核心逻辑（月份/日期/卡片/搜索/定位/lightbox）"
```

---

### Task 11: 全量回归测试 + README 更新

**Files:**
- Modify: `README.md`
- Test: `tests/`（全量）

- [ ] **Step 1: 跑全量测试确认无回归**

Run: `uv run pytest tests/ -v`
Expected: 全部 PASS（含 test_db / test_parser / test_crawler / test_server）

- [ ] **Step 2: README §2 项目结构补 server.py 与 web/**

修改 `README.md` 的 §2 项目结构树，在 `crawl_blog.py` 后、`pyproject.toml` 前插入 `server.py`，并在 `weibo_blog/` 包后补 `web/` 目录。修改后：

```
weiboblog/
├── crawl_blog.py          # CLI 入口（唯一可执行脚本）
├── server.py              # 消息查看器 web 服务（纯标准库）
├── pyproject.toml         # 项目配置 + 依赖
├── README.md              # 本文档
├── API.md                 # 接口契约（URL/参数/响应，与实现语言无关）
├── ARCHITECTURE.md        # 架构 / 数据流 / 跨语言迁移
├── weibo_blog.db          # SQLite 数据库（运行时生成，不入库）
├── qrcode.png             # 扫码二维码截图（--renew-cookie 生成，可删）
├── weibo_blog/            # 核心包
│   ├── __init__.py
│   ├── parser.py          # mymblog 单条 JSON → 扁平 dict（纯函数）
│   ├── db.py              # SQLite 建表 + 存取
│   └── crawler.py         # HTTP 客户端 + 翻页 + 长文补全 + cookie 续期
└── web/                   # 消息查看器前端（原生 HTML/CSS/JS）
    ├── index.html
    ├── app.js
    └── style.css
```

- [ ] **Step 3: README 新增查看器使用章节**

在 `README.md` 的 §10（测试）之前插入新章节 `## 10. 消息查看器（web server）`，并把原 §10/§11 顺延为 §11/§12。内容：

```markdown
## 10. 消息查看器（web server）

本地只读 web 查看器，浏览已抓取的博主微博。布局与 weibogroup 类似（顶栏 +
左侧列表 + 右侧内容），但视觉用微博橙 + 卡片流，且更简：无分页、无触顶触底
加载——点开某日一次展示当日全部微博，倒序（最新在上）。

### 10.1 启动

\`\`\`bash
uv run server.py                       # 默认 127.0.0.1:8766，读 weibo_blog.db
uv run server.py --port 9000           # 自定义端口
uv run server.py --db D:\\path\\to.db    # 自定义数据库
\`\`\`

浏览器访问 `http://127.0.0.1:8766`。端口 8766 避开 weibogroup 的 8765，
两个查看器可同时开。

### 10.2 功能

- **顶栏**：博主昵称（微博橙）+ 高级搜索按钮
- **左侧**：单层月份列表（YYYY-MM 降序），点开展开当月各日（懒加载）
- **右侧**：卡片流，点开某日一次性展示当日全部微博，最新在上
- **高级搜索**：关键词（模糊匹配正文）+ 起止日期范围，无发送者筛选
  （单博主）。点击结果定位到对应微博并高亮闪烁
- **图片**：缩略图点击放大（lightbox），直接用 sinaimg.cn 原始 URL，
  不走 server 代理

### 10.3 与 weibogroup 查看器的区别

| 维度 | weibogroup | weiboblog |
|------|-----------|-----------|
| 强调色 | Google 蓝 `#1a73e8` | 微博橙 `#ff8200` |
| 内容形式 | 聊天气泡 | 白卡片 + 投影 |
| 分页 | 游标分页 + 触顶触底加载 | 无，点日查全部 |
| 排列 | 升序（新在底） | 倒序（新在上） |
| 发送者筛选 | 有 | 无（单博主） |
| 媒体 | server 代理下载 | 原始 URL 直连 |
| 端口 | 8765 | 8766 |
```

> 注：以上代码块里的反引号转义（`\`\`\``）是本计划的展示需要；实际写入 README 时用普通三反引号 ` ``` `。

- [ ] **Step 4: README §11（原§11，现§12）文档列表补查看器 spec/plan**

在 `README.md` 的"设计与实现文档"列表（现 §12）追加两行：

```markdown
- 查看器设计规格：`docs/superpowers/specs/2026-06-20-weiboblog-viewer-design.md`
- 查看器实现计划：`docs/superpowers/plans/2026-06-20-weiboblog-viewer.md`
```

- [ ] **Step 5: 提交**

```bash
git add README.md
git commit -m "docs: README 补消息查看器使用说明 + 项目结构更新"
```

---

## 完成验证

实现完成后，执行以下验证清单：

1. `uv run pytest tests/ -v` 全绿（含 test_server.py 全部 7 类测试）
2. `uv run server.py` 启动正常，访问 `http://127.0.0.1:8766` 页面无 JS 报错
3. 有真实数据库时：月份/日期/卡片/搜索/定位/lightbox 全链路可用
4. 与 weibogroup 查看器（8765）视觉明显不同（橙 vs 蓝、卡片 vs 气泡、倒序 vs 升序）
5. `git log --oneline` 可见 ~11 个功能提交，每提交独立可运行
