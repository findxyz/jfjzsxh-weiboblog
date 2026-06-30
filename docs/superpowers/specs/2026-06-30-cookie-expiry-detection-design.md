# Cookie 过期精确检测设计

## 目标

在 `weiboblog` 和 `weibogroup` 抓取过程中，仅当微博返回明确的登录失效信号时，
提示用户运行对应的 `--renew-cookie` 命令并终止当前抓取。

## 核心约束

- 空列表不是 Cookie 过期证据。
- 正常翻页结束、目标时间段没有微博、群聊没有消息都不得提示 Cookie 过期。
- 网络错误、限流、微博服务异常和业务参数错误不得提示 Cookie 过期。
- 只有明确鉴权失败才抛出专用异常并显示续期命令。

## 明确的过期信号

### weiboblog

- 请求最终跳转到微博或新浪登录域名/登录路径。
- 微博 JSON 响应返回明确的未登录或鉴权错误码。

返回结构正常但 `data.list` 为空时，按正常空结果处理。

### weibogroup

- JSON 响应明确返回 Cookie 无效错误，例如 `error_code=21301`。
- HTTP 请求最终跳转到登录页。

`contacts=[]`、`result=false` 或其他非鉴权错误不能单独作为过期依据。

## 错误处理

两个项目各自定义 `CookieExpiredError`。API 边界识别到明确过期信号后抛出该异常；
CLI 顶层捕获后记录清晰错误，并提示：

- `weiboblog`: `uv run crawl_blog.py --renew-cookie`
- `weibogroup`: `uv run crawl.py --renew-cookie`

进程以非零状态退出，不继续抓取。

## 测试

- 明确登录跳转或 `21301` 时抛出 `CookieExpiredError`。
- 正常空列表不抛异常。
- `result=false`、限流或服务异常不误报 Cookie 过期。
- CLI 错误信息包含对应续期命令。

