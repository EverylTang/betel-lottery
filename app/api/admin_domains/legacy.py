import hashlib
import csv
import io
import secrets
from pathlib import Path
from datetime import datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import case, func, or_
from sqlmodel import select

from app.core.config import get_settings
from app.core.http import client_ip_or_unknown
from app.cache import enforce_rate_limit, redis_client
from app.deps import SessionDep, current_admin
from app.models import Activity, ActivityBatch, ActivityPrize, AdminAuditLog, AdminUser, CashPayment, CodeDistribution, CodeStatus, Consumer, CouponCode, Dealer, DealerAccount, DealerRedemption, LotteryPolicy, LotteryPolicyPrize, LotteryRecord, MVP_PRIZE_TYPES, Prize, PrizeType, QrCode, QrCodeBatch, RedemptionStatus, ReplenishmentTask, ScanEvent, Store, UpgradeOrder, utc_now
from app.schemas import ActivityCreate, ActivityPrizeCreate, ActivityPrizeUpdate, ActivityStatusUpdate, ActivityUpdate, AdminUserCreate, AdminUserUpdate, BatchCreate, CashPaymentUpdate, ConsumerProbabilityUpdate, ConsumerRiskUpdate, CouponGenerateRequest, DealerAccountCreate, DealerCreate, DealerPasswordUpdate, DistributionCreate, DrawNumberLookup, LoginRequest, LotteryPolicyCreate, LotteryPolicyStatusUpdate, PrizeCreate, PrizeStatusUpdate, PrizeUpdate, QrcodeActivateRequest, RedemptionBatchUpdate, RedemptionUpdate, ReplenishmentCreate, ReplenishmentUpdate, StoreCreate, StorePinUpdate, StoreStatusUpdate
from app.security import hash_password, make_token, verify_password
from app.security import encrypt_qr_token
from app.services.qrcode_export import export_code_csv, export_print_package
from app.services.activity_rules import generate_activity_rules

router = APIRouter(prefix="/api/admin", tags=["admin"])
settings = get_settings()
UPLOAD_DIRECTORY = Path(__file__).resolve().parents[2] / "uploads"
ALLOWED_IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}
MAX_IMAGE_SIZE = 2 * 1024 * 1024


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _audit(session, admin: AdminUser, action: str, target_type: str, target_id: int | None = None, detail: str = "") -> None:
    session.add(AdminAuditLog(admin_id=admin.id, action=action, target_type=target_type, target_id=target_id, detail=detail))


def _require_super_admin(admin: AdminUser) -> None:
    if admin.role != "super_admin":
        raise HTTPException(status_code=403, detail="仅超级管理员可执行此操作")


def _require_role(admin: AdminUser, *roles: str) -> None:
    if admin.role not in {"super_admin", *roles}:
        raise HTTPException(status_code=403, detail="当前账号没有此操作权限")


def _public_admin_user(user: AdminUser) -> dict:
    return {"id": user.id, "username": user.username, "role": user.role, "is_active": user.is_active, "created_at": user.created_at}


@router.post("/uploads/images", status_code=status.HTTP_201_CREATED)
async def upload_image(file: UploadFile = File(...), admin: AdminUser = Depends(current_admin)):
    """Store a prize asset locally and expose only its generated public path."""
    extension = ALLOWED_IMAGE_TYPES.get(file.content_type or "")
    if not extension:
        raise HTTPException(status_code=422, detail="仅支持 JPG、PNG、WebP 或 GIF 图片")
    content = await file.read(MAX_IMAGE_SIZE + 1)
    if not content or len(content) > MAX_IMAGE_SIZE:
        raise HTTPException(status_code=422, detail="图片不能为空且不能超过 2MB")
    # MIME headers can be forged; validate that Pillow can parse the uploaded image.
    try:
        from PIL import Image
        import io as binary_io
        with Image.open(binary_io.BytesIO(content)) as image:
            image.verify()
    except Exception as exc:
        raise HTTPException(status_code=422, detail="上传内容不是有效图片") from exc
    UPLOAD_DIRECTORY.mkdir(parents=True, exist_ok=True)
    filename = f"prize-{datetime.now():%Y%m%d%H%M%S}-{secrets.token_hex(8)}{extension}"
    (UPLOAD_DIRECTORY / filename).write_bytes(content)
    return {"url": f"/uploads/{filename}", "filename": filename, "uploaded_by": admin.username}


