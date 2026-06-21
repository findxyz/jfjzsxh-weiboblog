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
            {"mblogid": "a4", "post_id": 4, "uid": 1401527553,
             "text_raw": "看视频", "video_url": "http://video.weibo.com/stream.mp4",
             "created_at": base + 3000},  # 06-17，带视频
            {"mblogid": "a5", "post_id": 5, "uid": 1401527553,
             "text_raw": "转发这条",
             "retweeted_json": '{"post_id":999,"mblogid":"Rorig","text_raw":"原微博内容","uid":1401527553,"screen_name":"tombkeeper","created_at":"Mon Jun 08 08:55:15 +0800 2026"}',
             "created_at": base + 4000},  # 06-17，转发，最新
            {"mblogid": "a3", "post_id": 3, "uid": 1401527553,
             "text_raw": "次日", "created_at": base + 86400000},  # 06-18
        ])

    def test_posts_desc_newest_first(self):
        status, data = self._get_json("/api/posts?date=2025-06-17")
        self.assertEqual(status, 200)
        self.assertEqual(data["date"], "2025-06-17")
        mids = [p["mblogid"] for p in data["posts"]]
        self.assertEqual(mids, ["a5", "a4", "a2", "a1"])  # 倒序，最新在上

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
            "mblogid", "uid", "text_raw", "long_text", "is_long_text",
            "pics", "video_url", "retweeted", "source", "reposts_count",
            "comments_count", "attitudes_count", "created_at",
        })

    def test_posts_returns_uid_and_video_url(self):
        status, data = self._get_json("/api/posts?date=2025-06-17")
        self.assertEqual(status, 200)
        a4 = [p for p in data["posts"] if p["mblogid"] == "a4"][0]
        self.assertEqual(a4["uid"], 1401527553)
        self.assertEqual(a4["video_url"], "http://video.weibo.com/stream.mp4")
        # 无视频微博 video_url 为空串
        a1 = [p for p in data["posts"] if p["mblogid"] == "a1"][0]
        self.assertEqual(a1["video_url"], "")

    def test_posts_retweeted_parsed_to_object(self):
        status, data = self._get_json("/api/posts?date=2025-06-17")
        self.assertEqual(status, 200)
        a5 = [p for p in data["posts"] if p["mblogid"] == "a5"][0]
        self.assertEqual(a5["retweeted"], {
            "post_id": 999,
            "mblogid": "Rorig",
            "text_raw": "原微博内容",
            "uid": 1401527553,
            "screen_name": "tombkeeper",
            "created_at": "Mon Jun 08 08:55:15 +0800 2026",
        })
        # 非转发微博 retweeted 为 null
        a1 = [p for p in data["posts"] if p["mblogid"] == "a1"][0]
        self.assertIsNone(a1["retweeted"])


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


