from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlmodel import select

from app.core.http import client_ip_or_unknown, resolve_client_ip
from app.deps import SessionDep
from app.cache import enforce_rate_limit
from app.models import CodeDistribution, Dealer, DealerAccount, DealerRedemption, LotteryRecord, PrizeType, QrCode, RedemptionStatus, Store, UpgradeOrder, utc_now
from app.schemas import DealerDrawNumberLookup, DealerPortalLogin, DealerRedemptionCreate, DealerRedeemRequest, StoreRedeemRequest
from app.security import make_token, parse_token, verify_password

router = APIRouter(prefix="/api/dealer", tags=["dealer"])
store_router = APIRouter(prefix="/api/store", tags=["store"])


def current_dealer_account(session: SessionDep, authorization: str | None = Header(default=None)) -> DealerAccount:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="缺少经销商登录凭证")
    payload = parse_token(authorization.removeprefix("Bearer "), "dealer")
    account = session.get(DealerAccount, int(payload["sub"]))
    if not account or not account.is_active:
        raise HTTPException(status_code=401, detail="经销商账号不可用")
    dealer = session.get(Dealer, account.dealer_id)
    if not dealer or dealer.status != "active":
        raise HTTPException(status_code=403, detail="经销商已冻结，暂不可使用核销功能")
    return account


@router.post("/auth/login")
def login(payload: DealerPortalLogin, request: Request, session: SessionDep):
    client_ip = client_ip_or_unknown(request)
    enforce_rate_limit(f"rate:dealer-login:{client_ip}:{payload.username.lower()}", 10, 300)
    account = session.exec(select(DealerAccount).where(DealerAccount.username == payload.username)).first()
    if not account or not account.is_active or not verify_password(payload.password, account.password_hash):
        raise HTTPException(status_code=401, detail="账号或密码错误")
    return {"access_token": make_token(str(account.id), "dealer", 480, dealer_id=account.dealer_id), "token_type": "bearer"}


@router.get("/profile")
def profile(session: SessionDep, account: DealerAccount = Depends(current_dealer_account)):
    dealer = session.get(Dealer, account.dealer_id)
    return {"dealer_id": account.dealer_id, "dealer_name": dealer.company_name if dealer else "", "username": account.username}


@router.get("/redemptions")
def list_redemptions(session: SessionDep, account: DealerAccount = Depends(current_dealer_account)):
    rows = session.exec(
        select(UpgradeOrder, LotteryRecord, QrCode)
        .join(LotteryRecord, LotteryRecord.id == UpgradeOrder.lottery_record_id)
        .join(QrCode, QrCode.id == LotteryRecord.qrcode_id)
        .join(CodeDistribution, CodeDistribution.id == LotteryRecord.distribution_id)
        .where(CodeDistribution.dealer_id == account.dealer_id, UpgradeOrder.status == "redeemed")
        .order_by(UpgradeOrder.id.desc())
    ).all()
    return [{
        "id": order.id,
        "code_no": code.code_no,
        "prize_name": record.prize_name,
        "amount": str(order.amount),
        "owner_dealer_name": "当前经销商",
        "redemption_note": record.redemption_note or "换购码已核销",
        "redeemed_at": order.redeemed_at,
    } for order, record, code in rows]


@router.post("/redemptions/lookup")
def lookup_redemption(payload: DealerDrawNumberLookup, session: SessionDep, account: DealerAccount = Depends(current_dealer_account)):
    raise HTTPException(status_code=410, detail="MVP 仅支持通过换购码核销")


@router.post("/redemptions")
def create_redemption(payload: DealerRedemptionCreate, request: Request, session: SessionDep, account: DealerAccount = Depends(current_dealer_account)):
    raise HTTPException(status_code=410, detail="MVP 仅支持加价换购核销；现金奖请由用户在微信内领取")


def _upgrade_order_result(order: UpgradeOrder, record: LotteryRecord, code: QrCode, store: Store | None = None) -> dict:
    return {
        "id": order.id,
        "record_id": record.id,
        "redeem_code": order.redeem_code,
        "code_no": code.code_no,
        "prize_name": record.prize_name,
        "amount": str(order.amount),
        "status": order.status,
        "payment_method": order.payment_method,
        "store_id": order.store_id,
        "store_name": store.name if store else None,
        "expires_at": order.expires_at,
        "paid_at": order.paid_at,
        "redeemed_at": order.redeemed_at,
        "redemption_status": record.redemption_status,
    }


def _find_upgrade_order(redeem_code: str, session, *, lock: bool = False):
    query = select(UpgradeOrder).where(UpgradeOrder.redeem_code == redeem_code.strip())
    if lock:
        query = query.with_for_update()
    order = session.exec(query).first()
    if not order:
        raise HTTPException(status_code=404, detail="换购码不存在")
    record = session.get(LotteryRecord, order.lottery_record_id)
    code = session.get(QrCode, record.qrcode_id) if record else None
    if not record or not code:
        raise HTTPException(status_code=409, detail="换购订单数据不完整，请联系后台")
    owner = None
    if record.distribution_id:
        distribution = session.get(CodeDistribution, record.distribution_id)
        owner = session.get(Dealer, distribution.dealer_id) if distribution else None
    return order, record, code, owner


