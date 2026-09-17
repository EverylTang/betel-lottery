"""微信支付商家转账高层业务逻辑。

负责：
- 现金中奖单的自动发起/查询/终态落库（ensure_payment_transfer）；
- 供定时 worker 使用的入口（process_cash_payment）；
- H5 用户"立即领取"并返回拉起确认收款所需的 package_info（claim_cash_payment）；
- 超过确认时限单据的自动撤销（cancel_stale_confirmations）。

低层能力（签名、转账、回调）位于 signature.py / transfer.py / notification.py。

资金安全约束（与微信文档一致）：
- 收到非 200 响应时绝不立即更换商户单号重试；必须先用原商户单号查询并确认
  原单未创建/已撤销/已失败后，才允许生成新的商户单号重试。
- 成功或"待用户确认"不是终态；只有 SUCCESS/FAIL/CANCELLED 才算终态。
- 本服务默认不采集用户实名，故单笔转账限制在 0.30 ~ 1999.99 元；如需发放
  >= 2000 元必须另行实现 RSA-OAEP 加密的 user_name 实名收款方案。
"""

import logging
import secrets
from datetime import timedelta
from typing import Any, Callable

from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.cache import redis_client
from app.core.config import get_settings
from app.db import engine
from app.models import Activity, CashPayment, Consumer, LotteryRecord, PaymentStatus, RedemptionStatus, utc_now

from app.services.wechat_pay.signature import WechatPayConfigError
from app.services.wechat_pay.transfer import (
    TransferBillResult,
    TransferQueryFailed,
    TransferRequestFailed,
    cancel_transfer_bill,
    create_transfer_bill,
    query_transfer_bill,
    WECHAT_STATE_CANCELLED,
    WECHAT_STATE_FAIL,
    WECHAT_STATE_SUCCESS,
    WECHAT_STATE_WAIT_USER_CONFIRM,
    WECHAT_TERMINAL_STATES,
    WECHAT_STATE_ACCEPTED,
    WECHAT_STATE_PROCESSING,
    WECHAT_STATE_TRANSFERING,
    WECHAT_STATE_CANCELING,
)

logger = logging.getLogger("betel_lottery.wechat_pay")
settings = get_settings()

_ACTIONABLE_LOCAL_STATES = {PaymentStatus.PENDING, PaymentStatus.PROCESSING}


def _clear_provider_bill_slots(payment: CashPayment) -> None:
    """清空上一张商户转账单的本地引用。

    只允许在微信侧单据已被确认终态（SUCCESS/FAIL/CANCELLED）之后调用，
    否则旧单仍可能被用户确认，会造成重复打款。
    """
    payment.provider_out_bill_no = None
    payment.transfer_bill_no = None
    payment.transfer_state = None
    payment.package_info = None
    payment.notify_event_id = None
    payment.fail_reason = None


def _fresh_out_bill_no(payment: CashPayment) -> str:
    """生成下一张商户转账单的单号。

    微信按 out_bill_no 唯一记账，撤销/失败后的单号不可复用，否则会命中历史
    单据永远无法重新发起。首单直接使用 merchant_order_no；后续按 attempts
    追加 -R{attempts+1} 后缀。商户单号允许 - 字符且不超过 32 位。
    """
    base = (payment.merchant_order_no or "").strip()
    if not base:
        base = f"CASH{payment.lottery_record_id or 0:012d}"
    attempts = payment.attempts or 0
    if attempts < 1:
        return base[:32]
    stem = base[:28].rstrip("-")
    return f"{stem}-R{attempts + 1}"[:32]


def _reset_for_reclaim(session: Session, payment: CashPayment, note: str) -> None:
    """用户对已撤销单（微信 state=CANCELLED）主动重新领取时重置本地状态。"""
    payment.status = PaymentStatus.PENDING
    payment.provider = "wechat_mch_transfer"
    payment.processed_at = None
    payment.provider_error_note = (note or "")[:512]
    payment.provider_updated_at = utc_now()
    _clear_provider_bill_slots(payment)
    session.add(payment)
    session.commit()


def _apply_upstream_result(session: Session, payment: CashPayment, result: TransferBillResult) -> None:
    """将微信单状态落到 cash_payment / lottery_record（调用前须持有行锁）。"""
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


