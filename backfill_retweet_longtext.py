"""回填历史转发微博的长文原微博内容。

扫描 weibo_posts 中 retweeted_json 非空的记录，对原微博 is_long_text=1 且
long_text 为空的，逐条调 longtext 接口补全并 UPDATE。慢速抖动，避免风控。
"""
import json
import sqlite3
import argparse
import logging

from weibo_blog.crawler import BlogCrawler, _jitter_sleep

log = logging.getLogger("backfill_rt_longtext")


def main(db_path: str) -> None:
    crawler = BlogCrawler(db_path)  # 读库内 cookie，无则抛错提示 --set-cookie
    conn = crawler.conn
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, retweeted_json FROM weibo_posts WHERE retweeted_json != ''"
    ).fetchall()

    filled = skipped_done = skipped_notlong = failed = 0
    for r in rows:
        try:
            rt = json.loads(r["retweeted_json"])
        except (ValueError, TypeError):
            continue
        if not rt.get("is_long_text"):
            skipped_notlong += 1
            continue
        if rt.get("long_text"):
            skipped_done += 1
            continue
        try:
            rt["long_text"] = crawler.fetch_longtext(rt["mblogid"])
            conn.execute(
                "UPDATE weibo_posts SET retweeted_json=? WHERE id=?",
                (json.dumps(rt, ensure_ascii=False), r["id"]),
            )
            conn.commit()
            filled += 1
            log.info("补全 id=%s mblogid=%s (第 %d 条)", r["id"], rt["mblogid"], filled)
        except Exception as e:
            failed += 1
            log.warning("失败 id=%s mblogid=%s: %s", r["id"], rt["mblogid"], e)
        _jitter_sleep(0.5)

    log.info("完成：补全 %d，已补跳过 %d，非长文跳过 %d，失败 %d",
             filled, skipped_done, skipped_notlong, failed)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="回填转发长文原微博内容")
    ap.add_argument("--db", default="weibo_blog.db", help="数据库路径")
    args = ap.parse_args()
    main(args.db)
