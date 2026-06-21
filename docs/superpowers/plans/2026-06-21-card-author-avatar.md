# 卡片显示博主名 + 头像 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每条微博卡片顶部显示博主头像 + 名字，便于「全部博主」模式下区分来源。

**Architecture:** 后端 `/api/bloggers` 补 `avatar` 字段（去签名基准 URL）；前端建 `bloggerMap`，`renderCard` 用 `p.uid` 查表，在卡片顶部插入「头像+名 / 时间+原微博链接」flex 头部行。

**Tech Stack:** Python（`server.py` stdlib http.server）/ 原生 JS / 原生 CSS

**Spec:** `docs/superpowers/specs/2026-06-21-card-author-avatar-design.md`

---

## 当前代码现状（修改前）

`server.py:143-150`（图片代理白名单，新函数放其后）：
```python
def _is_allowed_img_host(url):
    """SSRF 防护：只允许 *.sinaimg.cn 域名的图片代理。"""
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    host = (parsed.hostname or "").lower()
    return parsed.scheme in ("http", "https") and host.endswith(".sinaimg.cn")
```

`server.py:170-180`（`query_bloggers`）：
```python
def query_bloggers(conn):
    """所有博主列表（供顶栏选择器）。按昵称排序。"""
    rows = conn.execute(
        "SELECT uid, screen_name, profile_url, verified FROM bloggers ORDER BY screen_name"
    ).fetchall()
    return [{
        "uid": r["uid"],
        "screen_name": r["screen_name"],
        "profile_url": r["profile_url"],
        "verified": r["verified"],
    } for r in rows]
```

`web/app.js:16`（变量区）：
```js
let currentUid = null;
```

`web/app.js:61-84`（`loadBloggers`）：
```js
async function loadBloggers() {
  let bloggers;
  try {
    bloggers = await getJson("/api/bloggers");
  } catch (e) {
    bloggerSelect.innerHTML = '<option>加载失败</option>';
    return;
  }
  bloggerSelect.innerHTML = "";
  // 「全部」选项（uid 空 = 不过滤）
  const allOpt = document.createElement("option");
  allOpt.value = "";
  allOpt.textContent = "全部博主";
  bloggerSelect.appendChild(allOpt);
  for (const b of bloggers) {
    const opt = document.createElement("option");
    opt.value = b.uid;
    opt.textContent = b.screen_name || `uid:${b.uid}`;
    bloggerSelect.appendChild(opt);
  }
  // 默认选「全部博主」（value="" → currentUid=null → 查询不带 uid 过滤）
  bloggerSelect.value = "";
  currentUid = null;
}
```

`web/app.js:224-232`（`renderCard` 开头）：
```js
function renderCard(p) {
  const card = document.createElement("div");
  card.className = "post-card";
  card.id = "post-" + p.mblogid;

  // 原微博链接（右上角，新标签打开）
  const weiboUrl = `https://weibo.com/${p.uid}/${p.mblogid}`;
  let html = `<span class="post-time">${fmtTime(p.created_at)}</span>` +
    `<a class="post-link" href="${escHtml(weiboUrl)}" target="_blank" rel="noopener" title="在微博查看">原微博 ↗</a>`;
