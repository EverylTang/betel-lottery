"""微信公众号 JS-SDK 配置（用于 H5 内拉起商家转账确认收款页）。

流程：H5 前端拿到当前页完整 URL → 后端校验属于 H5 可信域名 → 获取
jsapi_ticket → 返回 wx.config 所需的签名。access_token 与 jsapi_ticket
均有 7200 秒有效期，这里按 6900 秒缓存到 Redis。
"""

import hashlib
import secrets
import time
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException, status

from app.cache import redis_client
from app.core.config import get_settings

settings = get_settings()

_ACCESS_TOKEN_CACHE_KEY = "wechat:jsapi:access_token"
_TICKET_CACHE_KEY = "wechat:jsapi:ticket"


def _require_jsapi_config() -> None:
    if not (settings.wechat_app_id and settings.wechat_app_secret):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="公众号 JS-SDK 尚未配置（WECHAT_APP_ID/WECHAT_APP_SECRET）",
        )


def validate_h5_page_url(raw_url: str) -> str:
    """只允许当前 H5 可信域名下的 URL 参与签名，防止任意页面注入。"""
    if not raw_url or len(raw_url) > 2048:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="缺少页面 URL")
    parsed = urlsplit(raw_url)
    base = urlsplit(settings.h5_base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="页面 URL 格式无效")
    if parsed.scheme != base.scheme or parsed.netloc.lower() != base.netloc.lower():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="页面 URL 不属于本活动 H5 域名，无法生成微信 JS-SDK 配置",
        )
    base_path = base.path.rstrip("/")
    if base_path and parsed.path != base_path and not parsed.path.startswith(base_path + "/"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="页面 URL 路径不属于本活动 H5")
    clean = parsed.scheme + "://" + parsed.netloc + parsed.path
    if parsed.query:
        clean += "?" + parsed.query
    return clean


def _fetch_access_token() -> str:
    with httpx.Client(timeout=settings.wechat_api_timeout_seconds) as client:
        response = client.get(
            "https://api.weixin.qq.com/cgi-bin/token",
            params={
                "grant_type": "client_credential",
                "appid": settings.wechat_app_id,
                "secret": settings.wechat_app_secret,
            },
        )
        response.raise_for_status()
        data = response.json()
    if data.get("errcode") or not data.get("access_token"):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"获取微信 access_token 失败（{data.get('errcode', 'unknown')}）",
        )
    return str(data["access_token"])


def get_jsapi_access_token(*, force_refresh: bool = False) -> str:
    _require_jsapi_config()
    if not force_refresh:
        cached = redis_client.get(_ACCESS_TOKEN_CACHE_KEY)
        if cached:
            return cached
    token = _fetch_access_token()
    redis_client.setex(_ACCESS_TOKEN_CACHE_KEY, 6900, token)
    return token


def _fetch_jsapi_ticket(access_token: str) -> str:
    with httpx.Client(timeout=settings.wechat_api_timeout_seconds) as client:
        response = client.get(
            "https://api.weixin.qq.com/cgi-bin/ticket/getticket",
            params={"access_token": access_token, "type": "jsapi"},
        )
        response.raise_for_status()
        data = response.json()
    errcode = data.get("errcode")
    if errcode in (40001, 42001, 40014):
        # access_token 失效，向上抛出让调用方刷新一次。
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="微信 access_token 已失效，需要刷新",
        )
    if errcode or not data.get("ticket"):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"获取微信 jsapi_ticket 失败（{errcode or 'unknown'}）",
        )
    return str(data["ticket"])


def get_jsapi_ticket() -> str:
    cached = redis_client.get(_TICKET_CACHE_KEY)
    if cached:
        return cached
    try:
        token = get_jsapi_access_token()
        ticket = _fetch_jsapi_ticket(token)
    except HTTPException as exc:
        if exc.status_code != status.HTTP_401_UNAUTHORIZED:
            raise
        token = get_jsapi_access_token(force_refresh=True)
        ticket = _fetch_jsapi_ticket(token)
    redis_client.setex(_TICKET_CACHE_KEY, 6900, ticket)
    return ticket


def build_jsapi_config(page_url: str) -> dict[str, str]:
    """校验页面 URL 并生成 wx.config 所需参数。"""
    _require_jsapi_config()
    clean_url = validate_h5_page_url(page_url)
    ticket = get_jsapi_ticket()
    timestamp = str(int(time.time()))
    nonce = secrets.token_urlsafe(16)
    signature_source = f"jsapi_ticket={ticket}&noncestr={nonce}&timestamp={timestamp}&url={clean_url}"
    signature = hashlib.sha1(signature_source.encode()).hexdigest()
    return {
        "appId": settings.wechat_app_id,
        "timestamp": timestamp,
        "nonceStr": nonce,
        "signature": signature,
        "url": clean_url,
    }
