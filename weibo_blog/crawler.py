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
    set_db_path, get_conn,
    get_cookie, set_cookie,
    save_blogger, save_post, get_latest_post_id,
)

urllib3.disable_warnings()
log = logging.getLogger("weibo_blog.crawler")

API_BASE = "https://weibo.com"


def _jitter_sleep(base: float, jitter: float = 0.2):
    """带抖动的 sleep，避免请求节奏过于规律"""
    actual = base * (1 + random.uniform(-jitter, jitter))
    time.sleep(max(actual, 0.05))


def _request_with_retry(session, method, url, max_retries=3, **kwargs):
    """请求 + 重试：5xx/429/连接错误指数退避"""
    last_err = None
    for attempt in range(max_retries + 1):
        try:
            # 走 session 的具名方法（get/post/...），便于测试 mock session.get
            resp = getattr(session, method.lower())(url, **kwargs)
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
        set_db_path(db_path)
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
        data = resp.json().get("data", {}) or {}
        return data.get("since_id", "") or "", data.get("list", []) or []

    def fetch_longtext(self, mblogid: str) -> str:
        """调用 longtext 接口，返回长文全文"""
        resp = _request_with_retry(
            self.session, "GET", f"{API_BASE}/ajax/statuses/longtext",
            params={"id": mblogid}, timeout=15,
        )
        return resp.json().get("data", {}).get("longTextContent", "") or ""

    # ── 编排：全量回填 ───────────────────────────────

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

    # ── 编排：增量更新 ───────────────────────────────

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
