import hashlib
import logging
import secrets
from datetime import datetime, timedelta
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError, OperationalError, DBAPIError
from sqlmodel import Session, select

from app.cache import enforce_rate_limit, redis_client

logger = logging.getLogger("betel_lottery.lottery")
from app.models import (
    Activity,
    ActivityBatch,
    ActivityPrize,
    CashPayment,
    CodeDistribution,
    CodeStatus,
    Consumer,
    LotteryRecord,
    LotteryPolicy,
    LotteryPolicyPrize,
    Dealer,
    PaymentStatus,
    Prize,
    PrizeType,
    QrCode,
    RedemptionStatus,
    ScanEvent,
    UpgradeOrder,
    utc_now,
)
from app.security import make_token, parse_token
from app.services.location import resolve_location


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _handle_db_error(exc: Exception, operation: str) -> HTTPException:
    if isinstance(exc, IntegrityError):
        logger.error(f"Database integrity error during {operation}: {exc}", exc_info=True)
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="数据完整性冲突，请重试或联系技术支持"
        )
    if isinstance(exc, OperationalError):
        logger.error(f"Database operational error during {operation}: {exc}", exc_info=True)
        if "timeout" in str(exc).lower() or "timed out" in str(exc).lower():
            return HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="数据库连接超时，请稍后重试"
            )
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="数据库操作失败，请稍后重试"
        )
    if isinstance(exc, DBAPIError):
        logger.error(f"Database API error during {operation}: {exc}", exc_info=True)
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="数据库连接异常，请稍后重试"
        )
    logger.error(f"Unexpected error during {operation}: {exc}", exc_info=True)
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="系统错误，请稍后重试"
    )


def _draw_lock_key(code_id: int) -> str:
    return f"lottery:draw-lock:{code_id}"


def _acquire_draw_lock(code_id: int, consumer_id: int) -> str:
    """Reserve a valid code briefly while the scanned H5 page is open."""
    key = _draw_lock_key(code_id)
    lock_id = secrets.token_urlsafe(18)
    value = f"{consumer_id}:{lock_id}"
    if redis_client.set(key, value, nx=True, ex=300):
        return lock_id

    current = redis_client.get(key)
    if current and current.startswith(f"{consumer_id}:"):
        return current.split(":", 1)[1]
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该抽奖号码正在被核验，请稍后再试")


def _release_draw_lock(code_id: int, consumer_id: int, lock_id: str) -> None:
    key = _draw_lock_key(code_id)
    if redis_client.get(key) == f"{consumer_id}:{lock_id}":
        redis_client.delete(key)


def _distribution_for_code(session: Session, code: QrCode) -> CodeDistribution | None:
    return session.exec(
        select(CodeDistribution)
        .where(
            CodeDistribution.batch_id == code.batch_id,
            CodeDistribution.start_code_id <= code.id,
            CodeDistribution.end_code_id >= code.id,
            CodeDistribution.status == "effective",
        )
        .order_by(CodeDistribution.recorded_at.desc())
    ).first()


def _resolve_policy(session: Session, activity_id: int, distribution: CodeDistribution | None, lock: bool = False) -> LotteryPolicy | None:
    """Resolve the most-specific published policy; global activity pool remains fallback."""
    if not distribution:
        return None
    dealer = session.get(Dealer, distribution.dealer_id)
    if not dealer:
        return None
    query = select(LotteryPolicy).where(LotteryPolicy.activity_id == activity_id, LotteryPolicy.status == "active")
    if lock:
        query = query.with_for_update()
    dealer_policy = session.exec(query.where(LotteryPolicy.scope == "dealer", LotteryPolicy.dealer_id == dealer.id).order_by(LotteryPolicy.version.desc())).first()
    if dealer_policy:
        return dealer_policy
    scopes = (
        ("district", LotteryPolicy.province == dealer.province, LotteryPolicy.city == dealer.city, LotteryPolicy.district == dealer.district),
        ("city", LotteryPolicy.province == dealer.province, LotteryPolicy.city == dealer.city),
        ("province", LotteryPolicy.province == dealer.province),
    )
    for scope, *conditions in scopes:
        policy = session.exec(query.where(LotteryPolicy.scope == scope, *conditions).order_by(LotteryPolicy.version.desc())).first()
        if policy:
            return policy
    return None


