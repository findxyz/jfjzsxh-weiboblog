# 转发原微博长文显示完整内容 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 转发的原微博若是长文，查看器显示完整 `long_text` 而非截断 `text_raw`；并补抓历史 2804 条转发长文。

**Architecture:** 解析层 `retweeted_json` 加 `is_long_text`/`long_text` 字段；抓取层 crawler 补调 `fetch_longtext`；渲染层前端复用长文逻辑；新增补抓脚本回填历史数据。

**Tech Stack:** Python（stdlib）/ 原生 JS

**Spec:** `docs/superpowers/specs/2026-06-21-retweet-longtext-design.md`

---

## 当前代码现状（修改前）

`weibo_blog/parser.py:55-69`（`retweeted_json` 构造）：
```python
    # 转发原微博精简（含 pics/video，便于查看器展示原微博媒体）
    retweeted_json = ""
    rt = raw.get("retweeted_status")
    if rt:
        rt_user = rt.get("user", {}) or {}
        retweeted_json = json.dumps({
            "post_id": rt.get("id", 0),
            "mblogid": rt.get("mblogid", ""),
            "text_raw": rt.get("text_raw", ""),
            "uid": rt_user.get("id", 0),
            "screen_name": rt_user.get("screen_name", ""),
            "created_at": rt.get("created_at", ""),
            "pics": _extract_pics(rt),
            "video_url": _extract_video(rt),
        }, ensure_ascii=False)
```

`weibo_blog/crawler.py:64-75`（`BlogCrawler.__init__`，新方法放其后）：
```python
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
```

`weibo_blog/crawler.py:159-171`（`crawl_blog_backfill` 循环体）：
```python
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
```

`weibo_blog/crawler.py:206-217`（`crawl_blog_incremental` 循环体）：
```python
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
```

`web/app.js:270-279`（转发块渲染）：
```js
  // 转发原微博引用块（橙色竖线，含原微博媒体）
  if (p.retweeted && p.retweeted.text_raw) {
    const rt = p.retweeted;
    const rtUrl = `https://weibo.com/${rt.uid}/${rt.mblogid}`;
    html += `<div class="post-retweet">` +
      `<a class="retweet-name" href="${escHtml(rtUrl)}" target="_blank" rel="noopener">@${escHtml(rt.screen_name || "")}</a>` +
      `<div class="retweet-text">${linkify(escHtml(rt.text_raw))}</div>` +
      mediaHtml(rt.pics, rt.video_url, rtUrl) +
      `</div>`;
  }
```

---

### Task 1: 解析层 retweeted_json 加 is_long_text / long_text 字段

**Files:**
- Modify: `weibo_blog/parser.py:60-69`（`retweeted_json` 构造的 dict）

- [ ] **Step 1: 给 retweeted_json 的 dict 加两字段**

把 `weibo_blog/parser.py` 中的：

```python
        retweeted_json = json.dumps({
            "post_id": rt.get("id", 0),
            "mblogid": rt.get("mblogid", ""),
            "text_raw": rt.get("text_raw", ""),
            "uid": rt_user.get("id", 0),
            "screen_name": rt_user.get("screen_name", ""),
            "created_at": rt.get("created_at", ""),
            "pics": _extract_pics(rt),
            "video_url": _extract_video(rt),
        }, ensure_ascii=False)
```

改为：

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

- [ ] **Step 2: 写测试断言新字段**

在 `tests/test_parser.py` 的 `test_parse_post_retweet_includes_pics_and_video`（约 `:70`）之后新增：

```python
def test_parse_post_retweet_longtext_flag():
    """转发原微博 isLongText=True → retweeted_json 含 is_long_text=1、long_text 空串"""
    raw = load_fixture("post_with_retweet.json")
    raw["retweeted_status"]["isLongText"] = True
    p = parse_post(raw)
    rt = json.loads(p["retweeted_json"])
    assert rt["is_long_text"] == 1
    assert rt["long_text"] == ""  # 由 crawler 补全，parser 只置空
