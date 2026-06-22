"""爬虫核心 — mymblog/longtext 接口调用 + 翻页 + 长文补全 + 编排"""
from __future__ import annotations

import os
import sys
import json
import time
import random
import logging
import subprocess

import requests
import urllib3
from datetime import datetime, timezone, timedelta

from .parser import parse_post, parse_blogger
from .db import (
    set_db_path, get_conn,
    get_cookie, set_cookie,
    save_blogger, save_post, get_latest_post_id,
)

urllib3.disable_warnings()
log = logging.getLogger("weibo_blog.crawler")

API_BASE = "https://weibo.com"

CST = timezone(timedelta(hours=8))


def _date_to_timestamp(date_str: str, end_of_day: bool = False) -> int:
    """'2012-01-01' → 1325347200（当日 00:00:00 +0800）
    end_of_day=True → 当日 23:59:59 +0800（如 '2012-12-31' → 1356969599）
    """
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    if end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59)
    return int(dt.replace(tzinfo=CST).timestamp())


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
            raise RuntimeError("Cookie 未设置。请先运行: uv run crawl_blog.py --set-cookie '...'")
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

    def _fill_retweet_longtext(self, parsed: dict) -> None:
        """转发的原微博若为长文，调 longtext 接口补全 long_text，回写 retweeted_json。"""
        if not parsed["retweeted_json"]:
            return
        rt = json.loads(parsed["retweeted_json"])
        if not rt.get("is_long_text"):
            return
        rt["long_text"] = self.fetch_longtext(rt["mblogid"])
        parsed["retweeted_json"] = json.dumps(rt, ensure_ascii=False)

    def fetch_mymblog(self, uid: int, page: int, since_id: str = "") -> tuple[str, list[dict]]:
        """调用 mymblog 接口，返回 (next_since_id, posts_raw_list)

        按微博要求传 page + since_id 翻页（since_id 是服务端下发的游标，必须
        回传）。深翻（数百页）时偶发 414 Request-URI Too Large，此时降级重试
        一次：去掉 since_id 仅用 page 翻页，避免直接崩溃丢失整轮进度。降级重试
        仍 414 则抛出，由编排层优雅停止。
        """
        params = {"uid": uid, "page": page, "feature": 0}
        if since_id:
            params["since_id"] = since_id
        self.session.headers["referer"] = f"{API_BASE}/u/{uid}"
        try:
            resp = _request_with_retry(
                self.session, "GET", f"{API_BASE}/ajax/statuses/mymblog",
                params=params, timeout=15,
            )
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 414 and since_id:
                log.warning("  page %d 触发 414，降级重试（仅用 page，不带 since_id）", page)
                fallback = {k: v for k, v in params.items() if k != "since_id"}
                resp = _request_with_retry(
                    self.session, "GET", f"{API_BASE}/ajax/statuses/mymblog",
                    params=fallback, timeout=15,
                )
            else:
                raise
        data = resp.json().get("data", {}) or {}
        return data.get("since_id", "") or "", data.get("list", []) or []

    def fetch_longtext(self, mblogid: str) -> str:
        """调用 longtext 接口，返回长文全文"""
        self.session.headers["referer"] = f"{API_BASE}/"
        resp = _request_with_retry(
            self.session, "GET", f"{API_BASE}/ajax/statuses/longtext",
            params={"id": mblogid}, timeout=15,
        )
        return resp.json().get("data", {}).get("longTextContent", "") or ""

    def fetch_searchprofile(self, uid: int, page: int,
                            starttime: int, endtime: int) -> tuple[list[dict], int]:
        """调用 searchProfile 接口，返回 (posts_raw_list, total)

        返回的 list 内部是「新→旧」排列（首条最新），与 mymblog 相反。
        total 是该时间范围内的微博总数（字符串转 int），仅用于日志展示；
        翻页终止以 list 为空为准。
        """
        params = {
            "uid": uid, "page": page,
            "starttime": starttime, "endtime": endtime,
            "hasori": 1, "hasret": 1, "hastext": 1,
            "haspic": 1, "hasvideo": 1, "hasmusic": 1,
        }
        self.session.headers["referer"] = f"{API_BASE}/u/{uid}"
        resp = _request_with_retry(
            self.session, "GET", f"{API_BASE}/ajax/statuses/searchProfile",
            params=params, timeout=15,
        )
        data = resp.json().get("data", {}) or {}
        total = int(data.get("total", 0) or 0)  # "934" → 934
        return data.get("list", []) or [], total

    # ── 编排：全量回填 ───────────────────────────────

    def crawl_blog_backfill(self, uid: int, start_page: int = 1) -> dict:
        """全量回填：从 page=start_page 翻到 list 为空。

        start_page>1 用于断点续抓（如上次撞 414 停在 page 962，从 963 继续）。
        从中间页开始时 since_id 留空（无法接上游标）、跳过博主提取（前面页已存）。
        """
        new_count = 0
        page = start_page
        since_id = ""
        blogger_saved = start_page > 1  # 中途开始则视为博主已存，跳过提取

        while True:
            try:
                since_id, posts = self.fetch_mymblog(uid, page, since_id)
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 414:
                    log.warning("  page %d 触发 414（URI 过长），停止回填，已抓 %d 条", page, new_count)
                    break
                raise
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
                try:
                    self._fill_retweet_longtext(parsed)
                except Exception as e:
                    log.warning("  转发长文补全失败 mblogid=%s: %s", parsed["mblogid"], e)
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
        blogger_saved = False

        while True:
            try:
                since_id, posts = self.fetch_mymblog(uid, page, since_id)
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 414:
                    log.warning("  page %d 触发 414（URI 过长），停止增量，新增 %d 条", page, new_count)
                    break
                raise
            if not posts:
                break

            # 首页提取博主信息（顺带刷新昵称/头像等）
            if not blogger_saved and posts[0].get("user"):
                save_blogger(self.conn, parse_blogger(posts[0]["user"]))
                blogger_saved = True

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
                try:
                    self._fill_retweet_longtext(parsed)
                except Exception as e:
                    log.warning("  转发长文补全失败 mblogid=%s: %s", parsed["mblogid"], e)
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

    # ── 编排：按时间范围抓取 ───────────────────────

    def crawl_blog_by_range(self, uid: int, start_date: str, end_date: str) -> dict:
        """按时间范围抓取（补全历史缺口）。

        将范围按日拆分，逐日调 searchProfile（starttime/endtime=当天起止），
        逐页翻到 list 空。按日拆分避免按月范围翻页时分页边界丢数据——单日
        数据量通常 ≤ 50 条（一页即够），高频日仍会翻页，不丢数据。

        list 新→旧，逐条 parse_post → 长文补全 → save_post（mblogid 去重）。
        数据入 weibo_posts 表，与 mymblog 抓取的数据混存，靠 mblogid UNIQUE
        去重。单日失败不中断整体（记录告警跳过当日，已抓数据不丢）。
        """
        d_start = datetime.strptime(start_date, "%Y-%m-%d")
        d_end = datetime.strptime(end_date, "%Y-%m-%d")
        if d_start > d_end:
            d_start, d_end = d_end, d_start  # 容错：保证 start <= end

        days = []
        d = d_start
        while d <= d_end:
            days.append(d)
            d += timedelta(days=1)

        new_count = 0
        blogger_saved = False

        log.info("  按日抓取 uid=%d %s~%s（共 %d 天）",
                 uid, start_date, end_date, len(days))

        for i, day in enumerate(days, 1):
            day_str = day.strftime("%Y-%m-%d")
            starttime = _date_to_timestamp(day_str, end_of_day=False)
            endtime = _date_to_timestamp(day_str, end_of_day=True)
            day_new = 0
            page = 1
            day_total = 0

            try:
                while True:
                    posts, day_total = self.fetch_searchprofile(
                        uid, page, starttime, endtime)
                    if not posts:
                        break

                    # 首次提取博主信息（跨天只提一次）
                    if not blogger_saved and posts[0].get("user"):
                        save_blogger(self.conn, parse_blogger(posts[0]["user"]))
                        blogger_saved = True

                    for raw in posts:  # list 新→旧
                        parsed = parse_post(raw)
                        if parsed["is_long_text"]:
                            try:
                                parsed["long_text"] = self.fetch_longtext(parsed["mblogid"])
                            except Exception as e:
                                log.warning("  长文补全失败 mblogid=%s: %s",
                                            parsed["mblogid"], e)
                        try:
                            self._fill_retweet_longtext(parsed)
                        except Exception as e:
                            log.warning("  转发长文补全失败 mblogid=%s: %s",
                                        parsed["mblogid"], e)
                        if save_post(self.conn, parsed):
                            day_new += 1

                    log.info("  %s page %d: +%d (当日累计 %d/%s)",
                             day_str, page, len(posts), day_new, day_total or "?")
                    page += 1
                    _jitter_sleep(0.5)
            except Exception as e:
                log.warning("  %s 抓取失败: %s（跳过当日，已抓不丢）", day_str, e)

            new_count += day_new
            if day_new or i % 10 == 0 or i == len(days):
                log.info("▶ [%d/%d] %s: +%d（累计 %d）",
                         i, len(days), day_str, day_new, new_count)

        log.info("  范围抓取完成 uid=%d %s~%s: %d 条",
                 uid, start_date, end_date, new_count)
        return {"new": new_count, "total": new_count}

    def crawl_blog(self, uid: int, full: bool = False, start_page: int = 1) -> dict:
        """抓取博主微博。full=True 或无已存数据 → 全量回填，否则增量。

        start_page 仅对 full 回填有效，用于断点续抓。
        """
        if full or get_latest_post_id(self.conn, uid) is None:
            return self.crawl_blog_backfill(uid, start_page=start_page)
        return self.crawl_blog_incremental(uid)


