# 配置清单与需要调整的地方

> 想快点上手？先读 [`QUICKSTART.md`](QUICKSTART.md)（三分钟版：只需填 3 处）。本文件是完整核对表。

面向部署/联调人员的核对表：第 1~3 节是“要配什么”，第 4 节是“仓库里已经改了什么”，
第 5 节是“仍需要你决策的事项”。ngrok 本机隧道步骤见 [`ngrok-local.md`](ngrok-local.md)，
正式公众号/商户号接入见 [`wechat-production.md`](wechat-production.md)。

## 0. 配置分三层

| 层 | 位置 | 作用范围 |
| --- | --- | --- |
| 基础配置 | 根目录 `.env`（不入库） | 本机脚本、alembic、compose 的 `${...}` 插值、容器默认环境 |
| 覆盖层 | 根目录 `.env.ngrok`（不入库，可选） | 只覆盖 `api` 与 `wechat-pay-worker` 容器的同名项；文件删除即回到纯本机 development |
| 平台侧 | 微信公众号后台、微信支付商户平台 | 域名、JS 安全域名、商家转账产品/场景、AppID 绑定、通知地址 |

优先级：`docker-compose.yml` 的 `environment` > `.env.ngrok` > `.env`。
容器里 `DATABASE_URL`/`REDIS_URL` 被 `environment` 强制改成 `DOCKER_*` 的值，
所以 **compose 插值永远只读 `.env`**，`.env.ngrok` 里的同名项不会改变容器内连接串。

## 1. 环境变量

### 1.1 启动必需（缺失直接启动失败或使用弱默认值）

| 变量 | 说明 | 不配/配错的后果 |
| --- | --- | --- |
| `DATABASE_URL` | 必须以 `mysql+pymysql://` 开头 | 抛错拒绝启动；SQLite 不被支持 |
| `REDIS_URL` | `redis://` 或 `rediss://`；带口令时形如 `redis://:口令@127.0.0.1:6379/0` | 抛错拒绝启动；Redis 掉线时限流接口一律 503（fail-closed） |
| `DOCKER_DATABASE_URL` / `DOCKER_REDIS_URL` | 容器内地址，主机名用服务名 `mysql` / `redis`，端口是容器自己的 `3306` / `6379` | 用 `127.0.0.1` 会让容器连不上；用宿主发布端口 3306/6379 同样错 |
| `MYSQL_ROOT_PASSWORD` / `MYSQL_PASSWORD` | MySQL 容器初始化口令，compose 已改为从这里读取 | 只在建卷时生效；改口令要 `ALTER USER` 或删卷重建，且必须与两条 URL 同步 |
| `JWT_SECRET` | 同时用于：会话 JWT 签名、二维码 `token_ciphertext` 的 Fernet 密钥派生 | **三处耦合**：改动会踢掉全部登录态，并让已印刷二维码解密失败（开奖返回 409）。`.env` 与 `.env.ngrok` 必须一致；生产才允许换强随机值 |
| `ADMIN_BOOTSTRAP_USERNAME` / `ADMIN_BOOTSTRAP_PASSWORD` | 仅在 admin 行不存在时建号 | 改 `.env` **不会**更新已存在的口令；隧道/公网暴露前用 `.venv/bin/python -m scripts.set_admin_password` 重置 |

端口对照（`docker compose` 默认只绑回环）：

| 服务 | 宿主机 | 容器内 |
| --- | --- | --- |
| MySQL（root 口令 `root123456`） | `127.0.0.1:3306` | `mysql:3306` |
| Redis（口令 `redis123456`） | `127.0.0.1:6379` | `redis:6379` |
| API | `127.0.0.1:8000` | `8000` |

### 1.2 安全与审计

