# 同步后静默刷新 + 自动刷新 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 同步完成后用局部 diff 更新当前视图（不整页刷新），并增加每分钟一次、默认开启的自动刷新。

**Architecture:** 把"同步"和"页面更新"解耦：`runSync()` 统一入口（手动/自动共用）→ 轮询状态 → 成功后 `silentRefresh()` 做 diff 式 prepend。纯前端改动（app.js + index.html + style.css），server.py 不动。

**Tech Stack:** 原生 JS / HTML / CSS（无框架无构建），Python stdlib http.server 后端。无前端测试框架，用浏览器手动验收。

**Spec:** `docs/superpowers/specs/2026-06-21-silent-refresh-design.md`

---

## 文件结构

- **Modify** `web/app.js` — 抽取 `runSync()`、新增 `silentRefresh()` / `refreshDateTree()` / `refreshBloggerMap()` / 自动刷新控制 / 提示条操作；移除 `location.reload()`
- **Modify** `web/index.html` — 顶栏加自动刷新 toggle；`#post-list` 内加提示条占位元素
- **Modify** `web/style.css` — 自动刷新 toggle 样式、提示条样式
- **不改** `server.py` — 现有 `/api/sync`、`/api/sync/status`、`/api/posts`、`/api/months`、`/api/bloggers` 已够用

## 关键上下文（现有代码锚点）

- `web/app.js:459-500` 现有同步逻辑：`syncBtn` 点击 → POST `/api/sync` → `setInterval(pollSync, 2000)` → `pollSync` 里 `location.reload()`
- `web/app.js:182-209` `selectDay`：GET `/api/posts?date=...` → `renderPosts`
- `web/app.js:118-142` `loadMonths`：GET `/api/months` → 建 `.month-group` DOM
- `web/app.js:144-161` `toggleMonth`：GET `/api/dates?month=...` → `renderDays`，展开态靠 `.open` class，缓存靠 `monthCache`
- `web/app.js:72-96` `loadBloggers`：GET `/api/bloggers` → 填 `bloggerMap` + 下拉
- `web/app.js:16-20` 全局状态：`currentUid` / `bloggerMap` / `monthCache` / `currentDay`
- `web/style.css:23-30` `#sync-btn` / `#status` 样式
- `web/style.css:64` `#post-list` 是滚动容器
- `web/style.css:74-78` `.post-highlight` + `@keyframes highlight-flash` 已存在（搜索定位用）
- API 返回格式：`/api/months` → `[{month:"2025-06",count:N}]`；`/api/posts` → `{posts:[...]}`；`/api/bloggers` → `[{uid,screen_name,...}]`

---

### Task 1: 顶栏加自动刷新 toggle + 提示条占位（HTML）

**Files:**
- Modify: `web/index.html:10-15`（顶栏）+ `web/index.html:22-26`（viewer 区）

- [ ] **Step 1: 在顶栏同步按钮后加自动刷新 toggle**

把 `web/index.html` 顶栏（第 13-14 行之间）改为：

```html
    <button id="sync-btn" type="button" title="增量同步新微博">🔄 同步</button>
    <label id="auto-refresh-toggle" class="ar-toggle" title="每分钟自动同步一次">
      <input type="checkbox" id="auto-refresh-check" checked>
      <span class="ar-track"><span class="ar-thumb"></span></span>
      <span class="ar-label">自动刷新</span>
    </label>
    <span id="status"></span>
```

- [ ] **Step 2: 在 post-list 内加提示条占位元素**

把 `web/index.html` viewer 区（第 23-25 行）改为：

```html
      <div id="day-indicator"></div>
      <div id="post-list">
        <div id="new-posts-banner" hidden>
          <span class="npb-text"></span>
          <button class="npb-close" type="button" title="关闭">×</button>
        </div>
      </div>
      <div id="empty-hint" hidden></div>
```

- [ ] **Step 3: Commit**

```bash
git add web/index.html
git commit -m "feat(web): 顶栏加自动刷新开关 + 新增条目提示条占位"
```