```

- [ ] **Step 3: 运行测试确认通过**

```bash
uv run pytest tests/test_parser.py -v
```
Expected: 全部 PASS（含新增 `test_parse_post_retweet_longtext_flag` 与原有转发相关测试）。

- [ ] **Step 4: 提交**

```bash
git add weibo_blog/parser.py tests/test_parser.py
git commit -m "feat(parser): retweeted_json 增加 is_long_text/long_text 字段"
```

---

### Task 2: 抓取层 crawler 补全转发长文

**Files:**
- Modify: `weibo_blog/crawler.py`（`BlogCrawler` 新增方法 + 两处循环）

- [ ] **Step 1: 新增 `_fill_retweet_longtext` 方法**

在 `weibo_blog/crawler.py` 的 `BlogCrawler._make_session` 方法之后（`__init__` 之后），新增方法：

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

- [ ] **Step 2: 回填循环加转发长文补全**

把 `weibo_blog/crawler.py` `crawl_blog_backfill` 中的：

```python
            for raw in posts:  # list 旧→新，逐条处理
                parsed = parse_post(raw)
                if parsed["is_long_text"]:
                    try:
                        parsed["long_text"] = self.fetch_longtext(parsed["mblogid"])
                    except Exception as e:
                        log.warning("  长文补全失败 mblogid=%s: %s", parsed["mblogid"], e)
                if save_post(self.conn, parsed):
                    new_count += 1
```

改为：

```python
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
```

- [ ] **Step 3: 增量循环加转发长文补全**

把 `weibo_blog/crawler.py` `crawl_blog_incremental` 中的：

```python
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
```

改为：

```python
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
```

- [ ] **Step 4: 校验语法**

```bash
uv run python -c "import weibo_blog.crawler; print('import OK')"
```
Expected: `import OK`

- [ ] **Step 5: 提交**

```bash
git add weibo_blog/crawler.py
git commit -m "feat(crawler): 转发原微博长文调 fetch_longtext 补全"
```

---

### Task 3: 渲染层前端转发块长文逻辑

**Files:**
- Modify: `web/app.js:270-279`（转发块渲染）

- [ ] **Step 1: 转发块复用长文逻辑**

把 `web/app.js` 中的：

```js
  // 转发原微博引用块（橙色竖线，含原微博媒体）
  if (p.retweeted && p.retweeted.text_raw) {
    const rt = p.retweeted;
    const rtUrl = `https://weibo.com/${rt.uid}/${rt.mblogid}`;
    html += `<div class="post-retweet">` +
      `<a class="retweet-name" href="${escHtml(rtUrl)}" target="_blank" rel="noopener">@${escHtml(rt.screen_name || "")}</a>` +
      `<div class="retweet-text">${linkify(escHtml(rt.text_raw))}</div>` +
      mediaHtml(rt.pics, rt.video_url, rtUrl) +
      `</div>`;
  }
