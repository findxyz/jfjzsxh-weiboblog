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
