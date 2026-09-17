# 正式微信公众号与微信支付接入

本文对应本项目的两个微信能力：公众号内网页授权（获得用户 OpenID）与微信支付商家转账（现金奖确认收款）。在生产环境开始前，必须由公众号和商户号的管理员在微信后台完成开户、绑定和产品审批；代码不能代替这些平台侧审核。

## 账号与产品前置条件

1. 使用已认证的服务号作为活动公众号，并取得 AppID 与 AppSecret。扫码活动必须在该公众号的微信内置浏览器中打开，OAuth 获得的 OpenID 才能用于本项目的现金奖。
2. 使用主体一致或已完成授权关系的微信支付商户号，开通商家转账产品，并将该服务号 AppID 绑定到商户号。以商户平台实际产品准入和营销场景审批结果为准。
3. 在商户平台为营销活动报备本项目使用的转账场景；本项目默认发送 `transfer_scene_id=1000`，只能在该场景已获准时使用。不要为了绕过审批伪造场景 ID。
4. 准备两个公网 HTTPS 域名。H5 域名用于扫码页、网页授权和 JS-SDK；API 域名用于支付回调。它们可以是同一域名，但必须由反向代理分别把静态 H5 和 `/api` 路径路由到正确服务。

## 微信后台配置

在公众号后台完成以下配置，域名均为根域名，不填写协议、路径或端口：

| 配置位置 | 填写内容 | 作用 |
| --- | --- | --- |
| 公众号设置 → 功能设置 → 网页授权域名 | `promo.example.com` | 允许 OAuth 回调页面 |
| 公众号设置 → 功能设置 → JS 接口安全域名 | `promo.example.com` | 允许 `wx.config` 与 `requestMerchantTransfer` |
| 商户平台 → 产品中心/商家转账 | 本活动对应场景与回调 URL | 开通转账及通知 |
| 商户平台 → 账户中心 → AppID 账号管理 | 服务号 AppID | 将支付商户号与活动公众号绑定 |

`WECHAT_OAUTH_REDIRECT_URI` 必须是已登记网页授权域名下的完整 HTTPS 回调地址，例如 `https://promo.example.com/api/h5/auth/wechat/callback`。此处不要依赖容器内部生成的 URL。

保存上面两项域名时，微信会请求 `https://promo.example.com/MP_verify_xxxxxxxx.txt` 校验域名归属。把公众号后台提供的校验文件放进 H5 前端的静态目录（`frontend/h5/public/MP_verify_xxxxxxxx.txt`，本地联调同理），随前端构建产物发布到域名根路径；该文件已在 `.gitignore` 内，不要提交。

域名验证通过后，在公众号后台「网页授权域名」下方下载 `Wxpay_domain_files` 校验包并按提示上传到**支付回调域名**的根目录，否则商户平台会提示域名校验失败。

## 服务器密钥与环境变量

在服务器受限目录创建私钥和平台公钥文件，只授予运行 API 与 `wechat-pay-worker` 的账户读取权限。不要把 AppSecret、APIv3 Key 或 PEM 文件提交到仓库、镜像或前端构建产物。

```dotenv
ENVIRONMENT=production
H5_BASE_URL=https://promo.example.com/scan
WECHAT_OAUTH_REDIRECT_URI=https://promo.example.com/api/h5/auth/wechat/callback
WECHAT_APP_ID=wx...                 # 服务号 AppID
WECHAT_APP_SECRET=...               # 服务号 AppSecret

WECHATPAY_ENABLED=true
WECHATPAY_MCHID=16...               # 微信支付商户号
WECHATPAY_MCH_SERIAL_NO=...         # 商户 API 证书序列号
WECHATPAY_MCH_PRIVATE_KEY_PATH=/run/secrets/wechatpay/apiclient_key.pem
WECHATPAY_MCH_PUBLIC_KEY_ID=PUB_KEY_ID_...  # 微信支付平台公钥 ID，不是商户序列号
WECHATPAY_MCH_PUBLIC_KEY_PATH=/run/secrets/wechatpay/wechatpay_public_key.pem
WECHATPAY_API_V3_KEY=...            # 恰好 32 个字符
WECHATPAY_NOTIFY_BASE_URL=https://api.example.com
WECHATPAY_SCENE_ID=1000

FORCE_HTTPS=true
TRUSTED_HOSTS=promo.example.com,api.example.com
CORS_ORIGINS=https://promo.example.com,https://admin.example.com

# 生产强口令：ADMIN_BOOTSTRAP_* 只用于首次建号，之后用 set_admin_password 轮换
ADMIN_BOOTSTRAP_USERNAME=ops
ADMIN_BOOTSTRAP_PASSWORD=<随机强口令>
PERSONAL_DATA_RETENTION_DAYS=730
```

完整项清单见 [`docs/configuration-checklist.md`](configuration-checklist.md)。`ENVIRONMENT=production` 会触发启动期强校验：要求 HTTPS 公网 `H5_BASE_URL`、`FORCE_HTTPS=true`、仅 HTTPS 的 `CORS_ORIGINS`、至少 32 位 `JWT_SECRET`、至少 12 位管理员初始口令、`WECHATPAY_ENABLED=true`、非空的商户号/证书序列号/平台公钥 ID/两个密钥文件路径、恰好 32 字符的 APIv3 Key、完整 HTTPS 的 `WECHAT_OAUTH_REDIRECT_URI` 与无路径的 HTTPS `WECHATPAY_NOTIFY_BASE_URL`。任一项不满足 API 直接拒绝启动，而不是等到用户领奖时才失败。

