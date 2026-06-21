"use strict";
/* WeiboBlog 消息查看器前端 —— 原生 JS，无框架无构建。
 * 与 weibogroup 同构但更简：无游标分页、无触顶触底加载、无发送者筛选。
 * 点开某日一次查全部，倒序（最新在上）。
 */

const $ = (id) => document.getElementById(id);
const bloggerSelect = $("blogger-select");
const statusEl = $("status");
const dateList = $("date-list");
const dayIndicator = $("day-indicator");
const postList = $("post-list");
const emptyHint = $("empty-hint");

// 当前选中博主 uid（null=全部）；切换博主时月份/日期请求带此 uid
let currentUid = null;
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

function linkify(escaped) {
  // 在已转义的文本里把 URL 转链接（http(s):// 完整链接 + http://t.cn 短链）
  // URL 在中文标点（全角括号/逗号/句号等）及半角括号处截断，避免吞掉后续文字
  return escaped.replace(
    /(https?:\/\/[^\s<()（）［］【】，。；：、！？·…—]+)/g,
    '<a href="$1" target="_blank" rel="noopener">$1</a>'
  );
}

// ── 博主列表（顶栏选择器）──────────────
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

bloggerSelect.addEventListener("change", async () => {
  const v = bloggerSelect.value;
  currentUid = v ? Number(v) : null;
  // 切换博主：清空缓存与内容，重载月份，自动加载最近一天
  for (const k of Object.keys(monthCache)) delete monthCache[k];
  currentDay = null;
  postList.innerHTML = "";
  dayIndicator.textContent = "";
  emptyHint.hidden = true;
  await loadMonths();
  const firstMonthGrp = dateList.querySelector(".month-group");
  if (firstMonthGrp) {
    await toggleMonth(firstMonthGrp, firstMonthGrp.dataset.month);
    const firstDay = firstMonthGrp.querySelector(".date-item");
    if (firstDay) selectDay(firstDay.dataset.date, firstDay);
    else { emptyHint.textContent = "请从左侧选择日期"; emptyHint.hidden = false; }
  } else { emptyHint.textContent = "无微博数据"; emptyHint.hidden = false; }
});

