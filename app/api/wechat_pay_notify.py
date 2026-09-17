"""微信支付商家转账异步回调（公网 POST，无业务 Token）。

回调路径必须与转账单 notify_url 完全一致：
    {WECHATPAY_NOTIFY_BASE_URL}/api/wechat-pay/notify
收到回调后校验 Wechatpay-* 签名头并解密 resource，在 5 秒内应答 2xx。
重复通知幂等，可安全重放。
"""

import json
import logging
import time

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from sqlmodel import Session

from app.core.config import get_settings
from app.db import engine
from app.services.wechat_pay import apply_notification, verify_signature, wechatpay_ready

router = APIRouter(prefix="/api/wechat-pay", tags=["wechat-pay"])
settings = get_settings()
logger = logging.getLogger("betel_lottery.wechat_pay.notify")

# 允许的回调时间偏差（秒），避免服务器时钟漂移导致误拒。
MAX_TIMESTAMP_SKEW_SECONDS = 600


def _handle_notification(payload: dict):
    with Session(engine) as session:
        handled, detail = apply_notification(session, payload)
    return handled, detail


@router.post("/notify")
async def wechat_pay_notify(request: Request):
    raw_body = await request.body()
    timestamp = request.headers.get("Wechatpay-Timestamp", "")
    nonce = request.headers.get("Wechatpay-Nonce", "")
    signature = request.headers.get("Wechatpay-Signature", "")
    serial = request.headers.get("Wechatpay-Serial", "")

    if not wechatpay_ready():
        logger.error("notify received but wechatpay not configured")
        return JSONResponse(status_code=503, content={"code": "FAIL", "message": "微信支付未配置"})
    try:
        received_at = int(timestamp)
        if abs(time.time() - received_at) > MAX_TIMESTAMP_SKEW_SECONDS:
            return JSONResponse(status_code=401, content={"code": "FAIL", "message": "回调时间戳超时"})
        verify_signature(timestamp, nonce, signature, raw_body, serial)
    except (ValueError, TypeError):
        return JSONResponse(status_code=400, content={"code": "FAIL", "message": "回调签名头缺失或格式错误"})
    except Exception as exc:  # 签名校验失败/平台公钥不匹配
        logger.warning("notify signature rejected: %s", exc)
        return JSONResponse(status_code=401, content={"code": "FAIL", "message": "签名校验失败"})

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return JSONResponse(status_code=400, content={"code": "FAIL", "message": "回调内容不是有效 JSON"})
    if not isinstance(payload, dict) or "resource" not in payload:
        return JSONResponse(status_code=400, content={"code": "FAIL", "message": "回调缺少 resource"})

    try:
        handled, detail = await run_in_threadpool(_handle_notification, payload)
    except Exception:
        logger.exception("notify processing failed")
        return JSONResponse(status_code=500, content={"code": "FAIL", "message": "处理失败，请稍后重试"})
    if not handled:
        if detail == "payment-not-found":
            # 商户单号在本系统不存在：通常是旧单/测试单，回执成功停止微信重试并留日志。
            logger.error("notify for unknown out_bill_no ignored")
            return JSONResponse(status_code=200, content={"code": "FAIL", "message": "订单不存在，已忽略"})
        else:
            logger.error("notify processing error: %s", detail)
        return JSONResponse(status_code=500, content={"code": "FAIL", "message": detail or "处理失败"})
    return JSONResponse(status_code=200, content={"code": "SUCCESS", "message": "成功"})
