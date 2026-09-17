"""微信商家转账回调通知处理模块。

包含：
- APIv3 资源解密（AES-256-GCM）
- 商家转账终态通知处理
- 回调重复投递保护
"""

import base64
import json
import logging
from datetime import datetime, timezone
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlmodel import Session, select

from app.core.config import get_settings
from app.models import CashPayment, LotteryRecord, PaymentStatus, RedemptionStatus, utc_now
from app.services.wechat_pay.signature import WechatPayConfigError
from app.services.wechat_pay.transfer import TransferBillResult, WECHAT_TERMINAL_STATES

logger = logging.getLogger("betel_lottery.wechat_pay")
settings = get_settings()


def decrypt_resource(resource: dict[str, Any]) -> dict[str, Any]:
    """使用 APIv3 Key 解密商家转账回调的 resource（AES-256-GCM）。"""
    try:
        ciphertext = base64.b64decode(str(resource["ciphertext"]))
        associated_data = str(resource.get("associated_data", ""))
        nonce = str(resource["nonce"]).encode()
        api_v3_key = settings.wechatpay_api_v3_key.encode()
        plaintext = AESGCM(api_v3_key).decrypt(nonce, ciphertext, associated_data.encode())
    except (KeyError, ValueError, TypeError) as exc:
        raise WechatPayConfigError("微信回调资源解密失败，请核对 APIv3 Key") from exc
    try:
        return json.loads(plaintext.decode())
    except ValueError as exc:
        raise WechatPayConfigError("微信回调资源不是有效 JSON") from exc


def _parse_wx_time(value: Any):
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(int(value), tz=timezone.utc).replace(tzinfo=None)
    if not value:
        return None
    text = str(value)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _out_bill_base(out_bill_no: str) -> str:
    """从商户转账单号还原 merchant_order_no（去掉 -R{n} 重试后缀）。"""
    head, sep, tail = out_bill_no.rpartition("-R")
    if sep and tail.isdigit():
        return head
    return out_bill_no


def _apply_upstream_result(session: Session, payment: CashPayment, result: TransferBillResult) -> None:
    """将微信单状态落到 cash_payment / lottery_record（调用前须持有行锁）。"""
    from app.services.wechat_pay.transfer import WECHAT_STATE_SUCCESS, WECHAT_STATE_FAIL, WECHAT_STATE_CANCELLED

    now = utc_now()
    payment.provider = "wechat_mch_transfer"
    payment.provider_out_bill_no = result.out_bill_no
    payment.transfer_bill_no = result.transfer_bill_no or payment.transfer_bill_no
    payment.transfer_state = result.state
    if result.package_info:
        payment.package_info = result.package_info
    if result.fail_reason:
        payment.fail_reason = result.fail_reason[:256]
    if result.created_at:
        payment.provider_created_at = result.created_at
    payment.provider_updated_at = now
    record = session.get(LotteryRecord, payment.lottery_record_id)
    if result.state == WECHAT_STATE_SUCCESS:
        payment.status = PaymentStatus.SUCCESS
        payment.processed_at = now
        payment.fail_reason = None
        if record:
            record.redemption_status = RedemptionStatus.REDEEMED
            record.redeemed_at = now
            record.redemption_note = "微信商家转账成功，已到账"
    elif result.state == WECHAT_STATE_FAIL:
        payment.status = PaymentStatus.FAILED
        payment.processed_at = now
        if record:
            record.redemption_status = RedemptionStatus.PENDING
            record.redeemed_at = None
            record.redemption_note = result.fail_reason or f"微信商家转账{result.state}"
    elif result.state == WECHAT_STATE_CANCELLED:
        payment.status = PaymentStatus.FAILED
        payment.processed_at = now
        if record:
            record.redemption_status = RedemptionStatus.PENDING
            record.redeemed_at = None
            record.redemption_note = "微信转账单已撤销，可在\"我的奖品\"中点击重新领取"
    else:
        payment.status = PaymentStatus.PROCESSING
    session.add(payment)
    if record:
        session.add(record)
    session.commit()


def apply_notification(session: Session, notification: dict[str, Any]) -> tuple[bool, str]:
    """处理商家转账终态通知；重复投递安全。返回 (handled, detail)。"""
    try:
        resource = decrypt_resource(notification["resource"])
    except (KeyError, TypeError, WechatPayConfigError) as exc:
        logger.error("notification decrypt failed: %s", exc)
        return False, "decrypt-failed"
    out_bill_no = str(resource.get("out_bill_no") or "")
    transfer_state = str(resource.get("state") or "")
    event_id = str(resource.get("event_id") or notification.get("id") or "")
    if not out_bill_no or not transfer_state:
        return False, "missing-fields"
    payment = session.exec(
        select(CashPayment).where(CashPayment.provider_out_bill_no == out_bill_no).with_for_update()
    ).first()
    if not payment:
        payment = session.exec(
            select(CashPayment).where(CashPayment.merchant_order_no == out_bill_no).with_for_update()
        ).first()
    if not payment:
        base_no = _out_bill_base(out_bill_no)
        if base_no != out_bill_no:
            payment = session.exec(
                select(CashPayment).where(CashPayment.merchant_order_no == base_no).with_for_update()
            ).first()
    if not payment:
        logger.warning("notification out_bill_no=%s no payment", out_bill_no)
        return False, "payment-not-found"
    if payment.provider_out_bill_no and payment.provider_out_bill_no != out_bill_no:
        logger.info("ignoring stale notification out_bill_no=%s current=%s", out_bill_no, payment.provider_out_bill_no)
        return True, "stale-bill"
    if not payment.provider_out_bill_no and payment.merchant_order_no != out_bill_no:
        logger.warning("notification out_bill_no=%s does not match merchant_order_no", out_bill_no)
        return True, "stale-bill"
    if payment.notify_event_id == event_id and payment.status in {PaymentStatus.SUCCESS, PaymentStatus.FAILED}:
        return True, "duplicate"
    payment.notify_event_id = event_id[:64]
    result = TransferBillResult(
        out_bill_no=out_bill_no,
        state=transfer_state,
        transfer_bill_no=payment.transfer_bill_no or str(resource.get("transfer_bill_no") or ""),
        package_info=resource.get("package_info"),
        fail_reason=resource.get("fail_reason"),
        created_at=_parse_wx_time(resource.get("create_time")),
        updated_at=_parse_wx_time(resource.get("update_time")),
        raw=resource,
    )
    _apply_upstream_result(session, payment, result)
    return True, "ok"