def _mark_failed(session: Session, payment: CashPayment, reason: str, note: str | None = None) -> None:
    now = utc_now()
    payment.status = PaymentStatus.FAILED
    payment.fail_reason = (reason or "")[:256]
    payment.provider_error_note = (note or "")[:512]
    payment.processed_at = now
    payment.provider_updated_at = now
    record = session.get(LotteryRecord, payment.lottery_record_id)
    if record:
        record.redemption_status = RedemptionStatus.PENDING
        record.redeemed_at = None
        record.redemption_note = f"微信商家转账失败：{reason}"
        session.add(record)
    session.add(payment)
    session.commit()


def _claim_lock_key(payment_id: int) -> str:
    return f"wechatpay:claim:{payment_id}"


def _with_claim_lock(payment_id: int, callback: Callable[[], Any]) -> Any:
    """串行化同一笔转账单的创建/查询，防止并发重复提交。"""
    key = _claim_lock_key(payment_id)
    token = secrets.token_urlsafe(12)
    if not redis_client.set(key, token, nx=True, ex=180):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该奖励正在自动发放，请稍候再试")
    try:
        return callback()
    finally:
        try:
            if redis_client.get(key) == token:
                redis_client.delete(key)
        except Exception:
            logger.exception("failed to release claim lock payment_id=%s", payment_id)


def _bill_missing_upstream(exc: TransferRequestFailed) -> bool:
    text = str(exc)
    return "NOT_FOUND" in text or "订单不存在" in text


def _out_bill_base(out_bill_no: str) -> str:
    """从商户转账单号还原 merchant_order_no（去掉 -R{n} 重试后缀）。"""
    head, sep, tail = out_bill_no.rpartition("-R")
    if sep and tail.isdigit():
        return head
    return out_bill_no


def _create_with_fresh_bill_no(
    session: Session,
    payment: CashPayment,
    consumer: Consumer,
    record: LotteryRecord,
    activity: Activity | None,
) -> TransferBillResult:
    """分配新商户单号并创建微信转账单。

    先把新单号与 attempts 落库再发请求：若请求在网络层丢失，下轮会按已落库
    单号先查询，而不是盲目重复创建，避免同一单号产生两张单据。
    """
    out_bill_no = _fresh_out_bill_no(payment)
    payment.provider_out_bill_no = out_bill_no
    payment.attempts = (payment.attempts or 0) + 1
    payment.provider = "wechat_mch_transfer"
    payment.provider_updated_at = utc_now()
    session.add(payment)
    session.commit()
    return create_transfer_bill(
        out_bill_no=out_bill_no,
        openid=consumer.openid,
        amount=payment.amount,
        activity_name=activity.name if activity else "",
        prize_name=record.prize_name if record else "",
    )