---

### Task 2: 自动刷新 toggle 与提示条样式（CSS）

**Files:**
- Modify: `web/style.css`（顶栏区 `#sync-btn` 后追加；`#post-list` 区追加）

- [ ] **Step 1: 在 `#status` 规则后（第 30 行后）追加自动刷新 toggle 样式**

```css
/* 自动刷新开关 */
.ar-toggle { display: inline-flex; align-items: center; gap: 6px; cursor: pointer; user-select: none; font-size: 13px; color: #666; }
.ar-toggle input { display: none; }
.ar-track { width: 32px; height: 18px; border-radius: 9px; background: #ddd; position: relative; transition: background .2s; flex-shrink: 0; }
.ar-thumb { position: absolute; top: 2px; left: 2px; width: 14px; height: 14px; border-radius: 50%; background: #fff; transition: left .2s; }
.ar-toggle input:checked + .ar-track { background: #ff8200; }
.ar-toggle input:checked + .ar-track .ar-thumb { left: 16px; }
```

- [ ] **Step 2: 在 `#post-list` 规则后（第 64 行后）追加提示条样式**

```css
/* 新增条目提示条 */
#new-posts-banner {
  position: sticky; top: 0; z-index: 10;
  display: flex; align-items: center; justify-content: center; gap: 10px;
  margin: -12px -16px 12px; padding: 8px 16px;
  background: #fff4e6; border: 1px solid #ffb366; border-radius: 6px;
  color: #cc6600; font-size: 13px; cursor: pointer;
}
.npb-close { background: none; border: none; color: #cc6600; font-size: 18px; cursor: pointer; line-height: 1; padding: 0 4px; }
.npb-close:hover { color: #ff8200; }
```

- [ ] **Step 3: Commit**

```bash
git add web/style.css
git commit -m "feat(web): 自动刷新开关 + 新增条目提示条样式"
```

---

### Task 3: 抽取 runSync() 统一同步入口

**Files:**
- Modify: `web/app.js:459-500`（替换现有 syncBtn handler + pollSync）

把现有同步逻辑（第 459-500 行整段）替换为下面的代码。核心变化：抽出 `runSync()` 供手动/自动共用；`pollSync` 完成后不再 `location.reload()`，改为调用 `silentRefresh()`（Task 4 实现，此处先写调用，Task 4 会定义函数）。

- [ ] **Step 1: 替换 syncBtn handler + pollSync 为 runSync 体系**

用以下内容替换 `web/app.js` 中从 `const syncBtn = $("sync-btn");` 到 `pollSync` 函数结束（原第 459-500 行）的整段：

```js
// ── 同步按钮（增量抓取，后台子进程）────
const syncBtn = $("sync-btn");
let syncPollTimer = null;
// 同步进行中标志：避免手动+自动并发触发
let syncInProgress = false;

async function runSync() {
  if (syncInProgress) return;
  syncInProgress = true;
  syncBtn.disabled = true;
  syncBtn.textContent = "🔄 同步中...";
  try {
    const resp = await fetch("/api/sync", { method: "POST" });
    if (resp.status === 409) {
      // 已有同步在跑，直接开始轮询
    } else if (!resp.ok) {
      throw new Error("同步启动失败");
    }
  } catch (e) {
    syncInProgress = false;
    syncBtn.disabled = false;
    syncBtn.textContent = "🔄 同步";
    setStatus("同步启动失败");
    return;
  }
  syncPollTimer = setInterval(pollSync, 2000);
  pollSync();
}

syncBtn.addEventListener("click", () => runSync());

async function pollSync() {
  try {
    const data = await (await fetch("/api/sync/status")).json();
    if (!data.running) {
      clearInterval(syncPollTimer);
      syncPollTimer = null;
      syncInProgress = false;
      syncBtn.disabled = false;
      syncBtn.textContent = "🔄 同步";
      if (data.exit_code === 0) {
        await silentRefresh();  // 静默更新，不再 location.reload()
      } else {
        setStatus("同步失败（exit " + data.exit_code + "），请查看日志");
      }
    }
  } catch (e) {
    // 网络错误，继续轮询
  }
}
```