// ── 月份列表 ──────────────────────────
async function loadMonths() {
  let months;
  try {
    const uidParam = currentUid !== null ? `&uid=${currentUid}` : "";
    months = await getJson(`/api/months?${uidParam}`);
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
    const uidParam = currentUid !== null ? `&uid=${currentUid}` : "";
    const days = await getJson(`/api/dates?month=${encodeURIComponent(month)}${uidParam}`);
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
    const uidParam = currentUid !== null ? `&uid=${currentUid}` : "";
    data = await getJson(`/api/posts?date=${encodeURIComponent(date)}${uidParam}`);
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

// 生成媒体占位符 HTML（图片+视频），原微博与转发原微博共用
function mediaHtml(pics, videoUrl, weiboUrl) {
  let h = "";
  if (pics && pics.length) {
    const urls = JSON.stringify(pics.map(pic => pic.url_large || pic.url_bmiddle || ""));
    h += `<div class="post-pics" data-pics='${escHtml(urls)}'>` +
      `<button class="pics-btn" type="button"><span class="pics-icon">🖼</span> 图片 ${pics.length} 张</button>` +
      `</div>`;
  }
  if (videoUrl) {
    h += `<div class="post-video">` +
      `<a class="pics-btn" href="${escHtml(weiboUrl)}" target="_blank" rel="noopener">` +
      `<span class="pics-icon">🎬</span> 视频</a></div>`;
  }
  return h;
}

function renderCard(p) {
  const card = document.createElement("div");
  card.className = "post-card";
  card.id = "post-" + p.mblogid;

  // 原微博链接（右上角，新标签打开）
  const weiboUrl = `https://weibo.com/${p.uid}/${p.mblogid}`;
  let html = `<span class="post-time">${fmtTime(p.created_at)}</span>` +
    `<a class="post-link" href="${escHtml(weiboUrl)}" target="_blank" rel="noopener" title="在微博查看">原微博 ↗</a>`;

  // 正文（URL 转可点击链接，含 t.cn 短链）
  // 转发微博：text_raw 末尾的「 //@用户名:原微博内容」与引用块重复，截掉
  let bodyText = p.text_raw || "";
  if (p.retweeted) {
    const cut = bodyText.search(/\s*\/\/@/);
    if (cut >= 0) bodyText = bodyText.slice(0, cut);
  }
  // 长文微博：text_raw 是 long_text 的截断前缀，只显示 long_text（完整版），避免重复
  if (p.is_long_text && p.long_text) {
    html += `<div class="post-text">${linkify(escHtml(p.long_text))}</div>`;
  } else {
    html += `<div class="post-text">${linkify(escHtml(bodyText))}</div>`;
  }

  // 本微博媒体
  html += mediaHtml(p.pics, p.video_url, weiboUrl);

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

  // 元信息
  html += `<div class="post-meta">` +
    `<span class="count">转发 ${p.reposts_count}</span>` +
    `<span class="count">评论 ${p.comments_count}</span>` +
    `<span class="count">赞 ${p.attitudes_count}</span>` +
    (p.source ? `<span class="source">· ${escHtml(p.source)}</span>` : "") +
    `</div>`;

  card.innerHTML = html;

  // 图片占位符点击 → lightbox（支持多图翻页，本微博与转发原微博都绑定）
  card.querySelectorAll(".post-pics").forEach(picsEl => {
    let urls = [];
    try { urls = JSON.parse(picsEl.dataset.pics || "[]"); } catch (e) {}
    if (urls.length) {
      picsEl.querySelector(".pics-btn").addEventListener("click", () => openLightbox(urls, 0));
    }
  });
  return card;
}

// ── lightbox（支持多图翻页）──────────
const lightbox = $("lightbox");
const lbStage = lightbox.querySelector(".lightbox-stage");
let lbUrls = [];
let lbIndex = 0;

function openLightbox(urls, index = 0) {
  lbUrls = urls.filter(u => u);
  if (!lbUrls.length) return;
  lbIndex = Math.min(index, lbUrls.length - 1);
  renderLbImage();
  lightbox.classList.remove("hidden");
}

function renderLbImage() {
  const url = lbUrls[lbIndex] || "";
  const counter = lbUrls.length > 1 ? `${lbIndex + 1} / ${lbUrls.length}` : "";
  // 走 server 代理带 Referer，绕 sinaimg 防盗链（直链会 403）
  const proxySrc = `/api/img?url=${encodeURIComponent(url)}`;
  lbStage.innerHTML = `<img src="${escHtml(proxySrc)}" alt="图片">` +
    (counter ? `<div class="lb-counter">${counter}</div>` : "") +
    (lbIndex > 0 ? `<button class="lb-prev" type="button" title="上一张">‹</button>` : "") +
    (lbIndex < lbUrls.length - 1 ? `<button class="lb-next" type="button" title="下一张">›</button>` : "");
  const prev = lbStage.querySelector(".lb-prev");
  const next = lbStage.querySelector(".lb-next");
  if (prev) prev.addEventListener("click", (e) => { e.stopPropagation(); lbIndex--; renderLbImage(); });
  if (next) next.addEventListener("click", (e) => { e.stopPropagation(); lbIndex++; renderLbImage(); });
}

function closeLightbox() {
  lightbox.classList.add("hidden");
  lbStage.innerHTML = "";
  lbUrls = [];
  lbIndex = 0;
}
lightbox.querySelector(".lightbox-backdrop").addEventListener("click", closeLightbox);
lightbox.querySelector(".lightbox-close").addEventListener("click", closeLightbox);
// lightbox 内键盘：← → 翻页（Esc 关闭由下方统一监听处理）
document.addEventListener("keydown", (e) => {
  if (lightbox.classList.contains("hidden")) return;
  if (e.key === "ArrowLeft" && lbIndex > 0) { lbIndex--; renderLbImage(); }
  else if (e.key === "ArrowRight" && lbIndex < lbUrls.length - 1) { lbIndex++; renderLbImage(); }
});

// ── 搜索浮层 ──────────────────────────
const searchOverlay = $("search-overlay");
const searchKeyword = $("search-keyword");
const searchStart = $("search-start");
const searchEnd = $("search-end");
const searchStatus = $("search-status");
const searchResults = $("search-results");

function openSearch() {
  // 仅首次打开填默认起止（起 2010-01-01 至今），重开保留上次关键词/结果，便于继续查看
  if (!searchStart.value || !searchEnd.value) {
    const today = new Date();
    searchEnd.value = today.toISOString().slice(0, 10);
    searchStart.value = "2010-01-01";
  }
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
  if (currentUid !== null) params.set("uid", currentUid);
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

// ── 同步按钮（增量抓取，后台子进程）────
const syncBtn = $("sync-btn");
let syncPollTimer = null;

syncBtn.addEventListener("click", async () => {
  syncBtn.disabled = true;
  syncBtn.textContent = "同步中...";
  try {
    const resp = await fetch("/api/sync", { method: "POST" });
    if (resp.status === 409) {
      // 已有同步在跑，直接开始轮询
    } else if (!resp.ok) {
      throw new Error("同步启动失败");
    }
  } catch (e) {
    syncBtn.disabled = false;
    syncBtn.textContent = "🔄 同步";
    setStatus("同步启动失败");
    return;
  }
  // 轮询状态，每 2 秒
  syncPollTimer = setInterval(pollSync, 2000);
  pollSync();
});

async function pollSync() {
  try {
    const data = await (await fetch("/api/sync/status")).json();
    if (!data.running) {
      clearInterval(syncPollTimer);
      syncPollTimer = null;
      if (data.exit_code === 0) {
        location.reload();  // 同步成功，刷新页面
      } else {
        syncBtn.disabled = false;
        syncBtn.textContent = "🔄 同步";
        setStatus("同步失败（exit " + data.exit_code + "），请查看日志");
      }
    }
  } catch (e) {
    // 网络错误，继续轮询
  }
}

// ── 初始化 ────────────────────────────
(async function init() {
  await loadBloggers();
  await loadMonths();
  // 默认加载最近一天：展开最近月份 → 选中首个日期
  const firstMonthGrp = dateList.querySelector(".month-group");
  if (firstMonthGrp) {
    const month = firstMonthGrp.dataset.month;
    await toggleMonth(firstMonthGrp, month);  // 展开并加载日期
    const firstDay = firstMonthGrp.querySelector(".date-item");
    if (firstDay) {
      selectDay(firstDay.dataset.date, firstDay);  // 自动选中最近一天
    } else {
      emptyHint.textContent = "请从左侧选择日期";
      emptyHint.hidden = false;
    }
  } else {
    emptyHint.textContent = "无微博数据";
    emptyHint.hidden = false;
  }
})();
