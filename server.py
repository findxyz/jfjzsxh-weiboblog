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


# ---------- 图片代理 ----------

def fetch_image(url, referer="https://weibo.com/"):
    """带 Referer 请求 sinaimg.cn 图片，返回 requests.Response。

    sinaimg 防盗链：无 Referer 或非 weibo 域 Referer → 403。浏览器 <img> 带的是
    本查看器页面的 Referer（127.0.0.1:8766），故直链加载失败。由 server 统一带
    Referer: https://weibo.com/ 代理取图。requests 在 crawler 已是依赖，惰性导入
    不影响 server 模块静态导入。
    """
    import requests
    import urllib3
    urllib3.disable_warnings()
    headers = {
        "Referer": referer,
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
    }
    return requests.get(url, headers=headers, verify=False, timeout=15)


def _is_allowed_img_host(url):
    """SSRF 防护：只允许 *.sinaimg.cn 域名的图片代理。"""
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    host = (parsed.hostname or "").lower()
    return parsed.scheme in ("http", "https") and host.endswith(".sinaimg.cn")


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


def _parse_pics(pics_json):
    """pics_json 字符串解析为 list[dict]，失败/空返回 []。"""
    if not pics_json:
        return []
    try:
        pics = json.loads(pics_json)
        return pics if isinstance(pics, list) else []
    except (ValueError, TypeError):
        return []


def _cst_date_str(ts_ms):
    """UTC 毫秒 → CST 'YYYY-MM-DD' 字符串。"""
    import datetime
    dt = datetime.datetime.fromtimestamp(ts_ms / 1000, datetime.timezone.utc) + datetime.timedelta(hours=8)
    return dt.strftime("%Y-%m-%d")


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


def query_posts(conn, date):
    """某 CST 日期的全部微博，倒序（最新在上）。

    用 CST 当日区间 [start_ms, end_ms) 做 created_at 范围过滤，命中
    (uid, created_at) 复合索引。pics_json 在 server 端解析成数组返回。
    无数据返回空 posts 列表（非 404）。
    """
    start_ms, end_ms = _cst_day_bounds(date)
    rows = conn.execute(
        "SELECT mblogid, uid, text_raw, long_text, is_long_text, pics_json, "
        "video_url, source, reposts_count, comments_count, attitudes_count, created_at "
        "FROM weibo_posts "
        "WHERE created_at>=? AND created_at<? "
        "ORDER BY created_at DESC",
        (start_ms, end_ms),
    ).fetchall()
    posts = [{
        "mblogid": r["mblogid"],
        "uid": r["uid"],
        "text_raw": r["text_raw"],
        "long_text": r["long_text"],
        "is_long_text": r["is_long_text"],
        "pics": _parse_pics(r["pics_json"]),
        "video_url": r["video_url"],
        "source": r["source"],
        "reposts_count": r["reposts_count"],
        "comments_count": r["comments_count"],
        "attitudes_count": r["attitudes_count"],
        "created_at": r["created_at"],
    } for r in rows]
    return {"date": date, "posts": posts}


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
        conn = self.conn
        try:
            if path == "/api/blogger":
                b = query_blogger(conn)
                if b is None:
                    self._send_json({"error": "no blogger"}, status=404)
                else:
                    self._send_json(b)
            elif path == "/api/months":
                self._send_json(query_months(conn))
            elif path == "/api/dates":
                month = qs.get("month", [None])[0]
                if not month:
                    self._send_json({"error": "missing month"}, status=400)
                else:
                    self._send_json(query_month_days(conn, month))
            elif path == "/api/posts":
                date = qs.get("date", [None])[0]
                if not date:
                    self._send_json({"error": "missing date"}, status=400)
                else:
                    self._send_json(query_posts(conn, date))
            elif path == "/api/search":
                q = qs.get("q", [""])[0] or ""
                start = qs.get("start", [None])[0]
                end = qs.get("end", [None])[0]
                limit = int(qs.get("limit", ["1000"])[0])
                self._send_json(query_search(conn, q, start, end, limit))
            elif path == "/api/img":
                # 图片代理：带 Referer 取 sinaimg，绕防盗链。SSRF 限制只代理 sinaimg.cn
                url = qs.get("url", [None])[0]
                if not url:
                    self._send_json({"error": "missing url"}, status=400)
                elif not _is_allowed_img_host(url):
                    self._send_json({"error": "host not allowed"}, status=403)
                else:
                    resp = fetch_image(url, referer="https://weibo.com/")
                    ctype = resp.headers.get("Content-Type", "image/jpeg")
                    self._send_text(resp.content, status=resp.status_code, content_type=ctype)
            else:
                self._send_json({"error": "not found"}, status=404)
        except Exception as e:
            self._send_json({"error": str(e)}, status=500)

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
