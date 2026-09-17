# 部署文档

本文根据当前仓库配置编写，适用于本地 Docker、开发联调和生产部署。所有示例中的密码、密钥、AppID、域名均为占位符，禁止直接使用或提交真实凭据。

## 1. 部署前准备

项目由 FastAPI API、MySQL 8.4、Redis 7.2、管理后台、用户 H5、经销商核销端和微信支付 worker 组成。

建议准备：

- Docker、Docker Compose
- Python 3.12；本地开发建议使用仓库中的 `.venv`
- Node.js 和 npm
- 生产环境的 HTTPS 域名、反向代理和受限密钥目录
- 需要微信能力时，准备已认证服务号和已开通商家转账的微信支付商户号

```bash
git clone <仓库地址>
cd betel-lottery
cp .env.example .env
```

`.env`、`.env.ngrok`、微信支付 PEM 文件和公众号域名校验文件不要提交到仓库。

## 2. 环境变量填写

以 `.env.example` 为准填写根目录 `.env`。本地脚本和宿主机执行的 Alembic 使用 `DATABASE_URL`、`REDIS_URL`；容器中的 API 和 worker 使用 `DOCKER_DATABASE_URL`、`DOCKER_REDIS_URL`。

### 2.1 本地必填或建议修改

```dotenv
ENVIRONMENT=development
MYSQL_ROOT_PASSWORD=<本地随机口令>
MYSQL_PASSWORD=<本地随机口令>
DATABASE_URL=mysql+pymysql://root:<root口令>@127.0.0.1:3306/betel_lottery?charset=utf8mb4
REDIS_URL=redis://:redis123456@127.0.0.1:6379/0
DOCKER_DATABASE_URL=mysql+pymysql://root:<root口令>@mysql:3306/betel_lottery?charset=utf8mb4
DOCKER_REDIS_URL=redis://:redis123456@redis:6379/0
JWT_SECRET=<至少32位随机字符串>
ADMIN_BOOTSTRAP_USERNAME=admin
ADMIN_BOOTSTRAP_PASSWORD=<仅用于首次建号的随机强口令>
H5_BASE_URL=http://localhost:5174
CORS_ORIGINS=http://localhost:5173,http://localhost:5174,http://localhost:5175
TRUSTED_HOSTS=localhost,127.0.0.1
FORCE_HTTPS=false
TRUST_PROXY_HEADERS=true
WECHATPAY_ENABLED=false
```

`docker-compose.yml` 中 MySQL、Redis 和 API 端口只绑定到 `127.0.0.1`：MySQL 为 `3306`，Redis 为 `6379`，API 为 `8000`。不要把这些端口直接暴露到公网。

### 2.2 微信 OAuth 配置

需要公众号网页授权时填写：

```dotenv
WECHAT_APP_ID=<服务号AppID>
WECHAT_APP_SECRET=<服务号AppSecret>
WECHAT_OAUTH_SCOPE=snsapi_base
WECHAT_OAUTH_REDIRECT_URI=https://<H5域名>/api/h5/auth/wechat/callback
```

公众号后台的网页授权域名和 JS 接口安全域名只填写裸域名，不填写协议、端口或路径。域名校验文件放在 `frontend/h5/public/MP_verify_*.txt`，随前端静态资源发布。

### 2.3 微信支付配置

现金奖生产发放依赖微信支付新版商家转账。开发环境建议保持关闭：

```dotenv
WECHATPAY_ENABLED=false
```

生产环境填写：

```dotenv
WECHATPAY_ENABLED=true
WECHATPAY_MCHID=<商户号>
WECHATPAY_MCH_SERIAL_NO=<商户API证书序列号>
WECHATPAY_MCH_PRIVATE_KEY_PATH=/run/secrets/wechatpay/apiclient_key.pem
WECHATPAY_MCH_PUBLIC_KEY_ID=<微信支付平台公钥ID>
WECHATPAY_MCH_PUBLIC_KEY_PATH=/run/secrets/wechatpay/wechatpay_public_key.pem
WECHATPAY_API_V3_KEY=<恰好32位APIv3密钥>
WECHATPAY_NOTIFY_BASE_URL=https://<API域名>
WECHATPAY_SCENE_ID=1000
```

开发 Compose 会把宿主机的 `WECHATPAY_SECRET_DIR` 只读挂载到容器 `/run/secrets/wechatpay`。本地联调目录可使用 `secrets/wechatpay`，文件名必须为 `apiclient_key.pem` 和 `wechatpay_public_key.pem`，权限建议为 `600`。生产环境使用独立的 secret 管理方案或受限目录，不要把 PEM 内容写入 `.env`、镜像或前端。

