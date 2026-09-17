"""微信支付 APIv3 签名验证模块。

包含：
- 商户 API v3 请求签名（WECHATPAY2-SHA256-RSA2048）
- 响应/回调验签
- 密钥加载与管理
"""

import base64
import secrets
import time
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.core.config import get_settings

settings = get_settings()

WECHATPAY_SCHEMA = "WECHATPAY2-SHA256-RSA2048"


class WechatPayConfigError(RuntimeError):
    """微信支付未启用或配置不完整。属于可修复配置问题，不落 FAILED。"""


def wechatpay_ready() -> bool:
    return bool(
        settings.wechatpay_enabled
        and settings.wechatpay_mchid
        and settings.wechatpay_api_v3_key
        and settings.wechatpay_mch_private_key_path
        and settings.wechatpay_mch_serial_no
    )


def _require_configured() -> None:
    if not wechatpay_ready():
        raise WechatPayConfigError(
            "微信支付商家转账尚未配置，无法自动打款，请按 README 配置 WECHATPAY_* 后重试"
        )
    if not settings.wechatpay_mch_public_key_id or not settings.wechatpay_mch_public_key_path:
        raise WechatPayConfigError("缺少微信支付平台公钥（WECHATPAY_MCH_PUBLIC_KEY_ID/PATH），无法验签回调与响应")


def _load_private_key() -> rsa.RSAPrivateKey:
    key_path = Path(settings.wechatpay_mch_private_key_path).expanduser()
    try:
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    except (OSError, ValueError) as exc:
        raise WechatPayConfigError(f"无法读取商户 API 私钥文件 {key_path}") from exc
    if not isinstance(key, rsa.RSAPrivateKey):
        raise WechatPayConfigError("商户 API 私钥必须是 RSA 私钥")
    return key


def _load_public_key() -> rsa.RSAPublicKey:
    key_path = Path(settings.wechatpay_mch_public_key_path).expanduser()
    try:
        key = serialization.load_pem_public_key(key_path.read_bytes())
    except (OSError, ValueError) as exc:
        raise WechatPayConfigError(f"无法读取微信支付平台公钥文件 {key_path}") from exc
    if not isinstance(key, rsa.RSAPublicKey):
        raise WechatPayConfigError("微信支付平台公钥必须是 RSA 公钥")
    return key


def _sign_bytes(private_key: rsa.RSAPrivateKey, message: bytes) -> str:
    signature = private_key.sign(message, padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(signature).decode()


def _verify_signature(message: bytes, signature_b64: str) -> None:
    try:
        _load_public_key().verify(base64.b64decode(signature_b64), message, padding.PKCS1v15(), hashes.SHA256())
    except (InvalidSignature, ValueError) as exc:
        raise WechatPayConfigError("微信支付平台签名校验失败") from exc


def build_authorization(method: str, path_with_query: str, body: str) -> str:
    """按微信支付 APIv3 规则构造 Authorization 请求头。"""
    timestamp = str(int(time.time()))
    nonce = secrets.token_urlsafe(16)
    message = f"{method}\n{path_with_query}\n{timestamp}\n{nonce}\n{body}\n"
    signature = _sign_bytes(_load_private_key(), message.encode())
    return (
        f'{WECHATPAY_SCHEMA} mchid="{settings.wechatpay_mchid}",'
        f'nonce_str="{nonce}",signature="{signature}",timestamp="{timestamp}",'
        f'serial_no="{settings.wechatpay_mch_serial_no}"'
    )


def verify_signature(timestamp: str, nonce: str, signature: str, raw_body: bytes, serial: str | None) -> None:
    """验证微信回调/API 响应的 Wechatpay-Signature。"""
    _require_configured()
    if not timestamp or not nonce or not signature or not raw_body:
        raise WechatPayConfigError("微信回调缺少签名头")
    if serial and serial != settings.wechatpay_mch_public_key_id:
        raise WechatPayConfigError(f"微信回调证书序列号 {serial} 与平台公钥不匹配")
    message = f"{timestamp}\n{nonce}\n{raw_body.decode('utf-8')}\n".encode()
    _verify_signature(message, signature)
