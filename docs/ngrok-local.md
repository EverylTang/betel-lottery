# ngrok 本地微信联调

本方案只用于初步联调，不替代正式服务器。ngrok 只暴露本机 H5 开发服务器 `5174`，Vite 把同一域名下的 `/api` 转发到本机 API `8000`：

```text
微信内浏览器 → https://YOUR_NGROK_DOMAIN/
                       ├── H5 页面/SPA → 127.0.0.1:5174
                       └── /api/**     → 127.0.0.1:8000
```

这样 OAuth 回调和微信支付通知共用同一个公网 HTTPS 域名。**不要**把 MySQL、Redis、管理后台(5173) 或 API 端口本身挂到隧道上；只暴露 H5 开发服务器，由 Vite 代理 `/api`。

## 配置分层：`.env` + `.env.ngrok`

| 文件 | 作用 | 是否入库 |
|---|---|---|
| `.env` | 仓库基线配置（本机 development：端口、口令、`JWT_SECRET`） | 否（`.gitignore`） |
| `.env.ngrok` | ngrok 联调**覆盖层**，只放隧道差异项 | 否（`.gitignore`） |
| `.env.ngrok.example` | 覆盖层模板，脚本在缺失时自动复制 | 是 |

`./scripts/run_local.sh` 启动时会先导出 `.env.ngrok`（如存在），再启动 API/worker——后出现的文件覆盖先出现的。删除 `.env.ngrok` 即回到纯本机模式。

> 关键约束：覆盖层里的 `JWT_SECRET` 必须与 `.env` 一致。二维码 `token_ciphertext`、中奖 token、会话都由它派生加密，改动会让已印刷二维码解密失败并踢掉全部登录态。

需要人工填写的只有公众号凭据；隧道域名相关 5 个键（`H5_BASE_URL`、`WECHAT_OAUTH_REDIRECT_URI`、`WECHATPAY_NOTIFY_BASE_URL`、`TRUSTED_HOSTS`、`CORS_ORIGINS`）由脚本自动回写。完整配置项含义见 [`docs/configuration-checklist.md`](configuration-checklist.md)。

## 前置条件

1. 安装 ngrok 3.x 并执行一次 `ngrok config add-authtoken <token>`。token 只存在 ngrok 本机配置里，不要写进仓库。
2. 启动本机 MySQL/Redis 服务，并完成空库初始化（首次使用时），再启动 API：
   ```bash
   ./.venv/bin/python -m scripts.init_db
   ./scripts/run_local.sh
   ```
3. 从公众号后台下载域名归属校验文件 `MP_verify_xxxxxxxx.txt`，放到 `frontend/h5/public/`。隧道下它随 H5 一起被 Vite 发布，微信保存「网页授权域名 / JS 接口安全域名」时会请求 `https://<域名>/MP_verify_xxxxxxxx.txt`。
4. 随机 `ngrok-free.dev` 域名每次启动都会变，通常不适合微信后台长期配置。建议在 ngrok 账户里预留固定域名（付费域名或免费的 static domain），启动时通过 `NGROK_DOMAIN` 传入。

## 启动

```bash
# 固定域名（推荐）
NGROK_DOMAIN=promo.example.ngrok.app ./scripts/run_ngrok_local.sh

# 临时域名：脚本会打印 ngrok 分配的地址并自动写回配置
./scripts/run_ngrok_local.sh
```

脚本按顺序完成：环境自检 → 拉起 H5 dev server → 建立隧道 → 回写 `.env.ngrok` → 重启 api / wechat-pay-worker 原生进程 → 打印公众号后台要填的值 → 冒烟自检。结束时 `Ctrl-C`，`trap` 会同时回收 Vite 与 ngrok 子进程。

自检项（`preflight`）：
- 未装 ngrok / authtoken 无效 → 直接失败；
- `ENVIRONMENT=production` → 失败（生产强校验要求真实 HTTPS 域名与商户证书）；
- `ADMIN_BOOTSTRAP_PASSWORD` 为空或 `change-me-now` → **失败**（隧道会把 `/api/admin/auth/login` 暴露到公网，限流仅 10 次/5 分钟）；
- `WECHATPAY_ENABLED=true` 但 `secrets/wechatpay/*.pem` 缺失 → 告警；
- `frontend/h5/public/` 下无 `MP_verify_*.txt` → 告警。

### 常用开关

| 变量 | 默认 | 用途 |
|---|---|---|
| `NGROK_DOMAIN` | 空（临时域名） | 使用预留的固定域名 |
| `NGROK_ENV_FILE` | `.env.ngrok` | 换一份覆盖层 |
| `NGROK_LOCAL_PORT` | `5174` | 本机 H5 端口 |
| `LOCAL_API_URL` | `http://127.0.0.1:8000` | Vite `/api` 代理目标 |
| `NGROK_SYNC_ENV=0` | 写入 | 只打印建议值，不改文件 |
| `NGROK_RESTART_API=0` | 重启 | 不自动重启 API/worker 原生进程 |
| `NGROK_SKIP_BROWSER_WARNING=0` | `1` | 关闭 Vite 注入的跳提示页请求头 |

API 默认以 `--reload` 模式运行，修改 Python 代码后自动生效，无需手动重建。

### 停止与重启

前台运行时直接 `Ctrl-C`。如果脚本在后台/会话里跑：

```bash
pkill -INT -f run_ngrok_local.sh     # trap 会同时回收 Vite 与 ngrok 子进程
pgrep -fl 'ngrok http|vite'          # 确认没有残留进程
```