微信支付通知地址为：

```text
https://<API域名>/api/wechat-pay/notify
```

商户平台必须开通商家转账、绑定公众号 AppID、完成营销场景报备，并保证通知地址公网 HTTPS 可访问。

## 3. 本地 Docker 部署

首次启动：

```bash
docker compose config --quiet
docker compose up -d --build
```

查看状态和日志：

```bash
docker compose ps
docker compose logs -f api
docker compose logs -f wechat-pay-worker
```

访问地址：

| 服务 | 地址 |
| --- | --- |
| 管理后台 | `http://localhost:5173` |
| 用户 H5 | `http://localhost:5174/?t=<二维码token>` |
| 经销商核销端 | `http://localhost:5175` |
| API 文档 | `http://localhost:8000/docs` |
| 就绪检查 | `http://localhost:8000/ready` |

停止服务：

```bash
docker compose down
```

仅停止容器而保留 MySQL、Redis 数据卷时使用 `docker compose down`。不要在需要保留数据时使用 `docker compose down -v`。

## 4. 开发启动

后端：

```bash
./.venv/bin/uvicorn app.main:app --reload
```

三个前端分别启动：

```bash
npm --prefix frontend/admin run dev
npm --prefix frontend/h5 run dev
npm --prefix frontend/dealer run dev
```

开发时需先启动 MySQL 和 Redis，或先执行 `docker compose up -d mysql redis`。修改 `app/` 或 `scripts/` 后，Docker 容器需要重新构建：

```bash
docker compose up -d --build api wechat-pay-worker
```

## 5. 数据库初始化与迁移

当前应用启动时会通过 `bootstrap_data()` 执行 `create_all()` 并补充部分字段；应用启动不会自动调用 Alembic。首次本地启动通常可由应用完成建表。

仓库仍包含 Alembic。已有数据库升级前先备份，再检查 revision 与模型差异：

```bash
./scripts/backup_mysql.sh
./.venv/bin/alembic upgrade head
```

执行前确认 `.env` 中的 `DATABASE_URL` 指向目标数据库。迁移后检查：

```bash
./.venv/bin/alembic current
```

当前项目的迁移和启动建表逻辑并非完全统一。生产升级必须核对 `alembic/versions/`、`app/models.py` 和启动期补列逻辑，避免出现服务启动成功但写入缺列的情况。

## 6. 管理员初始化与密码轮换

`ADMIN_BOOTSTRAP_USERNAME` 和 `ADMIN_BOOTSTRAP_PASSWORD` 只在管理员账号不存在时生效。修改 `.env` 不会更新已存在账号。

首次启动后使用交互方式设置口令：

```bash
./.venv/bin/python -m scripts.set_admin_password --username admin
```

容器内轮换口令时，将口令通过外部环境变量 `BETEL_ADMIN_PASSWORD` 提供：

```bash
BETEL_ADMIN_PASSWORD='<随机强口令>' docker compose exec -T api python -m scripts.set_admin_password --username admin --password-from-env
```

不要把口令写入命令历史、文档、日志或版本库。生产管理员口令至少使用随机强口令，并在首次登录后再次轮换。

## 7. 生产配置

当前 `docker-compose.yml` 主要用于本地和联调，生产不要直接复用其中的明文默认口令。生产应使用独立 Compose、Kubernetes 或云平台编排，并满足：

```dotenv
ENVIRONMENT=production
H5_BASE_URL=https://<H5域名>/scan
WECHAT_OAUTH_REDIRECT_URI=https://<H5域名>/api/h5/auth/wechat/callback
WECHATPAY_NOTIFY_BASE_URL=https://<API域名>
FORCE_HTTPS=true
TRUSTED_HOSTS=<H5域名>,<API域名>
CORS_ORIGINS=https://<H5域名>,https://<后台域名>
TRUST_PROXY_HEADERS=true
JWT_SECRET=<至少32位随机字符串>
```

生产启动校验要求 HTTPS 地址、强 JWT 密钥、强管理员初始口令、微信支付必要字段和可用的密钥路径。Redis 当前本地 Compose 未设置密码，生产必须增加认证、隔离网络并限制访问。个人数据保留任务 `scripts/run_retention.py` 没有内置调度器，应按业务要求配置 cron 或任务平台定期运行。

## 8. Nginx 反向代理

API 端口只监听本机，由 Nginx 对外提供 HTTPS。关键是覆盖而不是追加 `X-Forwarded-For`：

```nginx
server {
    listen 443 ssl;
    server_name <API域名>;

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        client_max_body_size 1m;
    }
}
```