def _validate_store(store: Store, session, dealer_id: int | None = None) -> None:
    if store.status != "active":
        raise HTTPException(status_code=403, detail="门店已停用")
    dealer = session.get(Dealer, store.dealer_id)
    if not dealer or dealer.status != "active":
        raise HTTPException(status_code=403, detail="所属经销商已冻结")
    if dealer_id is not None and store.dealer_id != dealer_id:
        raise HTTPException(status_code=403, detail="门店不属于当前经销商")


@router.post("/upgrade-orders/lookup")
def lookup_upgrade_order(payload: DealerRedeemRequest, session: SessionDep, account: DealerAccount = Depends(current_dealer_account)):
    """Look up an upgrade order without changing its state."""
    enforce_rate_limit(f"rate:dealer-upgrade-lookup:{account.id}", 30, 300)
    order, record, code, owner = _find_upgrade_order(payload.redeem_code, session)
    if record.prize_type != PrizeType.UPGRADE:
        raise HTTPException(status_code=422, detail="该凭证不是换购码")
    if not owner or owner.id != account.dealer_id:
        raise HTTPException(status_code=403, detail="该换购码不属于当前经销商")
    return _upgrade_order_result(order, record, code)


def _redeem_upgrade_order(payload: DealerRedeemRequest, session, dealer_id: int, store: Store | None, request: Request | None = None, dealer_account_id: int | None = None):
    order, record, code, owner = _find_upgrade_order(payload.redeem_code, session, lock=True)
    if record.prize_type != PrizeType.UPGRADE:
        raise HTTPException(status_code=422, detail="该凭证不是换购码")
    if not owner or owner.id != dealer_id:
        raise HTTPException(status_code=403, detail="该换购码不属于当前经销商")
    if store:
        _validate_store(store, session, dealer_id)
    now = utc_now()
    if order.expires_at and order.expires_at < now:
        order.status = "expired"
        session.add(order)
        session.commit()
        raise HTTPException(status_code=422, detail="换购码已过期")
    if order.status != "pending_store_redemption":
        raise HTTPException(status_code=422, detail="该换购码已核销或已失效")
    if record.redemption_status != RedemptionStatus.PENDING:
        raise HTTPException(status_code=409, detail="中奖记录与换购订单状态不一致，请联系后台")
    order.status = "redeemed"
    order.store_id = store.id if store else payload.store_id
    order.payment_method = payload.payment_method.strip() or "manual"
    order.paid_at = now
    order.redeemed_at = now
    record.redemption_status = RedemptionStatus.REDEEMED
    record.redeemed_at = now
    record.redemption_note = f"换购码已核销（{order.payment_method}）"
    session.add_all([order, record])
    if dealer_account_id is not None:
        session.add(DealerRedemption(
            lottery_record_id=record.id,
            dealer_id=dealer_id,
            dealer_account_id=dealer_account_id,
            redemption_note=record.redemption_note,
            redeemed_at=now,
            request_ip=resolve_client_ip(request),
            user_agent=request.headers.get("user-agent") if request else None,
        ))
    session.commit()
    session.refresh(order)
    return {**_upgrade_order_result(order, record, code, store), "message": "换购核销成功"}


@router.post("/upgrade-orders/redeem")
def redeem_upgrade_order(payload: DealerRedeemRequest, request: Request, session: SessionDep, account: DealerAccount = Depends(current_dealer_account)):
    enforce_rate_limit(f"rate:dealer-upgrade-redeem:{account.id}", 20, 300)
    store = session.get(Store, payload.store_id) if payload.store_id else None
    if payload.store_id and not store:
        raise HTTPException(status_code=404, detail="门店不存在")
    return _redeem_upgrade_order(payload, session, account.dealer_id, store, request, account.id)


@router.post("/stores/redeem")
@router.post("/store/redeem")
@store_router.post("/redeem")
def redeem_upgrade_order_at_store(payload: StoreRedeemRequest, request: Request, session: SessionDep):
    """Store-side redemption using the store number and its private PIN."""
    enforce_rate_limit(f"rate:store-upgrade-redeem:{payload.store_no}", 20, 300)
    store = session.exec(select(Store).where(Store.store_no == payload.store_no.strip())).first()
    if not store:
        raise HTTPException(status_code=404, detail="门店不存在")
    _validate_store(store, session)
    if not verify_password(payload.redeemer_pin, store.redeemer_pin_hash):
        raise HTTPException(status_code=401, detail="门店核销 PIN 不正确")
    return _redeem_upgrade_order(DealerRedeemRequest(redeem_code=payload.redeem_code, payment_method=payload.payment_method), session, store.dealer_id, store, request)