def ensure_payment_transfer(
    session: Session,
    payment: CashPayment,
    *,
    allow_reclaim: bool = False,
) -> dict[str, Any]:
    """保证 cash_payment 在微信侧存在一张"当前有效"的转账单。

    资金安全规则：
    - SUCCESS：终态，永不重复发起；
    - FAIL：终态，不自动重试，只能人工处理；
    - CANCELLED（本地 FAILED + transfer_state=CANCELLED）：仅当用户主动点击
      "重新领取"（allow_reclaim=True）才清旧单引用并换新商户单号重发；
      定时 worker 永不自动换号，避免用户已离开仍不断产生可确认单据；
    - PENDING/PROCESSING：有 provider_out_bill_no 时先按该号查询上游单据；
      从未发起（无单号）才分配新单号并创建；
    - 创建请求被微信拒绝（HTTP 非 2xx）时绝不换号重发：先用同一商户单号查询
      一次；查到单据则以单据状态落库，查询返回"订单不存在/NOT_FOUND"才能确认
      该单从未创建并安全清引用，查询失败则保持 PENDING/PROCESSING，由后续
      worker 继续按原单号查询，直到微信侧单据状态可确认；
    - 已有单号查询返回 NOT_FOUND 时（单号确实未创建）才允许清空旧引用并换
      新商户单号重新发起，且受 WECHATPAY_MAX_ATTEMPTS 限制；
    - NO_AUTH / PARAM_ERROR 等配置或参数错误不会仅凭一次响应就落 FAILED 或
      换号：修正微信商户侧配置后，下一轮 worker 按原单号查询可自动恢复。
    """
    from app.services.wechat_pay.signature import _require_configured
    from app.services.wechat_pay.transfer import _validate_amount

    if payment.status == PaymentStatus.SUCCESS:
        return {"result": "success", "state": None, "detail": "现金奖励已到账"}

    if payment.status == PaymentStatus.FAILED:
        cancelled = payment.transfer_state == WECHAT_STATE_CANCELLED
        if allow_reclaim and cancelled:
            if (payment.attempts or 0) >= settings.wechatpay_max_attempts:
                return {
                    "result": "failed",
                    "state": WECHAT_STATE_CANCELLED,
                    "detail": "该奖励重试次数已达上限，请通过活动方客服人工处理",
                }
            _reset_for_reclaim(
                session,
                payment,
                f"用户重新领取（原单 {payment.provider_out_bill_no or ''} 已撤销）",
            )
        else:
            detail = payment.fail_reason or (
                "该奖励发放已失败"
                + ("（转账单已撤销，可点击重新领取）" if cancelled else "，请联系客服")
            )
            return {"result": "failed", "state": payment.transfer_state, "detail": detail}

    if payment.status == PaymentStatus.MANUAL:
        return {"result": "manual", "state": None, "detail": "该奖励已转人工处理"}
    if payment.status not in _ACTIONABLE_LOCAL_STATES:
        return {"result": "skipped", "state": None, "detail": f"当前状态 {payment.status} 无需自动打款"}

    record = session.get(LotteryRecord, payment.lottery_record_id)
    if not record:
        _mark_failed(session, payment, "中奖记录不存在，无法打款", "lottery_record 缺失")
        return {"result": "failed", "state": None, "detail": "中奖记录异常，请联系客服"}
    consumer = session.get(Consumer, record.consumer_id)
    if not consumer or not consumer.openid:
        _mark_failed(session, payment, "缺少用户 OpenID", "用户未通过公众号进入，无法自动打款")
        return {"result": "failed", "state": None, "detail": "当前账号尚未绑定微信身份，请联系客服补发"}
    try:
        _validate_amount(payment.amount)
    except HTTPException as exc:
        _mark_failed(session, payment, str(exc.detail), "金额超过商家转账免实名上限")
        return {"result": "failed", "state": None, "detail": str(exc.detail)}
    activity = session.get(Activity, record.activity_id) if record.activity_id else None
    _require_configured()

    existing_out_bill_no = payment.provider_out_bill_no
    retried_after_missing = False
    while True:
        try:
            if existing_out_bill_no:
                bill = query_transfer_bill(existing_out_bill_no)
            else:
                if (payment.attempts or 0) >= settings.wechatpay_max_attempts:
                    _mark_failed(session, payment, "达到微信商家转账尝试次数上限，需人工处理")
                    return {
                        "result": "failed",
                        "state": None,
                        "detail": "该奖励自动打款尝试次数已达上限，请通过活动方客服处理",
                    }
                bill = _create_with_fresh_bill_no(session, payment, consumer, record, activity)
            break
        except TransferQueryFailed:
            raise
        except TransferRequestFailed as exc:
            if existing_out_bill_no and _bill_missing_upstream(exc) and not retried_after_missing:
                payment.provider_out_bill_no = None
                payment.transfer_bill_no = None
                payment.transfer_state = None
                payment.package_info = None
                payment.provider_error_note = f"原单不存在已清空，将换新单号: {exc}"[:512]
                session.add(payment)
                session.commit()
                existing_out_bill_no = None
                retried_after_missing = True
                continue
            if not existing_out_bill_no:
                attempted_no = payment.provider_out_bill_no or ""
                if attempted_no:
                    try:
                        bill = query_transfer_bill(attempted_no)
                    except TransferRequestFailed as qexc:
                        if _bill_missing_upstream(qexc):
                            _clear_provider_bill_slots(payment)
                            _mark_failed(
                                session,
                                payment,
                                str(exc)[:256],
                                "创建转账单被微信拒绝，且已确认上游不存在该商户单号",
                            )
                            return {
                                "result": "failed",
                                "state": None,
                                "detail": "微信支付拒绝了该笔转账，请联系客服",
                            }
                        raise TransferQueryFailed(
                            f"create transfer-bill rejected, then query also failed, status unknown: {exc}"
                        ) from exc
                    except TransferQueryFailed:
                        raise
                    break
            raise TransferQueryFailed(
                f"transfer-bill status unknown, query rejected: {exc}"
            ) from exc

    _apply_upstream_result(session, payment, bill)
    state = bill.state
    if state == WECHAT_STATE_SUCCESS:
        return {"result": "success", "state": state, "detail": "现金奖励已到账"}
    if state == WECHAT_STATE_WAIT_USER_CONFIRM or (
        bill.package_info and state not in WECHAT_TERMINAL_STATES
    ):
        return {
            "result": "claim",
            "state": state,
            "package_info": bill.package_info or payment.package_info,
            "detail": "请在微信中确认收款",
        }
    if state in {WECHAT_STATE_FAIL, WECHAT_STATE_CANCELLED}:
        return {
            "result": "failed",
            "state": state,
            "detail": payment.fail_reason or "转账未完成，请稍后重试或联系客服",
        }
    return {"result": "processing", "state": state, "detail": "转账处理中，请稍后查看"}


