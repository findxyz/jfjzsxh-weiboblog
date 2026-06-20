"""一次性回填脚本：从 raw_json 重新提取转发原微博的 pics/video，补进 retweeted_json。

背景：早期 parser 的 retweeted_json 只存了 text/uid 等基本字段，没存原微博的
pics/video。查看器要在转发引用块里展示原微博媒体，需要回填。新抓的数据已由
parser 直接带上，本脚本只补历史数据。

用法：uv run backfill_retweet_media.py
"""
import json
import sqlite3
import sys

from weibo_blog.parser import _extract_pics, _extract_video


def backfill(db_path: str = "weibo_blog.db") -> dict:
    """回填所有转发微博的 retweeted_json，返回统计。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, raw_json, retweeted_json FROM weibo_posts "
        "WHERE retweeted_json != '' AND retweeted_json IS NOT NULL"
    ).fetchall()

    updated = 0
    with_media = 0
    for row in rows:
        rt = json.loads(row["retweeted_json"])
        # 已有 pics 字段则跳过（新抓的数据已带）
        if "pics" in rt and "video_url" in rt:
            continue
        raw = json.loads(row["raw_json"])
        rt_status = raw.get("retweeted_status") or {}
        rt["pics"] = _extract_pics(rt_status)
        rt["video_url"] = _extract_video(rt_status)
        if rt["pics"] or rt["video_url"]:
            with_media += 1
        conn.execute(
            "UPDATE weibo_posts SET retweeted_json=? WHERE id=?",
            (json.dumps(rt, ensure_ascii=False), row["id"]),
        )
        updated += 1
    conn.commit()
    conn.close()
    return {"total_retweets": len(rows), "updated": updated, "with_media": with_media}


if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else "weibo_blog.db"
    stats = backfill(db)
    print(f"回填完成：共 {stats['total_retweets']} 条转发，更新 {stats['updated']} 条，"
          f"其中 {stats['with_media']} 条原微博含媒体")