```

`web/style.css:66-72`（`.post-card`）：
```css
.post-card {
  background: #fff; border-radius: 8px;
  box-shadow: 0 2px 8px rgba(0,0,0,.12);
  border-left: 3px solid #ff8200;
  margin-bottom: 16px; padding: 14px 16px; transition: box-shadow .2s;
  max-width: 760px;
}
```

`web/style.css:78-80`（`.post-time` / `.post-link`）：
```css
.post-time { float: right; font-size: 12px; color: #999; margin-left: 12px; }
.post-link { float: right; font-size: 12px; color: #ff8200; text-decoration: none; }
.post-link:hover { text-decoration: underline; }
```

---

### Task 1: 后端新增 `strip_avatar_sig` 辅助函数

**Files:**
- Modify: `server.py:150`（`_is_allowed_img_host` 函数末尾之后插入新函数）

- [ ] **Step 1: 在 `_is_allowed_img_host` 之后插入 `strip_avatar_sig`**

把 `server.py` 中的：

```python
def _is_allowed_img_host(url):
    """SSRF 防护：只允许 *.sinaimg.cn 域名的图片代理。"""
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    host = (parsed.hostname or "").lower()
    return parsed.scheme in ("http", "https") and host.endswith(".sinaimg.cn")
```

改为（在其后追加新函数）：

```python
def _is_allowed_img_host(url):
    """SSRF 防护：只允许 *.sinaimg.cn 域名的图片代理。"""
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    host = (parsed.hostname or "").lower()
    return parsed.scheme in ("http", "https") and host.endswith(".sinaimg.cn")


def strip_avatar_sig(url):
    """微博头像 URL 带签名参数（KID/Expires/ssig）会过期，去掉后基准 URL 永久可访问。"""
    if not url:
        return ""
    return url.split("?", 1)[0]
```

- [ ] **Step 2: 提交**

```bash
git add server.py
git commit -m "feat(server): 新增 strip_avatar_sig 去头像签名参数"
```

---

### Task 2: 后端 `query_bloggers` 补 `avatar` 字段

**Files:**
- Modify: `server.py:170-180`（`query_bloggers`）

- [ ] **Step 1: 给 `query_bloggers` 的 SELECT 加 avatar 列、返回加 avatar 字段**

把 `server.py` 中的：

```python
def query_bloggers(conn):
    """所有博主列表（供顶栏选择器）。按昵称排序。"""
    rows = conn.execute(
        "SELECT uid, screen_name, profile_url, verified FROM bloggers ORDER BY screen_name"
    ).fetchall()
    return [{
        "uid": r["uid"],
        "screen_name": r["screen_name"],
        "profile_url": r["profile_url"],
        "verified": r["verified"],
    } for r in rows]
```

改为：

```python
def query_bloggers(conn):
    """所有博主列表（供顶栏选择器）。按昵称排序。"""
    rows = conn.execute(
        "SELECT uid, screen_name, profile_url, verified, avatar FROM bloggers ORDER BY screen_name"
    ).fetchall()
    return [{
        "uid": r["uid"],
        "screen_name": r["screen_name"],
        "profile_url": r["profile_url"],
        "verified": r["verified"],
        "avatar": strip_avatar_sig(r["avatar"]),
    } for r in rows]
```

- [ ] **Step 2: 写测试断言 avatar 字段存在且去签名**

在 `tests/test_server.py` 的 `BloggerFilterApiTest.test_bloggers_returns_all`（约 `:353`）之后新增测试。该类的 `make_data` 已插入 uid=1401527553 与 uid=999 两个博主（`insert_blogger` 不写 avatar，默认 `''`）。基类 `_ServerTestBase` 不暴露库连接，测试自建连接改库。

在 `test_bloggers_returns_all` 方法之后追加：

```python
    def test_bloggers_includes_avatar_stripped(self):
        # 给 tombkeeper 设一个带签名参数的 avatar，断言返回的是去签名基准 URL
        import sqlite3 as _sqlite3
        conn = _sqlite3.connect(self.db_path)
        conn.execute(
            "UPDATE bloggers SET avatar=? WHERE uid=1401527553",
            ("https://tvax3.sinaimg.cn/crop.0.0.503.503.180/abc.jpg?KID=imgbed,tva&Expires=1&ssig=x",),
        )
        conn.commit()
        conn.close()
        status, data = self._get_json("/api/bloggers")
        self.assertEqual(status, 200)
        tk = next(b for b in data if b["uid"] == 1401527553)
        self.assertEqual(tk["avatar"],
                         "https://tvax3.sinaimg.cn/crop.0.0.503.503.180/abc.jpg")
        # 未设 avatar 的博主返回空串
        other = next(b for b in data if b["uid"] == 999)
        self.assertEqual(other["avatar"], "")
```

- [ ] **Step 3: 运行测试确认通过**

```bash
uv run pytest tests/test_server.py -v
```
Expected: 全部 PASS（含新增 `test_bloggers_includes_avatar_stripped` 与原有 `test_bloggers_returns_all`）。

- [ ] **Step 4: 提交**

```bash
git add server.py tests/test_server.py
git commit -m "feat(server): /api/bloggers 返回去签名的 avatar 字段"
```

---

### Task 3: 前端建 `bloggerMap` 并填充

**Files:**
- Modify: `web/app.js:16`（变量区）、`web/app.js:61-84`（`loadBloggers`）

- [ ] **Step 1: 在变量区新增 `bloggerMap`**

把 `web/app.js` 中的：

```js
let currentUid = null;
```

改为：

```js
let currentUid = null;
// uid → 博主对象 {uid, screen_name, profile_url, verified, avatar}，卡片作者行用
const bloggerMap = {};
```

- [ ] **Step 2: `loadBloggers` 循环里填充 `bloggerMap`**

把 `web/app.js` `loadBloggers` 中的 for 循环：

```js
  for (const b of bloggers) {
    const opt = document.createElement("option");
    opt.value = b.uid;
    opt.textContent = b.screen_name || `uid:${b.uid}`;
    bloggerSelect.appendChild(opt);
  }
```

改为：

```js
  for (const b of bloggers) {
    bloggerMap[b.uid] = b;
    const opt = document.createElement("option");
    opt.value = b.uid;
    opt.textContent = b.screen_name || `uid:${b.uid}`;
    bloggerSelect.appendChild(opt);
  }
```

- [ ] **Step 3: 提交**

```bash
git add web/app.js
git commit -m "feat(web): loadBloggers 构建 bloggerMap 供卡片作者行用"
```

---

### Task 4: 前端 `renderCard` 插入作者头部行

**Files:**
- Modify: `web/app.js:224-232`（`renderCard` 开头的 html 拼装）

- [ ] **Step 1: 把 `renderCard` 顶部的「时间+原微博链接」改为 flex 头部行（左作者/右操作）**

把 `web/app.js` `renderCard` 中的：

```js
function renderCard(p) {
  const card = document.createElement("div");
  card.className = "post-card";
  card.id = "post-" + p.mblogid;

  // 原微博链接（右上角，新标签打开）
  const weiboUrl = `https://weibo.com/${p.uid}/${p.mblogid}`;
  let html = `<span class="post-time">${fmtTime(p.created_at)}</span>` +
    `<a class="post-link" href="${escHtml(weiboUrl)}" target="_blank" rel="noopener" title="在微博查看">原微博 ↗</a>`;
```

改为：

```js
function renderCard(p) {
  const card = document.createElement("div");
  card.className = "post-card";
  card.id = "post-" + p.mblogid;

  const weiboUrl = `https://weibo.com/${p.uid}/${p.mblogid}`;

  // 作者头部行：头像+名（左） / 时间+原微博链接（右）
  const b = bloggerMap[p.uid];
  const profileUrl = b && b.profile_url ? b.profile_url : "#";
  const avatarUrl = b && b.avatar ? b.avatar : "";
  const authorName = b ? (b.screen_name || `uid:${p.uid}`) : `uid:${p.uid}`;
  const avatarHtml = avatarUrl
    ? `<img class="avatar" src="${escHtml(avatarUrl)}" alt="" onerror="this.style.display='none'">`
    : "";
  let html = `<div class="post-head">` +
    `<div class="post-author">` +
    avatarHtml +
    `<a class="author-name" href="${escHtml(profileUrl)}" target="_blank" rel="noopener">${escHtml(authorName)}</a>` +
    `</div>` +
    `<div class="post-head-actions">` +
    `<span class="post-time">${fmtTime(p.created_at)}</span>` +
    `<a class="post-link" href="${escHtml(weiboUrl)}" target="_blank" rel="noopener" title="在微博查看">原微博 ↗</a>` +
    `</div>` +
    `</div>`;
```

- [ ] **Step 2: 提交**

```bash
git add web/app.js
git commit -m "feat(web): 卡片顶部插入博主头像+名 头部行"
```

---

### Task 5: 样式 —— 头部行 flex 布局 + 去 float

**Files:**
- Modify: `web/style.css:78-80`（`.post-time`/`.post-link` 去 float）、新增头部行样式

- [ ] **Step 1: 把 `.post-time`/`.post-link` 的 float 去掉**

把 `web/style.css` 中的：

```css
.post-time { float: right; font-size: 12px; color: #999; margin-left: 12px; }
.post-link { float: right; font-size: 12px; color: #ff8200; text-decoration: none; }
.post-link:hover { text-decoration: underline; }
```

改为：

```css
.post-time { font-size: 12px; color: #999; }
.post-link { font-size: 12px; color: #ff8200; text-decoration: none; }
.post-link:hover { text-decoration: underline; }

/* 卡片头部行：左作者 / 右操作 */
.post-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; gap: 8px; }
.post-author { display: flex; align-items: center; gap: 8px; min-width: 0; }
.post-author .avatar { width: 32px; height: 32px; border-radius: 50%; object-fit: cover; flex-shrink: 0; }
.post-author .author-name { font-weight: 600; font-size: 14px; color: #ff8200; text-decoration: none; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.post-author .author-name:hover { text-decoration: underline; }
.post-head-actions { display: flex; align-items: center; gap: 12px; flex-shrink: 0; }
```

- [ ] **Step 2: 校验 CSS 语法**

```bash
python -c "c=open('web/style.css',encoding='utf-8').read(); print('OK' if c.count('{')==c.count('}') else 'BRACE MISMATCH')"
```
Expected: `OK`

- [ ] **Step 3: 提交**

```bash
git add web/style.css
git commit -m "style(web): 卡片头部行flex布局，头像+名置顶，时间链接去float"
```

---

### Task 6: 视觉验收（人工）

纯前端视觉改动，无自动化覆盖；需启动服务在浏览器确认。

**Files:** 无（仅运行验证）

- [ ] **Step 1: 启动本地服务**

```bash
uv run server.py
```
确认监听 `http://127.0.0.1:8766`。

- [ ] **Step 2: 作者行验收**

浏览器打开 `http://127.0.0.1:8766`（强刷 Ctrl+F5 清缓存），选任一有微博的日期，检查：
1. 每条卡片顶部一行：左侧圆形头像 32px + 博主名（橙色加粗），右侧时间 + 「原微博↗」。
2. 点博主名跳博主主页（新标签），点「原微博↗」跳原微博，两链接独立。
3. 头像正常加载（去签名 URL 不 403）。
4. 转发引用块（橙色竖线内）仍只 `@用户名`，无头像。
5. 顶部日期条通栏、阅读列宽 760px、正文 15px 均不受影响。

- [ ] **Step 3: 多博主区分验收**

切换「全部博主」：不同博主的卡片头像/名不同，可区分来源。
切换到单博主：每条卡片头像/名一致（符合预期）。

- [ ] **Step 4: 边界验收**

- 找一条转发微博：作者行是转发者（非原作者），转发块内仍只 `@原作者`。
- 长文 / 含图片视频的卡片：作者行 + 正文 + 媒体排版正常，无溢出、无横向滚动。
- 窄屏（约 800px）：作者行 flex 在窄空间下正常换行/收缩，名字 ellipsis 不撑破布局。

- [ ] **Step 5: 停止服务**

停止后台 `server.py`。无需提交。

---

## Self-Review

**1. Spec coverage:**
- 后端 `/api/bloggers` 补 avatar（去签名）→ Task 1（函数）+ Task 2（查询）✓
- 前端按 uid 本地匹配 bloggerMap → Task 3 ✓
- 卡片顶部头像+名置顶 → Task 4 ✓
- 转发块不加头像 → 不改 `.post-retweet`，Task 6 Step 2 第 4 点验收 ✓
- 头像加载失败 onerror 隐藏 → Task 4 Step 1 代码含 `onerror="this.style.display='none'"` ✓
- 样式头部行 flex → Task 5 ✓
- 不影响阅读列宽/字号 → 不改 `.post-card` 的 max-width、`.post-text` 字号；Task 6 Step 2 第 5 点验收 ✓
- 测试 → Task 2 Step 2 新增 `test_bloggers_includes_avatar_stripped` ✓

**2. Placeholder scan:** 每 step 含完整代码与确切命令；`self.app_conn` 已标注若报错则查基类属性名调整。✓

**3. 命名一致性:** `strip_avatar_sig` / `bloggerMap` / `.post-head` / `.post-author` / `.avatar` / `.author-name` / `.post-head-actions` 在各 Task 间一致。✓