def public_record(record: LotteryRecord, payment: CashPayment | None = None) -> dict:
    result = {
        "record_id": record.id,
        "prize_id": record.prize_id,
        "prize_name": record.prize_name,
        "prize_type": record.prize_type,
        "amount": str(record.amount),
        "drawn_at": record.created_at.isoformat(),
        "payment_status": payment.status if payment else None,
        "payment_id": payment.id if payment else None,
        "redemption_status": record.redemption_status,
        "redeemed_at": record.redeemed_at.isoformat() if record.redeemed_at else None,
        "redemption_note": record.redemption_note,
    }
    if payment:
        # 仅暴露给本人前端展示领取动作所需的状态，不包含 openid/密钥等敏感字段。
        result["payment_state"] = payment.transfer_state
        result["payment_fail_reason"] = payment.fail_reason
        result["payment_attempts"] = payment.attempts
    return result


def _public_record_with_fulfillment(session: Session, record: LotteryRecord, payment: CashPayment | None = None) -> dict:
    """Return a stable draw response, including credentials on idempotent retries."""
    result = public_record(record, payment)
    upgrade = session.exec(select(UpgradeOrder).where(UpgradeOrder.lottery_record_id == record.id)).first()
    if upgrade:
        result.update({"upgrade_redeem_code": upgrade.redeem_code, "upgrade_amount": str(upgrade.amount), "upgrade_status": upgrade.status, "upgrade_expires_at": upgrade.expires_at.isoformat()})
    return result


def validate_scan(session: Session, consumer: Consumer, raw_token: str) -> dict:
    try:
        if consumer.risk_status == "blocked":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="当前账号暂不可参与活动")
        code = session.exec(select(QrCode).where(QrCode.token_hash == token_hash(raw_token))).first()
        if not code:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="二维码不存在")
        if code.status == CodeStatus.DRAWN:
            record = session.exec(select(LotteryRecord).where(LotteryRecord.qrcode_id == code.id)).first()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": "该二维码已抽奖",
                    "code_no": code.code_no,
                    "activity_id": record.activity_id if record else None,
                    "code_status": code.status,
                },
            )
        if code.status != CodeStatus.ACTIVE:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该二维码尚不可参与活动")

        now = utc_now()
        activity = session.exec(
            select(Activity)
            .outerjoin(ActivityBatch, ActivityBatch.activity_id == Activity.id)
            .where(
                or_(Activity.all_batches.is_(True), ActivityBatch.batch_id == code.batch_id),
                Activity.status == "active",
                Activity.start_at <= now,
                Activity.end_at >= now,
            )
            .order_by(Activity.id.desc())
        ).first()
        if not activity:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="当前没有可参与的活动")
        distribution = _distribution_for_code(session, code)
        policy = _resolve_policy(session, activity.id, distribution)
        if not policy and not session.exec(select(ActivityPrize.id).where(ActivityPrize.activity_id == activity.id).limit(1)).first():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="活动奖池尚未配置")

        if not code.redemption_code_hash:
            lock_id = _acquire_draw_lock(code.id, consumer.id)
            session.add(ScanEvent(consumer_id=consumer.id, qrcode_id=code.id, result="accepted"))
            session.commit()
            return {
                "draw_ticket": make_token(str(consumer.id), "draw_ticket", 5, code_id=code.id, activity_id=activity.id, lock_id=lock_id),
                "expires_in": 300,
                "activity_id": activity.id,
                "lottery_policy_id": policy.id if policy else None,
                "lottery_policy_scope": policy.scope if policy else "default",
                "code_no": code.code_no,
                "code_status": code.status,
                "requires_redemption_code": False,
            }

        session.add(ScanEvent(consumer_id=consumer.id, qrcode_id=code.id, result="accepted"))
        session.commit()
        return {
            "verification_ticket": make_token(str(consumer.id), "redemption_verify", 5, code_id=code.id, activity_id=activity.id),
            "expires_in": 300,
            "activity_id": activity.id,
            "lottery_policy_id": policy.id if policy else None,
            "lottery_policy_scope": policy.scope if policy else "default",
            "code_no": code.code_no,
            "code_status": code.status,
            "requires_redemption_code": True,
        }
    except HTTPException:
        raise
    except (IntegrityError, OperationalError, DBAPIError) as exc:
        session.rollback()
        raise _handle_db_error(exc, "validate_scan") from exc
    except Exception as exc:
        session.rollback()
        logger.error(f"Unexpected error in validate_scan: {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="扫码验证失败，请稍后重试"
        ) from exc


