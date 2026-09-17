from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models import ActivityStatus, MVP_PRIZE_TYPES, PaymentStatus, PrizeType, RedemptionStatus


class LoginRequest(BaseModel):
    username: str
    password: str


class AdminUserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    role: str = Field(default="operator", pattern="^(super_admin|operator|finance|viewer)$")


class AdminUserUpdate(BaseModel):
    is_active: bool


class DealerCreate(BaseModel):
    company_name: str
    contact_name: str
    phone: str = Field(min_length=6, max_length=20)
    province: str
    city: str
    district: str = ""
    level: int = Field(default=1, ge=1, le=3)
    remark: str | None = None


class StoreCreate(BaseModel):
    store_no: str = Field(min_length=3, max_length=32)
    name: str
    contact_name: str
    phone: str = Field(min_length=6, max_length=20)
    dealer_id: int
    redeemer_pin: str = Field(min_length=6, max_length=32)


class StoreStatusUpdate(BaseModel):
    status: str = Field(pattern="^(active|inactive)$")


class StorePinUpdate(BaseModel):
    redeemer_pin: str = Field(min_length=6, max_length=32)


class ReplenishmentCreate(BaseModel):
    store_id: int | None = None
    dealer_id: int | None = None
    prize_id: int
    quantity: int = Field(gt=0)
    note: str = Field(default="", max_length=512)

    @model_validator(mode="after")
    def validate_target(self):
        if self.store_id is None and self.dealer_id is None:
            raise ValueError("补货任务必须指定门店或经销商")
        if self.store_id is not None and self.dealer_id is not None:
            raise ValueError("补货任务不能同时指定门店和经销商")
        return self


class ReplenishmentUpdate(BaseModel):
    fulfilled_quantity: int = Field(ge=0)
    note: str = Field(default="", max_length=512)


class StoreRedeemRequest(BaseModel):
    store_no: str = Field(min_length=3, max_length=32)
    redeemer_pin: str = Field(min_length=6, max_length=32)
    redeem_code: str = Field(min_length=3, max_length=64)
    payment_method: str = Field(default="manual", max_length=32)


class DealerPortalLogin(BaseModel):
    username: str
    password: str


class DealerDrawNumberLookup(BaseModel):
    draw_number: str = Field(pattern=r"^\d{1,48}$")


class DealerRedemptionCreate(DealerDrawNumberLookup):
    redemption_note: str = Field(default="经销商登记兑换", max_length=512)


class DealerRedeemRequest(BaseModel):
    store_id: int | None = None
    redeem_code: str = Field(min_length=3, max_length=64)
    payment_method: str = Field(default="manual", max_length=32)


class DealerAccountCreate(BaseModel):
    dealer_id: int
    password: str = Field(min_length=8, max_length=128)


class DealerPasswordUpdate(BaseModel):
    password: str = Field(min_length=8, max_length=128)


class BatchCreate(BaseModel):
    batch_no: str = Field(min_length=3, max_length=32)
    batch_name: str
    prefix: str = Field(min_length=2, max_length=16)
    total_count: int = Field(gt=0, le=100_000)
    valid_start: datetime | None = None
    valid_end: datetime | None = None


class QrcodeActivateRequest(BaseModel):
    code_ids: list[int] = Field(min_length=1, max_length=100)


class DistributionCreate(BaseModel):
    batch_id: int
    dealer_id: int
    start_code_no: str | None = Field(default=None, max_length=48)
    end_code_no: str | None = Field(default=None, max_length=48)

    @model_validator(mode="after")
    def validate_code_range(self):
        has_start = bool(self.start_code_no and self.start_code_no.strip())
        has_end = bool(self.end_code_no and self.end_code_no.strip())
        if has_start != has_end:
            raise ValueError("起始和结束印刷号码需同时填写，或全部留空以登记整个批次")
        return self


class PrizeCreate(BaseModel):
    name: str
    type: PrizeType = PrizeType.CASH
    cash_amount: Decimal = Field(default=Decimal("0.00"), ge=0)
    upgrade_price: Decimal = Field(default=Decimal("0.00"), ge=0)
    total_stock: int = Field(ge=0)
    per_user_limit: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_prize_values(self):
        if self.type not in MVP_PRIZE_TYPES:
            raise ValueError("MVP 仅支持现金奖品和加价换购")
        if self.type == PrizeType.CASH and self.cash_amount <= 0:
            raise ValueError("现金红包金额必须大于 0")
        if self.type == PrizeType.UPGRADE and self.upgrade_price <= 0:
            raise ValueError("换购奖补差价必须大于 0")
        if self.type == PrizeType.CASH and self.upgrade_price:
            raise ValueError("现金奖品不能设置换购补差价")
        if self.type == PrizeType.UPGRADE and self.cash_amount:
            raise ValueError("加价换购不能设置现金金额")
        return self