def process_cash_payment(payment_id: int) -> dict[str, Any]:
    """供定时 worker 使用：对待处理现金单发起/确认商家转账。"""

    def run() -> dict[str, Any]:
        with Session(engine) as session:
            payment = session.exec(
                select(CashPayment).where(CashPayment.id == payment_id).with_for_update()
            ).first()
            if not payment:
                return {"payment_id": payment_id, "skipped": "missing"}
            if payment.status not in {PaymentStatus.PENDING, PaymentStatus.PROCESSING}:
                return {"payment_id": payment_id, "skipped": str(payment.status)}
            try:
                outcome = ensure_payment_transfer(session, payment, allow_reclaim=False)
            except WechatPayConfigError as exc:
                session.rollback()
                logger.warning("payment_id=%s worker deferred (config): %s", payment_id, exc)
                return {"payment_id": payment_id, "deferred": str(exc)[:200]}
            except HTTPException as exc:
                session.rollback()
                logger.warning("payment_id=%s worker http error: %s", payment_id, exc.detail)
                return {"payment_id": payment_id, "deferred": str(exc.detail)[:200]}
            except TransferQueryFailed as exc:
                session.rollback()
                logger.warning("payment_id=%s worker query deferred: %s", payment_id, exc)
                return {"payment_id": payment_id, "deferred": str(exc)[:200]}
            return {"payment_id": payment_id, **outcome}

    return _with_claim_lock(payment_id, run)


def claim_cash_payment(payment_id: int) -> dict[str, Any]:
    """H5 用户点击"立即领取"时调用：返回拉起微信确认收款页所需的 package。"""

    def run() -> dict[str, Any]:
        with Session(engine) as session:
            payment = session.exec(
                select(CashPayment).where(CashPayment.id == payment_id).with_for_update()
            ).first()
            if not payment:
                raise HTTPException(status_code=404, detail="现金奖励订单不存在")
            outcome = ensure_payment_transfer(session, payment, allow_reclaim=True)
            if outcome.get("result") != "claim":
                return {
                    "result": outcome.get("result", "skipped"),
                    "state": outcome.get("state"),
                    "detail": outcome.get("detail") or "当前无法领取",
                }
            package_info = outcome.get("package_info")
            if not package_info:
                raise HTTPException(status_code=409, detail="微信转账单尚未生成收款确认页，请稍后重试")
            return {
                "result": "claim",
                "app_id": settings.wechat_app_id,
                "mch_id": settings.wechatpay_mchid,
                "package": package_info,
            }

    return _with_claim_lock(payment_id, run)


def cancel_stale_confirmations(payment_id: int) -> dict[str, Any]:
    """超过确认时限（默认 72 小时）仍 WAIT_USER_CONFIRM 的单据自动撤销。"""

    def run() -> dict[str, Any]:
        with Session(engine) as session:
            payment = session.exec(
                select(CashPayment).where(CashPayment.id == payment_id).with_for_update()
            ).first()
            if not payment or payment.status != PaymentStatus.PROCESSING:
                return {"skipped": True}
            if payment.transfer_state not in {
                WECHAT_STATE_WAIT_USER_CONFIRM,
                WECHAT_STATE_PROCESSING,
                WECHAT_STATE_ACCEPTED,
                WECHAT_STATE_TRANSFERING,
                WECHAT_STATE_CANCELING,
            }:
                return {"skipped": True}
            reference = payment.provider_created_at or payment.provider_updated_at
            if not reference:
                return {"skipped": True}
            if (utc_now() - reference) < timedelta(minutes=settings.wechatpay_confirm_timeout_minutes):
                return {"skipped": True}
            out_bill_no = payment.provider_out_bill_no or payment.merchant_order_no
            try:
                cancel_transfer_bill(out_bill_no)
            except (WechatPayConfigError, TransferRequestFailed, TransferQueryFailed) as exc:
                logger.warning("payment_id=%s cancel failed: %s", payment_id, exc)
                return {"skipped": True, "note": str(exc)[:200]}
            payment.operator_note = "wechatpay_confirmation_timeout:cancelling"
            session.add(payment)
            session.commit()
            return {"cancelling": True}

    return _with_claim_lock(payment_id, run)