- [ ] **Step 2: 在 runSync 上方加一个 silentRefresh 桩（Task 4 会实现真正逻辑）**

在 `const syncBtn = $("sync-btn");` 这行之前插入：

```js
// 静默刷新：同步成功后局部更新当前视图（Task 4 实现）
async function silentRefresh() { /* 占位，Task 4 替换 */ }
```

- [ ] **Step 3: 浏览器手动验证**

启动 server（`.venv/Scripts/python.exe server.py`），打开查看器，点"🔄 同步"。
预期：按钮变"🔄 同步中..."，同步完成后按钮恢复，**不再整页刷新**（页面无闪动，选中日期/滚动位置保留）。控制台无报错（`silentRefresh` 是空函数，不报错即可）。

- [ ] **Step 4: Commit**

```bash
git add web/app.js
git commit -m "refactor(web): 抽取 runSync 统一同步入口，移除 location.reload"
```

---

### Task 4: 实现 silentRefresh 静默更新

**Files:**
- Modify: `web/app.js`（替换 Task 3 加的 `silentRefresh` 桩 + 新增辅助函数）

实现核心：记录旧 mblogid 集合 + scrollTop → 并行拉数据 → diff 新增 → prepend 卡片 + 高亮 → 更新计数/日期树/bloggerMap → 恢复 scrollTop → 显示提示条。

- [ ] **Step 1: 删除 Task 3 的 silentRefresh 桩，替换为完整实现**

把 Task 3 Step 2 插入的 `async function silentRefresh() { /* 占位，Task 4 替换 */ }` 替换为：

```js
// 静默刷新：同步成功后局部 diff 更新当前视图，不打断阅读
async function silentRefresh() {
  // 搜索面板打开时不更新
  if (!$("search-overlay").hidden) return;
  // 没选中日期时不更新帖子（但日期树/博主仍可刷新）
  const hadDay = currentDay !== null;

  // 并行拉取三部分数据
  const uidParam = currentUid !== null ? `&uid=${currentUid}` : "";
  const fetches = [
    getJson(`/api/months?${uidParam}`),
    getJson("/api/bloggers"),
  ];
  if (hadDay) {
    fetches.push(getJson(`/api/posts?date=${encodeURIComponent(currentDay)}${uidParam}`));
  }
  const [monthsData, bloggersData, postsData] = await Promise.all(fetches).catch(() => [null, null, null]);
  if (!monthsData) return;  // 拉取失败，放弃本次更新

  refreshDateTree(monthsData);
  refreshBloggerMap(bloggersData || []);

  if (!hadDay || !postsData) return;

  // diff 新增帖子
  const oldIds = new Set();
  for (const card of postList.querySelectorAll(".post-card")) {
    const id = card.id.replace("post-", "");
    if (id) oldIds.add(id);
  }
  const scrollTop = postList.scrollTop;
  const newPosts = postsData.posts.filter(p => !oldIds.has(p.mblogid));

  if (newPosts.length > 0) {
    // 新增卡片 prepend（微博列表按时间倒序，最新在前）
    for (let i = newPosts.length - 1; i >= 0; i--) {
      const card = renderCard(newPosts[i]);
      card.classList.add("post-highlight");
      postList.insertBefore(card, postList.firstChild);
      setTimeout(() => card.classList.remove("post-highlight"), 2000);
    }
    // 更新计数
    dayIndicator.textContent = `${currentDay}  共 ${postsData.posts.length} 条`;
    // 显示提示条
    showNewPostsBanner(newPosts.length);
  }

  // 恢复滚动位置（prepend 新卡片会下推已有内容）
  postList.scrollTop = scrollTop;
}
```

- [ ] **Step 2: 新增 refreshDateTree 辅助函数**

在 `silentRefresh` 函数下方追加：

