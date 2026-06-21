# 设计：转发原微博长文显示完整内容

- 日期：2026-06-21
- 范围：`weibo_blog/parser.py` + `weibo_blog/crawler.py` + `web/app.js` + 新增 `backfill_retweet_longtext.py`
- 目标：转发的原微博若是长文，查看器显示完整内容（`long_text`），而非截断前缀 `text_raw`。

## 根因

转发原微博的长文内容从未抓取，三层都缺：

1. **解析层** `weibo_blog/parser.py:60-69` 构造 `retweeted_json` 时未存 `is_long_text` / `long_text`。
2. **抓取层** `weibo_blog/crawler.py` 回填（`:159-167`）与增量（`:206-217`）两处循环只对本微博 `is_long_text` 调 `fetch_longtext`，从不对转发的原微博调用。
3. **渲染层** `web/app.js:276` 转发块只用 `rt.text_raw`（长文时是截断前缀）。

故转发长文原微博的完整内容压根没入库，前端自然显示不全。

## 数据现状

- 转发微博总数 11640，其中原微博是长文 2804 条（从 `raw_json.retweeted_status.isLongText` 判定）。
- 这些历史记录的 `retweeted_json` 里无 `long_text`，需补抓。

## 决策

| 项 | 决策 |
| --- | --- |
| 解析层 | `retweeted_json` 增加 `is_long_text` + `long_text`（初始空串） |
| 抓取层 | crawler 两处循环里，转发的原微博 `is_long_text=1` 时调 `fetch_longtext` 补全 |
| 渲染层 | 前端转发块复用正文长文逻辑：`rt.is_long_text && rt.long_text` 时显示 `long_text` |
| 历史数据 | 新增 `backfill_retweet_longtext.py` 补抓脚本回填 |
| 补抓限速 | 复用 `_jitter_sleep(0.5)`，慢速抖动，约 20-35 分钟跑完 2804 条 |
| 转发块长文显示 | 直接显示完整 `long_text`，不做点击展开 |

## 改动明细

### 1. 解析层 `weibo_blog/parser.py`

`parse_post` 中构造 `retweeted_json` 的 dict 增加 `is_long_text` 与 `long_text` 两字段：

```python
        retweeted_json = json.dumps({
            "post_id": rt.get("id", 0),
            "mblogid": rt.get("mblogid", ""),
            "text_raw": rt.get("text_raw", ""),
            "uid": rt_user.get("id", 0),
            "screen_name": rt_user.get("screen_name", ""),
            "created_at": rt.get("created_at", ""),
            "is_long_text": 1 if rt.get("isLongText") else 0,
            "long_text": "",
            "pics": _extract_pics(rt),
            "video_url": _extract_video(rt),
        }, ensure_ascii=False)
```

`long_text` 初始为空串，由 crawler 填充，与本微博 `long_text` 的处理一致（parser 只置空）。

### 2. 抓取层 `weibo_blog/crawler.py`

`BlogCrawler` 新增辅助方法：

```python
    def _fill_retweet_longtext(self, parsed: dict) -> None:
        """转发的原微博若为长文，调 longtext 接口补全 long_text，回写 retweeted_json。"""
        if not parsed["retweeted_json"]:
            return
        rt = json.loads(parsed["retweeted_json"])
        if not rt.get("is_long_text"):
            return
        rt["long_text"] = self.fetch_longtext(rt["mblogid"])
        parsed["retweeted_json"] = json.dumps(rt, ensure_ascii=False)
```

回填循环（`crawl_blog_backfill`，`:159-167`）与增量循环（`crawl_blog_incremental`，`:206-217`），在本微博长文补全之后、`save_post` 之前，新增转发长文补全：

```python
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
```

两处循环改法一致。转发长文补全失败不影响本微博入库（仅 warning）。

### 3. 渲染层 `web/app.js`

转发块渲染（`:271-279`）复用正文长文逻辑：

```js
  // 转发原微博引用块（橙色竖线，含原微博媒体）
  if (p.retweeted && p.retweeted.text_raw) {
    const rt = p.retweeted;
    const rtUrl = `https://weibo.com/${rt.uid}/${rt.mblogid}`;
    // 长文：显示完整 long_text；否则 text_raw
    const rtText = (rt.is_long_text && rt.long_text) ? rt.long_text : rt.text_raw;
    html += `<div class="post-retweet">` +
      `<a class="retweet-name" href="${escHtml(rtUrl)}" target="_blank" rel="noopener">@${escHtml(rt.screen_name || "")}</a>` +
      `<div class="retweet-text">${linkify(escHtml(rtText))}</div>` +
      mediaHtml(rt.pics, rt.video_url, rtUrl) +
      `</div>`;
  }
```

### 4. 补抓脚本 `backfill_retweet_longtext.py`（新建，仓库根）

复用 `BlogCrawler.fetch_longtext` 与 `_jitter_sleep`。需要 cookie（`BlogCrawler.__init__` 无 cookie 会抛错）。

```python
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
```

## 不改的部分

- 本微博长文逻辑（parser `long_text` 置空 + crawler 补全 + 前端正文显示）不动。
- 转发块样式（`.retweet-text` 13px）不动。
- `/api/posts` 返回结构不变（`_parse_retweeted` 原样返回 dict，多了 `is_long_text`/`long_text` 字段前端自然读到）。
- `save_post` / DB schema 不动（`retweeted_json` 是 TEXT 字段，内容变长无影响）。
- `post_with_retweet.json` fixture 不动（其原微博非长文）。

## 验证

1. **单元测试** `tests/test_parser.py`：新增 `test_parse_post_retweet_longtext_flag`，构造 `raw["retweeted_status"]["isLongText"]=True`，断言 `retweeted_json` 解析后 `is_long_text==1`、`long_text==""`。现有 `test_parse_post_with_retweet` 逐字段断言、不锁字段集合，加字段不破坏。
2. **抓取层**：跑一次小范围增量（或单条），确认转发长文原微博的 `retweeted_json` 里 `long_text` 非空。
3. **补抓脚本**：`python backfill_retweet_longtext.py`，日志显示补全计数；跑完查 DB 确认 `is_long_text=1` 的转发记录 `long_text` 非空。
4. **前端**：强刷查看器，转发长文块显示完整内容（不再是截断前缀）。
5. **回归**：非长文转发、无转发微博、本微博长文显示均不受影响；`pytest` 全绿。