# ── cookie 续期（Playwright 扫码） ─────────────────

QRCODE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "qrcode.png")


def _open_image_cross_platform(path: str):
    """用系统默认程序打开图片，失败静默忽略（仅扫码便利性）"""
    if not os.path.isfile(path):
        return
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", path], check=False)
        else:
            subprocess.run(["xdg-open", path], check=False)
    except Exception:
        pass


def check_playwright() -> bool:
    """检查 Playwright + Chromium 环境是否就绪"""
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
        log.info("✅ playwright Python 包可导入")
    except ImportError:
        log.error("❌ playwright Python 包未安装（当前解释器: %s）", sys.executable)
        log.error("   → uv pip install playwright --python %s", sys.executable)
        log.error("   → %s -m playwright install chromium", sys.executable)
        return False
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
            browser.close()
        log.info("✅ Chromium 启动正常")
    except Exception as e:
        log.error("❌ Chromium 启动失败: %s", str(e).split("\n")[0])
        log.error("   → %s -m playwright install chromium", sys.executable)
        return False
    return True


def _is_logged_in(page) -> bool:
    """判断 api.weibo.com/chat 当前是否处于登录态。

    判据：未登录时 URL 为 ``https://api.weibo.com/chat#/``，
    扫码登录成功后 hash 路由跳转为 ``.../#/chat``。

    不依赖 cookie 中的 SUB（未登录态 weibo 也会下发匿名 SUB），
    也不依赖 ``a[href*="/u/"]``（登录页的热门博主推荐就有大量此类链接）。
    用 URL hash 变化作为唯一可靠判据。
    """
    try:
        href = page.evaluate("window.location.href") or ""
    except Exception:
        return False
    return "#/chat" in href