```js
// 局部更新左侧日期树：已存在月份更新计数，新月份插入，保留展开态
function refreshDateTree(monthsData) {
  // 清空 monthCache 让下次 toggleMonth 重新拉取（日期计数可能变了）
  for (const k of Object.keys(monthCache)) delete monthCache[k];

  const existing = new Map();
  for (const grp of dateList.querySelectorAll(".month-group")) {
    existing.set(grp.dataset.month, grp);
  }
  const seenMonths = new Set();
  for (const m of monthsData) {
    seenMonths.add(m.month);
    let grp = existing.get(m.month);
    if (grp) {
      // 更新计数
      const cntEl = grp.querySelector(".month-header .count");
      if (cntEl) cntEl.textContent = `(${m.count})`;
    } else {
      // 新月份：插入到列表（monthsData 已倒序，按出现顺序追加即可）
      grp = document.createElement("div");
      grp.className = "month-group";
      grp.dataset.month = m.month;
      grp.innerHTML =
        `<div class="month-header">${escHtml(m.month)} <span class="count">(${m.count})</span></div>` +
        `<div class="month-days"></div>`;
      grp.querySelector(".month-header").addEventListener("click", () => toggleMonth(grp, m.month));
      dateList.appendChild(grp);
    }
  }
  // 月份在 monthsData 中消失的情况不处理（微博不会删，极少见）
}
```

- [ ] **Step 3: 新增 refreshBloggerMap 辅助函数**

在 `refreshDateTree` 函数下方追加：

```js
// 更新 bloggerMap（新博主加入 / 头像名字变化更新），不重渲染下拉
function refreshBloggerMap(bloggersData) {
  for (const b of bloggersData) {
    bloggerMap[b.uid] = b;
  }
}
```

- [ ] **Step 4: 浏览器手动验证**

启动 server，打开查看器，选中某天。手动往 DB 插一条该日期的测试微博（或等博主发新微博后点同步）。
预期：同步完成后，新帖子卡片出现在列表顶部、有橙色高亮闪动，已有卡片不动、滚动位置不变，顶部出现"新增 N 条微博"提示条。日期树计数更新。

- [ ] **Step 5: Commit**

```bash
git add web/app.js
git commit -m "feat(web): silentRefresh 静默 diff 更新——prepend 新卡片 + 日期树/博主局部更新"
```

---

### Task 5: 新增条目提示条交互

**Files:**
- Modify: `web/app.js`（在 silentRefresh 区域附近新增提示条函数）

- [ ] **Step 1: 新增 showNewPostsBanner / dismissNewPostsBanner 函数**

在 `silentRefresh` 函数上方（`runSync` 区块之前）追加：

```js
// ── 新增条目提示条 ───────────────────
const newPostsBanner = $("new-posts-banner");
const newPostsBannerText = newPostsBanner.querySelector(".npb-text");

function showNewPostsBanner(count) {
  newPostsBannerText.textContent = `新增 ${count} 条微博`;
  newPostsBanner.hidden = false;
}

function dismissNewPostsBanner() {
  newPostsBanner.hidden = true;
}

// 点提示条（非关闭按钮）：滚动到第一条新增帖子并高亮
newPostsBanner.addEventListener("click", (e) => {
  if (e.target.classList.contains("npb-close")) return;
  const firstNew = postList.querySelector(".post-card.post-highlight")
    || postList.querySelector(".post-card");
  if (firstNew) {
    firstNew.scrollIntoView({ block: "center", behavior: "smooth" });
  }
});

// 关闭按钮
newPostsBanner.querySelector(".npb-close").addEventListener("click", (e) => {
  e.stopPropagation();
  dismissNewPostsBanner();
});
```

- [ ] **Step 2: 浏览器手动验证**

触发一次有新增的同步，提示条出现后：
- 点 `×`：提示条消失
- 再次触发有新增的同步：提示条重新出现
- 点提示条非 `×` 区域：滚动到第一条新帖子

- [ ] **Step 3: Commit**

```bash
git add web/app.js
git commit -m "feat(web): 新增条目提示条交互——手动关闭 + 点击定位"
```