| 变量 | 默认 | 要点 |
| --- | --- | --- |
| `ENVIRONMENT` | `development` | `staging` 允许真实 OAuth 但不触发生产强校验；`production` 强制校验见下表 |
| `FORCE_HTTPS` | `false` | 只控制 `HTTPSRedirectMiddleware` + HSTS 头。**TLS 在代理/隧道边缘终止时必须 `false`**，否则代理转发的 `/api` 会被 307 到 `https://127.0.0.1` 死循环 |
| `TRUSTED_HOSTS` | `localhost,127.0.0.1` | Vite/nginx `changeOrigin` 会把 Host 改写成上游地址，所以走代理时不要求隧道域名；直连 API（如 `/docs`、`ngrok http 8000`）才必须加 |
| `CORS_ORIGINS` | 三个 dev 端口 | 逗号分隔、含协议、不带路径；同域部署（H5 与 `/api` 同源）其实不需要放行，保留本机端口便于后台调试 |
| `TRUST_PROXY_HEADERS` | `true` | 见 `app/core/http.py`：仅当**直连对端是回环/内网**时才采信 `X-Forwarded-For` 首跳，否则回落 socket 对端。API 端口直接暴露公网时必须 `false` |
| `PERSONAL_DATA_RETENTION_DAYS` | `730` | 只被 `scripts/run_retention.py` 读取，仓库内没有调度器 |
| `TIANDITU_API_KEY` | 空 | 空则坐标归属地解析降级，不影响开奖 |

`production` 的启动强校验（`Settings.validate_production_security`）：JWT ≥32 位、初始口令 ≥12 位、
`H5_BASE_URL` 必须 HTTPS、`FORCE_HTTPS=true`、CORS 全 HTTPS、`WECHATPAY_ENABLED=true`、
AppID/AppSecret、`WECHAT_OAUTH_REDIRECT_URI` 完整 HTTPS、商户号+序列号+APIv3 Key（恰好 32 字符）、
私钥与平台公钥路径、`WECHATPAY_NOTIFY_BASE_URL` 为**无路径** HTTPS 基址。任一条不满足直接抛错。

### 1.3 公众号 OAuth（拿 OpenID）

| 变量 | 要点 |
| --- | --- |
| `WECHAT_APP_ID` / `WECHAT_APP_SECRET` | 已认证服务号；现金转账要求与商户号同主体或已授权 |
| `WECHAT_OAUTH_SCOPE` | 默认 `snsapi_base`（静默授权，够用于取 OpenID） |
| `WECHAT_OAUTH_REDIRECT_URI` | 必须是完整 HTTPS 回调地址，路径固定 `/api/h5/auth/wechat/callback`，域名与“网页授权域名”一致。留空时由请求头推导，代理/容器环境极易推错 |
| `H5_BASE_URL` | OAuth 回跳、JS-SDK 签名域名校验、二维码印刷链接三处共用。**ngrok 本机联调不带子路径**；写成 `https://域名/scan` 会让签名与回跳 404 |

模式判定：`ENVIRONMENT` ∈ {staging, production} **且** AppID/AppSecret 非空 → 启用真实 OAuth，
`/api/h5/auth/dev-login` 返回 403；否则 H5 走 dev-login 预览用户。`GET /api/h5/auth/mode` 用于前端判定。

### 1.4 微信支付商家转账（现金奖）

| 变量 | 要点 |
| --- | --- |
| `WECHATPAY_ENABLED` | `false` 时不创建转账单；MVP 已移除人工登记现金入口，所以 `false` 下现金奖只会停在待领取 |
| `WECHATPAY_MCHID` / `WECHATPAY_MCH_SERIAL_NO` | 商户号需开通商家转账并绑定该服务号 AppID |
| `WECHATPAY_MCH_PRIVATE_KEY_PATH` | 商户 API 私钥 `apiclient_key.pem` 的**容器内路径**，本地 compose 为 `/run/secrets/wechatpay/...` |
| `WECHATPAY_MCH_PUBLIC_KEY_ID` / `..._PATH` | 微信支付**平台**公钥（不是商户证书），回调验签用；轮换时 ID 与文件必须一起换 |
| `WECHATPAY_API_V3_KEY` | 恰好 32 字符，AES-256-GCM 解密回调 resource |
| `WECHATPAY_NOTIFY_BASE_URL` | 无路径 HTTPS 基址，最终回调 `{base}/api/wechat-pay/notify`；生产不得由 `H5_BASE_URL` 推导 |
| `WECHATPAY_SCENE_ID` | 默认 `1000`，必须是商户平台已获批的报备场景 |
| `WECHATPAY_BILL_NAME` / `_REMARK_PREFIX` | 转账单名称与备注前缀，受 32 字长度裁剪 |
| `WECHATPAY_MAX_ATTEMPTS` | 同一笔现金奖可换新商户单号的次数上限，用尽后只能人工处理 |
| `WECHATPAY_CONFIRM_TIMEOUT_MINUTES` | `WAIT_USER_CONFIRM` 超时自动撤销，默认 4320 分钟（微信确认页 72 小时） |
| `WECHATPAY_WORKER_POLL_SECONDS` / `_BATCH_SIZE` | worker 轮询节奏；Redis `wechatpay:worker:leader` 保证单实例 |
| `PAYMENT_CALLBACK_SECRET` | 仅旧版 `/api/admin/cash-payments/callback` 使用，新版微信回调不依赖 |