class BloggerFilterApiTest(_ServerTestBase):
    """多博主场景：uid 过滤。months/dates/posts/search 带 uid 只返回该博主数据。"""
    def make_data(self, conn):
        insert_blogger(conn, 1401527553, "tombkeeper")
        insert_blogger(conn, 999, "other")
        base = 1750113600000  # 2025-06-17 00:00 CST
        insert_posts(conn, [
            {"mblogid": "t1", "post_id": 1, "uid": 1401527553,
             "text_raw": "tombkeeper 的微博", "created_at": base},
            {"mblogid": "o1", "post_id": 2, "uid": 999,
             "text_raw": "other 的微博", "created_at": base + 1000},
        ])

    def test_bloggers_returns_all(self):
        status, data = self._get_json("/api/bloggers")
        self.assertEqual(status, 200)
        names = [b["screen_name"] for b in data]
        self.assertEqual(names, ["other", "tombkeeper"])  # 按昵称排序

    def test_bloggers_includes_avatar_stripped(self):
        # 给 tombkeeper 设一个带签名参数的 avatar，断言返回的是去签名基准 URL
        import sqlite3 as _sqlite3
        conn = _sqlite3.connect(self.db_path)
        conn.execute(
            "UPDATE bloggers SET avatar=? WHERE uid=1401527553",
            ("https://tvax3.sinaimg.cn/crop.0.0.503.503.180/abc.jpg?KID=imgbed,tva&Expires=1&ssig=x",),
        )
        conn.commit()
        conn.close()
        status, data = self._get_json("/api/bloggers")
        self.assertEqual(status, 200)
        tk = next(b for b in data if b["uid"] == 1401527553)
        self.assertEqual(tk["avatar"],
                         "https://tvax3.sinaimg.cn/crop.0.0.503.503.180/abc.jpg")
        # 未设 avatar 的博主返回空串
        other = next(b for b in data if b["uid"] == 999)
        self.assertEqual(other["avatar"], "")

    def test_months_filtered_by_uid(self):
        # 不带 uid：两个博主合计
        status, data = self._get_json("/api/months")
        self.assertEqual(status, 200)
        self.assertEqual(data, [{"month": "2025-06", "count": 2}])
        # 带 uid=1401527553：只 tombkeeper 1 条
        status, data = self._get_json("/api/months?uid=1401527553")
        self.assertEqual(status, 200)
        self.assertEqual(data, [{"month": "2025-06", "count": 1}])

    def test_posts_filtered_by_uid(self):
        status, data = self._get_json("/api/posts?date=2025-06-17&uid=999")
        self.assertEqual(status, 200)
        mids = [p["mblogid"] for p in data["posts"]]
        self.assertEqual(mids, ["o1"])

    def test_search_filtered_by_uid(self):
        from urllib.parse import quote
        # 不带 uid：两条都命中"微博"
        path = f"/api/search?q={quote('微博')}&limit=1000"
        status, data = self._get_json(path)
        self.assertEqual(status, 200)
        self.assertEqual(data["total"], 2)
        # 带 uid=1401527553：只 tombkeeper 1 条
        path = f"/api/search?q={quote('微博')}&uid=1401527553&limit=1000"
        status, data = self._get_json(path)
        self.assertEqual(status, 200)
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["results"][0]["mblogid"], "t1")


class ImgProxyApiTest(_ServerTestBase):
    """/api/img?url= 代理 sinaimg.cn 图片，带 Referer 绕防盗链。

    不打真实网络：mock server.fetch_image 返回固定字节流。
    """
    def test_img_proxies_sinaimg(self):
        from unittest import mock
        fake = mock.MagicMock()
        fake.status_code = 200
        fake.content = b"\xff\xd8\xff\xe0FAKEJPEG"
        fake.headers = {"Content-Type": "image/jpeg"}
        with mock.patch("server.fetch_image", return_value=fake) as m:
            status, body = self._get("/api/img?url=https://wx2.sinaimg.cn/orj960/abc.jpg")
        self.assertEqual(status, 200)
        self.assertIn("image/jpeg", self._last_content_type)
        self.assertTrue(body.startswith(b"\xff\xd8"))
        # 确认带了 Referer: https://weibo.com/
        args, kwargs = m.call_args
        self.assertEqual(args[0], "https://wx2.sinaimg.cn/orj960/abc.jpg")
        self.assertEqual(kwargs.get("referer"), "https://weibo.com/")

    def test_img_rejects_non_sinaimg(self):
        # SSRF 防护：只允许 *.sinaimg.cn
        from urllib.parse import quote
        status, body = self._get_json(f"/api/img?url={quote('https://evil.com/x.jpg')}")
        self.assertEqual(status, 403)
        self.assertIn("error", body)

    def test_img_missing_url_returns_400(self):
        status, body = self._get_json("/api/img")
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_img_upstream_403_passes_through(self):
        # sinaimg 上游 403 → 透传给客户端
        from unittest import mock
        fake = mock.MagicMock()
        fake.status_code = 403
        fake.content = b"forbidden"
        fake.headers = {"Content-Type": "text/html"}
        with mock.patch("server.fetch_image", return_value=fake):
            status, body = self._get("/api/img?url=https://wx2.sinaimg.cn/orj960/abc.jpg")
        self.assertEqual(status, 403)


if __name__ == "__main__":
    unittest.main()
