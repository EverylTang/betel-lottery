from functools import lru_cache
from urllib.parse import urlsplit

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Betel Lottery API"
    environment: str = "development"
    database_url: str
    redis_url: str
    jwt_secret: str = "local-development-secret-change-me"
    jwt_algorithm: str = "HS256"
    h5_base_url: str = "http://localhost:5174/"
    admin_bootstrap_username: str = "admin"
    admin_bootstrap_password: str = "change-me-now"
    cors_origins: str = "http://localhost:5173,http://localhost:5174,http://localhost:3000"
    trusted_hosts: str = "localhost,127.0.0.1"
    force_https: bool = False
    # 反向代理/本地 ngrok 联调时允许从 X-Forwarded-For 读取真实客户端 IP，
    # 仅在直连对端为回环或内网地址时生效。若 8000 端口直接暴露公网，请设为 false。
    trust_proxy_headers: bool = True
    personal_data_retention_days: int = 730
    tianditu_api_key: str = ""
    wechat_app_id: str = ""
    wechat_app_secret: str = ""
    wechat_oauth_scope: str = "snsapi_base"
    wechat_oauth_redirect_uri: str = ""
    wechat_api_timeout_seconds: float = 8.0
    # 微信支付商家转账（新版 /v3/fund-app/mch-transfer）。密钥通过文件路径和
    # 32 位 APIv3 Key 提供，绝不写入代码仓库或日志。
    wechatpay_enabled: bool = False
    wechatpay_mchid: str = ""
    wechatpay_mch_serial_no: str = ""
    wechatpay_mch_private_key_path: str = ""
    wechatpay_mch_public_key_id: str = ""
    wechatpay_mch_public_key_path: str = ""
    wechatpay_api_v3_key: str = ""
    wechatpay_notify_base_url: str = ""
    wechatpay_scene_id: str = "1000"
    wechatpay_bill_name: str = "槟郎开袋有礼"
    wechatpay_bill_remark_prefix: str = "扫码抽奖现金奖励"
    wechatpay_max_attempts: int = 8
    wechatpay_confirm_timeout_minutes: int = 72 * 60
    wechatpay_worker_poll_seconds: float = 5.0
    wechatpay_worker_batch_size: int = 20

    @field_validator("database_url")
    @classmethod
    def require_mysql(cls, value: str) -> str:
        if not value.startswith("mysql+pymysql://"):
            raise ValueError("DATABASE_URL 必须使用 mysql+pymysql:// 连接 MySQL")
        return value

    @field_validator("redis_url")
    @classmethod
    def require_redis(cls, value: str) -> str:
        if not value.startswith(("redis://", "rediss://")):
            raise ValueError("REDIS_URL 必须使用 redis:// 或 rediss://")
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def trusted_host_list(self) -> list[str]:
        return [host.strip() for host in self.trusted_hosts.split(",") if host.strip()]

    @model_validator(mode="after")
    def validate_production_security(self):
        if self.environment != "production":
            return self
        if self.jwt_secret == "local-development-secret-change-me" or len(self.jwt_secret) < 32:
            raise ValueError("生产环境必须配置至少 32 位的 JWT_SECRET")
        if self.admin_bootstrap_password == "change-me-now" or len(self.admin_bootstrap_password) < 12:
            raise ValueError("生产环境必须配置至少 12 位的初始管理员密码")
        if not self.h5_base_url.startswith("https://"):
            raise ValueError("生产环境 H5_BASE_URL 必须使用 HTTPS")
        if not self.force_https:
            raise ValueError("生产环境必须启用 FORCE_HTTPS")
        if any(origin.startswith("http://") for origin in self.cors_origin_list):
            raise ValueError("生产环境 CORS_ORIGINS 仅允许 HTTPS 来源")
        if not self.wechatpay_enabled:
            raise ValueError("生产环境必须启用 WECHATPAY_ENABLED，现金奖仅支持微信商家转账")
        if not self.wechat_app_id or not self.wechat_app_secret:
            raise ValueError("生产环境必须配置公众号 WECHAT_APP_ID 与 WECHAT_APP_SECRET")
        if not self.wechat_oauth_redirect_uri:
            raise ValueError("生产环境必须显式配置 WECHAT_OAUTH_REDIRECT_URI，避免反向代理生成错误回调地址")
        oauth_redirect = urlsplit(self.wechat_oauth_redirect_uri)
        if oauth_redirect.scheme != "https" or not oauth_redirect.netloc:
            raise ValueError("WECHAT_OAUTH_REDIRECT_URI 必须使用公网 HTTPS 完整地址")
        if not self.wechatpay_mchid or not self.wechatpay_mch_serial_no or not self.wechatpay_api_v3_key:
            raise ValueError("启用 WECHATPAY_ENABLED 时必须配置商户号、证书序列号与 APIv3 Key")
        if len(self.wechatpay_api_v3_key) != 32:
            raise ValueError("WECHATPAY_API_V3_KEY 必须恰好为 32 个字符")
        if not self.wechatpay_mch_private_key_path:
            raise ValueError("启用 WECHATPAY_ENABLED 时必须配置商户 API 私钥文件路径")
        if not self.wechatpay_mch_public_key_id or not self.wechatpay_mch_public_key_path:
            raise ValueError("启用 WECHATPAY_ENABLED 时必须配置微信支付平台公钥 ID 与文件路径")
        if not self.wechatpay_notify_base_url:
            raise ValueError("生产环境必须显式配置 WECHATPAY_NOTIFY_BASE_URL，且不能从带路径的 H5_BASE_URL 推导")
        notify_base = urlsplit(self.wechatpay_notify_base_url)
        if notify_base.scheme != "https" or not notify_base.netloc or notify_base.path not in {"", "/"}:
            raise ValueError("WECHATPAY_NOTIFY_BASE_URL 必须是无路径的公网 HTTPS 基址")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
