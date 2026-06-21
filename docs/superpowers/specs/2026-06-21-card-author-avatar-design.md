# 设计：卡片显示博主名 + 头像

- 日期：2026-06-21
- 范围：`server.py`（1 处）+ `web/app.js`（1 处）+ `web/style.css`（新增样式）
- 目标：每条微博卡片顶部显示博主头像 + 名字，便于在「全部博主」模式下区分来源。

## 背景

当前卡片顶部只有右上角的时间与「原微博↗」链接，不显示发博人。「全部博主」模式下混排多条不同博主的微博时难以区分作者。

## 数据可行性结论

- `bloggers` 表含 `uid / screen_name / avatar / profile_url / verified`。
- `weibo_posts.uid` 可关联博主。
- `bloggers.avatar` 存的是带签名 URL（形如 `...xxx.jpg?KID=imgbed,tva&Expires=...&ssig=...`），签名 `Expires` 几小时即过期 → 403。
- **已验证**：去掉 `?` 之后签名参数的基准 URL 永久可直连（200）。故存储/展示时 strip 掉签名参数即可。
- `/api/bloggers` 当前只返回 `uid, screen_name, profile_url, verified`，**没有 avatar**，需补上。

## 决策

| 项 | 决策 |
| --- | --- |
| 头像获取 | 去签名基准 URL（永久可直连） |
| 显示位置 | 卡片顶部一行：圆形头像 32px + 博主名 |
| 转发引用块作者 | 不加头像，仍只 `@用户名` 文字链接 |
| 数据来源 | 前端按 `uid` 本地匹配 `bloggerMap`（后端 `/api/bloggers` 补 `avatar` 字段） |
| 头像加载失败 | `<img onerror>` 隐藏，名字仍显示 |

## 方案选型

头像获取三种方式：

- **方式 A（采纳）：去签名基准 URL**
  - strip 掉 `?KID=...&Expires=...&ssig=...`，基准 URL 永久可直连，已验证。
  - 前端 `<img>` 直连，无需走代理。
- **方式 B：走 `/api/img` 代理**：签名过期后代理也取不到，不能根治过期，不采纳。
- **方式 C：入库时 strip + 改 `bloggers.avatar`**：需写迁移改存量数据，且抓取端也要改，范围过大，不采纳。

数据来源两种方式：

- **方式 D（采纳）：前端按 uid 本地匹配**
  - `/api/bloggers` 已返回博主列表，前端建 `bloggerMap`，`renderCard` 用 `p.uid` 查表。
  - `/api/posts` 返回结构不变，后端 posts 查询不动。
- **方式 E：后端 `/api/posts` JOIN bloggers**：每条 post 都带名/头像，响应体变大；且 posts 查询逻辑要改。不采纳。

## 改动明细

### 1. 后端 `server.py`：`/api/bloggers` 补 `avatar` 字段（去签名）

新增辅助函数（放在 `_is_allowed_img_host` 附近）：

```python
def strip_avatar_sig(url):
    """微博头像 URL 带签名参数（KID/Expires/ssig）会过期，去掉后基准 URL 永久可访问。"""
    if not url:
        return ""
    return url.split("?", 1)[0]
```

修改 `query_bloggers`（`server.py:170`）：

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

### 2. 前端 `web/app.js`

**2a. 建 `bloggerMap`**

在文件头部变量区新增：
```js
// uid → {screen_name, avatar, profile_url, verified}，卡片作者行用
const bloggerMap = {};
```

`loadBloggers()`（`app.js:61`）填充映射，在追加 option 的循环里同步写 map：
```js
for (const b of bloggers) {
  bloggerMap[b.uid] = b;   // 新增
  const opt = document.createElement("option");
  opt.value = b.uid;
  opt.textContent = b.screen_name || `uid:${b.uid}`;
  bloggerSelect.appendChild(opt);
}
```

**2b. `renderCard`（`app.js:224`）顶部插入作者行**

把现有顶部「时间 + 原微博链接」从 `float:right` 改为一个 flex 头部行，左侧作者、右侧操作。新结构：

```js
function renderCard(p) {
  const card = document.createElement("div");
  card.className = "post-card";
  card.id = "post-" + p.mblogid;

  const weiboUrl = `https://weibo.com/${p.uid}/${p.mblogid}`;

  // 作者行：头像 + 名字（左） / 时间 + 原微博链接（右）
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

  // 正文（URL 转可点击链接，含 t.cn 短链）
  // ……（以下正文/媒体/转发/meta 逻辑不变，原样保留）
}
```

> 注意：原 `.post-time` / `.post-link` 的 `float:right` 因父级改为 flex 容器，需在 CSS 里去掉 float（见下）。

### 3. 样式 `web/style.css`

新增（放在 `.post-card` 规则之后）：
```css
.post-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; }
.post-author { display: flex; align-items: center; gap: 8px; min-width: 0; }
.post-author .avatar { width: 32px; height: 32px; border-radius: 50%; object-fit: cover; flex-shrink: 0; }
.post-author .author-name { font-weight: 600; font-size: 14px; color: #ff8200; text-decoration: none; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.post-author .author-name:hover { text-decoration: underline; }
.post-head-actions { display: flex; align-items: center; gap: 12px; flex-shrink: 0; }
```

修改 `.post-time` / `.post-link` 去掉 `float`（`style.css:78-79`）：
```css
/* 原：.post-time { float: right; ... } .post-link { float: right; ... } */
.post-time { font-size: 12px; color: #999; }
.post-link { font-size: 12px; color: #ff8200; text-decoration: none; }
```

## 不改的部分

- `/api/posts` 返回结构不变。
- 转发引用块（`.post-retweet`）仍只 `@用户名` 文字链接，不加头像。
- 长文 / 媒体 / 元信息逻辑不变。
- 阅读列宽 760px、正文字号 15px、转发 13px 不变（作者行在卡片内，受 `max-width` 约束）。
- 抓取端 / 入库逻辑不动（strip 只在展示层做）。

## 边界处理

- 头像加载失败（网络/防盗链）：`<img onerror="this.style.display='none'">` 隐藏 img，名字仍显示。
- 博主不在 `bloggerMap`（理论不会发生，`p.uid` 必来自已抓取博主）：fallback 显示 `uid:xxx`，不崩。
- `avatar` 为空字符串：不渲染 `<img>`，只显示名字。
- `profile_url` 为空：链接 `href="#"`。

## 验证

1. 启动本地服务，打开查看器：
   - 每条卡片顶部显示博主圆形头像 + 名字，右侧时间/原微博链接对齐。
   - 点名字跳博主主页，点「原微博↗」跳原微博（两个链接独立）。
   - 转发引用块仍只 `@用户名`，无头像。
2. 切换「全部博主」：不同博主的卡片头像/名不同，可区分来源。
3. 切换到单博主：每条卡片头像/名一致（重复但符合预期）。
4. 头像 URL 去签名后可正常加载（不 403）。
5. 阅读列宽 760px、正文字号 15px 不受影响，作者行在卡片内正常换行。
6. 长文 / 含转发 / 含图片视频的卡片排版正常，无溢出。
7. 后端 `pytest tests/test_server.py` 通过（`/api/bloggers` 多了 avatar 字段，已有断言需确认是否断言字段集合）。
