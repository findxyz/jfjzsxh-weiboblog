# Cookie Expiry Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make both crawlers print a renew command only when Weibo returns an explicit Cookie authentication failure.

**Architecture:** Each crawler module owns a `CookieExpiredError` and validates HTTP responses at the API boundary. Empty successful results keep their existing meaning. The CLI catches only the dedicated exception and prints the project-specific renew command.

**Tech Stack:** Python 3.13, requests, pytest/unittest, SQLite

---

### Task 1: Precise detection in weiboblog

**Files:**
- Modify: `D:\weiboblog\weibo_blog\crawler.py`
- Modify: `D:\weiboblog\crawl_blog.py`
- Test: `D:\weiboblog\tests\test_crawler.py`

- [ ] **Step 1: Write failing API-boundary tests**

Add tests that construct response doubles with:

```python
fake_resp.url = "https://login.sina.com.cn/sso/login.php"
fake_resp.history = [MagicMock(status_code=302)]
```

and assert `fetch_mymblog` raises `CookieExpiredError`. Add a separate response with
`{"ok": 1, "data": {"list": []}}` and assert it returns an empty list without raising.

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
uv run pytest tests/test_crawler.py -k "cookie_expired or empty_list" -v
```

Expected: failure because `CookieExpiredError` and response validation do not exist.

- [ ] **Step 3: Implement the minimal response check**

Define `CookieExpiredError(RuntimeError)` and a helper that inspects the final response URL.
Raise only when the final host/path is an explicit login destination. Call the helper from
`fetch_mymblog`, `fetch_searchprofile`, and `fetch_longtext` before parsing JSON.

- [ ] **Step 4: Preserve CookieExpiredError through range crawling**

In `crawl_blog_by_range`, re-raise `CookieExpiredError` before the existing broad per-day
exception handler. Other failures retain the existing skip-day behavior.

- [ ] **Step 5: Add CLI error output**

Catch `CookieExpiredError` around crawl execution and log:

```text
Cookie 已过期，请运行: uv run crawl_blog.py --renew-cookie
```

Exit with a non-zero status.

- [ ] **Step 6: Verify GREEN and full suite**

Run:

```powershell
uv run pytest -q
```

Expected: all weiboblog tests pass.

- [ ] **Step 7: Commit the weiboblog fix**

```powershell
git add weibo_blog/crawler.py crawl_blog.py tests/test_crawler.py
git commit -m "fix: report explicit cookie expiry during crawl"
```

### Task 2: Precise detection in weibogroup

**Files:**
- Modify: `D:\weibogroup\weibo_im\crawler.py`
- Modify: `D:\weibogroup\crawl.py`
- Create: `D:\weibogroup\tests\test_cookie_expiry.py`

- [ ] **Step 1: Write failing contacts tests**

Mock `_request_with_retry` with JSON responses and assert:

```python
{"error_code": 21301, "error": "Auth failed, Cookie expires or invalid."}
```

raises `CookieExpiredError`, while `{"contacts": []}` and
`{"error_code": 10012, "error": "服务异常"}` do not raise it.

- [ ] **Step 2: Write failing message tests**

Assert explicit `21301` raises, while `{"result": False}` without an authentication error
continues to return `[]`.

- [ ] **Step 3: Run tests and verify RED**

Run:

```powershell
uv run pytest tests/test_cookie_expiry.py -v
```

Expected: failure because `CookieExpiredError` and exact error-code handling do not exist.

- [ ] **Step 4: Implement exact API-boundary detection**

Define `CookieExpiredError(RuntimeError)` and check only:

```python
data.get("error_code") == 21301
```

plus explicit final redirects to a login destination. Apply the check in `fetch_contacts`
and `fetch_messages`; do not infer expiry from empty collections or `result=false`.

- [ ] **Step 5: Prevent orchestration from swallowing expiry**

In `Crawler.crawl_all`, re-raise `CookieExpiredError` before the broad per-group exception
handler so the CLI can report it.

- [ ] **Step 6: Add CLI error output**

Catch `CookieExpiredError` around group sync/crawling and log:

```text
Cookie 已过期，请运行: uv run crawl.py --renew-cookie
```

Exit with a non-zero status.

- [ ] **Step 7: Verify GREEN and full suite**

Run:

```powershell
uv run pytest -q
```

Expected: all weibogroup tests pass.

- [ ] **Step 8: Commit the weibogroup fix**

```powershell
git add weibo_im/crawler.py crawl.py tests/test_cookie_expiry.py
git commit -m "fix: report explicit cookie expiry during crawl"
```

### Task 3: Final cross-project verification

- [ ] **Step 1: Check both worktrees**

Run `git status --short` in both repositories and confirm only intended files changed.

- [ ] **Step 2: Verify exact-expiry and no-false-positive tests**

Run both full test suites once more after all implementation changes. Confirm explicit
authentication failures raise, normal empty data does not, and unrelated tests remain green.