H5 静态资源需要支持 SPA history 回退，并确保 `/MP_verify_*.txt` 从站点根路径可访问。支付回调必须保留原始请求体和 `Wechatpay-*` 请求头，不要让 WAF 或网关改写请求体。

不要使用 `$proxy_add_x_forwarded_for` 作为本项目的唯一配置，否则客户端伪造的请求头可能进入首跳并影响限流、风控和审计 IP。

## 9. ngrok 本地联调

ngrok 仅用于 OAuth、H5 和支付流程初步联调，不要用于正式活动或真实大额资金发放。

启动 H5、隧道、配置回写和冒烟检查：

```bash
./scripts/run_ngrok_local.sh
```

使用预留固定域名：

```bash
NGROK_DOMAIN=<预留域名> ./scripts/run_ngrok_local.sh
```

只检查当前配置：

```bash
./scripts/run_ngrok_local.sh check
```

脚本会把隧道域名写入独立的 `.env.ngrok`，不会修改 `.env`，并重建或重启 API 与 worker。临时域名重启后可能变化，变化后必须同步修改公众号网页授权域名、JS 接口安全域名和 OAuth 回调地址。ngrok 免费套餐可能显示浏览器提示页，首次访问时需要在浏览器会话中选择 Visit Site。

## 10. 部署验证

代码和前端构建验证：

```bash
./.venv/bin/python -m compileall -q app scripts
./.venv/bin/pytest -q
npm --prefix frontend/admin run build
npm --prefix frontend/h5 run build
npm --prefix frontend/dealer run build
docker compose config --quiet
```

服务验证：

```bash
curl -fsS http://127.0.0.1:8000/ready
docker compose ps
docker compose exec -T api python -c 'import os;p=[os.environ.get(k,"") for k in ("WECHATPAY_MCH_PRIVATE_KEY_PATH","WECHATPAY_MCH_PUBLIC_KEY_PATH")];print([(x, os.access(x, os.R_OK) if x else "UNSET") for x in p])'
```

生产上线还应验证：

1. HTTPS 证书、H5 静态资源和 SPA 路由正常。
2. 微信 OAuth 回调能获得服务号 OpenID。
3. `/api/wechat-pay/notify` 可公网访问并保留验签所需请求头。
4. 小额现金奖能拉起微信确认收款。
5. 最终到账只以微信异步回调或 worker 查询到的成功状态为准。
6. 重复回调、用户取消、超时撤销和重试不会重复发放。

## 11. 常见问题

### API 容器无法连接 MySQL 或 Redis

确认容器使用的是 `DOCKER_DATABASE_URL` 和 `DOCKER_REDIS_URL`，主机名应为 `mysql`、`redis`，端口应为容器端口 `3306`、`6379`。宿主机脚本才使用 `127.0.0.1:3306`、`127.0.0.1:6379`（Redis 连接串需带口令，如 `redis://:redis123456@127.0.0.1:6379/0`）。

### 修改 `.env` 后管理员口令没有变化

初始化变量只在账号不存在时使用。使用 `scripts.set_admin_password` 轮换，不要删除生产账号或直接修改数据库密码字段。

### 修改 Python 代码后容器仍运行旧代码

镜像在构建时复制 `app` 和 `scripts`，不是 bind mount。执行：

```bash
docker compose up -d --build api wechat-pay-worker
```

### 微信 OAuth 回调失败

检查 OAuth 回调 URL 是否为完整 HTTPS 地址，域名是否已登记为网页授权域名，公众号 AppID 和 AppSecret 是否匹配，并确认 `TRUSTED_HOSTS`、`CORS_ORIGINS` 和 Nginx 转发头配置正确。

### 微信支付回调验签失败

确认平台公钥文件不是商户私钥，`WECHATPAY_MCH_PUBLIC_KEY_ID` 与平台公钥匹配，APIv3 Key 恰好 32 位，Nginx 没有改写 body，并保留所有 `Wechatpay-*` 请求头。

### ngrok 浏览器打开空白或显示拦截页

免费 ngrok 可能注入浏览器提示页。先在同一浏览器会话访问隧道并选择 Visit Site；同时执行 `./scripts/run_ngrok_local.sh check` 检查域名和容器配置。

### 数据库升级后运行时报缺少字段

当前启动建表和 Alembic 并非完全统一。先备份数据库，核对模型、revision 和启动期补列逻辑，再执行 `./.venv/bin/alembic upgrade head` 并检查 `alembic current`。

### 生产环境获取到错误客户端 IP

确保 Nginx 使用 `proxy_set_header X-Forwarded-For $remote_addr;` 覆盖请求头，并确保 API 端口不被公网直连。若 API 直接暴露公网，应设置 `TRUST_PROXY_HEADERS=false`。
