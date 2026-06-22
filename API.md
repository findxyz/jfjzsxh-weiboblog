# 微博博主微博接口规范文档

> 本文档是**与实现无关的接口契约**：每个接口都给出 URL、方法、入参、出参结构、前置条件、错误处理。可作为 Python 之外（Java / Go / Rust / Node）的对接蓝本。
>
> 所有内容来自 `D:\weiboblog\weibo_blog\crawler.py`、`parser.py`、`db.py` 的实际实现，并已逐一对照源码。

---

## 目录

- [0. 全局约定](#0-全局约定)
- [1. 接口清单（一览）](#1-接口清单一览)
- [2. 接口详细规范](#2-接口详细规范)
  - [2.1 扫码登录](#21-扫码登录-web-页面非-json-接口)
  - [2.2 获取博主微博列表（翻页）](#22-获取博主微博列表翻页)
  - [2.3 获取长文全文](#23-获取长文全文)
- [3. 公共数据结构](#3-公共数据结构)
- [4. 错误与频率处理规范](#4-错误与频率处理规范)
- [5. Cookie 字段说明](#5-cookie-字段说明)
- [6. 翻页算法](#6-翻页算法)
- [7. 跨语言对接清单](#7-跨语言对接清单)

---

## 0. 全局约定

### 0.1 域名

| 用途 | 域名 | 说明 |
|------|------|------|
| API 服务 | `weibo.com` | mymblog / longtext |
| 扫码登录 | `api.weibo.com` | 登录态 Cookie 为 `.weibo.com` 域共享，对 API 同样有效 |

### 0.2 公共请求头

```
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36
            (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36 Edg/149.0.0.0
accept: application/json, text/plain, */*
accept-language: zh-CN,zh;q=0.9,en;q=0.8
x-requested-with: XMLHttpRequest
referer: https://weibo.com/u/{uid}
Cookie: <见 §5>
```

> ⚠️ **无需 x-xsrf-token**。实测 mymblog/longtext 只需 Cookie 头即可通过，不需要从 cookie 里取 XSRF-TOKEN 再回填到请求头（群聊接口才需要）。这点与 weibogroup 不同。

### 0.3 Cookie 要求

所有受保护接口都需要登录 Cookie。**最少**需要 `SUB`：

| Cookie 键 | 必需 | 说明 |
|-----------|------|------|
| `SUB` | ✅ | 主会话票据，失效后所有接口都拒绝 |
| `SUBP` | 推荐 | 辅助票据 |
| `_T_WM` / `ALF` / `SCF` / `SRF` 等 | 自动 | 扫码登录后会自动写入，无需关心 |

> **判断 Cookie 是否有效**：调用 §2.2，返回 200 且 `data.list` 数组非空即有效。

### 0.4 HTTPS / SSL

```python
verify=False   # 微博证书链在某些客户端下校验会失败
urllib3.disable_warnings()
```

### 0.5 编码与时间

- 请求/响应均为 UTF-8。
- 微博 `created_at` 字段是字符串如 `"Wed May 14 21:15:26 +0800 2025"`，需用
  `strptime("%a %b %d %H:%M:%S %z %Y")` 解析后转**毫秒时间戳**存储。
- 存储统一用毫秒时间戳。

---

## 1. 接口清单（一览）

| # | 名称 | 方法 | URL | 鉴权 | 用途 |
|---|------|------|-----|------|------|
| 1 | 扫码登录页 | GET | `https://api.weibo.com/chat` | 无 | 取得 Cookie |
| 2 | 博主微博列表 | GET | `https://weibo.com/ajax/statuses/mymblog` | Cookie | 翻页拉微博 |
| 3 | 长文全文 | GET | `https://weibo.com/ajax/statuses/longtext` | Cookie | 补全长文 |
| 4 | 按时间范围搜索博主微博 | GET | `https://weibo.com/ajax/statuses/searchProfile` | Cookie | 时间范围抓取 |

> **接口顺序**：先用 ① 拿 Cookie → 用 ② 翻页拉微博 → 对 `isLongText=true` 的用 ③ 补全文。

---

## 2. 接口详细规范

### 2.1 扫码登录 (Web 页面，非 JSON 接口)

**用途**：取得一个微博登录态 Cookie，供后续两个 JSON 接口使用。

| 项 | 值 |
|----|-----|
| URL | `https://api.weibo.com/chat` |
| 方法 | `GET` |
| 鉴权 | 无 |
| 返回 | HTML 主站 |

**流程（必须用无头浏览器模拟，不是简单 HTTP）：**

1. 用 Playwright / Selenium / Puppeteer 打开 `https://api.weibo.com/chat`。
2. 未登录时页面直接渲染二维码图片（180×180，src 指向 `v2.qr.weibo.cn/inf/gen`），截图即可扫码。
3. 等待用户用微博 APP 扫码 + 手机端确认。
4. 登录成功后：页面 hash 路由由 `#/` 跳转为 `#/chat`。
5. 提取所有 `domain` 以 `.weibo.com` 结尾的 Cookie，拼成 `k1=v1; k2=v2` 字符串存库。

> 选 `api.weibo.com/chat` 而非 `weibo.com`：后者登录是 SPA 弹层，二维码未必渲染、
> URL 也不稳定；前者未登录即渲染二维码，登录后 hash 变化明确可靠。
> 该域下发的登录态 Cookie 是 `.weibo.com` 域共享的，对 `weibo.com/ajax/*` 接口同样有效。

**判定登录成功的判据（源码 `crawler.py:_is_logged_in`）：**

```python
def _is_logged_in(page) -> bool:
    try:
        href = page.evaluate("window.location.href") or ""
    except Exception:
        return False
    return "#/chat" in href
```

> 不依赖 cookie 中的 `SUB`（未登录态也会下发匿名 SUB），也不依赖
> `a[href*="/u/"]`（登录页的热门博主推荐就有大量此类链接，会误判）。

**前置条件**

- 安装无头浏览器（Playwright + Chromium）。
- 用户已安装微博 APP 且账号可登录。

**注意事项**

| 问题 | 处理 |
|------|------|
| 反爬检测 | `--disable-blink-features=AutomationControlled` 启动参数 |
| 无桌面环境 | headless 模式 + 截图二维码到 `qrcode.png`，用图片查看器打开 |
| User-Agent | 伪装为桌面 Chrome |
| 二维码有效期 | 实现里轮询 120 秒超时 |
| Cookie 有效期 | `SUB` 大约几天到几周，过期需重扫 |

**输出：** 一个 cookie 字符串（直接进数据库 config 表）。

---

### 2.2 获取博主微博列表（翻页）

> ⭐ **核心接口**。爬虫 90% 的时间花在这里。

| 项 | 值 |
|----|-----|
| URL | `https://weibo.com/ajax/statuses/mymblog` |
| 方法 | `GET` |
| 鉴权 | Cookie（需含 `SUB`） |
| 返回 | JSON |

**Query 参数**

| 参数 | 类型 | 必需 | 默认/示例 | 说明 |
|------|------|------|-----------|------|
| `uid` | int | 是 | `1401527553` | 博主 uid |
| `page` | int | 是 | `1` | **页码，递增取更旧**（见 §6） |
| `feature` | int | 是 | `0` | 固定 0 |
| `since_id` | string | 否 | — | 服务端下发的分页游标，必须回传以正确翻页（不传会漏数据/被风控）。深翻偶发 414 时，由客户端降级重试一次（仅用 page，不带 since_id），见 §6 |

**关键：返回顺序 ⚠️**

**`data.list` 内部按「从旧到新」(oldest first) 排列**：

```
list[0]   = 这一页里最旧的微博
list[-1]  = 这一页里最新的微博  →  停止条件判定（增量模式）
```

**page 递增 = 取更旧的数据**：page=1 是最新的一屏，page=2 是更旧的，依此类推，直到某页 list 为空表示到底。

> 这与直觉相反（直觉以为 page 递增取更新），**翻译成其他语言时务必遵守这个顺序假设**。

**成功响应（200）**

```json
{
  "ok": 1,
  "data": {
    "since_id": "abc_kp2",
    "list": [
      {
        "id": 5166313246299004,
        "mblogid": "PrP6QqqEQ",
        "created_at": "Wed May 14 21:15:26 +0800 2025",
        "text": "带标签的正文",
        "text_raw": "纯文本正文",
        "source": "<a href=\"...\">微博网页版</a>",
        "region_name": "发布于 北京",
        "isLongText": false,
        "reposts_count": 5,
        "comments_count": 20,
        "attitudes_count": 393,
        "user": { "id": 1401527553, "screen_name": "tombkeeper", ... },
        "pic_infos": { ... },
        "page_info": { "media_info": { "stream_url": "..." } },
        "retweeted_status": { ... }
      }
    ]
  }
}
```

**字段语义**

| 字段 | 类型 | 含义 | 备注 |
|------|------|------|------|
| `id` | int | **post_id**，数字 ID，单调递增 | 增量比较新旧用 |
| `mblogid` | string | 短链 ID（如 `PrP6QqqEQ`） | UNIQUE 去重键 |
| `created_at` | string | `"Wed May 14 21:15:26 +0800 2025"` | 需 strptime 解析 |
| `text` / `text_raw` | string | 带标签原文 / 纯文本 | |
| `source` | string | 来源（含 `<a>` 标签） | 解析时去标签取纯文本 |
| `region_name` | string | 发布地（如 "发布于 北京"） | |
| `isLongText` | bool | 是否长文 | true 时需调 §2.3 补全文 |
| `reposts/comments/attitudes_count` | int | 转发/评论/赞数 | |
| `user` | object | 博主信息 | 首页 list[0].user 用于提取博主入库 |
| `pic_infos` | object | 图片信息（见 §3.2） | |
| `page_info.media_info.stream_url` | string | 视频直链 | |
| `retweeted_status` | object | 转发原微博（见 §3.3） | 无则为普通原创微博 |

**前置条件**

- Cookie 有效。
- `uid` 是公开可见的博主。

**注意事项**

| 场景 | 表现/处理 |
|------|----------|
| Cookie 失效 | 200 但 `list` 为空，或 302 跳登录 |
| 限流 | 短时间高频会 429，详见 §4 |
| 频率建议 | 每页间 **≥ 0.5s**（带 ±20% 抖动） |
| 历史深度 | **无年限限制**——实测可翻到 2011 年的微博，直到 list 为空 |

---

### 2.3 获取长文全文

| 项 | 值 |
|----|-----|
| URL | `https://weibo.com/ajax/statuses/longtext` |
| 方法 | `GET` |
| 鉴权 | Cookie |
| 返回 | JSON |

**Query 参数**

| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `id` | string | 是 | 微博 mblogid（短链 ID） |

**成功响应（200）**

```json
{
  "ok": 1,
  "data": {
    "longTextContent": "长文全文内容..."
  }
}
```

**前置条件**

- Cookie 有效。
- `id` 是 `isLongText=true` 的微博 mblogid。

**注意事项**

| 场景 | 处理 |
|------|------|
| 补全失败 | 捕获异常，记 warning，`long_text` 留空，不中断整体抓取 |
| 频率 | 跟随翻页节奏（每条长文一个请求） |

---

### 2.4 按时间范围搜索博主微博（searchProfile）

| 项 | 值 |
|----|-----|
| URL | `https://weibo.com/ajax/statuses/searchProfile` |
| 方法 | `GET` |
| 鉴权 | Cookie（需含 `SUB`） |
| 返回 | JSON |

**Query 参数**

| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `uid` | int | 是 | 博主 uid |
| `page` | int | 是 | 页码，递增取更旧 |
| `starttime` | int | 是 | 起始秒级时间戳（+0800） |
| `endtime` | int | 是 | 结束秒级时间戳（+0800） |
| `hasori` | int | 是 | 含原创，固定 1 |
| `hasret` | int | 是 | 含转发，固定 1 |
| `hastext` | int | 是 | 含文本，固定 1 |
| `haspic` | int | 是 | 含图片，固定 1 |
| `hasvideo` | int | 是 | 含视频，固定 1 |
| `hasmusic` | int | 是 | 含音乐，固定 1 |

**成功响应（200）**

```json
{
  "ok": 1,
  "data": {
    "total": "934",
    "absstr": "",
    "list": [ {mblog}, ... ]
  }
}
```

**字段语义**

| 字段 | 类型 | 含义 | 备注 |
|------|------|------|------|
| `total` | string | 时间范围内微博总数 | 字符串需转 int；仅日志参考，不精确 |
| `absstr` | string | 摘要 | 实测为空 |
| `list` | array | mblog 数组 | 结构与 §2.2 mymblog 一致，可复用 `parse_post` |

**关键差异（与 §2.2 mymblog 对比）**

| 维度 | mymblog | searchProfile |
|------|---------|---------------|
| list 内部排序 | 旧→新（首条最旧） | **新→旧**（首条最新） |
| 分页游标 | since_id（必须回传） | **无**，纯 page 翻页 |
| 时间范围 | 无 | starttime/endtime |
| 414 降级 | 有（since_id 导致 URI 过长） | **不需要**（无 since_id，URL 短） |

**前置条件**

- Cookie 有效。
- `uid` 是公开可见的博主。
- `starttime`/`endtime` 用秒级时间戳（非毫秒），时区 +0800。

**注意事项**

| 场景 | 表现/处理 |
|------|----------|
| Cookie 失效 | 200 但 `list` 为空（与 mymblog 一致） |
| 时间范围内无微博 | `list` 为空（与 cookie 失效无法区分） |
| 翻页终止 | `list` 为空即到底 |
| 频率建议 | 每页间 ≥ 0.5s（带 ±20% 抖动） |

**编排策略（按日拆分）**：调用方 `crawl_blog_by_range` 将 `start_date ~ end_date`
按日拆分，逐日以当天 00:00:00~23:59:59 为 starttime/endtime 翻页。按日拆分避免
了大范围（按月/按年）翻页时分页边界丢数据的问题（实测按月会丢）；单日数据量通常
≤ 50 条一页即完，高频日仍会翻页，不丢数据。跨天重复的微博靠 mblogid 去重。

---

## 3. 公共数据结构

### 3.1 微博统一格式（解析后）

`parser.parse_post(raw)` 输出的扁平字典，存入 `weibo_posts` 表：

```python
{
  "mblogid":         str,      # 短链 ID，UNIQUE
  "post_id":         int,      # 数字 ID，单调递增
  "uid":             int,      # 博主 uid
  "text":            str,      # 带标签原文
  "text_raw":        str,      # 纯文本
  "long_text":       str,      # 长文全文（crawler 补全，非长文为空）
  "is_long_text":    int,      # 0/1
  "source":          str,      # 去标签后的来源（如 "微博网页版"）
  "region":          str,      # 发布地
  "pics_json":       str,      # JSON string，见 §3.2
  "video_url":       str,      # 视频直链
  "retweeted_json":  str,      # JSON string，见 §3.3（无转发则空）
  "reposts_count":   int,
  "comments_count":  int,
  "attitudes_count": int,
  "created_at":      int,      # ms 时间戳
  "raw_json":        str,      # 原始 JSON 永久备份
}
```

### 3.2 pics_json（图片精简）

原始 `pic_infos` 是 `{pid: {large:{url,w,h}, bmiddle:{url}}}`，精简为：

```json
[
  {
    "pid": "53899d01ly1i1f0qezg9nj20mj0oytjr",
    "url_large": "https://wx3.sinaimg.cn/orj960/53899d01ly1i1f0qezg9nj20mj0oytjr.jpg",
    "url_bmiddle": "https://wx3.sinaimg.cn/wap360/53899d01ly1i1f0qezg9nj20mj0oytjr.jpg",
    "w": 811,
    "h": 898
  }
]
```

只保留 large/bmiddle 两个尺寸 + 宽高，丢弃其他冗余尺寸（thumbnail/Small/cmw720 等）。

### 3.3 retweeted_json（转发精简）

原始 `retweeted_status` 是完整微博对象，精简为：

```json
{
  "post_id": 4636881109388307,
  "mblogid": "KftrEDokj",
  "text_raw": "原微博纯文本",
  "uid": 1401527553,
  "screen_name": "tombkeeper",
  "created_at": "Fri May 14 22:21:08 +0800 2021"
}
```

### 3.4 博主信息（parse_blogger）

从首页 `list[0].user` 提取：

```python
{
  "uid":         int,      # user.id
  "screen_name": str,
  "avatar":      str,      # avatar_large 优先，否则 profile_image_url
  "profile_url": str,      # 如 /u/1401527553
  "verified":    int,      # 0/1
  "raw_json":    str,      # 原始 user JSON
}
```

---

## 4. 错误与频率处理规范

### 4.1 HTTP 状态码处理矩阵

| 状态码 | 含义 | 重试？ | 退避 |
|--------|------|--------|------|
| 200 | 成功 | — | — |
| 429 | 限流 | ✅ | 4^n × (1 + rand[0,0.5]) 秒 |
| 414 | URI 过长 | 降级重试一次 | 深翻偶发；去掉 since_id 仅用 page 重试，仍 414 则编排层优雅停止保留已抓数据（见 §2.2/§6） |
| 5xx | 服务端错 | ✅ | 2^n × (1 + rand[0,0.5]) 秒 |
| 4xx (非429) | 客户端错 | ❌ | 立即抛出 |
| ConnectionError / Timeout | 网络错 | ✅ | 2^n × (1 + rand[0,0.5]) 秒 |

最大重试次数：**3**。

### 4.2 业务级错误

| 错误 | 检测 | 处理 |
|------|------|------|
| Cookie 过期 | §2.2 返回空 list | 提示重新扫码登录 |
| 限流 | 429 状态 | 退避重试 |
| 长文补全失败 | §2.3 异常 | 记 warning，留空 long_text，继续 |

### 4.3 频率限制（实测经验值）

| 接口 | 建议间隔 |
|------|---------|
| §2.2 翻页 | ≥ 0.5s（带 ±20% 抖动） |
| §2.3 长文 | 跟随翻页，不额外间隔 |

> 抖动 (`random.uniform(-0.2, 0.2)`) 让请求节奏不规则，规避固定间隔的简单频控。

---

## 5. Cookie 字段说明

| 键 | 必需 | 失效表现 | 续期方式 |
|----|------|---------|---------|
| `SUB` | ✅ | 接口返回空 list / 302 | §2.1 扫码登录 |
| `SUBP` | 推荐 | 同上 | 同上 |
| `_T_WM` / `ALF` / `SCF` / `SRF` | 自动 | — | 自动写入 |

**Cookie 失效的可靠检测**：调用 §2.2，返回 200 且 `data.list` 为空 → 失效。

---

## 6. 翻页算法

### 6.1 全量回填（backfill）

> `since_id` 是服务端下发的分页游标，**必须回传**以正确翻页。深翻（数百页）
> 偶发 414 Request-URI Too Large 时，`fetch_mymblog` 内部降级重试一次
> （仅用 page、不带 since_id）；降级重试仍 414 则抛出，编排层捕获后优雅停止，
> 保留已抓数据（`save_post` 逐条 commit，已落库不丢）。

```
1. page = 1, since_id = ""
2. loop:
     try: since_id, posts = fetch_mymblog(uid, page, since_id)   # list 旧→新
                                                                  # 内部遇 414 自动降级重试
     except 414: break                                           # 降级仍 414，优雅停
     if not posts: break                                         # 到底
     if page == 1 and posts[0].user: save_blogger(...)           # 首页提取博主
     for raw in posts:
       parsed = parse_post(raw)
       if parsed.is_long_text: parsed.long_text = fetch_longtext(mblogid)
       save_post(parsed)                                         # mblogid 去重
     page += 1
     sleep(0.5s ± 20%)
```

### 6.2 增量更新（incremental）

```
1. latest = get_latest_post_id(uid)   # DB 里最大 post_id
   if latest is None: → 走全量回填
2. page = 1, since_id = ""
3. loop:
     try: since_id, posts = fetch_mymblog(uid, page, since_id)   # list 旧→新
     except 414: break                                            # URI 过长，优雅停
     if not posts: break
     for raw in posts:                    # 从旧到新
       if raw.id <= latest: continue      # 比已存旧，跳过
       parsed = parse_post(raw)
       if parsed.is_long_text: parsed.long_text = fetch_longtext(mblogid)
       save_post(parsed)
     if posts[-1].id <= latest: break     # 当页最新都不比 latest 新 → 整页已知，停
     page += 1
     sleep(0.5s ± 20%)
```

### 6.3 三层去重

| 层 | 作用 |
|----|------|
| 翻页层 | 当页末条 `post_id <= latest` → 整页丢弃，不再翻 |
| 内存过滤 | `post_id <= latest` 的 continue，不解析不入库 |
| DB 约束 | `mblogid` UNIQUE + `INSERT OR IGNORE` 兜底 |

> 增量模式**只补更新、不补更早**。比已存更早的历史只能靠全量回填。

---

## 7. 跨语言对接清单

### 7.1 最小可用流程

```
1. (Playwright/Selenium) 登录 api.weibo.com/chat → 取 Cookie 串
2. (HTTP GET 翻页) fetch_mymblog(uid, page=1..N) → 解析存库（mblogid 去重）
3. (HTTP GET) 对 isLongText=true 的调 longtext 补全文
```

### 7.2 关键迁移决策点

| 决策 | Python 选择 | 推荐做法 |
|------|-------------|---------|
| 去重键 | `mblogid` TEXT UNIQUE | 同（字符串唯一键） |
| 新旧比较 | `post_id` int 单调递增 | 同（数字比较） |
| 时间戳 | INTEGER ms | BIGINT |
| 原始 JSON | TEXT 永久保留 | 同（便于重新解析） |
| Cookie 存储 | DB config 表 | 同（KV 表） |
| 重试退避 | `time.sleep` 指数+抖动 | 同 |
| 翻页方向 | page 递增取更旧，list 旧→新 | **务必遵守** |

### 7.3 必须原样保留的设计

1. **三层去重**（§6.3）—— 翻页层 + 内存过滤 + DB UNIQUE
2. **抖动 sleep** —— 固定间隔易被风控
3. **list 旧→新 + page 递增取更旧** —— 顺序假设不能反
4. **`raw_json` 永久保留** —— 解析逻辑会演进
5. **`created_at` 字符串解析** —— 含时区，用 `%z` 解析
6. **无需 x-xsrf-token** —— 与群聊接口不同

---

## 附录：实现文件对照表

| 章节 | 源码位置 |
|------|---------|
| §0.2 公共请求头 | `crawler.py:_make_session` |
| §2.1 登录流程 | `crawler.py:renew_cookie` |
| §2.2 微博列表 | `crawler.py:fetch_mymblog` |
| §2.3 长文 | `crawler.py:fetch_longtext` |
| §3.1 统一格式 | `parser.py:parse_post` |
| §3.2 图片精简 | `parser.py:parse_post` (pics 部分) |
| §3.3 转发精简 | `parser.py:parse_post` (retweeted 部分) |
| §3.4 博主信息 | `parser.py:parse_blogger` |
| §4 错误处理 | `crawler.py:_request_with_retry` |
| §6 翻页算法 | `crawler.py:crawl_blog_backfill` / `crawl_blog_incremental` |
| 表结构 DDL | `db.py:init_db` |