def renew_cookie(db_path: str, headless: bool = False) -> str:
    """用 Playwright 打开 api.weibo.com/chat 扫码登录，提取 cookie 存入数据库并返回

    选用 api.weibo.com/chat 而非 weibo.com：该页面未登录时直接渲染二维码
    （截图即可扫码），登录成功后 hash 路由由 ``#/`` 变为 ``#/chat``，判据可靠。
    weibo.com 首页登录是 SPA 弹层，二维码未必渲染、URL 也不稳定。

    Args:
        db_path: 数据库路径
        headless: True=无头模式（截图保存文件靠图片查看二维码）；
                  False=有头模式（直接弹出浏览器窗口扫码，更直观）。
    """
    if not check_playwright():
        raise RuntimeError("Playwright 环境未就绪，请按上面提示安装后重试")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        launch_args = ["--disable-blink-features=AutomationControlled"]
        if headless:
            launch_args += ["--no-sandbox", "--disable-setuid-sandbox"]
        browser = pw.chromium.launch(headless=headless, args=launch_args)
        try:
            ctx = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
                locale="zh-CN",
            )
            page = ctx.new_page()

            log.info("打开 api.weibo.com/chat ...")
            page.goto("https://api.weibo.com/chat", wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)

            current_href = page.evaluate("window.location.href")
            log.info("当前 URL: %s", current_href)

            if _is_logged_in(page):
                log.info("已有有效登录态，直接提取 cookie")
            else:
                # 截图二维码，无头模式下尝试用系统程序打开
                page.screenshot(path=QRCODE_PATH)
                log.info("=" * 60)
                log.info("📱 二维码已截图 -> %s", QRCODE_PATH)
                log.info("请用微博 APP 扫码登录（等待最多 120 秒）")
                if headless:
                    _open_image_cross_platform(QRCODE_PATH)
                else:
                    log.info("（如未自动弹出二维码，请查看上面的图片路径）")
                log.info("=" * 60)

                log.info("等待扫码...")
                detected = False
                for _ in range(120):
                    time.sleep(1)
                    try:
                        if _is_logged_in(page):
                            log.info("🔍 检测到扫码登录，正在处理...")
                            try:
                                page.wait_for_load_state("networkidle", timeout=15000)
                            except Exception:
                                pass
                            time.sleep(2)
                            detected = True
                            break
                    except Exception:
                        break
                if not detected:
                    browser.close()
                    raise RuntimeError("扫码超时（120 秒），请重新运行 --renew-cookie")

            # 只拿 .weibo.com 域名的 cookie
            raw_cookies = ctx.cookies()
            deduped: dict[str, str] = {}
            for c in raw_cookies:
                domain = c.get("domain", "")
                if not domain.endswith(".weibo.com") and domain != "weibo.com":
                    continue
                deduped[c["name"]] = c["value"]
            cookie_str = "; ".join(f"{k}={v}" for k, v in sorted(deduped.items()))
        finally:
            browser.close()

    if "SUB" not in deduped:
        raise RuntimeError("未提取到 SUB cookie，登录可能未成功")

    # 存入数据库
    set_db_path(db_path)
    conn = get_conn()
    set_cookie(conn, cookie_str)
    conn.close()
    log.info("✅ Cookie 已存入数据库 (%s)", db_path)
    return cookie_str
