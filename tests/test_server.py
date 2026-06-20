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


if __name__ == "__main__":
    unittest.main()