@router.post("/auth/login")
def login(payload: LoginRequest, request: Request, session: SessionDep):
    client_ip = client_ip_or_unknown(request)
    enforce_rate_limit(f"rate:admin-login:{client_ip}:{payload.username.lower()}", 10, 300)
    admin = session.exec(select(AdminUser).where(AdminUser.username == payload.username)).first()
    if not admin or not admin.is_active or not verify_password(payload.password, admin.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号或密码错误")
    return {"access_token": make_token(str(admin.id), "admin", 480, role=admin.role), "token_type": "bearer"}


@router.get("/users")
def list_admin_users(session: SessionDep, admin: AdminUser = Depends(current_admin)):
    _require_super_admin(admin)
    return [_public_admin_user(user) for user in session.exec(select(AdminUser).order_by(AdminUser.id.desc())).all()]


@router.post("/users", status_code=status.HTTP_201_CREATED)
def create_admin_user(payload: AdminUserCreate, session: SessionDep, admin: AdminUser = Depends(current_admin)):
    _require_super_admin(admin)
    if session.exec(select(AdminUser).where(AdminUser.username == payload.username)).first():
        raise HTTPException(status_code=409, detail="账号已存在")
    user = AdminUser(username=payload.username, password_hash=hash_password(payload.password), role=payload.role)
    session.add(user)
    session.flush()
    _audit(session, admin, "create", "admin_user", user.id, user.username)
    session.commit()
    session.refresh(user)
    return _public_admin_user(user)


@router.patch("/users/{user_id}")
def update_admin_user(user_id: int, payload: AdminUserUpdate, session: SessionDep, admin: AdminUser = Depends(current_admin)):
    _require_super_admin(admin)
    user = session.get(AdminUser, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="账号不存在")
    if user.id == admin.id and not payload.is_active:
        raise HTTPException(status_code=422, detail="不能停用当前账号")
    user.is_active = payload.is_active
    session.add(user)
    _audit(session, admin, "update", "admin_user", user.id, f"is_active={payload.is_active}")
    session.commit()
    return _public_admin_user(user)


@router.get("/audit-logs")
def list_audit_logs(session: SessionDep, admin: AdminUser = Depends(current_admin), page: int = Query(default=1, ge=1), page_size: int = Query(default=50, ge=1, le=100)):
    _require_super_admin(admin)
    total = session.exec(select(func.count(AdminAuditLog.id))).one()
    rows = session.exec(select(AdminAuditLog, AdminUser.username).join(AdminUser, AdminUser.id == AdminAuditLog.admin_id).order_by(AdminAuditLog.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [{"id": log.id, "username": username, "action": log.action, "target_type": log.target_type, "target_id": log.target_id, "detail": log.detail, "created_at": log.created_at} for log, username in rows], "total": total}


@router.get("/dealers")
def list_dealers(session: SessionDep, _admin: AdminUser = Depends(current_admin)):
    return session.exec(select(Dealer).order_by(Dealer.id.desc())).all()


@router.get("/stores")
def list_stores(session: SessionDep, _admin: AdminUser = Depends(current_admin), dealer_id: int | None = None):
    query = select(Store).order_by(Store.id.desc())
    if dealer_id is not None:
        query = query.where(Store.dealer_id == dealer_id)
    return [store.model_dump(exclude={"redeemer_pin_hash"}) for store in session.exec(query).all()]


@router.post("/stores", status_code=status.HTTP_201_CREATED)
def create_store(payload: StoreCreate, session: SessionDep, admin: AdminUser = Depends(current_admin)):
    _require_role(admin, "operator")
    dealer = session.get(Dealer, payload.dealer_id)
    if not dealer:
        raise HTTPException(status_code=404, detail="经销商不存在")
    if dealer.status != "active":
        raise HTTPException(status_code=422, detail="冻结经销商不能创建门店")
    if session.exec(select(Store).where(Store.store_no == payload.store_no)).first():
        raise HTTPException(status_code=409, detail="门店编号已存在")
    store = Store(
        store_no=payload.store_no,
        name=payload.name,
        contact_name=payload.contact_name,
        phone=payload.phone,
        dealer_id=payload.dealer_id,
        redeemer_pin_hash=hash_password(payload.redeemer_pin),
    )
    session.add(store)
    session.flush()
    _audit(session, admin, "create", "store", store.id, store.store_no)
    session.commit()
    session.refresh(store)
    return store.model_dump(exclude={"redeemer_pin_hash"})


@router.patch("/stores/{store_id}/status")
def update_store_status(store_id: int, payload: StoreStatusUpdate, session: SessionDep, admin: AdminUser = Depends(current_admin)):
    _require_role(admin, "operator")
    store = session.get(Store, store_id)
    if not store:
        raise HTTPException(status_code=404, detail="门店不存在")
    store.status = payload.status
    session.add(store)
    _audit(session, admin, "status", "store", store.id, payload.status)
    session.commit()
    return store.model_dump(exclude={"redeemer_pin_hash"})


@router.patch("/stores/{store_id}/pin")
def update_store_pin(store_id: int, payload: StorePinUpdate, session: SessionDep, admin: AdminUser = Depends(current_admin)):
    _require_role(admin, "operator")
    store = session.get(Store, store_id)
    if not store:
        raise HTTPException(status_code=404, detail="门店不存在")
    store.redeemer_pin_hash = hash_password(payload.redeemer_pin)
    session.add(store)
    _audit(session, admin, "reset_pin", "store", store.id)
    session.commit()
    return store.model_dump(exclude={"redeemer_pin_hash"})


@router.get("/replenishments")
def list_replenishments(
    session: SessionDep,
    _admin: AdminUser = Depends(current_admin),
    status_filter: str | None = Query(default=None, alias="status", pattern="^(pending|partial|fulfilled|cancelled)$"),
    dealer_id: int | None = None,
):
    raise HTTPException(status_code=status.HTTP_410_GONE, detail="MVP 不包含补货任务")


@router.post("/replenishments", status_code=status.HTTP_201_CREATED)
def create_replenishment(payload: ReplenishmentCreate, session: SessionDep, admin: AdminUser = Depends(current_admin)):
    raise HTTPException(status_code=status.HTTP_410_GONE, detail="MVP 不包含补货任务")


@router.patch("/replenishments/{task_id}")
def update_replenishment(task_id: int, payload: ReplenishmentUpdate, session: SessionDep, admin: AdminUser = Depends(current_admin)):
    raise HTTPException(status_code=status.HTTP_410_GONE, detail="MVP 不包含补货任务")


@router.post("/replenishments/{task_id}/cancel")
def cancel_replenishment(task_id: int, session: SessionDep, admin: AdminUser = Depends(current_admin)):
    raise HTTPException(status_code=status.HTTP_410_GONE, detail="MVP 不包含补货任务")


@router.get("/consumers")
def list_consumers(
    session: SessionDep,
    _admin: AdminUser = Depends(current_admin),
    keyword: str | None = Query(default=None, max_length=64),
):
    statement = (
        select(
            Consumer,
            func.count(LotteryRecord.id).label("draw_count"),
            func.coalesce(
                func.sum(case((LotteryRecord.prize_type != PrizeType.NONE, 1), else_=0)),
                0,
            ).label("win_count"),
            func.max(LotteryRecord.created_at).label("last_draw_at"),
        )
        .outerjoin(LotteryRecord, LotteryRecord.consumer_id == Consumer.id)
        .group_by(Consumer.id)
        .order_by(Consumer.id.desc())
    )
    if keyword:
        pattern = f"%{keyword.strip()}%"
        statement = statement.where(Consumer.openid.like(pattern) | Consumer.nickname.like(pattern))
    return [
        {
            "id": consumer.id,
            "openid": consumer.openid,
            "nickname": consumer.nickname or "微信用户",
            "risk_status": consumer.risk_status,
            "risk_note": consumer.risk_note,
            "win_probability_multiplier": str(consumer.win_probability_multiplier),
            "anonymized_at": consumer.anonymized_at,
            "created_at": consumer.created_at,
            "draw_count": draw_count,
            "win_count": int(win_count or 0),
            "last_draw_at": last_draw_at,
        }
        for consumer, draw_count, win_count, last_draw_at in session.exec(statement).all()
    ]


@router.get("/consumers/{consumer_id}")
def consumer_detail(consumer_id: int, session: SessionDep, _admin: AdminUser = Depends(current_admin)):
    consumer = session.get(Consumer, consumer_id)
    if not consumer:
        raise HTTPException(status_code=404, detail="用户不存在")
    rows = session.exec(
        select(LotteryRecord, QrCode, Activity)
        .join(QrCode, QrCode.id == LotteryRecord.qrcode_id)
        .join(Activity, Activity.id == LotteryRecord.activity_id)
        .where(LotteryRecord.consumer_id == consumer_id)
        .order_by(LotteryRecord.id.desc())
    ).all()
    return {"consumer": consumer, "records": [{"id": record.id, "code_no": code.code_no, "activity_name": activity.name, "prize_name": record.prize_name, "prize_type": record.prize_type, "redemption_status": record.redemption_status, "client_ip": record.client_ip, "client_location": record.client_location, "client_province": record.client_province, "client_city": record.client_city, "client_district": record.client_district, "client_address": record.client_address, "created_at": record.created_at} for record, code, activity in rows]}


@router.patch("/consumers/{consumer_id}/risk")
def update_consumer_risk(consumer_id: int, payload: ConsumerRiskUpdate, session: SessionDep, admin: AdminUser = Depends(current_admin)):
    consumer = session.get(Consumer, consumer_id)
    if not consumer:
        raise HTTPException(status_code=404, detail="用户不存在")
    consumer.risk_status, consumer.risk_note = payload.risk_status, payload.risk_note
    session.add(consumer)
    _audit(session, admin, "update_risk", "consumer", consumer.id, f"{payload.risk_status}: {payload.risk_note}")
    session.commit()
    return consumer


@router.patch("/consumers/{consumer_id}/win-probability")
def update_consumer_win_probability(consumer_id: int, payload: ConsumerProbabilityUpdate, session: SessionDep, admin: AdminUser = Depends(current_admin)):
    consumer = session.get(Consumer, consumer_id)
    if not consumer:
        raise HTTPException(status_code=404, detail="用户不存在")
    consumer.win_probability_multiplier = payload.win_probability_multiplier
    session.add(consumer)
    _audit(session, admin, "update_win_probability", "consumer", consumer.id, f"multiplier={payload.win_probability_multiplier}")
    session.commit()
    return {"id": consumer.id, "win_probability_multiplier": str(consumer.win_probability_multiplier)}


@router.post("/consumers/{consumer_id}/anonymize")
def anonymize_consumer(consumer_id: int, session: SessionDep, admin: AdminUser = Depends(current_admin)):
    """Fulfil a personal-data deletion request without breaking reward accounting."""
    _require_super_admin(admin)
    consumer = session.get(Consumer, consumer_id)
    if not consumer:
        raise HTTPException(status_code=404, detail="用户不存在")
    if consumer.anonymized_at:
        return {"status": "already_anonymized"}
    pending = session.exec(
        select(func.count(LotteryRecord.id)).where(
            LotteryRecord.consumer_id == consumer.id,
            LotteryRecord.redemption_status == RedemptionStatus.PENDING,
        )
    ).one()
    if pending:
        raise HTTPException(status_code=422, detail="用户仍有待兑奖奖品，完成履约后才可匿名化")
    records = session.exec(select(LotteryRecord).where(LotteryRecord.consumer_id == consumer.id)).all()
    for record in records:
        record.client_ip = None
        record.client_location = None
        record.client_province = None
        record.client_city = None
        record.client_district = None
        record.client_address = None
        session.add(record)
    consumer.openid = f"anonymized-{consumer.id}-{secrets.token_hex(12)}"
    consumer.nickname = None
    consumer.risk_status = "blocked"
    consumer.risk_note = "已按数据删除请求匿名化"
    consumer.anonymized_at = utc_now()
    session.add(consumer)
    _audit(session, admin, "anonymize", "consumer", consumer.id, f"records={len(records)}")
    session.commit()
    return {"status": "anonymized", "record_count": len(records)}


@router.post("/dealers", status_code=status.HTTP_201_CREATED)
def create_dealer(payload: DealerCreate, session: SessionDep, _admin: AdminUser = Depends(current_admin)):
    if session.exec(select(Dealer).where(Dealer.phone == payload.phone)).first():
        raise HTTPException(status_code=409, detail="手机号已存在")
    serial = (session.exec(select(Dealer).order_by(Dealer.id.desc())).first().id or 0) + 1 if session.exec(select(Dealer)).first() else 1
    dealer = Dealer(dealer_no=f"DL{serial:06d}", **payload.model_dump())
    session.add(dealer)
    session.commit()
    session.refresh(dealer)
    return dealer


@router.post("/dealer-accounts", status_code=status.HTTP_201_CREATED)
def create_dealer_account(payload: DealerAccountCreate, session: SessionDep, admin: AdminUser = Depends(current_admin)):
    _require_super_admin(admin)
    dealer = session.get(Dealer, payload.dealer_id)
    if not dealer:
        raise HTTPException(status_code=404, detail="经销商不存在")
    if session.exec(select(DealerAccount).where((DealerAccount.dealer_id == payload.dealer_id) | (DealerAccount.username == dealer.phone))).first():
        raise HTTPException(status_code=409, detail="经销商账号或账号名已存在")
    account = DealerAccount(dealer_id=payload.dealer_id, username=dealer.phone, password_hash=hash_password(payload.password))
    session.add(account)
    session.flush()
    _audit(session, admin, "create", "dealer_account", account.id, account.username)
    session.commit()
    return {"id": account.id, "dealer_id": account.dealer_id, "username": account.username}


@router.get("/dealer-accounts")
def list_dealer_accounts(session: SessionDep, _admin: AdminUser = Depends(current_admin)):
    return session.exec(select(DealerAccount).order_by(DealerAccount.id.desc())).all()


@router.patch("/dealer-accounts/{account_id}/password")
def reset_dealer_account_password(account_id: int, payload: DealerPasswordUpdate, session: SessionDep, admin: AdminUser = Depends(current_admin)):
    _require_super_admin(admin)
    account = session.get(DealerAccount, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="经销商账号不存在")
    account.password_hash = hash_password(payload.password)
    session.add(account)
    _audit(session, admin, "reset_password", "dealer_account", account.id, account.username)
    session.commit()
    return {"id": account.id, "dealer_id": account.dealer_id, "username": account.username, "message": "密码已重置"}


@router.post("/dealers/{dealer_id}/freeze")
def freeze_dealer(dealer_id: int, session: SessionDep, admin: AdminUser = Depends(current_admin)):
    _require_super_admin(admin)
    dealer = session.get(Dealer, dealer_id)
    if not dealer:
        raise HTTPException(status_code=404, detail="经销商不存在")
    dealer.status = "frozen"
    session.add(dealer)
    accounts = session.exec(
        select(DealerAccount).where(
            DealerAccount.dealer_id == dealer_id,
            DealerAccount.is_active.is_(True),
        )
    ).all()
    for account in accounts:
        account.is_active = False
        session.add(account)
    _audit(session, admin, "freeze", "dealer", dealer.id, f"disabled_accounts={len(accounts)}")
    session.commit()
    return {"id": dealer.id, "status": dealer.status, "note": "冻结不影响二维码、活动或抽奖资格"}


@router.post("/dealers/{dealer_id}/unfreeze")
def unfreeze_dealer(dealer_id: int, session: SessionDep, admin: AdminUser = Depends(current_admin)):
    _require_super_admin(admin)
    dealer = session.get(Dealer, dealer_id)
    if not dealer:
        raise HTTPException(status_code=404, detail="经销商不存在")
    dealer.status = "active"
    session.add(dealer)
    accounts = session.exec(select(DealerAccount).where(DealerAccount.dealer_id == dealer_id, DealerAccount.is_active.is_(False))).all()
    for account in accounts:
        account.is_active = True
        session.add(account)
    _audit(session, admin, "unfreeze", "dealer", dealer.id, f"enabled_accounts={len(accounts)}")
    session.commit()
    return {"id": dealer.id, "status": dealer.status, "note": "经销商和其核销账号已恢复"}


@router.delete("/dealers/{dealer_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_dealer(dealer_id: int, session: SessionDep, admin: AdminUser = Depends(current_admin)):
    _require_super_admin(admin)
    dealer = session.get(Dealer, dealer_id)
    if not dealer:
        raise HTTPException(status_code=404, detail="经销商不存在")
    if dealer.status == "active":
        raise HTTPException(status_code=422, detail="请先冻结经销商后再删除")
    references = (
        session.exec(select(CodeDistribution.id).where(CodeDistribution.dealer_id == dealer_id).limit(1)).first()
        or session.exec(select(Store.id).where(Store.dealer_id == dealer_id).limit(1)).first()
        or session.exec(select(DealerAccount.id).where(DealerAccount.dealer_id == dealer_id).limit(1)).first()
        or session.exec(select(ReplenishmentTask.id).where(ReplenishmentTask.dealer_id == dealer_id).limit(1)).first()
        or session.exec(select(LotteryPolicy.id).where(LotteryPolicy.dealer_id == dealer_id).limit(1)).first()
    )
    if references:
        raise HTTPException(status_code=422, detail="经销商已有业务关联记录，不能删除")
    session.delete(dealer)
    _audit(session, admin, "delete", "dealer", dealer_id)
    session.commit()

@router.get("/qrcode/batches")
def list_batches(session: SessionDep, _admin: AdminUser = Depends(current_admin)):
    batches = session.exec(select(QrCodeBatch).order_by(QrCodeBatch.id.desc())).all()
    now = utc_now()
    result = []
    for batch in batches:
        inactive_count = session.exec(
            select(func.count(QrCode.id)).where(
                QrCode.batch_id == batch.id,
                QrCode.status == CodeStatus.INACTIVE,
            )
        ).one()
        code_count = session.exec(select(func.count(QrCode.id)).where(QrCode.batch_id == batch.id)).one()
        activated_count = code_count - inactive_count
        activation_status = "inactive" if activated_count == 0 else "active" if inactive_count == 0 else "partial"
        can_activate = bool(session.exec(
            select(Activity.id)
            .outerjoin(ActivityBatch, ActivityBatch.activity_id == Activity.id)
            .where(
                or_(Activity.all_batches.is_(True), ActivityBatch.batch_id == batch.id),
                Activity.status == "active",
                Activity.start_at <= now,
                Activity.end_at >= now,
            )
            .limit(1)
        ).first())
        result.append({
            **batch.model_dump(),
            "activation_status": activation_status,
            "inactive_count": inactive_count,
            "activated_count": activated_count,
            "can_activate": can_activate,
            "activation_block_reason": None if can_activate else "当前没有处于有效期内的启用活动",
        })
    return result


@router.post("/qrcode/batches", status_code=status.HTTP_201_CREATED)
def create_batch(payload: BatchCreate, session: SessionDep, _admin: AdminUser = Depends(current_admin)):
    if session.exec(select(QrCodeBatch).where(QrCodeBatch.batch_no == payload.batch_no)).first():
        raise HTTPException(status_code=409, detail="批次号已存在")
    try:
        batch = QrCodeBatch(**payload.model_dump())
        session.add(batch)
        session.flush()
        codes = []
        for index in range(1, payload.total_count + 1):
            token = secrets.token_urlsafe(24)
            redemption_code = f"{secrets.randbelow(10_000):04d}"
            codes.append(QrCode(code_no=f"{payload.prefix}{index:08d}", token_hash=_hash_token(token), token_ciphertext=encrypt_qr_token(token), redemption_code_hash=_hash_token(redemption_code), redemption_code_ciphertext=encrypt_qr_token(redemption_code), batch_id=batch.id, status=CodeStatus.INACTIVE))
        session.add_all(codes)
        session.commit()
    except Exception:
        session.rollback()
        raise
    session.refresh(batch)
    return {"batch": batch, "generated_count": len(codes), "sample_scan_url": f"{settings.h5_base_url}?t=<random-token>"}


@router.post("/qrcode/batches/{batch_id}/activate")
def activate_batch(batch_id: int, session: SessionDep, _admin: AdminUser = Depends(current_admin)):
    batch = session.get(QrCodeBatch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")
    now = utc_now()
    effective_activity = session.exec(
        select(Activity.id)
        .outerjoin(ActivityBatch, ActivityBatch.activity_id == Activity.id)
        .where(
            or_(Activity.all_batches.is_(True), ActivityBatch.batch_id == batch_id),
            Activity.status == "active",
            Activity.start_at <= now,
            Activity.end_at >= now,
        )
        .limit(1)
    ).first()
    if not effective_activity:
        raise HTTPException(status_code=409, detail="当前没有生效活动，不能激活二维码批次")
    codes = session.exec(select(QrCode).where(QrCode.batch_id == batch_id, QrCode.status == CodeStatus.INACTIVE)).all()
    for code in codes:
        code.status = CodeStatus.ACTIVE
        session.add(code)
    batch.status = "active"
    session.add(batch)
    session.commit()
    return {"batch_id": batch_id, "activated_count": len(codes)}


@router.post("/qrcode/batches/{batch_id}/activate-selected")
def activate_selected_codes(batch_id: int, payload: QrcodeActivateRequest, session: SessionDep, admin: AdminUser = Depends(current_admin)):
    batch = session.get(QrCodeBatch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")
    now = utc_now()
    effective_activity = session.exec(
        select(Activity.id)
        .outerjoin(ActivityBatch, ActivityBatch.activity_id == Activity.id)
        .where(
            or_(Activity.all_batches.is_(True), ActivityBatch.batch_id == batch_id),
            Activity.status == "active",
            Activity.start_at <= now,
            Activity.end_at >= now,
        )
        .limit(1)
    ).first()
    if not effective_activity:
        raise HTTPException(status_code=409, detail="当前没有生效活动，不能激活抽奖号码")
    codes = session.exec(
        select(QrCode).where(
            QrCode.batch_id == batch_id,
            QrCode.id.in_(set(payload.code_ids)),
            QrCode.status == CodeStatus.INACTIVE,
        )
    ).all()
    for code in codes:
        code.status = CodeStatus.ACTIVE
        session.add(code)
        _audit(session, admin, "activate", "qrcode", code.id, f"code_no={code.code_no}")
    if codes:
        batch.status = "active"
        session.add(batch)
    session.commit()
    return {"batch_id": batch_id, "activated_count": len(codes)}


@router.get("/qrcode/batches/{batch_id}/codes")
def list_batch_codes(
    batch_id: int,
    session: SessionDep,
    _admin: AdminUser = Depends(current_admin),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    code_status: CodeStatus | None = Query(default=None),
):
    if not session.get(QrCodeBatch, batch_id):
        raise HTTPException(status_code=404, detail="批次不存在")
    conditions = [QrCode.batch_id == batch_id]
    if code_status:
        conditions.append(QrCode.status == code_status)
    base_query = select(QrCode).where(*conditions)
    total = session.exec(select(func.count(QrCode.id)).where(*conditions)).one()
    rows = session.exec(base_query.order_by(QrCode.id).offset((page - 1) * page_size).limit(page_size)).all()
    return {
        "items": [{**code.model_dump(exclude={"token_hash", "token_ciphertext", "redemption_code_hash", "redemption_code_ciphertext"}), "has_redemption_code": bool(code.redemption_code_hash)} for code in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.patch("/qrcode/{code_id}/void")
def void_qrcode(code_id: int, session: SessionDep, admin: AdminUser = Depends(current_admin)):
    code = session.get(QrCode, code_id)
    if not code:
        raise HTTPException(status_code=404, detail="抽奖号码不存在")
    if code.status == CodeStatus.DRAWN:
        raise HTTPException(status_code=422, detail="已开奖号码不可作废")
    if code.status == CodeStatus.VOID:
        return {"id": code.id, "status": code.status, "message": "该号码已作废"}
    code.status = CodeStatus.VOID
    session.add(code)
    _audit(session, admin, "void", "qrcode", code.id, f"code_no={code.code_no}")
    session.commit()
    redis_client.delete(f"lottery:draw-lock:{code.id}")
    return {"id": code.id, "status": code.status, "message": "抽奖号码已作废"}


@router.get("/qrcode/batches/{batch_id}/export")
def export_batch(batch_id: int, session: SessionDep, _admin: AdminUser = Depends(current_admin)):
    filename, content = export_print_package(session, batch_id)
    return StreamingResponse(iter([content]), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/qrcode/batches/{batch_id}/export.csv")
def export_batch_csv(batch_id: int, session: SessionDep, _admin: AdminUser = Depends(current_admin)):
    filename, content = export_code_csv(session, batch_id)
    return StreamingResponse(iter([content]), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/qrcode/distributions", status_code=status.HTTP_201_CREATED)
def create_distribution(payload: DistributionCreate, session: SessionDep, _admin: AdminUser = Depends(current_admin)):
    dealer, batch = session.get(Dealer, payload.dealer_id), session.get(QrCodeBatch, payload.batch_id)
    if not dealer or not batch:
        raise HTTPException(status_code=404, detail="经销商或批次不存在")
    if payload.start_code_no and payload.start_code_no.strip():
        start_code = session.exec(select(QrCode).where(QrCode.batch_id == batch.id, QrCode.code_no == payload.start_code_no.strip())).first()
        end_code = session.exec(select(QrCode).where(QrCode.batch_id == batch.id, QrCode.code_no == payload.end_code_no.strip())).first()
    else:
        start_code = session.exec(select(QrCode).where(QrCode.batch_id == batch.id).order_by(QrCode.id.asc())).first()
        end_code = session.exec(select(QrCode).where(QrCode.batch_id == batch.id).order_by(QrCode.id.desc())).first()
    if not start_code or not end_code or start_code.id > end_code.id:
        raise HTTPException(status_code=422, detail="起止印刷号码不存在、未属于该批次、批次没有号码或顺序不正确")
    codes = session.exec(select(QrCode).where(QrCode.batch_id == batch.id, QrCode.id >= start_code.id, QrCode.id <= end_code.id)).all()
    expected = end_code.id - start_code.id + 1
    if len(codes) != expected:
        raise HTTPException(status_code=422, detail="码段不连续或不属于该批次")
    overlap = session.exec(
        select(CodeDistribution.id).where(
            CodeDistribution.batch_id == batch.id,
            CodeDistribution.status == "effective",
            CodeDistribution.start_code_id <= end_code.id,
            CodeDistribution.end_code_id >= start_code.id,
        ).limit(1)
    ).first()
    if overlap:
        raise HTTPException(status_code=409, detail="该印刷号码段已绑定经销商，不能重复登记")
    distribution = CodeDistribution(
        distribution_no=f"DIST{utc_now():%Y%m%d%H%M%S}{secrets.randbelow(1000):03d}",
        batch_id=batch.id,
        dealer_id=dealer.id,
        start_code_id=start_code.id,
        end_code_id=end_code.id,
        quantity=expected,
    )
    session.add(distribution)
    session.commit()
    session.refresh(distribution)
    return distribution


@router.get("/qrcode/distributions")
def list_distributions(session: SessionDep, _admin: AdminUser = Depends(current_admin)):
    rows = session.exec(
        select(CodeDistribution, Dealer, QrCodeBatch)
        .join(Dealer, Dealer.id == CodeDistribution.dealer_id)
        .join(QrCodeBatch, QrCodeBatch.id == CodeDistribution.batch_id)
        .order_by(CodeDistribution.id.desc())
    ).all()
    result = []
    for distribution, dealer, batch in rows:
        start_code, end_code = session.get(QrCode, distribution.start_code_id), session.get(QrCode, distribution.end_code_id)
        result.append({
            "id": distribution.id,
            "distribution_no": distribution.distribution_no,
            "batch_id": batch.id,
            "batch_no": batch.batch_no,
            "dealer_id": dealer.id,
            "dealer_name": dealer.company_name,
            "start_code_no": start_code.code_no if start_code else "",
            "end_code_no": end_code.code_no if end_code else "",
            "quantity": distribution.quantity,
            "status": distribution.status,
            "recorded_at": distribution.recorded_at,
        })
    return result


@router.get("/qrcode/distributions/anomalies")
def distribution_anomalies(session: SessionDep, admin: AdminUser = Depends(current_admin)):
    """List winning records whose code has no effective dealer distribution."""
    _require_role(admin, "finance", "viewer")
    rows = session.exec(
        select(LotteryRecord, QrCode, QrCodeBatch, Activity)
        .join(QrCode, QrCode.id == LotteryRecord.qrcode_id)
        .join(QrCodeBatch, QrCodeBatch.id == QrCode.batch_id)
        .join(Activity, Activity.id == LotteryRecord.activity_id)
        .where(LotteryRecord.distribution_id.is_(None), LotteryRecord.prize_type != PrizeType.NONE)
        .order_by(LotteryRecord.id.desc()).limit(500)
    ).all()
    return [{"record_id": record.id, "code_no": code.code_no, "batch_no": batch.batch_no, "activity_name": activity.name, "prize_name": record.prize_name, "created_at": record.created_at} for record, code, batch, activity in rows]


@router.post("/prizes", status_code=status.HTTP_201_CREATED)
def create_prize(payload: PrizeCreate, session: SessionDep, _admin: AdminUser = Depends(current_admin)):
    prize = Prize(**payload.model_dump())
    session.add(prize)
    session.commit()
    session.refresh(prize)
    return prize


@router.get("/prizes")
def list_prizes(session: SessionDep, _admin: AdminUser = Depends(current_admin)):
    return session.exec(select(Prize).where(Prize.type.in_(MVP_PRIZE_TYPES)).order_by(Prize.id.desc())).all()


@router.patch("/prizes/{prize_id}")
def update_prize(prize_id: int, payload: PrizeUpdate, session: SessionDep, _admin: AdminUser = Depends(current_admin)):
    prize = session.get(Prize, prize_id)
    if not prize:
        raise HTTPException(status_code=404, detail="奖品不存在")
    if prize.type not in MVP_PRIZE_TYPES:
        raise HTTPException(status_code=410, detail="历史奖品类型已下线")
    if payload.type != prize.type:
        raise HTTPException(status_code=422, detail="已创建奖品不能变更类型，请新建奖品")
    if payload.total_stock < prize.issued_stock:
        raise HTTPException(status_code=422, detail="总库存不能小于已发放数量")
    for field, value in payload.model_dump().items():
        setattr(prize, field, value)
    session.add(prize)
    session.commit()
    session.refresh(prize)
    activity_ids = session.exec(select(ActivityPrize.activity_id).where(ActivityPrize.prize_id == prize.id)).all()
    for activity_id in activity_ids:
        redis_client.delete(f"h5:activity:{activity_id}:detail")
    return prize


@router.patch("/prizes/{prize_id}/status")
def update_prize_status(prize_id: int, payload: PrizeStatusUpdate, session: SessionDep, admin: AdminUser = Depends(current_admin)):
    _require_role(admin, "operator")
    prize = session.get(Prize, prize_id)
    if not prize:
        raise HTTPException(status_code=404, detail="奖品不存在")
    prize.status = payload.status
    session.add(prize)
    _audit(session, admin, "status", "prize", prize.id, payload.status)
    session.commit()
    session.refresh(prize)
    activity_ids = session.exec(select(ActivityPrize.activity_id).where(ActivityPrize.prize_id == prize.id)).all()
    for activity_id in activity_ids:
        redis_client.delete(f"h5:activity:{activity_id}:detail")
    return prize


@router.delete("/prizes/{prize_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_prize(prize_id: int, session: SessionDep, admin: AdminUser = Depends(current_admin)):
    _require_super_admin(admin)
    prize = session.get(Prize, prize_id)
    if not prize:
        raise HTTPException(status_code=404, detail="奖品不存在")
    if prize.status == "active":
        raise HTTPException(status_code=422, detail="请先停用奖品后再删除")
    references = (
        session.exec(select(ActivityPrize.id).where(ActivityPrize.prize_id == prize_id).limit(1)).first()
        or session.exec(select(LotteryPolicyPrize.id).where(LotteryPolicyPrize.prize_id == prize_id).limit(1)).first()
        or session.exec(select(LotteryRecord.id).where(LotteryRecord.prize_id == prize_id).limit(1)).first()
        or session.exec(select(CouponCode.id).where(CouponCode.prize_id == prize_id).limit(1)).first()
        or session.exec(select(ReplenishmentTask.id).where(ReplenishmentTask.prize_id == prize_id).limit(1)).first()
        or session.exec(select(UpgradeOrder.id).where(UpgradeOrder.prize_id == prize_id).limit(1)).first()
    )
    if references:
        raise HTTPException(status_code=422, detail="奖品已被奖池、策略、开奖记录、券码或履约数据引用，不能删除")
    session.delete(prize)
    _audit(session, admin, "delete", "prize", prize_id)
    session.commit()


@router.post("/prizes/{prize_id}/coupon-codes/generate", status_code=status.HTTP_201_CREATED)
def generate_coupon_codes(prize_id: int, payload: CouponGenerateRequest, session: SessionDep, admin: AdminUser = Depends(current_admin)):
    raise HTTPException(status_code=410, detail="MVP 不提供优惠券功能")


@router.get("/prizes/{prize_id}/coupon-codes")
def list_coupon_codes(prize_id: int, session: SessionDep, _admin: AdminUser = Depends(current_admin), status_filter: str | None = Query(default=None, alias="status")):
    raise HTTPException(status_code=410, detail="MVP 不提供优惠券功能")


@router.patch("/coupon-codes/{coupon_id}/redeem")
def redeem_coupon(coupon_id: int, session: SessionDep, admin: AdminUser = Depends(current_admin)):
    raise HTTPException(status_code=410, detail="MVP 不提供优惠券功能")


@router.patch("/cash-payments/{payment_id}")
def update_cash_payment(payment_id: int, payload: CashPaymentUpdate, session: SessionDep, admin: AdminUser = Depends(current_admin)):
    raise HTTPException(status_code=410, detail="现金奖仅由微信商家转账结果驱动，后台不支持人工改写状态")


@router.get("/analytics/trend")
def analytics_trend(session: SessionDep, _admin: AdminUser = Depends(current_admin), days: int = Query(default=7, ge=1, le=90)):
    start = utc_now() - timedelta(days=days - 1)
    records = session.exec(select(LotteryRecord).where(LotteryRecord.created_at >= start)).all()
    scans = session.exec(select(ScanEvent).where(ScanEvent.created_at >= start)).all()
    buckets: dict[str, dict] = {}
    for index in range(days):
        day = (start + timedelta(days=index)).date().isoformat()
        buckets[day] = {"date": day, "scan_count": 0, "draw_count": 0, "winner_count": 0, "cash_amount": "0.00"}
    for scan in scans:
        buckets.setdefault(scan.created_at.date().isoformat(), {"date": scan.created_at.date().isoformat(), "scan_count": 0, "draw_count": 0, "winner_count": 0, "cash_amount": "0.00"})["scan_count"] += 1
    for record in records:
        bucket = buckets.setdefault(record.created_at.date().isoformat(), {"date": record.created_at.date().isoformat(), "scan_count": 0, "draw_count": 0, "winner_count": 0, "cash_amount": "0.00"})
        bucket["draw_count"] += 1
        if record.prize_type != PrizeType.NONE:
            bucket["winner_count"] += 1
        if record.prize_type == PrizeType.CASH:
            bucket["cash_amount"] = str(Decimal(bucket["cash_amount"]) + record.amount)
    return list(buckets.values())


@router.get("/overview")
def overview(session: SessionDep, _admin: AdminUser = Depends(current_admin)):
    return {
        "draw_count": session.exec(select(func.count(LotteryRecord.id))).one(),
        "winner_count": session.exec(select(func.count(LotteryRecord.id)).where(LotteryRecord.prize_type != PrizeType.NONE)).one(),
        "cash_issued": str(session.exec(select(func.coalesce(func.sum(LotteryRecord.amount), 0)).where(LotteryRecord.prize_type == PrizeType.CASH)).one()),
        "pending_redemptions": session.exec(select(func.count(LotteryRecord.id)).where(LotteryRecord.redemption_status == RedemptionStatus.PENDING)).one(),
    }


@router.get("/lottery-records")
def list_lottery_records(
    session: SessionDep,
    _admin: AdminUser = Depends(current_admin),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    redemption_status: RedemptionStatus | None = None,
    keyword: str | None = Query(default=None, max_length=128),
):
    conditions = []
    if redemption_status:
        conditions.append(LotteryRecord.redemption_status == redemption_status)
    if keyword:
        pattern = f"%{keyword.strip()}%"
        conditions.append((Consumer.nickname.like(pattern)) | (Consumer.openid.like(pattern)) | (QrCode.code_no.like(pattern)) | (LotteryRecord.prize_name.like(pattern)) | (CouponCode.code.like(pattern)))
    statement = (
        select(LotteryRecord, Consumer, QrCode, Activity, CashPayment, CouponCode, CodeDistribution, Dealer, QrCodeBatch, DealerRedemption, DealerAccount, UpgradeOrder)
        .join(Consumer, Consumer.id == LotteryRecord.consumer_id)
        .join(QrCode, QrCode.id == LotteryRecord.qrcode_id)
        .join(Activity, Activity.id == LotteryRecord.activity_id)
        .outerjoin(CashPayment, CashPayment.lottery_record_id == LotteryRecord.id)
        .outerjoin(CouponCode, CouponCode.lottery_record_id == LotteryRecord.id)
        .outerjoin(CodeDistribution, CodeDistribution.id == LotteryRecord.distribution_id)
        .outerjoin(Dealer, Dealer.id == CodeDistribution.dealer_id)
        .join(QrCodeBatch, QrCodeBatch.id == QrCode.batch_id)
        .outerjoin(DealerRedemption, DealerRedemption.lottery_record_id == LotteryRecord.id)
        .outerjoin(DealerAccount, DealerAccount.id == DealerRedemption.dealer_account_id)
        .outerjoin(UpgradeOrder, UpgradeOrder.lottery_record_id == LotteryRecord.id)
    )
    count_statement = (
        select(func.count(LotteryRecord.id))
        .join(Consumer, Consumer.id == LotteryRecord.consumer_id)
        .join(QrCode, QrCode.id == LotteryRecord.qrcode_id)
        .outerjoin(CouponCode, CouponCode.lottery_record_id == LotteryRecord.id)
    )
    if conditions:
        statement = statement.where(*conditions)
        count_statement = count_statement.where(*conditions)
    total = session.exec(count_statement).one()
    rows = session.exec(statement.order_by(LotteryRecord.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return {
        "items": [
            {
                "id": record.id,
                "consumer_id": consumer.id,
                "consumer_name": consumer.nickname or "微信用户",
                "consumer_openid": consumer.openid,
                "client_ip": record.client_ip,
                "client_location": record.client_location,
                "client_province": record.client_province,
                "client_city": record.client_city,
                "client_district": record.client_district,
                "client_address": record.client_address,
                "code_no": code.code_no,
                "batch_no": batch.batch_no,
                "distribution_no": distribution.distribution_no if distribution else None,
                "dealer_name": dealer.company_name if dealer else "未绑定经销商",
                "lottery_policy_scope": record.lottery_policy_scope,
                "lottery_policy_version": record.lottery_policy_version,
                "activity_name": activity.name,
                "prize_name": record.prize_name,
                "prize_type": record.prize_type,
                "prize_id": record.prize_id,
                "amount": str(record.amount),
                "coupon_id": coupon.id if coupon else None,
                "redemption_code": coupon.code if coupon else None,
                "redemption_code_type": "券码" if coupon else None,
                "coupon_status": coupon.status if coupon else None,
                "cash_order_no": payment.merchant_order_no if payment else None,
                "payment_id": payment.id if payment else None,
                "upgrade_redeem_code": upgrade.redeem_code if upgrade else None,
                "upgrade_amount": str(upgrade.amount) if upgrade else None,
                "upgrade_status": upgrade.status if upgrade else None,
                "redemption_status": record.redemption_status,
                "redeemed_at": record.redeemed_at,
                "redemption_note": record.redemption_note,
                "payment_status": payment.status if payment else None,
                "dealer_redemption": {
                    "owner_dealer_name": dealer.company_name if dealer else "未绑定经销商",
                    "redeeming_dealer_name": session.get(Dealer, dealer_redemption.dealer_id).company_name if session.get(Dealer, dealer_redemption.dealer_id) else "",
                    "dealer_name": dealer.company_name if dealer else "",
                    "account_name": dealer_account.username,
                    "redeemed_at": dealer_redemption.redeemed_at,
                    "note": dealer_redemption.redemption_note,
                } if dealer_redemption and dealer_account else None,
                "drawn_at": record.created_at,
            }
            for record, consumer, code, activity, payment, coupon, distribution, dealer, batch, dealer_redemption, dealer_account, upgrade in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("/activities/{activity_id}/batches/{batch_id}", status_code=status.HTTP_201_CREATED)
def link_activity_batch(activity_id: int, batch_id: int, session: SessionDep, _admin: AdminUser = Depends(current_admin)):
    if not session.get(Activity, activity_id) or not session.get(QrCodeBatch, batch_id):
        raise HTTPException(status_code=404, detail="活动或二维码批次不存在")
    if session.exec(select(ActivityBatch).where(ActivityBatch.activity_id == activity_id, ActivityBatch.batch_id == batch_id)).first():
        raise HTTPException(status_code=409, detail="该批次已关联当前活动")
    link = ActivityBatch(activity_id=activity_id, batch_id=batch_id)
    session.add(link)
    session.commit()
    redis_client.delete(f"h5:activity:{activity_id}:detail")
    return {"activity_id": activity_id, "batch_id": batch_id}


@router.get("/lottery-records/export.csv")
def export_lottery_records(
    session: SessionDep,
    _admin: AdminUser = Depends(current_admin),
    redemption_status: RedemptionStatus | None = None,
    keyword: str | None = Query(default=None, max_length=128),
):
    conditions = []
    if redemption_status:
        conditions.append(LotteryRecord.redemption_status == redemption_status)
    if keyword:
        pattern = f"%{keyword.strip()}%"
        conditions.append((Consumer.nickname.like(pattern)) | (Consumer.openid.like(pattern)) | (QrCode.code_no.like(pattern)) | (LotteryRecord.prize_name.like(pattern)) | (CouponCode.code.like(pattern)))
    rows = session.exec(
        select(LotteryRecord, Consumer, QrCode, Activity, CashPayment, CouponCode)
        .join(Consumer, Consumer.id == LotteryRecord.consumer_id)
        .join(QrCode, QrCode.id == LotteryRecord.qrcode_id)
        .join(Activity, Activity.id == LotteryRecord.activity_id)
        .outerjoin(CashPayment, CashPayment.lottery_record_id == LotteryRecord.id)
        .outerjoin(CouponCode, CouponCode.lottery_record_id == LotteryRecord.id)
        .where(*conditions)
        .order_by(LotteryRecord.id.desc())
    ).all()
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["record_id", "微信昵称", "OpenID", "IP", "省", "市", "区县", "解析地址", "原始定位", "抽奖号码", "活动", "奖品", "金额", "券码", "现金订单号", "兑换状态", "抽奖时间", "兑换时间"])
    for record, consumer, code, activity, payment, coupon in rows:
        writer.writerow([record.id, consumer.nickname or "微信用户", consumer.openid, record.client_ip or "未采集", record.client_province or "", record.client_city or "", record.client_district or "", record.client_address or "", record.client_location or "未授权定位", code.code_no, activity.name, record.prize_name, record.amount, coupon.code if coupon else "", payment.merchant_order_no if payment else "", record.redemption_status, record.created_at, record.redeemed_at or ""])
    return StreamingResponse(iter([("\ufeff" + output.getvalue()).encode()]), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="lottery-records.csv"'})


@router.patch("/lottery-records/{record_id}/redemption")
def update_redemption(record_id: int, payload: RedemptionUpdate, session: SessionDep, _admin: AdminUser = Depends(current_admin)):
    raise HTTPException(status_code=410, detail="MVP 不支持后台直接兑奖；现金奖由微信发放，换购奖由经销商核销")


@router.post("/redemption-lookup")
def lookup_redemption_by_draw_number(payload: DrawNumberLookup, session: SessionDep, admin: AdminUser = Depends(current_admin)):
    """Find a draw result from the printed number without its batch prefix."""
    _require_role(admin, "finance")
    rows = session.exec(
        select(QrCode, QrCodeBatch, LotteryRecord)
        .join(QrCodeBatch, QrCodeBatch.id == QrCode.batch_id)
        .outerjoin(LotteryRecord, LotteryRecord.qrcode_id == QrCode.id)
        .where(func.substr(QrCode.code_no, func.length(QrCodeBatch.prefix) + 1) == payload.draw_number)
    ).all()
    if not rows:
        raise HTTPException(status_code=404, detail="未找到该抽奖号码")
    if len(rows) > 1:
        raise HTTPException(status_code=409, detail="该抽奖号码在多个批次中重复，请通过中奖记录确认")
    code, batch, record = rows[0]
    if not record or record.prize_type == PrizeType.NONE:
        return {"code_no": code.code_no, "draw_number": payload.draw_number, "batch_no": batch.batch_no, "is_winner": False, "message": "该号码未中奖"}
    return {
        "id": record.id,
        "code_no": code.code_no,
        "draw_number": payload.draw_number,
        "batch_no": batch.batch_no,
        "is_winner": True,
        "prize_name": record.prize_name,
        "prize_type": record.prize_type,
        "amount": str(record.amount),
        "redemption_status": record.redemption_status,
        "redeemed_at": record.redeemed_at,
        "redemption_note": record.redemption_note,
    }


@router.patch("/lottery-records/redemption/batch")
def batch_update_redemption(payload: RedemptionBatchUpdate, session: SessionDep, admin: AdminUser = Depends(current_admin)):
    raise HTTPException(status_code=410, detail="MVP 不支持批量人工兑奖")


@router.get("/activities")
def list_activities(session: SessionDep, _admin: AdminUser = Depends(current_admin)):
    return session.exec(select(Activity).order_by(Activity.id.desc())).all()


@router.get("/activities/{activity_id}/configuration")
def activity_configuration(activity_id: int, session: SessionDep, _admin: AdminUser = Depends(current_admin)):
    activity = session.get(Activity, activity_id)
    if not activity:
        raise HTTPException(status_code=404, detail="活动不存在")
    pools = session.exec(
        select(ActivityPrize, Prize)
        .join(Prize, Prize.id == ActivityPrize.prize_id)
        .where(ActivityPrize.activity_id == activity_id)
        .order_by(ActivityPrize.id)
    ).all()
    batch_ids = session.exec(select(ActivityBatch.batch_id).where(ActivityBatch.activity_id == activity_id)).all()
    return {
        "activity": activity,
        "all_batches": activity.all_batches,
        "batch_ids": batch_ids,
        "prize_pools": [
            {
                "id": pool.id,
                "prize_id": prize.id,
                "prize_name": prize.name,
                "prize_type": prize.type,
                "cash_amount": str(prize.cash_amount),
                "probability": str(pool.probability),
                "daily_limit": pool.daily_limit,
                "issued_today": pool.issued_today,
                "total_stock": prize.total_stock,
                "issued_stock": prize.issued_stock,
            }
            for pool, prize in pools
        ],
        "policies": _policy_summaries(session, activity_id),
    }


def _policy_summaries(session, activity_id: int) -> list[dict]:
    policies = session.exec(select(LotteryPolicy).where(LotteryPolicy.activity_id == activity_id).order_by(LotteryPolicy.scope, LotteryPolicy.dealer_id, LotteryPolicy.province, LotteryPolicy.city, LotteryPolicy.district, LotteryPolicy.version.desc())).all()
    result = []
    for policy in policies:
        rows = session.exec(select(LotteryPolicyPrize, Prize).join(Prize, Prize.id == LotteryPolicyPrize.prize_id).where(LotteryPolicyPrize.policy_id == policy.id).order_by(LotteryPolicyPrize.id)).all()
        result.append({
            "id": policy.id,
            "scope": policy.scope,
            "dealer_id": policy.dealer_id,
            "province": policy.province,
            "city": policy.city,
            "district": policy.district,
            "version": policy.version,
            "status": policy.status,
            "note": policy.note,
            "created_at": policy.created_at,
            "prizes": [{"id": item.id, "prize_id": prize.id, "prize_name": prize.name, "prize_type": prize.type, "probability": str(item.probability), "daily_limit": item.daily_limit, "issued_today": item.issued_today} for item, prize in rows],
        })
    return result


@router.get("/activities/{activity_id}/policies")
def list_lottery_policies(activity_id: int, session: SessionDep, _admin: AdminUser = Depends(current_admin)):
    if not session.get(Activity, activity_id):
        raise HTTPException(status_code=404, detail="活动不存在")
    return _policy_summaries(session, activity_id)


@router.post("/activities/{activity_id}/policies", status_code=status.HTTP_201_CREATED)
def publish_lottery_policy(activity_id: int, payload: LotteryPolicyCreate, session: SessionDep, admin: AdminUser = Depends(current_admin)):
    _require_role(admin, "operator")
    if not session.get(Activity, activity_id):
        raise HTTPException(status_code=404, detail="活动不存在")
    if payload.dealer_id and not session.get(Dealer, payload.dealer_id):
        raise HTTPException(status_code=404, detail="经销商不存在")
    prizes = [session.get(Prize, item.prize_id) for item in payload.prizes]
    if any(prize is None for prize in prizes):
        raise HTTPException(status_code=404, detail="奖品不存在")
    if any(prize.status != "active" or prize.type not in MVP_PRIZE_TYPES for prize in prizes):
        raise HTTPException(status_code=422, detail="策略仅可配置启用中的现金奖或加价换购")
    target = [LotteryPolicy.activity_id == activity_id, LotteryPolicy.scope == payload.scope]
    if payload.scope == "dealer":
        target.append(LotteryPolicy.dealer_id == payload.dealer_id)
    else:
        target.extend([LotteryPolicy.province == payload.province, LotteryPolicy.city == payload.city, LotteryPolicy.district == payload.district])
    previous = session.exec(select(LotteryPolicy).where(*target).with_for_update()).all()
    version = max((item.version for item in previous), default=0) + 1
    for item in previous:
        if item.status == "active":
            item.status = "superseded"
            session.add(item)
    policy = LotteryPolicy(
        activity_id=activity_id,
        scope=payload.scope,
        dealer_id=payload.dealer_id,
        province=payload.province.strip(),
        city=payload.city.strip(),
        district=payload.district.strip(),
        version=version,
        note=payload.note,
        published_by=admin.id,
    )
    session.add(policy)
    session.flush()
    session.add_all([LotteryPolicyPrize(policy_id=policy.id, prize_id=item.prize_id, probability=item.probability, daily_limit=item.daily_limit) for item in payload.prizes])
    _audit(session, admin, "publish", "lottery_policy", policy.id, f"activity={activity_id};scope={policy.scope};version={version}")
    session.commit()
    session.refresh(policy)
    redis_client.delete(f"h5:activity:{activity_id}:detail")
    return policy


def _validate_policy_payload(payload: LotteryPolicyCreate, session: SessionDep) -> None:
    if payload.dealer_id and not session.get(Dealer, payload.dealer_id):
        raise HTTPException(status_code=404, detail="经销商不存在")
    prizes = [session.get(Prize, item.prize_id) for item in payload.prizes]
    if any(prize is None for prize in prizes):
        raise HTTPException(status_code=404, detail="奖品不存在")
    if any(prize.status != "active" or prize.type not in MVP_PRIZE_TYPES for prize in prizes):
        raise HTTPException(status_code=422, detail="策略仅可配置启用中的现金奖或加价换购")


@router.patch("/policies/{policy_id}")
def update_lottery_policy(policy_id: int, payload: LotteryPolicyCreate, session: SessionDep, admin: AdminUser = Depends(current_admin)):
    _require_role(admin, "operator")
    policy = session.get(LotteryPolicy, policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="策略不存在")
    _validate_policy_payload(payload, session)
    if policy.status != "active":
        raise HTTPException(status_code=409, detail="历史策略不可修改，请基于当前生效策略发布新版本")
    same_target = (
        policy.scope == payload.scope
        and policy.dealer_id == payload.dealer_id
        and policy.province == payload.province.strip()
        and policy.city == payload.city.strip()
        and policy.district == payload.district.strip()
    )
    if not same_target:
        raise HTTPException(status_code=422, detail="已发布策略不能变更适用范围；请新发布策略")
    target = [LotteryPolicy.activity_id == policy.activity_id, LotteryPolicy.scope == payload.scope]
    if payload.scope == "dealer":
        target.append(LotteryPolicy.dealer_id == payload.dealer_id)
    else:
        target.extend([LotteryPolicy.province == payload.province.strip(), LotteryPolicy.city == payload.city.strip(), LotteryPolicy.district == payload.district.strip()])
    same_target_rows = session.exec(select(LotteryPolicy).where(*target).with_for_update()).all()
    version = max((item.version for item in same_target_rows), default=0) + 1
    for item in same_target_rows:
        if item.status == "active":
            item.status = "superseded"
            session.add(item)
    new_policy = LotteryPolicy(
        activity_id=policy.activity_id,
        scope=payload.scope,
        dealer_id=payload.dealer_id,
        province=payload.province.strip(),
        city=payload.city.strip(),
        district=payload.district.strip(),
        version=version,
        note=payload.note,
        published_by=admin.id,
    )
    session.add(new_policy)
    session.flush()
    session.add_all([LotteryPolicyPrize(policy_id=new_policy.id, prize_id=item.prize_id, probability=item.probability, daily_limit=item.daily_limit) for item in payload.prizes])
    _audit(session, admin, "publish", "lottery_policy", new_policy.id, f"replaces={policy.id};version={version}")
    session.commit()
    session.refresh(new_policy)
    redis_client.delete(f"h5:activity:{policy.activity_id}:detail")
    return new_policy


@router.patch("/policies/{policy_id}/status")
def update_lottery_policy_status(policy_id: int, payload: LotteryPolicyStatusUpdate, session: SessionDep, admin: AdminUser = Depends(current_admin)):
    _require_role(admin, "operator")
    policy = session.get(LotteryPolicy, policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="策略不存在")
    if policy.status != "active":
        raise HTTPException(status_code=409, detail="历史策略为审计数据，不允许变更状态")
    if payload.status == "active":
        return policy
    policy.status = payload.status
    session.add(policy)
    _audit(session, admin, "status", "lottery_policy", policy_id, payload.status)
    session.commit()
    redis_client.delete(f"h5:activity:{policy.activity_id}:detail")
    return policy


@router.post("/activities", status_code=status.HTTP_201_CREATED)
def create_activity(payload: ActivityCreate, session: SessionDep, _admin: AdminUser = Depends(current_admin)):
    if payload.end_at <= payload.start_at:
        raise HTTPException(status_code=422, detail="结束时间必须晚于开始时间")
    activity = Activity(**payload.model_dump())
    activity.rules = generate_activity_rules(activity)
    session.add(activity)
    session.commit()
    session.refresh(activity)
    return activity


@router.patch("/activities/{activity_id}")
def update_activity(activity_id: int, payload: ActivityUpdate, session: SessionDep, _admin: AdminUser = Depends(current_admin)):
    activity = session.get(Activity, activity_id)
    if not activity:
        raise HTTPException(status_code=404, detail="活动不存在")
    if payload.end_at <= payload.start_at:
        raise HTTPException(status_code=422, detail="结束时间必须晚于开始时间")
    for field, value in payload.model_dump().items():
        setattr(activity, field, value)
    activity.rules = generate_activity_rules(activity)
    session.add(activity)
    session.commit()
    session.refresh(activity)
    redis_client.delete(f"h5:activity:{activity_id}:detail")
    return activity


@router.patch("/activities/{activity_id}/status")
def update_activity_status(activity_id: int, payload: ActivityStatusUpdate, session: SessionDep, admin: AdminUser = Depends(current_admin)):
    _require_role(admin, "operator")
    activity = session.get(Activity, activity_id)
    if not activity:
        raise HTTPException(status_code=404, detail="活动不存在")
    activity.status = payload.status
    session.add(activity)
    _audit(session, admin, "status", "activity", activity.id, payload.status.value)
    session.commit()
    session.refresh(activity)
    redis_client.delete(f"h5:activity:{activity_id}:detail")
    return activity


@router.delete("/activities/{activity_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_activity(activity_id: int, session: SessionDep, admin: AdminUser = Depends(current_admin)):
    _require_super_admin(admin)
    activity = session.get(Activity, activity_id)
    if not activity:
        raise HTTPException(status_code=404, detail="活动不存在")
    if activity.status == "active":
        raise HTTPException(status_code=422, detail="请先停用活动后再删除")
    if session.exec(select(LotteryRecord.id).where(LotteryRecord.activity_id == activity_id).limit(1)).first():
        raise HTTPException(status_code=422, detail="活动已有开奖记录，不能删除")
    policies = session.exec(select(LotteryPolicy).where(LotteryPolicy.activity_id == activity_id)).all()
    for policy in policies:
        for item in session.exec(select(LotteryPolicyPrize).where(LotteryPolicyPrize.policy_id == policy.id)).all():
            session.delete(item)
        session.delete(policy)
    for pool in session.exec(select(ActivityPrize).where(ActivityPrize.activity_id == activity_id)).all():
        session.delete(pool)
    for link in session.exec(select(ActivityBatch).where(ActivityBatch.activity_id == activity_id)).all():
        session.delete(link)
    session.delete(activity)
    _audit(session, admin, "delete", "activity", activity_id)
    session.commit()
    redis_client.delete(f"h5:activity:{activity_id}:detail")


@router.post("/activities/{activity_id}/prizes", status_code=status.HTTP_201_CREATED)
def link_prize(activity_id: int, payload: ActivityPrizeCreate, session: SessionDep, _admin: AdminUser = Depends(current_admin)):
    prize = session.get(Prize, payload.prize_id)
    if not session.get(Activity, activity_id) or not prize:
        raise HTTPException(status_code=404, detail="活动或奖品不存在")
    if prize.type not in MVP_PRIZE_TYPES or prize.status != "active":
        raise HTTPException(status_code=422, detail="活动奖池仅可配置启用中的现金奖或加价换购")
    if session.exec(select(ActivityPrize).where(ActivityPrize.activity_id == activity_id, ActivityPrize.prize_id == payload.prize_id)).first():
        raise HTTPException(status_code=409, detail="该奖品已在当前奖池中")
    current = session.exec(select(ActivityPrize).where(ActivityPrize.activity_id == activity_id)).all()
    if sum(item.probability for item in current) + payload.probability > 1:
        raise HTTPException(status_code=422, detail="奖池概率之和不能超过 1")
    item = ActivityPrize(activity_id=activity_id, **payload.model_dump())
    session.add(item)
    session.commit()
    session.refresh(item)
    redis_client.delete(f"h5:activity:{activity_id}:detail")
    return item


@router.patch("/activities/{activity_id}/prizes/{pool_id}")
def update_activity_prize(activity_id: int, pool_id: int, payload: ActivityPrizeUpdate, session: SessionDep, _admin: AdminUser = Depends(current_admin)):
    pool = session.get(ActivityPrize, pool_id)
    if not pool or pool.activity_id != activity_id:
        raise HTTPException(status_code=404, detail="奖池项不存在")
    current = session.exec(select(ActivityPrize).where(ActivityPrize.activity_id == activity_id, ActivityPrize.id != pool_id)).all()
    if sum(item.probability for item in current) + payload.probability > 1:
        raise HTTPException(status_code=422, detail="奖池概率之和不能超过 1")
    pool.probability, pool.daily_limit = payload.probability, payload.daily_limit
    session.add(pool)
    session.commit()
    session.refresh(pool)
    redis_client.delete(f"h5:activity:{activity_id}:detail")
    return pool


@router.delete("/activities/{activity_id}/prizes/{pool_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_activity_prize(activity_id: int, pool_id: int, session: SessionDep, _admin: AdminUser = Depends(current_admin)):
    pool = session.get(ActivityPrize, pool_id)
    if not pool or pool.activity_id != activity_id:
        raise HTTPException(status_code=404, detail="奖池项不存在")
    session.delete(pool)
    session.commit()
    redis_client.delete(f"h5:activity:{activity_id}:detail")