def verify_redemption_code(session: Session, consumer: Consumer, verification_ticket: str, redemption_code: str) -> dict:
    payload = parse_token(verification_ticket, "redemption_verify")
    if int(payload["sub"]) != consumer.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="兑奖码核验资格不属于当前用户")
    if consumer.risk_status == "blocked":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="当前账号暂不可参与活动")
    code = session.exec(select(QrCode).where(QrCode.id == int(payload["code_id"])).with_for_update()).first()
    activity = session.get(Activity, int(payload["activity_id"]), with_for_update=True)
    if not code or not activity or code.status != CodeStatus.ACTIVE:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="当前二维码不可参与活动")
    now = utc_now()
    if activity.status != "active" or not (activity.start_at <= now <= activity.end_at):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="当前没有可参与的活动")
    if not code.redemption_code_hash:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该二维码缺少袋内兑奖码，请联系活动客服")
    enforce_rate_limit(f"rate:h5-redemption-code:qrcode:{code.id}", 20, 300)
    if not secrets.compare_digest(code.redemption_code_hash, token_hash(redemption_code)):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="袋内 4 位兑奖码不正确")
    lock_id = _acquire_draw_lock(code.id, consumer.id)
    return {"draw_ticket": make_token(str(consumer.id), "draw_ticket", 5, code_id=code.id, activity_id=activity.id, lock_id=lock_id), "expires_in": 300, "code_no": code.code_no}


def _pick_prize(session: Session, activity_id: int, consumer_id: int, policy: LotteryPolicy | None = None, win_probability_multiplier: Decimal = Decimal("1")) -> tuple[ActivityPrize | LotteryPolicyPrize | None, Prize | None]:
    pools = session.exec(select(LotteryPolicyPrize).where(LotteryPolicyPrize.policy_id == policy.id).with_for_update()).all() if policy else session.exec(select(ActivityPrize).where(ActivityPrize.activity_id == activity_id).with_for_update()).all()
    today = utc_now().date().isoformat()
    candidates: list[tuple[ActivityPrize | LotteryPolicyPrize, Prize]] = []
    none_prize: tuple[ActivityPrize | LotteryPolicyPrize, Prize] | None = None
    for pool in pools:
        prize = session.get(Prize, pool.prize_id, with_for_update=True)
        if not prize or prize.status != "active":
            continue
        if pool.stat_date != today:
            pool.stat_date, pool.issued_today = today, 0
        if prize.type == PrizeType.NONE:
            none_prize = (pool, prize)
            continue
        # Legacy prize types are intentionally excluded from all new draws.
        if prize.type not in {PrizeType.CASH, PrizeType.UPGRADE}:
            continue
        if prize.issued_stock >= prize.total_stock or (pool.daily_limit and pool.issued_today >= pool.daily_limit):
            continue
        issued_to_consumer = session.exec(
            select(func.count(LotteryRecord.id)).where(
                LotteryRecord.consumer_id == consumer_id,
                LotteryRecord.prize_id == prize.id,
            )
        ).one()
        if issued_to_consumer >= prize.per_user_limit:
            continue
        candidates.append((pool, prize))

    total = sum(float(pool.probability) for pool, _ in candidates)
    if total <= 0:
        return none_prize if none_prize else (None, None)
    # A residual probability is intentionally left for the configured "谢谢参与" prize.
    pointer, cursor = secrets.SystemRandom().random(), 0.0
    for candidate in candidates:
        cursor += float(candidate[0].probability) * float(win_probability_multiplier)
        if pointer <= cursor:
            return candidate
    return none_prize if none_prize else (None, None)


