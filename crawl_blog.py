#!/usr/bin/env python3
"""微博博主微博抓取 CLI

用法:
    python crawl_blog.py --set-cookie 'SUB=xxx; ...'   # 设置 cookie
    python crawl_blog.py --uid 1401527553               # 增量抓取
    python crawl_blog.py --uid 1401527553 --full        # 全量回填
    python crawl_blog.py --all                          # 增量抓取所有已存博主
    python crawl_blog.py --renew-cookie                 # 浏览器扫码续期 cookie
    python crawl_blog.py --check-playwright             # 检查 Playwright 环境就绪
"""
import argparse
import logging
import sqlite3
import sys

from weibo_blog.crawler import BlogCrawler
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

    # 抓取模式
    if not args.uid and not args.all:
        parser.print_help()
        sys.exit(1)

    crawler = BlogCrawler(db_path=args.db)

    if args.uid:
        result = crawler.crawl_blog(args.uid, full=args.full)
        print(f"uid={args.uid}: 新增 {result['new']} 条")
    elif args.all:
        for b in get_blogger_list(crawler.conn):
            try:
                result = crawler.crawl_blog(b["uid"])
                print(f"[{b['screen_name']}] +{result['new']}")
            except Exception as e:
                logging.warning("uid=%s 抓取失败: %s", b["uid"], e)


if __name__ == "__main__":
    main()
