#!/usr/bin/env python3
"""微博博主微博抓取 CLI

用法:
    python crawl_blog.py --set-cookie 'SUB=xxx; ...'   # 设置 cookie
    python crawl_blog.py --uid 1401527553               # 增量抓取
    python crawl_blog.py --uid 1401527553 --full        # 全量回填
    python crawl_blog.py --uid 1401527553 --full --start-page 963  # 断点续抓
    python crawl_blog.py --all                          # 增量抓取所有已存博主
    python crawl_blog.py --renew-cookie                 # 浏览器扫码续期 cookie
    python crawl_blog.py --check-playwright             # 检查 Playwright 环境就绪
"""
import argparse
import logging
import sqlite3
import sys

from weibo_blog.crawler import BlogCrawler, CookieExpiredError
from weibo_blog.db import init_db, set_cookie, get_blogger_list


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
    parser.add_argument("--start-page", type=int, default=1,
                        help="全量回填起始页码（断点续抓，如上次 414 停在 962 则 --start-page 963）")
    parser.add_argument("--start", default="",
                        help="起始日期 YYYY-MM-DD（与 --end 配合，按时间范围抓取）")
    parser.add_argument("--end", default="",
                        help="结束日期 YYYY-MM-DD（含当天，与 --start 配合）")
    parser.add_argument("--all", action="store_true", help="增量抓取所有已存博主")
    parser.add_argument("--renew-cookie", action="store_true", help="浏览器扫码续期 cookie")
    parser.add_argument("--check-playwright", action="store_true",
                        help="检查 Playwright + Chromium 环境就绪")
    parser.add_argument("--headless", action="store_true",
                        help="--renew-cookie 时无头模式（仅截图，不弹窗；默认有头弹窗）")
    args = parser.parse_args()

    # --set-cookie: 只存 cookie 退出
    if args.set_cookie:
        conn = sqlite3.connect(args.db)
        conn.row_factory = sqlite3.Row
        init_db(conn)
        set_cookie(conn, args.set_cookie)
        print(f"cookie 已保存到 {args.db}")
        return

    # --check-playwright: 验证环境
    if args.check_playwright:
        from weibo_blog.crawler import check_playwright
        ok = check_playwright()
        sys.exit(0 if ok else 1)

    # --renew-cookie: 扫码登录提取 cookie
    if args.renew_cookie:
        from weibo_blog.crawler import renew_cookie
        cookie = renew_cookie(db_path=args.db, headless=args.headless)
        print(f"cookie 已续期并保存到 {args.db}（长度 {len(cookie)}）")
        return

    # --start/--end：按时间范围抓取
    if args.start or args.end:
        if not (args.start and args.end):
            parser.error("--start 和 --end 必须同时指定")
        if args.full:
            parser.error("--start/--end 与 --full 互斥")
        if args.all:
            parser.error("--start/--end 与 --all 互斥")
        if not args.uid:
            parser.error("--start/--end 需配合 --uid")
        from datetime import datetime as _dt
        d1 = _dt.strptime(args.start, "%Y-%m-%d")
        d2 = _dt.strptime(args.end, "%Y-%m-%d")
        if d1 > d2:
            parser.error("--start 不能晚于 --end")
        crawler = BlogCrawler(db_path=args.db)
        result = crawler.crawl_blog_by_range(args.uid, args.start, args.end)
        print(f"uid={args.uid} {args.start}~{args.end}: 新增 {result['new']} 条")
        return

    # 抓取模式
    if not args.uid and not args.all:
        parser.print_help()
        sys.exit(1)

    crawler = BlogCrawler(db_path=args.db)

    if args.uid:
        result = crawler.crawl_blog(args.uid, full=args.full, start_page=args.start_page)
        print(f"uid={args.uid}: 新增 {result['new']} 条")
    elif args.all:
        for b in get_blogger_list(crawler.conn):
            try:
                result = crawler.crawl_blog(b["uid"])
                print(f"[{b['screen_name']}] +{result['new']}")
            except CookieExpiredError:
                raise
            except Exception as e:
                logging.warning("uid=%s 抓取失败: %s", b["uid"], e)


def cli():
    try:
        main()
    except CookieExpiredError:
        logging.error(
            "Cookie 已过期，请运行: uv run crawl_blog.py --renew-cookie"
        )
        raise SystemExit(2)


if __name__ == "__main__":
    cli()