金额边界：未采集收款人实名时单笔 `0.30 ~ 1999.99` 元（`app/services/wechat_pay/payout.py`）。
≥2000 元需要收款人姓名 RSA-OAEP 加密传输与合规改造，不能只改奖品金额。

### 1.5 密钥文件位置

```text
secrets/wechatpay/apiclient_key.pem            → 容器 /run/secrets/wechatpay/apiclient_key.pem
secrets/wechatpay/wechatpay_public_key.pem     → 容器 /run/secrets/wechatpay/wechatpay_public_key.pem
```

compose 已把该目录只读挂给 `api` 与 `wechat-pay-worker`（同一份文件）。宿主机目录由
`WECHATPAY_SECRET_DIR` 控制（默认 `./secrets/wechatpay`）——它是 **compose 变量插值**，
所以只认根目录 `.env`，写在 `.env.ngrok` 里无效；`scripts/run_ngrok_local.sh` 的自检也按同一
顺序（shell 变量 > `.env` > 默认值）解析，两边看到的目录始终一致。
目录内容、`.env`、`.env.ngrok` 均在 `.gitignore` 内；生产请改用受限路径或 docker secret，并 `chmod 600`。

## 2. 微信后台侧必须完成的动作

| 位置 | 填什么 | 备注 |
| --- | --- | --- |
| 公众号 → 设置 → 功能设置 → 网页授权域名 | 裸域名，无协议/端口/路径 | 保存时微信访问 `https://<域名>/MP_verify_xxx.txt` 校验归属；ngrok 联调时把该文件放进 `frontend/h5/public/` |
| 同上 → JS 接口安全域名 | 同上 | `wx.config` 与 `requestMerchantTransfer` 必需 |
| 公众号 →  OAuth 网页授权回调 | `https://<域名>/api/h5/auth/wechat/callback` | 与 `WECHAT_OAUTH_REDIRECT_URI` 完全一致 |
| 商户平台 → 产品中心 → 商家转账 | 开通并绑定服务号 AppID | 主体一致或完成授权关系 |
| 商户平台 → 营销场景报备 | 场景 `1000` | 未获批不要伪造场景 ID |
| 商户平台 → API 安全 | APIv3 密钥、商户 API 证书（序列号+私钥）、平台公钥/证书 | 平台公钥轮换需同步 `WECHATPAY_MCH_PUBLIC_KEY_ID` 与文件 |
| 商家转账通知地址 | `https://<API 域名>/api/wechat-pay/notify` | 必须公网 HTTPS 可达、不改写请求体、保留 `Wechatpay-*` 头 |

## 3. 验证命令

```bash
./.venv/bin/python -m compileall -q app scripts
./.venv/bin/pytest -q
npm --prefix frontend/h5 run build          # 另两端：admin、dealer
docker compose config --quiet               # 校验编排与插值
./scripts/run_ngrok_local.sh check          # 隧道配置一致性体检
docker compose exec -T redis redis-cli keys 'rate:admin-login:*'   # 确认限流键里是真实来源 IP
```

## 4. 本次已经在仓库里改好的地方

