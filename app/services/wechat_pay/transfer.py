"""微信商家转账（/v3/fund-app/mch-transfer）模块。

包含：
- 创建商家转账单
- 按商户单号查询
- 撤销转账单
- HTTP 请求封装
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException, status

from app.core.config import get_settings
from app.services.wechat_pay.signature import (
    WechatPayConfigError,
    _require_configured,
    build_authorization,
    verify_signature,
)

logger = logging.getLogger("betel_lottery.wechat_pay")
settings = get_settings()

WECHATPAY_API_BASE = "https://api.mch.weixin.qq.com"

WECHAT_STATE_ACCEPTED = "ACCEPTED"
WECHAT_STATE_PROCESSING = "PROCESSING"
WECHAT_STATE_WAIT_USER_CONFIRM = "WAIT_USER_CONFIRM"
WECHAT_STATE_TRANSFERING = "TRANSFERING"
WECHAT_STATE_SUCCESS = "SUCCESS"
WECHAT_STATE_FAIL = "FAIL"
WECHAT_STATE_CANCELING = "CANCELING"
WECHAT_STATE_CANCELLED = "CANCELLED"
WECHAT_TERMINAL_STATES = {WECHAT_STATE_SUCCESS, WECHAT_STATE_FAIL, WECHAT_STATE_CANCELLED}

MIN_TRANSFER_AMOUNT = Decimal("0.30")
MAX_TRANSFER_AMOUNT_WITHOUT_REALNAME = Decimal("1999.99")


class TransferRequestFailed(RuntimeError):
    """微信侧明确拒绝了转账单（HTTP 非 2xx 或业务级失败）。"""


class TransferQueryFailed(RuntimeError):
    """查询上游单失败，可稍后重试。"""


@dataclass
class TransferBillResult:
    out_bill_no: str
    state: str
    transfer_bill_no: str | None = None
    package_info: str | None = None
    fail_reason: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def _http_request(method: str, url: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    _require_configured()
    body_text = "" if body is None else json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    parsed = httpx.URL(url)
    path_with_query = parsed.path + (f"?{parsed.query}" if parsed.query else "")
    headers = {
        "Authorization": build_authorization(method, path_with_query, body_text),
        "Accept": "application/json",
        "User-Agent": "betel-lottery-api",
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    try:
        logger.debug(f"微信支付 API 请求开始: {method} {url}")
        with httpx.Client(timeout=settings.wechat_api_timeout_seconds + 5.0) as client:
            response = client.request(
                method,
                url,
                headers=headers,
                content=body_text.encode() if body is not None else None,
            )
        logger.debug(f"微信支付 API 响应: status_code={response.status_code}")
    except httpx.TimeoutException as exc:
        logger.error(f"微信支付 API 请求超时: {method} {url}, timeout={settings.wechat_api_timeout_seconds + 5.0}s, error={exc}", exc_info=True)
        raise TransferQueryFailed(f"微信支付接口请求超时（{settings.wechat_api_timeout_seconds + 5.0}秒无响应）") from exc
    except httpx.ConnectError as exc:
        logger.error(f"微信支付 API 连接失败: {method} {url}, error={exc}", exc_info=True)
        raise TransferQueryFailed(f"无法连接到微信支付服务器，请检查网络连接：{type(exc).__name__}") from exc
    except httpx.NetworkError as exc:
        logger.error(f"微信支付 API 网络错误: {method} {url}, error={exc}", exc_info=True)
        raise TransferQueryFailed(f"微信支付接口网络异常：{type(exc).__name__}") from exc
    except httpx.HTTPError as exc:
        logger.error(f"微信支付 API HTTP 错误: {method} {url}, error={exc}", exc_info=True)
        raise TransferQueryFailed(f"微信支付接口网络请求失败: {type(exc).__name__}") from exc
    try:
        verify_signature(
            response.headers.get("Wechatpay-Timestamp", ""),
            response.headers.get("Wechatpay-Nonce", ""),
            response.headers.get("Wechatpay-Signature", ""),
            response.content,
            response.headers.get("Wechatpay-Serial"),
        )
    except Exception as exc:
        logger.error(f"微信支付响应签名验证失败: {exc}", exc_info=True)
        raise TransferQueryFailed(f"微信支付响应签名验证失败：{exc}") from exc
    try:
        payload = response.json()
    except ValueError as exc:
        logger.error(f"微信支付 API 响应解析失败: status_code={response.status_code}, body={response.text[:200]}, error={exc}", exc_info=True)
        raise TransferQueryFailed("微信支付接口响应不是有效 JSON") from exc
    if not (200 <= response.status_code < 300):
        code = payload.get("code", f"HTTP_{response.status_code}")
        message = payload.get("message", "微信支付接口拒绝")
        logger.error(f"微信支付 API 业务错误: {method} {url}, status_code={response.status_code}, code={code}, message={message}")
        raise TransferRequestFailed(f"{code}: {message}")
    return payload


def _notify_url() -> str:
    configured_base = settings.wechatpay_notify_base_url.strip()
    if configured_base:
        base = configured_base.rstrip("/")
    else:
        parsed = urlsplit(settings.h5_base_url)
        base = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    if not base:
        raise WechatPayConfigError("缺少回调基址，无法为转账单设置 notify_url")
    if not base.startswith("https://"):
        raise WechatPayConfigError("商家转账 notify_url 必须使用公网 HTTPS")
    return f"{base}/api/wechat-pay/notify"


def _clip(value: str, limit: int) -> str:
    return (value or "").strip()[:limit]


def _scene_report_infos(activity_name: str, prize_name: str) -> list[dict[str, str]]:
    return [
        {"info_type": "活动名称", "info_content": _clip(activity_name, 32) or _clip(settings.wechatpay_bill_name, 32)},
        {"info_type": "奖励说明", "info_content": _clip(prize_name, 32) or _clip(settings.wechatpay_bill_remark_prefix, 32)},
    ]


def _validate_amount(amount: Decimal) -> None:
    if amount < MIN_TRANSFER_AMOUNT or amount > MAX_TRANSFER_AMOUNT_WITHOUT_REALNAME:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "现金奖品金额超出微信商家转账单笔限额（0.30~1999.99 元）；"
                "2000 元以上需采集用户实名并通过 RSA 加密提交，请联系活动方调整奖品金额"
            ),
        )


def _parse_wx_time(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(int(value), tz=timezone.utc).replace(tzinfo=None)
    if not value:
        return None
    text = str(value)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def create_transfer_bill(
    *,
    out_bill_no: str,
    openid: str,
    amount: Decimal,
    activity_name: str,
    prize_name: str,
) -> TransferBillResult:
    """创建商家转账单并返回当前状态。只有 WAIT_USER_CONFIRM 才可引导确认。"""
    _require_configured()
    if not out_bill_no or len(out_bill_no) > 32:
        raise HTTPException(status_code=422, detail="转账商户单号格式错误")
    if not openid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="该中奖用户没有微信公众号 OpenID，无法自动打款，请引导用户重新进入公众号活动页面",
        )
    _validate_amount(amount)
    amount_fen = int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if not settings.wechat_app_id:
        raise WechatPayConfigError("缺少公众号 AppID（WECHAT_APP_ID），无法发起商家转账")
    body = {
        "out_bill_no": out_bill_no,
        "transfer_scene_id": settings.wechatpay_scene_id or "1000",
        "openid": openid,
        "transfer_amount": amount_fen,
        "transfer_remark": _clip(prize_name or settings.wechatpay_bill_remark_prefix, 32),
        "transfer_scene_report_infos": _scene_report_infos(activity_name, prize_name),
        "user_recv_perception": "现金奖励",
        "notify_url": _notify_url(),
        "appid": settings.wechat_app_id,
    }
    payload = _http_request("POST", f"{WECHATPAY_API_BASE}/v3/fund-app/mch-transfer/transfer-bills", body)
    state = str(payload.get("state") or "")
    if not state:
        raise TransferQueryFailed("创建转账单响应缺少单据状态")
    return TransferBillResult(
        out_bill_no=out_bill_no,
        state=state,
        transfer_bill_no=payload.get("transfer_bill_no"),
        package_info=payload.get("package_info"),
        raw=payload,
    )


def query_transfer_bill(out_bill_no: str) -> TransferBillResult:
    _require_configured()
    url = f"{WECHATPAY_API_BASE}/v3/fund-app/mch-transfer/transfer-bills/out-bill-no/{out_bill_no}"
    payload = _http_request("GET", url)
    return TransferBillResult(
        out_bill_no=out_bill_no,
        state=str(payload.get("state") or ""),
        transfer_bill_no=payload.get("transfer_bill_no"),
        package_info=payload.get("package_info"),
        fail_reason=payload.get("fail_reason"),
        created_at=_parse_wx_time(payload.get("create_time")),
        updated_at=_parse_wx_time(payload.get("update_time")),
        raw=payload,
    )


def cancel_transfer_bill(out_bill_no: str) -> None:
    _require_configured()
    url = f"{WECHATPAY_API_BASE}/v3/fund-app/mch-transfer/transfer-bills/out-bill-no/{out_bill_no}/cancel"
    _http_request("POST", url)