重启后若拿到的仍是新的临时域名，脚本会重新回写 `.env.ngrok` 并重启 API/worker；**公众号后台的域名必须同步改掉**，否则 OAuth 与回调会失败。改一次配置又想让域名保持不变，请用 `NGROK_DOMAIN=<预留域名>`。

## 只做体检（不启动进程）

```bash
./scripts/run_ngrok_local.sh check
```

`check` 读取 `.env.ngrok` 里的当前域名，打印公众号后台应填值，并探测本机 API 与 `https://<域名>/api/h5/auth/mode` 是否可达。适合改完配置或重启电脑后确认一致性。

## 免费隧道的提示页（ERR_NGROK_6024）

ngrok 免费套餐会给**浏览器 User-Agent**注入「You are about to visit …」拦截页，且作用于所有路径（含 `/api`）。表现为：页面空白、`curl` 正常但浏览器拿不到内容、微信内打开卡在 ngrok 页面。

处理方式：
1. 首次在浏览器/微信里打开隧道首页时，点一次 **Visit Site**。ngrok 写入放行 cookie 后，同一浏览器会话内不再拦截。
2. 脚本默认让 Vite 对 `/api` 代理注入 `ngrok-skip-browser-warning: 1`（`frontend/h5/vite.config.ts`，由 `VITE_NGROK_SKIP_BROWSER_WARNING` 控制），因此 SPA 里的接口调用、以及服务端/`curl` 发起的回调探测不受影响。
3. 想彻底根除：使用 ngrok 预留固定域名并升级到付费套餐，或改用自有 HTTPS 服务器（见 [`docs/wechat-production.md`](wechat-production.md)）。

## 客户端真实 IP

ngrok 会用 `X-Forwarded-For` 携带真实公网 IP。`TRUST_PROXY_HEADERS=true`（覆盖层默认）时，API 只在请求直接来自受信代理网段（`app/core/http.py` 的 `TRUSTED_PROXY_NETWORKS`：回环 + RFC1918/RFC4193 + 链路本地）时才采信该头，管理后台登录限流、抽奖风控与审计日志因此记录真实 IP。可用下面的命令查看限流键：

```bash
./.venv/bin/python -c 'from app.cache import redis_client; print(list(redis_client.scan_iter(match="rate:admin-login:*")))'
```

出现 `rate:admin-login:<公网IP>...` 即正确；出现 `172.x`/`Unknown` 说明头没透传或代理网段未信任。

## 联调顺序

1. 保持 `WECHATPAY_ENABLED=false`、`WECHAT_APP_ID`/`WECHAT_APP_SECRET` 留空：`oauth_enabled=false`，H5 退回 `dev-login` 预览，先在手机浏览器确认扫码 → 开奖 → 记录列表正常。
2. 填入公众号 AppID/AppSecret 并重启 API/worker（脚本已自动重启），在微信内打开验证网页授权跳转与回调。此时 `ENVIRONMENT=staging` 会**拒绝** `dev-login`，避免伪造 OpenID 混入联调数据。
3. 最后配微信支付商户参数、把两份只读 PEM 放进 `secrets/wechatpay/`、置 `WECHATPAY_ENABLED=true`，用最低额度现金奖验证 `WAIT_USER_CONFIRM`、微信确认收款页与 `/api/wechat-pay/notify` 回调。

## 限制

- 隧道关闭或本机休眠时微信回调不可达；回调失败由微信按策略重试，但绝不能依赖本地隧道做生产服务。
- OAuth、JS-SDK、支付回调都要求域名配置匹配。临时域名变化后必须同步 `H5_BASE_URL`、`WECHAT_OAUTH_REDIRECT_URI`、`WECHATPAY_NOTIFY_BASE_URL`、`TRUSTED_HOSTS`、`CORS_ORIGINS` 与微信后台域名——用脚本启动即自动完成。
- `H5_BASE_URL` 在隧道下必须是隧道根地址，不能带 `/scan` 之类子路径，否则 JS-SDK 签名校验与 OAuth 回跳都会 404。
- TLS 在 ngrok 边缘终止，服务只收到明文 HTTP，因此覆盖层保持 `FORCE_HTTPS=false`；置为 `true` 会让 `HTTPSRedirectMiddleware` 把代理转发的请求重定向到 `127.0.0.1` 形成死循环。
- 真实商户号 + 随机 ngrok 域名不适合承载生产资金流，只做小额验证。

## 常见问题

| 现象 | 原因 / 处理 |
|---|---|
| `x ADMIN_BOOTSTRAP_PASSWORD 仍是默认值` | 改 `.env.ngrok`，或 `./.venv/bin/python -m scripts.set_admin_password` 重置已建账号口令 |
| 保存网页授权域名时微信提示校验文件失败 | `MP_verify_*.txt` 未放进 `frontend/h5/public/`，或隧道当时没在跑 |
| 接口全部 502/timeout | 本机 API 未就绪：`./scripts/run_local.sh status`、`./scripts/run_local.sh logs api` |
| 配置改了没生效 | 执行 `./scripts/run_local.sh restart` 重启 API/worker，或等脚本自动重启 |
| `等待 ngrok 隧道就绪超时` | authtoken 无效、固定域名不属于当前账户、或本机 `4040` 检查端口被另一个 ngrok 实例占用 |
| 扫码返回 409 | 该二维码已开奖，或 `JWT_SECRET` 与生成二维码时不一致导致解密失败 |