```

改为：

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

- [ ] **Step 2: 提交**

```bash
git add web/app.js
git commit -m "feat(web): 转发块长文显示完整 long_text"
```

---

### Task 4: 补抓脚本 backfill_retweet_longtext.py

**Files:**
- Create: `backfill_retweet_longtext.py`（仓库根）

- [ ] **Step 1: 创建补抓脚本**

创建 `backfill_retweet_longtext.py`：

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

- [ ] **Step 2: 校验语法与导入**

```bash
uv run python -c "import backfill_retweet_longtext; print('import OK')"
```
Expected: `import OK`

- [ ] **Step 3: 提交**

```bash
git add backfill_retweet_longtext.py
git commit -m "feat(backfill): 新增转发长文原微博补抓脚本"
```

---

### Task 5: 运行补抓脚本回填历史数据

**Files:** 无（运行脚本改库）

- [ ] **Step 1: 确认补抓前待补数量**

```bash
uv run python -c "
import sqlite3, json
c=sqlite3.connect('weibo_blog.db'); c.row_factory=sqlite3.Row
rows=c.execute(\"SELECT retweeted_json FROM weibo_posts WHERE retweeted_json!=''\").fetchall()
todo=done=notlong=0
for r in rows:
    try: rt=json.loads(r['retweeted_json'])
    except: continue
    if not rt.get('is_long_text'): notlong+=1; continue
    if rt.get('long_text'): done+=1
    else: todo+=1
print(f'待补 {todo}，已补 {done}，非长文 {notlong}')
"
```
Expected: `待补 2804，已补 0，非长文 8836`（数字以实际为准，待补约 2804）。

- [ ] **Step 2: 运行补抓脚本（后台，慢速约 20-35 分钟）**

```bash
uv run python backfill_retweet_longtext.py
```
后台运行，日志逐条输出补全进度。期间可断点续跑（脚本跳过 `long_text` 已非空的）。

- [ ] **Step 3: 确认补抓后数量**

等脚本跑完（或跑一段时间后），重跑 Step 1 的统计命令，确认「待补」数下降、「已补」数上升。

```bash
uv run python -c "
import sqlite3, json
c=sqlite3.connect('weibo_blog.db'); c.row_factory=sqlite3.Row
rows=c.execute(\"SELECT retweeted_json FROM weibo_posts WHERE retweeted_json!=''\").fetchall()
todo=done=notlong=0
for r in rows:
    try: rt=json.loads(r['retweeted_json'])
    except: continue
    if not rt.get('is_long_text'): notlong+=1; continue
    if rt.get('long_text'): done+=1
    else: todo+=1
print(f'待补 {todo}，已补 {done}，非长文 {notlong}')
"
```
Expected: `待补` 接近 0，`已补` 接近 2804。

- [ ] **Step 4: 无文件改动，无需提交**

---

### Task 6: 全量测试 + 视觉验收

**Files:** 无（运行验证）

- [ ] **Step 1: 跑全量测试**

```bash
uv run pytest -v
```
Expected: 全部 PASS。

- [ ] **Step 2: 重启服务（加载新前端 + 后端无变化，主要强刷前端）**

服务已在运行则强刷浏览器（Ctrl+F5）；若未运行则启动：
```bash
uv run python server.py
```

- [ ] **Step 3: 视觉验收**

浏览器打开 `http://127.0.0.1:8766`（强刷），找一条转发长文微博（转发块原微博内容较长、之前显示截断 + …的），检查：
1. 转发块显示完整内容，不再是截断前缀。
2. 转发块文字仍 13px、橙色竖线样式不变。
3. 非长文转发、无转发微博、本微博长文显示均正常。
4. 卡片作者头像/名、阅读列宽 760px、正文 15px 等既有改动不受影响。

- [ ] **Step 4: 停止服务，无提交**

---

## Self-Review

**1. Spec coverage:**
- 解析层 retweeted_json 加字段 → Task 1 ✓
- 抓取层 crawler 补全 → Task 2（新方法 + 两处循环）✓
- 渲染层前端 → Task 3 ✓
- 补抓脚本回填历史 → Task 4（创建）+ Task 5（运行）✓
- 慢速抖动 → 脚本用 `_jitter_sleep(0.5)` ✓
- 显示完整 long_text 不做展开 → Task 3 直接用 long_text ✓
- 单元测试 → Task 1 Step 2 新增 `test_parse_post_retweet_longtext_flag` ✓
- 不破坏现有 fixture → Task 1 测试用 `raw["retweeted_status"]["isLongText"]=True` 临时改 fixture 副本，不改 fixture 文件 ✓
- 全量测试 → Task 6 Step 1 ✓

**2. Placeholder scan:** 每 step 含完整代码与确切命令；补抓数量标注「以实际为准」。✓

**3. 命名一致性:** `is_long_text` / `long_text` / `_fill_retweet_longtext` / `fetch_longtext` 在各 Task 间一致；与现有本微博长文字段名一致。✓
