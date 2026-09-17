# 快速配置说明（精简版）

整个服务已经能跑。你只需要按下方三个步骤填空即可，其余都由脚本自动处理。

---

## 第 1 步：告诉脚本你有哪些密钥

编辑仓库根目录的 `secrets/wechatpay/` 文件夹（文件夹已存在，私钥文件不会提交进 git）：

```text
secrets/wechatpay/apiclient_key.pem          # 商户 API 私钥
secrets/wechatpay/wechatpay_public_key.pem   # 微信支付平台公钥（不是商户证书）
```

> 只用联调 OAuth、不联调真实打款时，这两个文件可以暂时不放。

## 第 2 步：填公众号信息

编辑仓库根目录的 `.env.ngrok`，把这两处填上真实值（其余不用动）：

```dotenv
WECHAT_APP_ID=
WECHAT_APP_SECRET=
```

- 填上空 → H5 走预览账号，不调微信，适合先跑通页面流程。
- 填上真实值 → 微信内打开才会走网页授权并且能拿到 OpenID。
- 域名请不要改，脚本已自动写好，直接沿用即可。

如果要联调真实小额打款，再额外在这份文件里打开（默认是 `false`）：

```dotenv
WECHATPAY_ENABLED=true
```

## 第 3 步：把域名填进微信后台

当前公网地址是：`https://storm-driving-upon.ngrok-free.dev`

在微信公众号后台照着填：

| 位置 | 填写内容 |
| --- | --- |
| 功能设置 → 网页授权域名 | `storm-driving-upon.ngrok-free.dev` |
| 功能设置 → JS 接口安全域名 | `storm-driving-upon.ngrok-free.dev` |
| 商户平台 → 商家转账通知地址 | `https://storm-driving-upon.ngrok-free.dev/api/wechat-pay/notify` |

> 保存「网页授权域名」时微信会访问 `https://域名/MP_verify_xxx.txt` 校验归属。请先把公众号后台下载的 `MP_verify_*.txt` 放进 `frontend/h5/public/`，否则保存会失败。

---

## 运行与检查

```bash
# 只体检，不启动任何进程（确认各项是否就绪）
./scripts/run_ngrok_local.sh check

# 完整启动隧道（H5 + ngrok；一般已经跑着，重复执行会重启）
# ./scripts/run_ngrok_local.sh
```

启动后：
- 手机浏览器打开 `https://storm-driving-upon.ngrok-free.dev/`（第一次要点一次 **Visit Site**）走抽奖流程；
- 微信内打开同一地址走真实 OAuth。

## 当前状态

| 项 | 状态 |
| --- | --- |
| API / MySQL / Redis / worker | 运行中 |
| 公网隧道 | `https://storm-driving-upon.ngrok-free.dev` 可达 |
| 管理后台登录 | 可用（用户名 `admin`，口令见 `/tmp/betel_admin_pw.txt`） |
| 公众号 OAuth | **未启用**（AppID/AppSecret 为空，填上后生效） |
| 微信小额打款 | **未启用**（需填商户参数并把两个 PEM 放好后开 `WECHATPAY_ENABLED=true`） |

---

## 对接微信时最容易卡住的三个坑

1. **MP_verify 文件没放** → 网页授权域名保存失败。放到 `frontend/h5/public/` 即可。
2. **API 配置改了没生效** → 脚本已自动重启本地 API/worker；手动改过后执行：
   ```bash
   ./scripts/run_local.sh restart
   ```
3. **临时域名会变** → 现在这个免费域名每次重启可能改变，且对浏览器 UA 插提示页。要长期稳定就在 ngrok 里领一个固定域名，然后用 `NGROK_DOMAIN=<域名> ./scripts/run_ngrok_local.sh`。