| 文件 | 改动 | 为什么 |
| --- | --- | --- |
| `app/core/http.py`（新） | `resolve_client_ip()` / `client_ip_or_unknown()`；`TRUSTED_PROXY_NETWORKS` 白名单 | 原来所有接口直接用 `request.client.host`。经 Vite/nginx/ngrok 后全部访客塌缩成同一个 IP：一个人就能把后台登录打到 429，审计日志也全失真。刻意不用 `is_private`，它把 `198.51.100.0/24` 等文档保留段也算内网 |
| `app/core/config.py` | 新增 `trust_proxy_headers: bool = True` | 允许在 8000 直连公网时彻底退回“只认 socket 对端” |
| `app/api/{admin,h5,dealer}.py` | 5 处取 IP 的调用点改走 helper | 统一入口，避免漏改 |
| `docker-compose.yml` | `env_file: [.env, .env.ngrok(可选)]`；`${WECHATPAY_SECRET_DIR:-./secrets/wechatpay}:/run/secrets/wechatpay:ro`；三个端口全部只绑 `127.0.0.1`；MySQL 口令从 `.env` 插值 | 原来密钥路径写死在镜像假设里、端口默认发布到 `0.0.0.0`、口令是明文硬编码 |
| `frontend/h5/vite.config.ts` | `allowedHosts: true`、`xfwd: true`、按需注入 `ngrok-skip-browser-warning` | 临时隧道域名无法预置白名单；不透传 IP 则限流键全塌缩；免费隧道会拦掉 `/api` |
| `frontend/{admin,dealer}/vite.config.ts` | `xfwd: true` | 同上 |
| `.env.example` | 修端口（3306/6379）、补 `DOCKER_*` / `MYSQL_*` / `REDIS_PASSWORD`、CORS 加 5175、`TRUST_PROXY_HEADERS`、密钥路径改 `/run/secrets/wechatpay/...` | 原模板照抄会直接连不上 |
| `.env.ngrok.example`（新） | 覆盖层模板 | 见第 0 节 |
| `frontend/h5/public/.gitkeep`、`secrets/wechatpay/README.md`（新） | 占位目录 | 保证 `MP_verify_*.txt` 与 PEM 有明确落点 |
| `.gitignore` | 追加 `.env.ngrok*`、`secrets/wechatpay/*`、`MP_verify_*.txt` | 防私钥/校验文件入库 |
| `scripts/run_ngrok_local.sh` | 重写：preflight 硬失败、`check` 子命令、隧道 URL 自动发现、5 键自动回写、默认 `--build` 重建、冒烟自检、`trap` 回收 | 原来要求手改 `.env` 且改完不生效 |
| `scripts/set_admin_password.py`（新） | 重置/轮换后台口令 | `ADMIN_BOOTSTRAP_PASSWORD` 只在建号时生效，改 `.env` 是无效操作 |
| `tests/test_client_ip.py`（新） | 14 项 IP 解析回归 | 锁住伪造 XFF、代理网段、开关关闭等行为 |

## 5. 仍需要你决策/提供的事项

1. **Alembic 从未被调用**：`bootstrap_data()` 用 `create_all()` + 手写 `ALTER TABLE` 建列。要么规定“迁移一律 `alembic upgrade head`，并补齐 revision”，要么删掉 `alembic/` 目录避免双头维护。当前状态下升级旧库有“启动正常、写入缺列”的风险。
2. **前端生产 nginx/静态托管配置缺失**：仓库里没有生产用 nginx 模板，`/api` 路由、`MP_verify` 归属校验、SPA history 回退都靠部署方自建。需要的话我可以补一份带正确 `X-Forwarded-For` 覆盖写法的模板（写法要求见 `wechat-production.md`）。
3. **ngrok 固定域名**：现在是免费临时域名 `storm-driving-upon.ngrok-free.dev`，重启即变、且对浏览器 UA 插入提示页。建议 ngrok 里领取/购买 static domain 后用 `NGROK_DOMAIN=...` 启动，并在微信后台一次性配好。
4. **Redis 口令**：compose 已通过 `--requirepass` 启用口令 `redis123456`，连接串必须写成 `redis://:redis123456@host:6379/0` 形式。生产必须另设强口令 + 独立网络。
5. **临时域名不要接真实资金**：ngrok 隧道只用于 OAuth/流程联调，最多 0.30 元小额验证；正式活动必须迁到自有 HTTPS 服务器。
6. **真实微信凭据待填**：`.env.ngrok` 里 `WECHAT_APP_ID`/`WECHAT_APP_SECRET` 目前为空（`oauth_enabled=false`，H5 走 dev-login 预览），商户号相关全部为空。
7. **口令落盘位置**：admin 口令已重置为随机强值，同一个值已同步写进 `.env.ngrok` 的 `ADMIN_BOOTSTRAP_PASSWORD`；明文另存了一份在 `/tmp/betel_admin_pw.txt`（未进仓库）。确认可登录后请删除该临时文件，并按需再轮换一次。
8. **数据保留任务无调度器**：`scripts/run_retention.py` 需自行加 cron；不清理则 OpenID/坐标等个人数据永久留存。
