#!/usr/bin/env python3
"""长文补全修复脚本（一次性 / 可重复执行）

用途：修复 weibo_blog.db 中 is_long_text=1 但 long_text 为空（或
retweeted_json 里转发原帖 long_text 为空）的历史微博。这些记录来自
抓取时 longtext 接口偶发 414 / 限流，正文被截断在 text_raw（常见 147 字）。

默认 dry-run；加 --execute 才写库。

用法：
    python repair_longtext.py --limit 10                 # 看前 10 条待修
    python repair_longtext.py --uid 1401527553 --limit 50
    python repair_longtext.py --limit 200 --sleep 0.8 --execute
    python repair_longtext.py --only-retweets --limit 100 --execute

特性：
    - PC 接口 414/403/5xx 自动退避重试，仍失败则改用 m.weibo.cn 接口
    - 逐条限速（默认 0.8s），避免再次触发风控
    - 只有拿到非空长文才写库；失败条目原样保留，下次可重跑
"""
import argparse
import json
import random
import sqlite3
import sys
import time
import warnings

import requests

warnings.filterwarnings("ignore")

PC_URL = "https://weibo.com/ajax/statuses/longtext"
M_URL = "https://m.weibo.cn/statuses/extend"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36 Edg/149.0.0.0"
)


def make_session(cookie: str) -> requests.Session:
    s = requests.Session()
    s.verify = False
    s.headers.update({
        "accept": "application/json, text/plain, */*",
        "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
        "user-agent": UA,
        "x-requested-with": "XMLHttpRequest",
    })
    for part in cookie.split("; "):
        if "=" in part:
            k, v = part.split("=", 1)
            s.cookies.set(k, v)
    return s


def get_cookie(db: str) -> str:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    row = conn.execute("select value from config where key='weibo_cookie'").fetchone()
    conn.close()
    if not row or not row[0]:
        sys.exit("DB 中没有 weibo_cookie，先运行 crawl_blog.py --set-cookie")
    return row[0]


def fetch_text(session: requests.Session, mblogid: str, retries: int = 3) -> str:
    """先 PC 接口，失败再试 m.weibo.cn 接口。返回空串表示失败。"""
    for attempt in range(retries):
        try:
            r = session.get(PC_URL, params={"id": mblogid},
                            headers={"referer": "https://weibo.com/"}, timeout=15)
            if r.status_code == 200:
                data = (r.json() or {}).get("data") or {}
                txt = data.get("longTextContent") or ""
                if txt:
                    return txt
            elif r.status_code in (403, 414, 429) or r.status_code >= 500:
                pass  # 走退避
            else:
                pass
        except Exception:
            pass
        time.sleep((2 ** attempt) * (1 + random.random() * 0.5))
    # 移动端兜底
    try:
        r = session.get(M_URL, params={"id": mblogid},
                        headers={"referer": "https://m.weibo.cn/"}, timeout=15)
        if r.status_code == 200:
            return ((r.json() or {}).get("data") or {}).get("longTextContent") or ""
    except Exception:
        pass
    return ""


def select_rows(conn, uid: int, only_retweets: bool, limit: int):
    conds = []
    params = []
    if uid:
        conds.append("uid = ?")
        params.append(uid)
    if only_retweets:
        conds.append("retweeted_json != '' and json_extract(retweeted_json,'$.is_long_text')=1 "
                     "and coalesce(json_extract(retweeted_json,'$.long_text'),'')=''")
    else:
        conds.append("is_long_text = 1 and (long_text is null or long_text = '')")
    sql = ("select id, uid, mblogid, retweeted_json from weibo_posts where "
           + " and ".join(conds) + " order by created_at desc limit ?")
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def fix_retweet(rt_json: str, mblogid: str, text: str) -> str:
    rt = json.loads(rt_json)
    rt["long_text"] = text
    # 原帖 mblogid 仅用于定位，不写入
    return json.dumps(rt, ensure_ascii=False)


def main():
    ap = argparse.ArgumentParser(description="修复缺失的长文全文")
    ap.add_argument("--db", default="weibo_blog.db")
    ap.add_argument("--uid", type=int, default=0)
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--sleep", type=float, default=0.8)
    ap.add_argument("--execute", action="store_true", help="实际写库（默认 dry-run）")
    ap.add_argument("--only-retweets", action="store_true",
                    help="只修转发原帖的 long_text，不修主帖")
    args = ap.parse_args()

    cookie = get_cookie(args.db)
    session = make_session(cookie)
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    rows = select_rows(conn, args.uid, args.only_retweets, args.limit)
    print(f"待修 {len(rows)} 条（{'仅转发' if args.only_retweets else '主帖+转发'}，"
          f"{'写库' if args.execute else 'dry-run'}）")
    ok = fail = 0
    for i, row in enumerate(rows, 1):
        mid = row["mblogid"]
        # 转发原帖的 mblogid 在 retweeted_json 里
        target_mid = mid
        if args.only_retweets or (row["retweeted_json"] and not row["mblogid"]):
            try:
                target_mid = (json.loads(row["retweeted_json"]).get("mblogid") or mid)
            except Exception:
                target_mid = mid
        text = fetch_text(session, target_mid)
        if not text:
            fail += 1
            print(f"[{i}/{len(rows)}] FAIL {target_mid}")
        else:
            ok += 1
            print(f"[{i}/{len(rows)}] OK   {target_mid} len={len(text)}")
            if args.execute:
                if args.only_retweets or (row["retweeted_json"] and not row["mblogid"]):
                    conn.execute("update weibo_posts set retweeted_json=? where id=?",
                                 (fix_retweet(row["retweeted_json"], target_mid, text), row["id"]))
                else:
                    conn.execute("update weibo_posts set long_text=? where id=?",
                                 (text, row["id"]))
                conn.commit()
        time.sleep(args.sleep + random.random() * 0.3)

    print(f"完成：成功 {ok}，失败 {fail}")
    if fail and not args.execute:
        print("（dry-run 未写库；失败的条目可稍后重试）")
    conn.close()


if __name__ == "__main__":
    main()