---

### Task 6: 自动刷新定时器（默认开启）

**Files:**
- Modify: `web/app.js`（sync 区块附近新增自动刷新控制 + init 末尾启动）

- [ ] **Step 1: 在 sync 区块末尾（pollSync 之后）新增自动刷新控制**

在 `pollSync` 函数结束后追加：

```js
// ── 自动刷新（每 60 秒，默认开启）─────
const autoRefreshCheck = $("auto-refresh-check");
let autoRefreshTimer = null;
const AUTO_REFRESH_INTERVAL = 60 * 1000;

function startAutoRefresh() {
  if (autoRefreshTimer) return;
  autoRefreshTimer = setInterval(autoRefreshTick, AUTO_REFRESH_INTERVAL);
}

function stopAutoRefresh() {
  if (autoRefreshTimer) {
    clearInterval(autoRefreshTimer);
    autoRefreshTimer = null;
  }
}

async function autoRefreshTick() {
  // 已有同步在跑则跳过，避免并发
  try {
    const st = await (await fetch("/api/sync/status")).json();
    if (st.running) return;
  } catch (e) {
    return;  // 状态查询失败，跳过本次
  }
  runSync();
}

autoRefreshCheck.addEventListener("change", () => {
  if (autoRefreshCheck.checked) {
    // 开启：立即同步一次，然后启动定时器
    runSync();
    startAutoRefresh();
  } else {
    stopAutoRefresh();
  }
});
```

- [ ] **Step 2: 在 init 末尾启动自动刷新**

把 `web/app.js` 末尾的 init IIFE（原第 503-522 行）的最后一行 `})();` 前插入启动调用：

```js
  // 默认开启自动刷新
  startAutoRefresh();
})();
```

- [ ] **Step 3: 浏览器手动验证**

启动 server，打开查看器。
预期：
- 页面加载后自动刷新默认开启（toggle 处于开启态）
- 等 60 秒（或临时把 `AUTO_REFRESH_INTERVAL` 改小测试），自动触发一次同步，同步完成后静默更新
- 关闭 toggle：定时器停止，不再自动同步
- 重新打开 toggle：立即触发一次同步，然后恢复定时
- 同步进行中时，自动刷新 tick 跳过（不并发）

- [ ] **Step 4: Commit**

```bash
git add web/app.js
git commit -m "feat(web): 自动刷新——每 60 秒默认开启，开关联动立即同步"
```

---

### Task 7: 全量回归验收

**Files:**
- 无代码改动，纯验收

- [ ] **Step 1: 跑后端测试确保无回归**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: 63 passed（前端改动不影响后端测试）

- [ ] **Step 2: 浏览器全流程验收清单**

启动 server，逐项确认：

1. **手动同步不整页刷新**：选中某天 → 点同步 → 同步完成后页面无闪动、选中日期/滚动位置保留
2. **新增帖子 prepend + 高亮**：同步有新微博时，新卡片在列表顶部、橙色高亮 2 秒淡出
3. **提示条**：有新增时顶部出现"新增 N 条微博"；点 `×` 关闭；点提示条滚动到首条新帖
4. **无新增时无提示条**：同步后无新微博时不显示提示条
5. **日期树更新**：同步后左侧月份计数更新；有新月份时插入新月份项；已展开月份保持展开
6. **搜索视图不更新**：打开搜索面板做搜索 → 触发同步 → 搜索结果不变
7. **自动刷新默认开启**：页面加载后 toggle 开启态；60 秒后自动触发同步
8. **自动刷新开关**：关闭→停止定时；开启→立即同步一次 + 恢复定时
9. **并发保护**：同步进行中时，自动刷新 tick 跳过（不重复触发）
10. **同步失败**：同步 exit_code≠0 时状态栏提示"同步失败"，页面不变

- [ ] **Step 3: 合并到 master**

```bash
git checkout master
git merge --no-ff <branch-name> -m "merge: 同步后静默刷新 + 自动刷新"
```
