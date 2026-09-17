# 微信支付密钥目录（只读挂载到容器 /run/secrets/wechatpay）

放在这里、且永不提交的文件：

- `apiclient_key.pem`            商户 API 私钥（商户平台下载）
- `wechatpay_public_key.pem`     微信支付平台公钥（不是商户证书）

容器内对应路径写进 `.env` / `.env.ngrok`：

```dotenv
WECHATPAY_MCH_PRIVATE_KEY_PATH=/run/secrets/wechatpay/apiclient_key.pem
WECHATPAY_MCH_PUBLIC_KEY_PATH=/run/secrets/wechatpay/wechatpay_public_key.pem
```

权限建议 `chmod 600 *.pem`。ngrok 联调时目录里的真实密钥只在需要联调现金转账时才放；
只测 OAuth/抽奖保持 `WECHATPAY_ENABLED=false`。生产环境不要用本目录，改用受限路径
或 docker secret，并保证 `api` 与 `wechat-pay-worker` 读同一份文件。
