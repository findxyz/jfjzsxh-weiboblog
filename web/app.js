"use strict";
/* WeiboBlog 消息查看器前端 —— 原生 JS，无框架无构建。
 * 与 weibogroup 同构但更简：无游标分页、无触顶触底加载、无发送者筛选。
 * 点开某日一次查全部，倒序（最新在上）。
 */

const $ = (id) => document.getElementById(id);
const bloggerName = $("blogger-name");
const statusEl = $("status");
const dateList = $("date-list");
const dayIndicator = $("day-indicator");
const postList = $("post-list");
const emptyHint = $("empty-hint");

// 月份日期缓存：{ "2025-06": [{date,count}, ...] }
const monthCache = {};
let currentDay = null;

// ── 工具 ──────────────────────────────
function fmtTime(ms) {
  // ms → CST HH:MM（按 +8 计算，不依赖系统时区）
  const d = new Date(ms + 8 * 3600 * 1000);
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mm = String(d.getUTCMinutes()).padStart(2, "0");
  return `${hh}:${mm}`;
}

function setStatus(msg) { statusEl.textContent = msg || ""; }

function escHtml(s) {
  return (s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

// snippet 用 \x00 \x01 包裹命中词，转成 <mark>
function snippetToHtml(snippet) {
  return escHtml(snippet).replace(/\x00/g, "<mark>").replace(/\x01/g, "</mark>");
}

async function getJson(path) {
  const resp = await fetch(path);
  if (!resp.ok) {
    const txt = await resp.text().catch(() => "");
    throw new Error(`${resp.status} ${txt}`);
  }
  return resp.json();
}

// ── 博主信息 ──────────────────────────
async function loadBlogger() {
  try {
    const b = await getJson("/api/blogger");
    bloggerName.textContent = b.screen_name || `uid:${b.uid}`;
    bloggerName.title = b.verified ? "已认证" : "";
  } catch (e) {
    if (String(e).includes("404")) {
      bloggerName.textContent = "（无博主数据，请先抓取）";
    } else {
      bloggerName.textContent = "加载失败";
      setStatus("博主信息加载失败");
    }
  }
}

// ── 月份列表 ──────────────────────────
async function loadMonths() {
  let months;
  try {
    months = await getJson("/api/months");
  } catch (e) {
    setStatus("月份列表加载失败");
    return;
  }
  dateList.innerHTML = "";
  if (!months.length) {
    dateList.innerHTML = '<div style="padding:16px;color:#999;font-size:13px">无微博数据</div>';
    return;
  }
  for (const m of months) {
    const grp = document.createElement("div");
    grp.className = "month-group";
    grp.dataset.month = m.month;
    grp.innerHTML =
      `<div class="month-header">${escHtml(m.month)} <span class="count">(${m.count})</span></div>` +
      `<div class="month-days"></div>`;
    grp.querySelector(".month-header").addEventListener("click", () => toggleMonth(grp, m.month));
    dateList.appendChild(grp);
  }
}

async function toggleMonth(grp, month) {
  const daysEl = grp.querySelector(".month-days");
  const isOpen = grp.classList.toggle("open");
  if (!isOpen) return;
  if (monthCache[month]) {
    renderDays(daysEl, monthCache[month]);
    return;
  }
  daysEl.innerHTML = '<div style="padding:8px 24px;color:#bbb;font-size:12px">加载中…</div>';
  try {
    const days = await getJson(`/api/dates?month=${encodeURIComponent(month)}`);
    monthCache[month] = days;
    renderDays(daysEl, days);
  } catch (e) {
    daysEl.innerHTML = '<div style="padding:8px 24px;color:#c00;font-size:12px">加载失败</div>';
  }
}

function renderDays(daysEl, days) {
  daysEl.innerHTML = "";
  if (!days.length) {
    daysEl.innerHTML = '<div style="padding:8px 24px;color:#bbb;font-size:12px">（无）</div>';
    return;
  }
  for (const d of days) {
    const item = document.createElement("div");
    item.className = "date-item";
    item.dataset.date = d.date;
    // 日期显示为 MM-DD
    const md = d.date.slice(5);
    item.innerHTML = `${escHtml(md)} <span class="count">${d.count}</span>`;
    item.addEventListener("click", () => selectDay(d.date, item));
    daysEl.appendChild(item);
  }
}

// ── 选中日期 → 加载卡片流 ─────────────
async function selectDay(date, itemEl) {
  // 高亮当前选中
  document.querySelectorAll(".date-item.active").forEach(el => el.classList.remove("active"));
  if (itemEl) itemEl.classList.add("active");
  currentDay = date;
  dayIndicator.textContent = `${date}  加载中…`;
  postList.innerHTML = "";
  emptyHint.hidden = true;

  let data;
  try {
    data = await getJson(`/api/posts?date=${encodeURIComponent(date)}`);
  } catch (e) {
    dayIndicator.textContent = date;
    postList.innerHTML = "";
    emptyHint.textContent = "加载失败";
    emptyHint.hidden = false;
    return;
  }
  dayIndicator.textContent = `${date}  共 ${data.posts.length} 条`;
  if (!data.posts.length) {
    emptyHint.textContent = "该日无微博";
    emptyHint.hidden = false;
    return;
  }
  renderPosts(data.posts);
}

function renderPosts(posts) {
  postList.innerHTML = "";
  emptyHint.hidden = true;
  for (const p of posts) {
    postList.appendChild(renderCard(p));
  }
}

function renderCard(p) {
  const card = document.createElement("div");
  card.className = "post-card";
  card.id = "post-" + p.mblogid;

  let html = `<span class="post-time">${fmtTime(p.created_at)}</span>`;

  // 正文
  html += `<div class="post-text">${escHtml(p.text_raw)}</div>`;
  if (p.is_long_text && p.long_text) {
    html += `<div class="post-text long-text">${escHtml(p.long_text)}</div>`;
  }

  // 图片
  if (p.pics && p.pics.length) {
    html += '<div class="post-pics">';
    for (const pic of p.pics) {
      const url = pic.url_bmiddle || pic.url_large || "";
      const large = pic.url_large || pic.url_bmiddle || "";
      if (url) {
        html += `<img src="${escHtml(url)}" data-large="${escHtml(large)}" ` +
          `onerror="this.onerror=null;this.classList.add('pic-error');this.alt='图片加载失败';this.src=''">`;
      }
    }
    html += "</div>";
  }

  // 元信息
  html += `<div class="post-meta">` +
    `<span class="count">转发 ${p.reposts_count}</span>` +
    `<span class="count">评论 ${p.comments_count}</span>` +
    `<span class="count">赞 ${p.attitudes_count}</span>` +
    (p.source ? `<span class="source">· ${escHtml(p.source)}</span>` : "") +
    `</div>`;

  card.innerHTML = html;

  // 图片点击 → lightbox
  card.querySelectorAll(".post-pics img").forEach(img => {
    img.addEventListener("click", () => openLightbox(img.dataset.large));
  });
  return card;
}

// ── lightbox ──────────────────────────
const lightbox = $("lightbox");
function openLightbox(url) {
  const stage = lightbox.querySelector(".lightbox-stage");
  stage.innerHTML = `<img src="${escHtml(url)}" onerror="this.alt='图片加载失败'">`;
  lightbox.classList.remove("hidden");
}
function closeLightbox() {
  lightbox.classList.add("hidden");
  lightbox.querySelector(".lightbox-stage").innerHTML = "";
}
lightbox.querySelector(".lightbox-backdrop").addEventListener("click", closeLightbox);
lightbox.querySelector(".lightbox-close").addEventListener("click", closeLightbox);

// ── 搜索浮层 ──────────────────────────
const searchOverlay = $("search-overlay");
const searchKeyword = $("search-keyword");
const searchStart = $("search-start");
const searchEnd = $("search-end");
const searchStatus = $("search-status");
const searchResults = $("search-results");

function openSearch() {
  // 默认起止：最近 3 个月
  const today = new Date();
  const end = today.toISOString().slice(0, 10);
  const startD = new Date(today.getTime() - 90 * 86400000);
  searchEnd.value = end;
  searchStart.value = startD.toISOString().slice(0, 10);
  searchStatus.textContent = "";
  searchResults.innerHTML = "";
  searchOverlay.hidden = false;
  searchKeyword.focus();
}
function closeSearch() {
  searchOverlay.hidden = true;
}
$("search-btn").addEventListener("click", openSearch);
$("search-close").addEventListener("click", closeSearch);
searchOverlay.addEventListener("click", (e) => {
  if (e.target === searchOverlay) closeSearch();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if (!searchOverlay.hidden) closeSearch();
    if (!lightbox.classList.contains("hidden")) closeLightbox();
  }
});

$("search-submit").addEventListener("click", doSearch);
searchKeyword.addEventListener("keydown", (e) => {
  if (e.key === "Enter") doSearch();
});

async function doSearch() {
  const q = searchKeyword.value.trim();
  const start = searchStart.value || "";
  const end = searchEnd.value || "";
  // spec：搜索只搜内容，关键词必填；时间范围为可选过滤
  if (!q) {
    searchStatus.textContent = "请输入关键词";
    return;
  }
  const params = new URLSearchParams();
  params.set("q", q);
  if (start) params.set("start", start);
  if (end) params.set("end", end);
  params.set("limit", "1000");
  searchStatus.textContent = "搜索中…";
  searchResults.innerHTML = "";
  let data;
  try {
    data = await getJson(`/api/search?${params}`);
  } catch (e) {
    searchStatus.textContent = "搜索失败";
    return;
  }
  if (!data.results.length) {
    searchStatus.textContent = data.total > 0 ? `已达上限（${data.total} 条），请缩小范围` : "未找到匹配微博";
    return;
  }
  searchStatus.textContent = `共 ${data.total} 条结果${data.total > data.results.length ? `（已显示前 ${data.results.length} 条）` : ""}`;
  for (const r of data.results) {
    const item = document.createElement("div");
    item.className = "search-result-item";
    item.innerHTML =
      `<div class="sr-date">${escHtml(r.date)} ${fmtTime(r.created_at)}</div>` +
      `<div class="sr-snippet">${snippetToHtml(r.snippet)}</div>`;
    item.addEventListener("click", () => jumpToPost(r.date, r.mblogid));
    searchResults.appendChild(item);
  }
}

// ── 搜索结果 → 定位高亮 ───────────────
async function jumpToPost(date, mblogid) {
  closeSearch();
  // 若不在该日期，先加载
  if (currentDay !== date) {
    // 展开对应月份
    const month = date.slice(0, 7);
    const grp = dateList.querySelector(`.month-group[data-month="${month}"]`);
    if (grp && !grp.classList.contains("open")) {
      await toggleMonth(grp, month);
    }
    // 选中日期项
    const itemEl = dateList.querySelector(`.date-item[data-date="${date}"]`);
    await selectDay(date, itemEl);
  }
  // 等待渲染后定位
  requestAnimationFrame(() => {
    const el = document.getElementById("post-" + mblogid);
    if (el) {
      el.scrollIntoView({ block: "center", behavior: "smooth" });
      el.classList.add("post-highlight");
      setTimeout(() => el.classList.remove("post-highlight"), 1700);
    }
  });
}

// ── 初始化 ────────────────────────────
(async function init() {
  emptyHint.textContent = "请从左侧选择日期";
  emptyHint.hidden = false;
  await loadBlogger();
  await loadMonths();
})();