class PrizeUpdate(PrizeCreate):
    status: str = Field(default="active", pattern="^(active|inactive)$")


class PrizeStatusUpdate(BaseModel):
    status: str = Field(pattern="^(active|inactive)$")


class ActivityCreate(BaseModel):
    name: str
    description: str = Field(default="", max_length=512)
    rules: str = Field(default="", max_length=2000)
    start_at: datetime
    end_at: datetime
    all_batches: bool = True
    status: ActivityStatus = ActivityStatus.DRAFT

    @field_validator("start_at", "end_at")
    @classmethod
    def normalize_activity_time(cls, value: datetime) -> datetime:
        """Store local admin input as naive UTC for MySQL DATETIME comparisons."""
        source = value.replace(tzinfo=ZoneInfo("Asia/Shanghai")) if value.tzinfo is None else value
        return source.astimezone(timezone.utc).replace(tzinfo=None)


class ActivityPrizeCreate(BaseModel):
    prize_id: int
    probability: Decimal = Field(ge=0, le=1)
    daily_limit: int = Field(default=0, ge=0)


class ActivityPrizeUpdate(BaseModel):
    probability: Decimal = Field(ge=0, le=1)
    daily_limit: int = Field(default=0, ge=0)


class LotteryPolicyPrizeInput(BaseModel):
    prize_id: int
    probability: Decimal = Field(ge=0, le=1)
    daily_limit: int = Field(default=0, ge=0)


class LotteryPolicyCreate(BaseModel):
    scope: str = Field(pattern="^(dealer|province|city|district)$")
    dealer_id: int | None = None
    province: str = Field(default="", max_length=32)
    city: str = Field(default="", max_length=32)
    district: str = Field(default="", max_length=32)
    note: str = Field(default="", max_length=512)
    prizes: list[LotteryPolicyPrizeInput] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_scope_and_pool(self):
        if self.scope == "dealer" and not self.dealer_id:
            raise ValueError("经销商策略必须指定 dealer_id")
        if self.scope != "dealer" and self.dealer_id:
            raise ValueError("地区策略不能指定 dealer_id")
        if self.scope != "dealer":
            fields = {"province": self.province, "city": self.city, "district": self.district}
            required = {"province": ["province"], "city": ["province", "city"], "district": ["province", "city", "district"]}[self.scope]
            if any(not fields[name].strip() for name in required):
                raise ValueError("地区策略缺少地域信息")
        if len({item.prize_id for item in self.prizes}) != len(self.prizes):
            raise ValueError("同一策略中不能重复配置奖品")
        if sum(item.probability for item in self.prizes) > 1:
            raise ValueError("奖池概率之和不能超过 1")
        return self


class LotteryPolicyStatusUpdate(BaseModel):
    status: str = Field(pattern="^(active|inactive)$")


class ActivityUpdate(ActivityCreate):
    pass


class ActivityStatusUpdate(BaseModel):
    status: ActivityStatus


class DevLoginRequest(BaseModel):
    openid: str = Field(min_length=3, max_length=64)
    nickname: str | None = Field(default=None, max_length=64)


class ScanValidateRequest(BaseModel):
    token: str = Field(min_length=16, max_length=256)


class RedemptionCodeVerifyRequest(BaseModel):
    verification_ticket: str
    redemption_code: str = Field(pattern="^\\d{4}$")


class DrawRequest(BaseModel):
    draw_ticket: str
    idempotency_key: str = Field(min_length=8, max_length=64)
    client_location: str | None = Field(default=None, max_length=255)


class RedemptionUpdate(BaseModel):
    redemption_status: RedemptionStatus
    redemption_note: str | None = Field(default=None, max_length=512)


class RedemptionBatchUpdate(BaseModel):
    record_ids: list[int] = Field(min_length=1, max_length=100)
    redemption_note: str | None = Field(default=None, max_length=512)


class DrawNumberLookup(BaseModel):
    draw_number: str = Field(pattern=r"^\d{1,48}$")


class ConsumerRiskUpdate(BaseModel):
    risk_status: str = Field(pattern="^(normal|blocked)$")
    risk_note: str = Field(default="", max_length=512)


class ConsumerProbabilityUpdate(BaseModel):
    win_probability_multiplier: Decimal = Field(ge=0, le=1, decimal_places=4)


class CashPaymentUpdate(BaseModel):
    status: PaymentStatus
    operator_note: str = Field(default="", max_length=512)


class CouponGenerateRequest(BaseModel):
    quantity: int = Field(ge=1, le=10_000)
    expires_at: datetime | None = None