def draw(session: Session, consumer: Consumer, draw_ticket: str, idempotency_key: str, client_ip: str | None = None, client_location: str | None = None) -> dict:
    try:
        payload = parse_token(draw_ticket, "draw_ticket")
        if int(payload["sub"]) != consumer.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="抽奖资格不属于当前用户")
        if consumer.risk_status == "blocked":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="当前账号暂不可参与活动")

        existing = session.exec(
            select(LotteryRecord).where(LotteryRecord.consumer_id == consumer.id, LotteryRecord.idempotency_key == idempotency_key)
        ).first()
        if existing:
            payment = session.exec(select(CashPayment).where(CashPayment.lottery_record_id == existing.id)).first()
            return _public_record_with_fulfillment(session, existing, payment)

        code_id = int(payload["code_id"])
        lock_id = str(payload.get("lock_id", ""))
        code_record = session.exec(
            select(LotteryRecord).where(LotteryRecord.consumer_id == consumer.id, LotteryRecord.qrcode_id == code_id)
        ).first()
        if code_record:
            payment = session.exec(select(CashPayment).where(CashPayment.lottery_record_id == code_record.id)).first()
            return _public_record_with_fulfillment(session, code_record, payment)
        lock_matches = lock_id and redis_client.get(_draw_lock_key(code_id)) == f"{consumer.id}:{lock_id}"
        if not lock_matches:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="抽奖凭证已失效，请重新扫码核验")

        code = session.exec(select(QrCode).where(QrCode.id == code_id).with_for_update()).first()
        activity = session.get(Activity, int(payload["activity_id"]), with_for_update=True)
        if not code or not activity:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="抽奖资格已失效")
        now = utc_now()
        if code.status == CodeStatus.DRAWN:
            record = session.exec(select(LotteryRecord).where(LotteryRecord.qrcode_id == code.id)).first()
            if record:
                payment = session.exec(select(CashPayment).where(CashPayment.lottery_record_id == record.id)).first()
                return _public_record_with_fulfillment(session, record, payment)
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该二维码已抽奖")
        if code.status != CodeStatus.ACTIVE or activity.status != "active" or not (activity.start_at <= now <= activity.end_at):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="当前无法抽奖")

        distribution = _distribution_for_code(session, code)
        policy = _resolve_policy(session, activity.id, distribution, lock=True)
        pool, prize = _pick_prize(
            session,
            activity.id,
            consumer.id,
            policy,
            consumer.win_probability_multiplier,
        )
        if prize and prize.type != PrizeType.NONE:
            prize.issued_stock += 1
            assert pool is not None
            pool.issued_today += 1

        resolved_location = resolve_location(client_location)
        record = LotteryRecord(
            consumer_id=consumer.id,
            qrcode_id=code.id,
            activity_id=activity.id,
            distribution_id=distribution.id if distribution else None,
            lottery_policy_id=policy.id if policy else None,
            lottery_policy_version=policy.version if policy else None,
            lottery_policy_scope=policy.scope if policy else "default",
            prize_id=prize.id if prize and prize.type != PrizeType.NONE else None,
            prize_name=prize.name if prize else "谢谢参与",
            prize_type=prize.type if prize else PrizeType.NONE,
            amount=prize.cash_amount if prize else Decimal("0.00"),
            redemption_status=RedemptionStatus.PENDING if prize and prize.type in {PrizeType.CASH, PrizeType.UPGRADE} else RedemptionStatus.NOT_REQUIRED,
            client_ip=client_ip,
            client_location=client_location,
            client_province=resolved_location.province if resolved_location else None,
            client_city=resolved_location.city if resolved_location else None,
            client_district=resolved_location.district if resolved_location else None,
            client_address=resolved_location.address if resolved_location else None,
            idempotency_key=idempotency_key,
        )
        code.status, code.drawn_user_id, code.drawn_at = CodeStatus.DRAWN, consumer.id, now
        session.add(record)
        session.flush()

        payment = None
        if prize and prize.type == PrizeType.CASH and prize.cash_amount > 0:
            payment = CashPayment(
                lottery_record_id=record.id,
                merchant_order_no=f"CASH{record.id:012d}",
                amount=prize.cash_amount,
                status=PaymentStatus.PENDING,
            )
            session.add(payment)
        elif prize and prize.type == PrizeType.UPGRADE:
            session.add(UpgradeOrder(
                lottery_record_id=record.id,
                consumer_id=consumer.id,
                prize_id=prize.id,
                redeem_code=f"UPG-{secrets.token_urlsafe(12)}",
                amount=prize.upgrade_price,
                payment_method="manual",
                expires_at=now + timedelta(days=30),
            ))
        session.commit()
        session.refresh(record)
        try:
            return _public_record_with_fulfillment(session, record, payment)
        finally:
            _release_draw_lock(code_id, consumer.id, lock_id)
    except HTTPException:
        raise
    except (IntegrityError, OperationalError, DBAPIError) as exc:
        session.rollback()
        raise _handle_db_error(exc, "draw") from exc
    except Exception as exc:
        session.rollback()
        logger.error(f"Unexpected error in draw: {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="抽奖失败，请稍后重试"
        ) from exc
