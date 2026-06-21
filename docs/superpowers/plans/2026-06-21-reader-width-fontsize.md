# 阅读列宽与字号优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将微博卡片内容收窄到 760px 左对齐、正文字号 14px→15px，转发文字保持 13px。

**Architecture:** 仅改 `web/style.css` 两处：给 `.post-card` 加 `max-width: 760px`，`.post-text` 的 `font-size` 改 15px。零 JS 改动，无构建步骤。

**Tech Stack:** 原生 CSS（无预处理器/无框架）

**Spec:** `docs/superpowers/specs/2026-06-21-reader-width-fontsize-design.md`

---

## 当前 CSS 现状（修改前）

`web/style.css:66-71`（`.post-card`）：
```css
.post-card {
  background: #fff; border-radius: 8px;
  box-shadow: 0 2px 8px rgba(0,0,0,.12);
  border-left: 3px solid #ff8200;
  margin-bottom: 16px; padding: 14px 16px; transition: box-shadow .2s;
}
```

`web/style.css:81`（`.post-text`）：
```css
.post-text { font-size: 14px; color: #333; white-space: pre-wrap; word-break: break-word; line-height: 1.6; }
```

`web/style.css:91`（`.retweet-text`，保持不变）：
```css
.retweet-text { font-size: 13px; color: #666; white-space: pre-wrap; word-break: break-word; line-height: 1.5; }
```

---

### Task 1: 卡片最大宽度 760px

**Files:**
- Modify: `web/style.css:66-71`（`.post-card` 规则块）

- [ ] **Step 1: 给 `.post-card` 加 `max-width: 760px`**

把 `web/style.css` 中的 `.post-card` 规则块：

```css
.post-card {
  background: #fff; border-radius: 8px;
  box-shadow: 0 2px 8px rgba(0,0,0,.12);
  border-left: 3px solid #ff8200;
  margin-bottom: 16px; padding: 14px 16px; transition: box-shadow .2s;
}
```

改为（新增最后一行 `max-width`）：

```css
.post-card {
  background: #fff; border-radius: 8px;
  box-shadow: 0 2px 8px rgba(0,0,0,.12);
  border-left: 3px solid #ff8200;
  margin-bottom: 16px; padding: 14px 16px; transition: box-shadow .2s;
  max-width: 760px;
}
```

- [ ] **Step 2: 校验 CSS 语法**

运行：
```bash
python -c "import re,sys; c=open('web/style.css',encoding='utf-8').read(); print('OK' if c.count('{')==c.count('}') else 'BRACE MISMATCH')"
```
Expected: `OK`

- [ ] **Step 3: 提交**

```bash
git add web/style.css
git commit -m "style(web): 卡片最大宽度760px，内容左对齐右侧留白"
```

---

### Task 2: 正文字号 15px

**Files:**
- Modify: `web/style.css:81`（`.post-text` 规则）

- [ ] **Step 1: 把 `.post-text` 的 `font-size` 从 14px 改为 15px**

把 `web/style.css` 中的：

```css
.post-text { font-size: 14px; color: #333; white-space: pre-wrap; word-break: break-word; line-height: 1.6; }
```

改为：

```css
.post-text { font-size: 15px; color: #333; white-space: pre-wrap; word-break: break-word; line-height: 1.6; }
```

- [ ] **Step 2: 确认 `.retweet-text` 未被改动**

运行：
```bash
grep -n "retweet-text\|post-text" web/style.css
```
Expected 输出包含且 `retweet-text` 一行仍为 `font-size: 13px`，`post-text` 一行为 `font-size: 15px`。例如：
```
81:.post-text { font-size: 15px; color: #333; ... line-height: 1.6; }
91:.retweet-text { font-size: 13px; color: #666; ... line-height: 1.5; }
```
若 `retweet-text` 不是 13px，回退本步只改 `.post-text`。

- [ ] **Step 3: 提交**

```bash
git add web/style.css
git commit -m "style(web): 正文字号14px→15px，转发文字保持13px"
```

---

### Task 3: 视觉验收（人工）

纯 CSS 视觉改动，无自动化测试可写；需启动本地服务在浏览器确认。

**Files:** 无（仅运行验证）

- [ ] **Step 1: 启动本地服务**

运行（后台）：
```bash
python server.py
```
确认终端打印监听端口（默认应为 `http://127.0.0.1:8000` 或类似）。

- [ ] **Step 2: 宽屏验收（≥1200px 视口）**

浏览器打开服务地址，选任一有微博的日期，检查：
1. 卡片内容收窄到约 760px，左对齐，右侧大片留白。
2. 滚动条仍贴视口最右侧（`#post-list` 全宽未变）。
3. 正文文字明显比之前大（15px）。
4. 转发引用块（橙色竖线块）文字比正文小（13px），层级分明。
5. 顶部日期指示条「YYYY-MM-DD 共 N 条」仍通栏，未收窄。
6. 长文微博、含转发原微博、含图片/视频的卡片排版正常，无横向溢出。

- [ ] **Step 3: 窄屏验收（约 800px 视口）**

浏览器开发者工具或缩窄窗口到约 800px 宽，检查：
1. 卡片自适应收缩到视口宽度，不出现横向滚动条。
2. 行为同现状，正文仍为 15px、转发 13px。

- [ ] **Step 4: 停止服务并收尾**

停止后台 `server.py`（Ctrl+C 或关闭终端）。无需提交（本任务无文件改动）。

---

## Self-Review

**1. Spec coverage:**
- 内容列最大宽度 760px → Task 1 ✓
- 正文字号 15px → Task 2 ✓
- 转发文字 13px 不变 → Task 2 Step 2 显式校验未改动 ✓
- 左对齐右侧留白 → `.post-card` 块级 + max-width 天然左对齐（已在 spec 说明）✓
- 顶部日期条保持通栏 → 不改 `#day-indicator`，Task 3 Step 2 第 5 点验收 ✓
- 不改 HTML/JS → 全程仅 `web/style.css` ✓
- 验证（宽屏/窄屏/各种卡片类型）→ Task 3 ✓

**2. Placeholder scan:** 无 TBD/TODO；每步含完整代码与确切命令。✓

**3. Type/命名一致性:** 选择器名 `.post-card` / `.post-text` / `.retweet-text` 与源码一致，无歧义。✓