启动校验只检查密钥**路径非空**，不验证文件真的可读；缺失的 PEM 会在首次签发转账单或验签回调时才报错。上线前先在容器里自查一次：

```bash
docker compose exec -T api python -c 'import os;p=[os.environ.get(k,"") for k in ("WECHATPAY_MCH_PRIVATE_KEY_PATH","WECHATPAY_MCH_PUBLIC_KEY_PATH")];print([(x, os.access(x, os.R_OK) if x else "UNSET") for x in p])'
```

两项都应为 `True`。管理员口令同理：`ADMIN_BOOTSTRAP_PASSWORD` 只在账号**不存在时**建号，改环境变量不会更新已存在的行，轮换请用：

```bash
docker compose exec -T api python -m scripts.set_admin_password --username ops --password-from-env   # BETEL_ADMIN_PASSWORD 由外部环境变量提供
```

配置文件中的 `WECHATPAY_MCH_PUBLIC_KEY_*` 是兼容已有环境变量的历史名称，实际值必须是微信支付**平台**公钥及其 ID。平台公钥轮换时，在微信商户平台下载新文件，在通知可能使用新 ID 前原子地替换服务器文件并更新环境变量，然后重启 API 和 worker。

容器部署时，把密钥以只读 secret 或只读 bind mount 挂到上述路径，并让 `api` 和 `wechat-pay-worker` 使用同一份只读文件。当前 `docker-compose.yml` 只适用于本地开发；生产环境应使用独立的 secret 管理和反向代理，不应把证书内容放入 `.env`。

## 网络与回调

反向代理需要保证以下地址无需登录、可从公网 HTTPS 访问：

```text
https://promo.example.com/scan/                         H5 静态资源
https://promo.example.com/api/h5/auth/wechat/callback   OAuth 回调
https://promo.example.com/api/h5/wechat/js-config       JS-SDK 签名 API
https://api.example.com/api/wechat-pay/notify           微信支付回调
```

### 反向代理必须覆盖 `X-Forwarded-For`

后台登录限流、抽奖风控和审计日志都以客户端 IP 为键。API 只有当**直连对端**是内网/回环地址时才采信 `X-Forwarded-For`（`TRUST_PROXY_HEADERS`，默认 `true`；`app/core/http.py`），且只取首跳。因此：

```nginx
location /api/ {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    # 关键：覆盖而非追加，否则客户端自带的伪造值会成为首跳
    proxy_set_header X-Forwarded-For   $remote_addr;
    proxy_set_header X-Forwarded-Proto $scheme;
    client_max_body_size 1m;
}
```

- 用 `$proxy_add_x_forwarded_for` 会把客户端原始值留在首跳，攻击者可随意伪造 IP 绕开限流。
- `8000` 端口**不要**发布到公网或局域网；只监听 `127.0.0.1`（`docker-compose.yml` 的本地写法即为示范）。若确实存在直连公网的入口，必须 `TRUST_PROXY_HEADERS=false`。
- 前端域名与 API 域名不同源时，把两者都写进 `CORS_ORIGINS`，把主机名写进 `TRUSTED_HOSTS`，否则 `TrustedHostMiddleware` 会返回 404/400。

支付回调不能经过会修改请求体的 WAF、网关或日志中间件。项目使用微信支付的原始请求体和 `Wechatpay-*` 头验签，再用 APIv3 Key 解密资源；修改 body 或丢弃这些头会导致回调被拒绝。回调处理已具备幂等性，微信重试时应继续返回成功确认而不是重复发款。

## 上线顺序与验收

0. 建库与迁移：`bootstrap_data()` 用 `create_all()` 加手写 `ALTER TABLE` 补齐列，**不会调用 Alembic**。首次上线直接启动 API 即可建表；已有库升级时必须先比对 `alembic/versions/` 与 `app/models.py`，确认手写 DDL 覆盖所有新增列，再显式执行一次 `alembic upgrade head` 并核对 `alembic_version`，否则会出现「启动不报错、写入时缺列」。迁移前先用 `scripts/backup_mysql.sh` 备份。
1. 先在微信支付平台的测试能力或小额真实活动环境验证商户 API 证书签名、平台公钥验签及 APIv3 Key 解密。
2. 在微信内打开一个真实二维码，确认网页授权后数据库中创建的消费者 OpenID 来自服务号；普通手机浏览器应不能绕过生产 OAuth。
3. 用 0.30 元现金奖验证一次 `WAIT_USER_CONFIRM`、H5 `requestMerchantTransfer` 拉起和用户确认；不要把“JS API 调用成功”当作到账结果。
4. 以 `/api/wechat-pay/notify` 的已验签异步回调，或 worker 查询到的 `SUCCESS`，作为唯一到账判定。检查后台记录的 `transfer_state`、商户单号与微信商户平台流水一致。
5. 验证用户取消、72 小时未确认、回调重复投递和网络超时。系统只在上游确认旧单已撤销后才允许新单号重新领取，避免重复付款。

当前实现的额度边界是未采集收款人实名情况下每笔 0.30 至 1999.99 元。若活动需要 2000 元及以上的现金奖，必须先按微信支付最新要求增加收款人姓名的 RSA-OAEP 加密传输、合规告知和相应测试，不能仅修改奖品金额。
