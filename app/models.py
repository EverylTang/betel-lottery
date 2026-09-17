from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import Column, DateTime, Numeric, String, UniqueConstraint
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    """Return a naive UTC value for MySQL DATETIME columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class CodeStatus(StrEnum):
    INACTIVE = "inactive"
    ACTIVE = "active"
    DRAWN = "drawn"
    VOID = "void"


class ActivityStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    ENDED = "ended"


class PrizeType(StrEnum):
    # Legacy values remain readable for historical records. New MVP
    # configuration accepts only CASH and UPGRADE.
    NONE = "none"
    CASH = "cash"
    COUPON = "coupon"
    PHYSICAL = "physical"
    POINTS = "points"
    RETRY = "retry"
    UPGRADE = "upgrade"


MVP_PRIZE_TYPES = frozenset({PrizeType.CASH, PrizeType.UPGRADE})


class PaymentStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCESS = "success"
    FAILED = "failed"
    MANUAL = "manual"


class RedemptionStatus(StrEnum):
    PENDING = "pending"
    REDEEMED = "redeemed"
    EXPIRED = "expired"
    NOT_REQUIRED = "not_required"


class AdminUser(SQLModel, table=True):
    __tablename__ = "admin_user"
    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(sa_column=Column(String(64), unique=True, nullable=False))
    password_hash: str
    role: str = Field(default="super_admin", max_length=32)
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=utc_now)


class AdminAuditLog(SQLModel, table=True):
    __tablename__ = "admin_audit_log"
    id: int | None = Field(default=None, primary_key=True)
    admin_id: int = Field(foreign_key="admin_user.id", index=True)
    action: str = Field(max_length=64)
    target_type: str = Field(max_length=64)
    target_id: int | None = None
    detail: str = Field(default="", max_length=1024)
    created_at: datetime = Field(default_factory=utc_now)


class Dealer(SQLModel, table=True):
    __tablename__ = "dealer"
    id: int | None = Field(default=None, primary_key=True)
    dealer_no: str = Field(sa_column=Column(String(32), unique=True, nullable=False))
    company_name: str = Field(max_length=128)
    contact_name: str = Field(max_length=32)
    phone: str = Field(sa_column=Column(String(20), unique=True, nullable=False))
    province: str = Field(max_length=32)
    city: str = Field(max_length=32)
    district: str = Field(default="", max_length=32)
    level: int = Field(default=1, ge=1, le=3)
    status: str = Field(default="active", max_length=16)
    remark: str | None = Field(default=None, max_length=512)
    created_at: datetime = Field(default_factory=utc_now)


class DealerAccount(SQLModel, table=True):
    __tablename__ = "dealer_account"
    id: int | None = Field(default=None, primary_key=True)
    dealer_id: int = Field(foreign_key="dealer.id", unique=True, index=True)
    username: str = Field(sa_column=Column(String(64), unique=True, nullable=False))
    password_hash: str = Field(max_length=256)
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=utc_now)


class Store(SQLModel, table=True):
    __tablename__ = "store"
    id: int | None = Field(default=None, primary_key=True)
    store_no: str = Field(sa_column=Column(String(32), unique=True, nullable=False))
    name: str = Field(max_length=128)
    contact_name: str = Field(max_length=32)
    phone: str = Field(max_length=20)
    dealer_id: int = Field(foreign_key="dealer.id", index=True)
    redeemer_pin_hash: str = Field(max_length=256)
    status: str = Field(default="active", max_length=16)
    created_at: datetime = Field(default_factory=utc_now)


class ReplenishmentTask(SQLModel, table=True):
    __tablename__ = "replenishment_task"
    id: int | None = Field(default=None, primary_key=True)
    store_id: int | None = Field(default=None, foreign_key="store.id", index=True)
    dealer_id: int = Field(foreign_key="dealer.id", index=True)
    prize_id: int = Field(foreign_key="prize.id", index=True)
    quantity: int = Field(gt=0)
    fulfilled_quantity: int = Field(default=0, ge=0)
    status: str = Field(default="pending", max_length=16, index=True)
    note: str = Field(default="", max_length=512)
    fulfilled_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)


class UpgradeOrder(SQLModel, table=True):
    __tablename__ = "upgrade_order"
    id: int | None = Field(default=None, primary_key=True)
    lottery_record_id: int = Field(foreign_key="lottery_record.id", unique=True)
    consumer_id: int = Field(foreign_key="consumer.id", index=True)
    prize_id: int = Field(foreign_key="prize.id")
    redeem_code: str = Field(sa_column=Column(String(64), unique=True, nullable=False))
    amount: Decimal = Field(sa_column=Column(Numeric(10, 2), nullable=False))
    status: str = Field(default="pending_store_redemption", max_length=32, index=True)
    store_id: int | None = Field(default=None, foreign_key="store.id")
    payment_method: str = Field(default="manual", max_length=32)
    paid_at: datetime | None = None
    redeemed_at: datetime | None = None
    expires_at: datetime
    created_at: datetime = Field(default_factory=utc_now)


class Activity(SQLModel, table=True):
    __tablename__ = "activity"
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(max_length=128)
    description: str = Field(default="", max_length=512)
    rules: str = Field(default="", max_length=2000)
    start_at: datetime = Field(sa_column=Column(DateTime, nullable=False))
    end_at: datetime = Field(sa_column=Column(DateTime, nullable=False))
    timezone: str = Field(default="Asia/Shanghai", max_length=64)
    all_batches: bool = Field(default=True)
    status: ActivityStatus = Field(default=ActivityStatus.DRAFT)
    created_at: datetime = Field(default_factory=utc_now)


class QrCodeBatch(SQLModel, table=True):
    __tablename__ = "qrcode_batch"
    id: int | None = Field(default=None, primary_key=True)
    batch_no: str = Field(sa_column=Column(String(32), unique=True, nullable=False))
    batch_name: str = Field(max_length=128)
    prefix: str = Field(max_length=16)
    total_count: int = Field(default=0, ge=0)
    valid_start: datetime | None = None
    valid_end: datetime | None = None
    status: str = Field(default="inactive", max_length=16)
    created_at: datetime = Field(default_factory=utc_now)


class ActivityBatch(SQLModel, table=True):
    __tablename__ = "activity_batch"
    activity_id: int = Field(foreign_key="activity.id", primary_key=True)
    batch_id: int = Field(foreign_key="qrcode_batch.id", primary_key=True)
    created_at: datetime = Field(default_factory=utc_now)


class QrCode(SQLModel, table=True):
    __tablename__ = "qrcode"
    id: int | None = Field(default=None, primary_key=True)
    code_no: str = Field(sa_column=Column(String(48), unique=True, nullable=False))
    token_hash: str = Field(sa_column=Column(String(64), unique=True, nullable=False))
    token_ciphertext: str | None = Field(default=None, max_length=512)
    redemption_code_hash: str | None = Field(default=None, max_length=64, index=True)
    redemption_code_ciphertext: str | None = Field(default=None, max_length=512)
    batch_id: int = Field(foreign_key="qrcode_batch.id", index=True)
    status: CodeStatus = Field(default=CodeStatus.INACTIVE, index=True)
    drawn_user_id: int | None = Field(default=None, foreign_key="consumer.id")
    drawn_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)


class CodeDistribution(SQLModel, table=True):
    __tablename__ = "code_distribution"
    id: int | None = Field(default=None, primary_key=True)
    distribution_no: str = Field(sa_column=Column(String(32), unique=True, nullable=False))
    batch_id: int = Field(foreign_key="qrcode_batch.id", index=True)
    dealer_id: int = Field(foreign_key="dealer.id", index=True)
    start_code_id: int = Field(foreign_key="qrcode.id")
    end_code_id: int = Field(foreign_key="qrcode.id")
    quantity: int = Field(gt=0)
    status: str = Field(default="effective", max_length=16)
    recorded_at: datetime = Field(default_factory=utc_now)


class Prize(SQLModel, table=True):
    __tablename__ = "prize"
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(max_length=128)
    type: PrizeType = Field(default=PrizeType.CASH)
    cash_amount: Decimal = Field(default=Decimal("0.00"), sa_column=Column(Numeric(10, 2), nullable=False))
    points_amount: int = Field(default=0, ge=0)
    upgrade_price: Decimal = Field(default=Decimal("0.00"), sa_column=Column(Numeric(10, 2), nullable=False))
    total_stock: int = Field(default=0, ge=0)
    issued_stock: int = Field(default=0, ge=0)
    per_user_limit: int = Field(default=1, ge=1)
    status: str = Field(default="active", max_length=16)
    created_at: datetime = Field(default_factory=utc_now)


class ActivityPrize(SQLModel, table=True):
    __tablename__ = "activity_prize"
    __table_args__ = (UniqueConstraint("activity_id", "prize_id", name="uq_activity_prize"),)
    id: int | None = Field(default=None, primary_key=True)
    activity_id: int = Field(foreign_key="activity.id", index=True)
    prize_id: int = Field(foreign_key="prize.id", index=True)
    probability: Decimal = Field(sa_column=Column(Numeric(7, 6), nullable=False))
    daily_limit: int = Field(default=0, ge=0)
    issued_today: int = Field(default=0, ge=0)
    stat_date: str | None = Field(default=None, max_length=10)
    created_at: datetime = Field(default_factory=utc_now)


class LotteryPolicy(SQLModel, table=True):
    """An immutable, published draw policy for one dealer or geographic scope."""
    __tablename__ = "lottery_policy"
    id: int | None = Field(default=None, primary_key=True)
    activity_id: int = Field(foreign_key="activity.id", index=True)
    scope: str = Field(max_length=16)  # dealer, province, city, district
    dealer_id: int | None = Field(default=None, foreign_key="dealer.id", index=True)
    province: str = Field(default="", max_length=32)
    city: str = Field(default="", max_length=32)
    district: str = Field(default="", max_length=32)
    version: int = Field(default=1, ge=1)
    status: str = Field(default="active", max_length=16, index=True)
    note: str = Field(default="", max_length=512)
    published_by: int | None = Field(default=None, foreign_key="admin_user.id")
    created_at: datetime = Field(default_factory=utc_now)


class LotteryPolicyPrize(SQLModel, table=True):
    __tablename__ = "lottery_policy_prize"
    __table_args__ = (UniqueConstraint("policy_id", "prize_id", name="uq_lottery_policy_prize"),)
    id: int | None = Field(default=None, primary_key=True)
    policy_id: int = Field(foreign_key="lottery_policy.id", index=True)
    prize_id: int = Field(foreign_key="prize.id", index=True)
    probability: Decimal = Field(sa_column=Column(Numeric(7, 6), nullable=False))
    daily_limit: int = Field(default=0, ge=0)
    issued_today: int = Field(default=0, ge=0)
    stat_date: str | None = Field(default=None, max_length=10)
    created_at: datetime = Field(default_factory=utc_now)


class Consumer(SQLModel, table=True):
    __tablename__ = "consumer"
    id: int | None = Field(default=None, primary_key=True)
    openid: str = Field(sa_column=Column(String(64), unique=True, nullable=False))
    nickname: str | None = Field(default=None, max_length=64)
    risk_status: str = Field(default="normal", max_length=16, index=True)
    risk_note: str = Field(default="", max_length=512)
    win_probability_multiplier: Decimal = Field(default=Decimal("1.0000"), sa_column=Column(Numeric(5, 4), nullable=False, server_default="1.0000"))
    anonymized_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)


class ScanEvent(SQLModel, table=True):
    __tablename__ = "scan_event"
    id: int | None = Field(default=None, primary_key=True)
    consumer_id: int | None = Field(default=None, foreign_key="consumer.id", index=True)
    qrcode_id: int | None = Field(default=None, foreign_key="qrcode.id", index=True)
    result: str = Field(default="accepted", max_length=32, index=True)
    created_at: datetime = Field(default_factory=utc_now)


class LotteryRecord(SQLModel, table=True):
    __tablename__ = "lottery_record"
    __table_args__ = (
        UniqueConstraint("consumer_id", "idempotency_key", name="uq_lottery_idempotency"),
    )
    id: int | None = Field(default=None, primary_key=True)
    consumer_id: int = Field(foreign_key="consumer.id", index=True)
    qrcode_id: int = Field(foreign_key="qrcode.id", index=True)
    activity_id: int = Field(foreign_key="activity.id", index=True)
    distribution_id: int | None = Field(default=None, foreign_key="code_distribution.id")
    lottery_policy_id: int | None = Field(default=None, foreign_key="lottery_policy.id", index=True)
    lottery_policy_version: int | None = Field(default=None)
    lottery_policy_scope: str | None = Field(default=None, max_length=16)
    prize_id: int | None = Field(default=None, foreign_key="prize.id")
    prize_name: str = Field(max_length=128)
    prize_type: PrizeType = Field(default=PrizeType.NONE)
    amount: Decimal = Field(default=Decimal("0.00"), sa_column=Column(Numeric(10, 2), nullable=False))
    redemption_status: RedemptionStatus = Field(default=RedemptionStatus.NOT_REQUIRED, sa_column=Column(String(16), nullable=False))
    redeemed_at: datetime | None = None
    redemption_note: str | None = Field(default=None, max_length=512)
    client_ip: str | None = Field(default=None, max_length=64)
    client_location: str | None = Field(default=None, max_length=255)
    client_province: str | None = Field(default=None, max_length=64)
    client_city: str | None = Field(default=None, max_length=64)
    client_district: str | None = Field(default=None, max_length=64)
    client_address: str | None = Field(default=None, max_length=512)
    idempotency_key: str = Field(max_length=64)
    is_retry: bool = Field(default=False)
    created_at: datetime = Field(default_factory=utc_now)


class DealerRedemption(SQLModel, table=True):
    __tablename__ = "dealer_redemption"
    __table_args__ = (UniqueConstraint("lottery_record_id", name="uq_dealer_redemption_record"),)
    id: int | None = Field(default=None, primary_key=True)
    lottery_record_id: int = Field(foreign_key="lottery_record.id", index=True)
    dealer_id: int = Field(foreign_key="dealer.id", index=True)
    dealer_account_id: int = Field(foreign_key="dealer_account.id")
    redemption_note: str = Field(default="", max_length=512)
    redeemed_at: datetime = Field(default_factory=utc_now)
    request_ip: str | None = Field(default=None, max_length=64)
    user_agent: str | None = Field(default=None, max_length=512)


class CashPayment(SQLModel, table=True):
    __tablename__ = "cash_payment"
    __table_args__ = (UniqueConstraint("lottery_record_id", name="uq_cash_payment_lottery"),)
    id: int | None = Field(default=None, primary_key=True)
    lottery_record_id: int = Field(foreign_key="lottery_record.id")
    merchant_order_no: str = Field(sa_column=Column(String(64), unique=True, nullable=False))
    amount: Decimal = Field(sa_column=Column(Numeric(10, 2), nullable=False))
    status: PaymentStatus = Field(default=PaymentStatus.PENDING)
    # WeChat merchant transfer / generic provider tracking. provider_out_bill_no
    # is the order number sent to the provider and may receive a retry suffix
    # after a terminal FAIL/CANCELLED attempt; it is nullable before the first
    # payout request so legacy/manual-only rows remain valid.
    provider: str = Field(default="", max_length=32)
    provider_out_bill_no: str | None = Field(default=None, max_length=32)
    transfer_bill_no: str | None = Field(default=None, max_length=64)
    transfer_state: str | None = Field(default=None, max_length=32)
    package_info: str | None = Field(default=None, max_length=2048)
    notify_event_id: str | None = Field(default=None, max_length=64)
    fail_reason: str | None = Field(default=None, max_length=256)
    provider_error_note: str | None = Field(default=None, max_length=512)
    attempts: int = Field(default=0, ge=0)
    provider_created_at: datetime | None = None
    provider_updated_at: datetime | None = None
    processed_at: datetime | None = None
    operator_note: str = Field(default="", max_length=512)
    created_at: datetime = Field(default_factory=utc_now)


class CouponCode(SQLModel, table=True):
    __tablename__ = "coupon_code"
    id: int | None = Field(default=None, primary_key=True)
    prize_id: int = Field(foreign_key="prize.id", index=True)
    code: str = Field(sa_column=Column(String(64), unique=True, nullable=False))
    status: str = Field(default="available", max_length=16, index=True)
    lottery_record_id: int | None = Field(default=None, foreign_key="lottery_record.id", unique=True)
    expires_at: datetime | None = None
    redeemed_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)


class PointLedger(SQLModel, table=True):
    __tablename__ = "point_ledger"
    id: int | None = Field(default=None, primary_key=True)
    consumer_id: int = Field(foreign_key="consumer.id", index=True)
    lottery_record_id: int = Field(foreign_key="lottery_record.id", unique=True)
    points: int = Field(gt=0)
    balance_after: int = Field(ge=0)
    description: str = Field(default="抽奖积分奖励", max_length=256)
    created_at: datetime = Field(default_factory=utc_now)
