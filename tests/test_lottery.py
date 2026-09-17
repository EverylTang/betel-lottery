import zipfile
from datetime import timedelta
from decimal import Decimal
from io import BytesIO

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlmodel import Session, SQLModel, select

from app.cache import check_redis
from app.cache import redis_client
from app.api.admin import activate_batch, activate_selected_codes, batch_update_redemption, cancel_replenishment, create_dealer_account, create_distribution, create_replenishment, delete_activity, delete_prize, freeze_dealer, list_batches, list_consumers, list_lottery_records, lookup_redemption_by_draw_number, redeem_coupon, unfreeze_dealer, update_activity_status, update_cash_payment, update_lottery_policy, update_prize_status, update_redemption, update_replenishment
from app.api.dealer import create_redemption, redeem_upgrade_order
from app.core.config import Settings, get_settings
from app.models import Activity, ActivityBatch, ActivityPrize, AdminUser, CashPayment, CodeDistribution, CodeStatus, Consumer, CouponCode, Dealer, DealerAccount, LotteryPolicy, LotteryPolicyPrize, LotteryRecord, PointLedger, Prize, PrizeType, QrCode, QrCodeBatch, RedemptionStatus, Store, UpgradeOrder, utc_now
from app.security import encrypt_qr_token
from app.services.lottery import draw, token_hash, validate_scan, verify_redemption_code
from app.services.location import ResolvedLocation
from app.services.activity_rules import generate_activity_rules
from app.services.qrcode_export import export_code_csv, export_print_package
from app.schemas import ActivityCreate, ActivityStatusUpdate, CashPaymentUpdate, DealerAccountCreate, DealerRedemptionCreate, DealerRedeemRequest, DistributionCreate, DrawNumberLookup, LotteryPolicyCreate, PrizeCreate, PrizeStatusUpdate, QrcodeActivateRequest, RedemptionBatchUpdate, RedemptionUpdate, ReplenishmentCreate, ReplenishmentUpdate
from app.api import h5 as h5_api


@pytest.fixture()
def mysql_session():
    settings = get_settings()
    test_url = make_url(settings.database_url).set(database="betel_lottery_test")
    admin_engine = create_engine(settings.database_url)
    with admin_engine.begin() as connection:
        connection.execute(text("CREATE DATABASE IF NOT EXISTS betel_lottery_test CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"))
    engine = create_engine(test_url, pool_pre_ping=True)
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    SQLModel.metadata.drop_all(engine)
    engine.dispose()
    admin_engine.dispose()


def test_real_mysql_and_redis_are_available(mysql_session: Session):
    assert mysql_session.exec(text("SELECT 1")).one() == (1,)
    assert check_redis() is True


def test_production_configuration_rejects_insecure_defaults():
    with pytest.raises(ValueError, match="JWT_SECRET"):
        Settings(
            environment="production",
            database_url="mysql+pymysql://user:password@localhost:3306/lottery",
            redis_url="redis://localhost:6379/0",
        )


def test_staging_with_wechat_credentials_uses_real_oauth_mode(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(h5_api.settings, "environment", "staging")
    monkeypatch.setattr(h5_api.settings, "wechat_app_id", "wx-test")
    monkeypatch.setattr(h5_api.settings, "wechat_app_secret", "secret-test")

    assert h5_api.auth_mode() == {"oauth_enabled": True}


def test_production_configuration_requires_explicit_wechat_runtime_settings():
    with pytest.raises(ValueError, match="WECHAT_APP_ID"):
        Settings(
            environment="production",
            database_url="mysql+pymysql://user:password@localhost:3306/lottery",
            redis_url="redis://localhost:6379/0",
            jwt_secret="x" * 32,
            admin_bootstrap_password="strong-password-123",
            h5_base_url="https://promo.example.com/scan",
            force_https=True,
            cors_origins="https://promo.example.com",
            wechatpay_enabled=True,
            wechatpay_mchid="1234567890",
            wechatpay_mch_serial_no="serial",
            wechatpay_mch_private_key_path="/run/secrets/apiclient_key.pem",
            wechatpay_mch_public_key_id="PUB_KEY_ID",
            wechatpay_mch_public_key_path="/run/secrets/wechatpay_public_key.pem",
            wechatpay_api_v3_key="k" * 32,
            wechatpay_notify_base_url="https://api.example.com",
        )


def test_production_configuration_rejects_pathful_payment_callback_base():
    with pytest.raises(ValueError, match="无路径"):
        Settings(
            environment="production",
            database_url="mysql+pymysql://user:password@localhost:3306/lottery",
            redis_url="redis://localhost:6379/0",
            jwt_secret="x" * 32,
            admin_bootstrap_password="strong-password-123",
            h5_base_url="https://promo.example.com/scan",
            force_https=True,
            cors_origins="https://promo.example.com",
            wechat_app_id="wx123",
            wechat_app_secret="secret",
            wechat_oauth_redirect_uri="https://promo.example.com/api/h5/auth/wechat/callback",
            wechatpay_enabled=True,
            wechatpay_mchid="1234567890",
            wechatpay_mch_serial_no="serial",
            wechatpay_mch_private_key_path="/run/secrets/apiclient_key.pem",
            wechatpay_mch_public_key_id="PUB_KEY_ID",
            wechatpay_mch_public_key_path="/run/secrets/wechatpay_public_key.pem",
            wechatpay_api_v3_key="k" * 32,
            wechatpay_notify_base_url="https://api.example.com/callback",
        )


def test_h5_rules_display_time_without_participation_limits():
    activity = Activity(
        name="规则测试",
        start_at=utc_now() - timedelta(days=1),
        end_at=utc_now() + timedelta(days=1),
    )
    rules = generate_activity_rules(activity)
    assert "每日最多参与2次" not in rules
    assert "累计最多参与8次" not in rules
    assert "活动时间：" in rules
    assert "每个二维码仅限抽奖一次" in rules


def test_activity_accepts_no_draw_limit_configuration():
    activity = ActivityCreate(name="无限次活动", start_at=utc_now(), end_at=utc_now() + timedelta(hours=1))
    assert "daily_limit_per_user" not in activity.model_dump()


def test_prize_and_activity_status_can_be_updated_without_editing_full_forms(mysql_session: Session):
    admin = AdminUser(username="status-admin", password_hash="unused", role="super_admin")
    prize = Prize(name="状态奖品", type=PrizeType.CASH, total_stock=10)
    activity = Activity(name="状态活动", start_at=utc_now() - timedelta(hours=1), end_at=utc_now() + timedelta(hours=1), status="draft")
    mysql_session.add_all([admin, prize, activity])
    mysql_session.commit()

    update_prize_status(prize.id, PrizeStatusUpdate(status="inactive"), mysql_session, admin)
    update_activity_status(activity.id, ActivityStatusUpdate(status="active"), mysql_session, admin)
    mysql_session.refresh(prize)
    mysql_session.refresh(activity)
    assert prize.status == "inactive"
    assert activity.status == "active"


def test_only_stopped_unreferenced_prizes_and_activities_can_be_deleted(mysql_session: Session):
    admin = AdminUser(username="delete-admin", password_hash="unused", role="super_admin")
    prize = Prize(name="待删除奖品", type=PrizeType.CASH, total_stock=1)
    activity = Activity(name="待删除活动", start_at=utc_now() - timedelta(hours=1), end_at=utc_now() + timedelta(hours=1), status="draft")
    mysql_session.add_all([admin, prize, activity])
    mysql_session.commit()

    with pytest.raises(HTTPException, match="先停用奖品"):
        delete_prize(prize.id, mysql_session, admin)
    update_prize_status(prize.id, PrizeStatusUpdate(status="inactive"), mysql_session, admin)
    delete_prize(prize.id, mysql_session, admin)
    delete_activity(activity.id, mysql_session, admin)
    assert mysql_session.get(Prize, prize.id) is None
    assert mysql_session.get(Activity, activity.id) is None


def test_qrcode_batch_activation_requires_an_effective_activity(mysql_session: Session):
    batch = QrCodeBatch(batch_no="ACT001", batch_name="活动校验", prefix="V", total_count=1)
    mysql_session.add(batch)
    mysql_session.commit()
    code = QrCode(code_no="V00000001", token_hash=token_hash("h" * 24), batch_id=batch.id, status=CodeStatus.INACTIVE)
    mysql_session.add(code)
    mysql_session.commit()

    with pytest.raises(HTTPException, match="没有生效活动"):
        activate_batch(batch.id, mysql_session, None)

    mysql_session.add(Activity(name="有效活动", start_at=utc_now() - timedelta(hours=1), end_at=utc_now() + timedelta(hours=1), status="active"))
    mysql_session.commit()
    assert activate_batch(batch.id, mysql_session, None)["activated_count"] == 1
    mysql_session.refresh(code)
    assert code.status == CodeStatus.ACTIVE


def test_selected_qrcodes_can_be_activated_individually(mysql_session: Session):
    admin = AdminUser(username="selected-code-admin", password_hash="unused", role="super_admin")
    batch = QrCodeBatch(batch_no="SELECT001", batch_name="单码激活", prefix="S", total_count=2)
    activity = Activity(name="生效活动", start_at=utc_now() - timedelta(hours=1), end_at=utc_now() + timedelta(hours=1), status="active")
    mysql_session.add_all([admin, batch, activity])
    mysql_session.commit()
    first = QrCode(code_no="S00000001", token_hash=token_hash("s" * 24), batch_id=batch.id)
    second = QrCode(code_no="S00000002", token_hash=token_hash("t" * 24), batch_id=batch.id)
    mysql_session.add_all([first, second])
    mysql_session.commit()

    result = activate_selected_codes(batch.id, QrcodeActivateRequest(code_ids=[first.id]), mysql_session, admin)

    mysql_session.refresh(first)
    mysql_session.refresh(second)
    assert result["activated_count"] == 1
    assert first.status == CodeStatus.ACTIVE
    assert second.status == CodeStatus.INACTIVE

    batch_summary = next(item for item in list_batches(mysql_session, admin) if item["id"] == batch.id)
    assert batch_summary["activation_status"] == "partial"
    assert batch_summary["inactive_count"] == 1


def test_distribution_defaults_to_the_entire_batch_when_range_is_empty(mysql_session: Session):
    admin = AdminUser(username="distribution-admin", password_hash="unused", role="super_admin")
    dealer = Dealer(dealer_no="D001", company_name="测试经销商", contact_name="测试", phone="13800000000", province="湖南", city="长沙")
    batch = QrCodeBatch(batch_no="DIST001", batch_name="流向测试", prefix="D", total_count=2)
    mysql_session.add_all([admin, dealer, batch])
    mysql_session.commit()
    mysql_session.add_all([
        QrCode(code_no="D00000001", token_hash=token_hash("d" * 24), batch_id=batch.id),
        QrCode(code_no="D00000002", token_hash=token_hash("e" * 24), batch_id=batch.id),
    ])
    mysql_session.commit()

    distribution = create_distribution(DistributionCreate(batch_id=batch.id, dealer_id=dealer.id), mysql_session, admin)

    assert distribution.quantity == 2
    assert distribution.start_code_id < distribution.end_code_id


def test_distribution_requires_both_range_endpoints_or_neither():
    with pytest.raises(ValueError, match="同时填写"):
        DistributionCreate(batch_id=1, dealer_id=1, start_code_no="D00000001")


def test_dealer_account_uses_dealer_phone_as_the_login_username(mysql_session: Session):
    admin = AdminUser(username="account-admin", password_hash="unused", role="super_admin")
    dealer = Dealer(dealer_no="D002", company_name="账号测试经销商", contact_name="测试", phone="13800000002", province="湖南", city="长沙")
    mysql_session.add_all([admin, dealer])
    mysql_session.commit()

    created = create_dealer_account(DealerAccountCreate(dealer_id=dealer.id, password="password-123"), mysql_session, admin)

    assert created["username"] == dealer.phone


def test_scan_rejects_an_inactive_activity(mysql_session: Session):
    consumer = Consumer(openid="inactive-activity-openid")
    activity = Activity(name="未生效活动", start_at=utc_now() - timedelta(hours=1), end_at=utc_now() + timedelta(hours=1), status="draft")
    batch = QrCodeBatch(batch_no="INACTIVE001", batch_name="未生效", prefix="I", total_count=1, status="active")
    mysql_session.add_all([consumer, activity, batch])
    mysql_session.commit()
    mysql_session.add(QrCode(code_no="I00000001", token_hash=token_hash("i" * 24), batch_id=batch.id, status=CodeStatus.ACTIVE))
    mysql_session.commit()

    with pytest.raises(HTTPException, match="没有可参与的活动"):
        validate_scan(mysql_session, consumer, "i" * 24)


def test_new_qrcode_requires_matching_inner_redemption_code(mysql_session: Session):
    consumer = Consumer(openid="inner-code-openid")
    activity = Activity(name="袋内码测试", start_at=utc_now() - timedelta(hours=1), end_at=utc_now() + timedelta(hours=1), status="active")
    batch = QrCodeBatch(batch_no="INNER001", batch_name="袋内码", prefix="I", total_count=1, status="active")
    mysql_session.add_all([consumer, activity, batch])
    mysql_session.commit()
    raw_token = "v" * 24
    code = QrCode(code_no="I00000001", token_hash=token_hash(raw_token), token_ciphertext=encrypt_qr_token(raw_token), redemption_code_hash=token_hash("4829"), redemption_code_ciphertext=encrypt_qr_token("4829"), batch_id=batch.id, status=CodeStatus.ACTIVE)
    none = Prize(name="袋内码谢谢参与", type=PrizeType.NONE)
    mysql_session.add_all([code, none])
    mysql_session.commit()
    mysql_session.add(ActivityPrize(activity_id=activity.id, prize_id=none.id, probability=1))
    mysql_session.commit()

    scan = validate_scan(mysql_session, consumer, raw_token)
    assert scan["requires_redemption_code"] is True
    assert "draw_ticket" not in scan
    with pytest.raises(HTTPException, match="不正确"):
        verify_redemption_code(mysql_session, consumer, scan["verification_ticket"], "0000")
    verified = verify_redemption_code(mysql_session, consumer, scan["verification_ticket"], "4829")
    assert verified["draw_ticket"]


def test_one_code_can_only_draw_once(mysql_session: Session, monkeypatch: pytest.MonkeyPatch):
    consumer = Consumer(openid="test-openid")
    activity = Activity(name="test", start_at=utc_now() - timedelta(hours=1), end_at=utc_now() + timedelta(hours=1), status="active")
    batch = QrCodeBatch(batch_no="B001", batch_name="test", prefix="T", total_count=1, status="active")
    mysql_session.add_all([consumer, activity, batch])
    mysql_session.commit()
    code = QrCode(code_no="T00000001", token_hash=token_hash("a" * 24), batch_id=batch.id, status=CodeStatus.ACTIVE)
    prize = Prize(name="谢谢参与", type=PrizeType.NONE, total_stock=0)
    mysql_session.add_all([code, prize, ActivityBatch(activity_id=activity.id, batch_id=batch.id)])
    mysql_session.commit()
    mysql_session.add(ActivityPrize(activity_id=activity.id, prize_id=prize.id, probability=1))
    mysql_session.commit()

    monkeypatch.setattr(
        "app.services.lottery.resolve_location",
        lambda _value: ResolvedLocation(province="上海市", city="上海市", district="黄浦区", address="上海市黄浦区人民大道200号"),
    )
    ticket = validate_scan(mysql_session, consumer, "a" * 24)["draw_ticket"]
    first = draw(mysql_session, consumer, ticket, "idem-key-0001", client_ip="203.0.113.8", client_location="31.230400,121.473700")
    second = draw(mysql_session, consumer, ticket, "idem-key-0002")
    record = mysql_session.get(LotteryRecord, first["record_id"])
    assert first["record_id"] == second["record_id"]
    assert first["prize_type"] == PrizeType.NONE
    assert record.client_ip == "203.0.113.8"
    assert record.client_location == "31.230400,121.473700"
    assert record.client_province == "上海市"
    assert record.client_city == "上海市"
    assert record.client_district == "黄浦区"
    assert record.client_address == "上海市黄浦区人民大道200号"


def test_consumer_winner_count_counts_every_non_none_prize(mysql_session: Session):
    consumer = Consumer(openid="winner-count-consumer")
    activity = Activity(name="中奖统计", start_at=utc_now() - timedelta(hours=1), end_at=utc_now() + timedelta(hours=1), status="active")
    batch = QrCodeBatch(batch_no="WINCOUNT001", batch_name="中奖统计", prefix="W", total_count=3, status="active")
    mysql_session.add_all([consumer, activity, batch])
    mysql_session.commit()
    mysql_session.add_all([
        QrCode(code_no="W00000001", token_hash=token_hash("winner-count-code-000001"), batch_id=batch.id),
        QrCode(code_no="W00000002", token_hash=token_hash("winner-count-code-000002"), batch_id=batch.id),
        QrCode(code_no="W00000003", token_hash=token_hash("winner-count-code-000003"), batch_id=batch.id),
    ])
    mysql_session.commit()
    codes = mysql_session.exec(select(QrCode).where(QrCode.batch_id == batch.id).order_by(QrCode.id)).all()
    mysql_session.add_all([
        LotteryRecord(consumer_id=consumer.id, qrcode_id=codes[0].id, activity_id=activity.id, prize_name="谢谢参与", prize_type=PrizeType.NONE, idempotency_key="winner-count-none"),
        LotteryRecord(consumer_id=consumer.id, qrcode_id=codes[1].id, activity_id=activity.id, prize_name="10 元换购", prize_type=PrizeType.UPGRADE, idempotency_key="winner-count-upgrade-1"),
        LotteryRecord(consumer_id=consumer.id, qrcode_id=codes[2].id, activity_id=activity.id, prize_name="20 元换购", prize_type=PrizeType.UPGRADE, idempotency_key="winner-count-upgrade-2"),
    ])
    mysql_session.commit()

    summary = next(item for item in list_consumers(mysql_session, None, None) if item["id"] == consumer.id)
    assert summary["draw_count"] == 3
    assert summary["win_count"] == 2


def test_activity_does_not_limit_draw_count(mysql_session: Session):
    consumer = Consumer(openid="unlimited-draw-consumer")
    activity = Activity(name="不限次数", start_at=utc_now() - timedelta(hours=1), end_at=utc_now() + timedelta(hours=1), status="active")
    batch = QrCodeBatch(batch_no="UNLIMITED001", batch_name="不限次数", prefix="N", total_count=4, status="active")
    prize = Prize(name="不限次数谢谢参与", type=PrizeType.NONE, total_stock=0)
    mysql_session.add_all([consumer, activity, batch, prize])
    mysql_session.commit()
    tokens = [f"unlimited-token-{index:02d}".ljust(24, "x") for index in range(4)]
    mysql_session.add_all([QrCode(code_no=f"N{index:08d}", token_hash=token_hash(token), batch_id=batch.id, status=CodeStatus.ACTIVE) for index, token in enumerate(tokens, start=1)])
    mysql_session.add(ActivityPrize(activity_id=activity.id, prize_id=prize.id, probability=1))
    mysql_session.commit()

    for index, token in enumerate(tokens, start=1):
        ticket = validate_scan(mysql_session, consumer, token)["draw_ticket"]
        assert draw(mysql_session, consumer, ticket, f"unlimited-draw-{index:02d}")["prize_type"] == PrizeType.NONE
    assert len(mysql_session.exec(select(LotteryRecord).where(LotteryRecord.consumer_id == consumer.id)).all()) == 4


def test_dealer_policy_overrides_default_pool_and_is_recorded(mysql_session: Session):
    consumer = Consumer(openid="dealer-policy-consumer")
    activity = Activity(name="经销商策略", start_at=utc_now() - timedelta(hours=1), end_at=utc_now() + timedelta(hours=1), status="active")
    batch = QrCodeBatch(batch_no="POLICY001", batch_name="策略批次", prefix="D", total_count=1, status="active")
    dealer = Dealer(dealer_no="DLR001", company_name="华东经销商", contact_name="张三", phone="13800000001", province="上海市", city="上海市", district="浦东新区")
    mysql_session.add_all([consumer, activity, batch, dealer])
    mysql_session.commit()
    raw_token = "p" * 24
    code = QrCode(code_no="D00000001", token_hash=token_hash(raw_token), batch_id=batch.id, status=CodeStatus.ACTIVE)
    cash = Prize(name="经销商现金奖", type=PrizeType.CASH, cash_amount=2, total_stock=1)
    default_none = Prize(name="默认谢谢参与", type=PrizeType.NONE, total_stock=0)
    mysql_session.add_all([code, cash, default_none])
    mysql_session.commit()
    mysql_session.add(ActivityPrize(activity_id=activity.id, prize_id=default_none.id, probability=1))
    mysql_session.add(CodeDistribution(distribution_no="DIST001", batch_id=batch.id, dealer_id=dealer.id, start_code_id=code.id, end_code_id=code.id, quantity=1))
    policy = LotteryPolicy(activity_id=activity.id, scope="dealer", dealer_id=dealer.id, version=1)
    mysql_session.add(policy)
    mysql_session.commit()
    mysql_session.add(LotteryPolicyPrize(policy_id=policy.id, prize_id=cash.id, probability=1))
    mysql_session.commit()

    result = draw(mysql_session, consumer, validate_scan(mysql_session, consumer, raw_token)["draw_ticket"], "dealer-policy-draw")
    record = mysql_session.get(LotteryRecord, result["record_id"])
    assert result["prize_name"] == "经销商现金奖"
    assert record.lottery_policy_id == policy.id
    assert record.lottery_policy_version == 1
    assert record.lottery_policy_scope == "dealer"
    admin = AdminUser(username="record-admin", password_hash="unused")
    mysql_session.add(admin)
    mysql_session.commit()
    record_page = list_lottery_records(mysql_session, admin, page=1, page_size=20, keyword=None)
    assert record_page["items"][0]["batch_no"] == batch.batch_no
    assert record_page["items"][0]["dealer_name"] == dealer.company_name


def test_active_policy_update_publishes_immutable_next_version(mysql_session: Session):
    admin = AdminUser(username="policy-version-admin", password_hash="unused", role="super_admin")
    dealer = Dealer(dealer_no="VERSION001", company_name="版本经销商", contact_name="测试", phone="13800000009", province="湖南", city="长沙")
    activity = Activity(name="策略版本活动", start_at=utc_now() - timedelta(hours=1), end_at=utc_now() + timedelta(hours=1), status="active")
    prize = Prize(name="版本奖品", type=PrizeType.CASH, cash_amount=1, total_stock=10)
    mysql_session.add_all([admin, dealer, activity, prize])
    mysql_session.commit()
    current = LotteryPolicy(activity_id=activity.id, scope="dealer", dealer_id=dealer.id, version=1, status="active", note="v1")
    mysql_session.add(current)
    mysql_session.commit()
    mysql_session.add(LotteryPolicyPrize(policy_id=current.id, prize_id=prize.id, probability=1))
    mysql_session.commit()

    updated = update_lottery_policy(
        current.id,
        LotteryPolicyCreate(scope="dealer", dealer_id=dealer.id, note="v2", prizes=[{"prize_id": prize.id, "probability": "0.5", "daily_limit": 2}]),
        mysql_session,
        admin,
    )

    mysql_session.refresh(current)
    assert current.status == "superseded"
    assert current.version == 1
    assert updated.id != current.id
    assert updated.status == "active"
    assert updated.version == 2


@pytest.mark.parametrize("prize_type", [PrizeType.COUPON, PrizeType.POINTS, PrizeType.PHYSICAL, PrizeType.RETRY, PrizeType.NONE])
def test_mvp_prize_schema_rejects_legacy_prize_types(prize_type: PrizeType):
    with pytest.raises(ValidationError, match="MVP 仅支持现金奖品和加价换购"):
        PrizeCreate(name="历史奖品", type=prize_type, total_stock=1)


def test_replenishment_and_manual_fulfillment_apis_are_gone():
    for operation in (
        lambda: create_replenishment(None, None, None),
        lambda: update_replenishment(1, None, None, None),
        lambda: cancel_replenishment(1, None, None),
        lambda: update_redemption(1, None, None, None),
        lambda: batch_update_redemption(None, None, None),
        lambda: update_cash_payment(1, None, None, None),
    ):
        with pytest.raises(HTTPException) as exc:
            operation()
        assert exc.value.status_code == 410


def test_consumer_win_probability_multiplier_can_suppress_winning(mysql_session: Session):
    consumer = Consumer(openid="zero-win-probability", win_probability_multiplier=0)
    activity = Activity(name="用户系数测试", start_at=utc_now() - timedelta(hours=1), end_at=utc_now() + timedelta(hours=1), status="active")
    batch = QrCodeBatch(batch_no="MULTIPLIER001", batch_name="用户系数", prefix="M", total_count=1, status="active")
    mysql_session.add_all([consumer, activity, batch])
    mysql_session.commit()
    raw_token = "m" * 24
    mysql_session.add_all([
        QrCode(code_no="M00000001", token_hash=token_hash(raw_token), batch_id=batch.id, status=CodeStatus.ACTIVE),
        Prize(name="必中奖品", type=PrizeType.CASH, cash_amount=1, total_stock=1),
        Prize(name="谢谢参与", type=PrizeType.NONE),
    ])
    mysql_session.commit()
    prizes = mysql_session.exec(select(Prize).where(Prize.name.in_(["必中奖品", "谢谢参与"]))).all()
    mysql_session.add_all([ActivityPrize(activity_id=activity.id, prize_id=prize.id, probability=1 if prize.name == "必中奖品" else 0) for prize in prizes])
    mysql_session.commit()

    result = draw(mysql_session, consumer, validate_scan(mysql_session, consumer, raw_token)["draw_ticket"], "zero-win-probability")
    assert result["prize_type"] == PrizeType.NONE


def test_prize_per_user_limit_excludes_prize_after_it_is_won(mysql_session: Session):
    consumer = Consumer(openid="prize-limit-openid")
    activity = Activity(
        name="奖品限额测试",
        start_at=utc_now() - timedelta(hours=1),
        end_at=utc_now() + timedelta(hours=1),
        status="active",
    )
    batch = QrCodeBatch(batch_no="LIMIT001", batch_name="限额测试", prefix="U", total_count=2, status="active")
    mysql_session.add_all([consumer, activity, batch])
    mysql_session.commit()
    first_token, second_token = "f" * 24, "g" * 24
    mysql_session.add_all(
        [
            QrCode(code_no="U00000001", token_hash=token_hash(first_token), batch_id=batch.id, status=CodeStatus.ACTIVE),
            QrCode(code_no="U00000002", token_hash=token_hash(second_token), batch_id=batch.id, status=CodeStatus.ACTIVE),
            Prize(name="限领现金", type=PrizeType.CASH, cash_amount=1, total_stock=10, per_user_limit=1),
            Prize(name="谢谢参与", type=PrizeType.NONE, total_stock=0),
        ]
    )
    mysql_session.commit()
    cash_prize = mysql_session.exec(select(Prize).where(Prize.name == "限领现金")).one()
    none_prize = mysql_session.exec(select(Prize).where(Prize.name == "谢谢参与")).one()
    mysql_session.add_all(
        [
            ActivityPrize(activity_id=activity.id, prize_id=cash_prize.id, probability=1),
            ActivityPrize(activity_id=activity.id, prize_id=none_prize.id, probability=0),
        ]
    )
    mysql_session.commit()

    first_ticket = validate_scan(mysql_session, consumer, first_token)["draw_ticket"]
    assert draw(mysql_session, consumer, first_ticket, "prize-limit-first")["prize_name"] == "限领现金"
    second_ticket = validate_scan(mysql_session, consumer, second_token)["draw_ticket"]
    assert draw(mysql_session, consumer, second_ticket, "prize-limit-second")["prize_type"] == PrizeType.NONE


def test_cash_draw_creates_pending_wechat_payment(mysql_session: Session):
    consumer = Consumer(openid="cash-reward-openid")
    activity = Activity(name="现金奖", start_at=utc_now() - timedelta(hours=1), end_at=utc_now() + timedelta(hours=1), status="active")
    batch = QrCodeBatch(batch_no="CASH001", batch_name="现金", prefix="R", total_count=1, status="active")
    mysql_session.add_all([consumer, activity, batch])
    mysql_session.commit()
    code = QrCode(code_no="R00000001", token_hash=token_hash("r" * 24), batch_id=batch.id, status=CodeStatus.ACTIVE)
    prize = Prize(name="2 元现金", type=PrizeType.CASH, cash_amount=Decimal("2.00"), total_stock=1)
    mysql_session.add_all([code, prize])
    mysql_session.commit()
    mysql_session.add(ActivityPrize(activity_id=activity.id, prize_id=prize.id, probability=1))
    mysql_session.commit()

    result = draw(mysql_session, consumer, validate_scan(mysql_session, consumer, "r" * 24)["draw_ticket"], "cash-reward-1")
    payment = mysql_session.exec(select(CashPayment).where(CashPayment.lottery_record_id == result["record_id"])).one()

    assert result["prize_type"] == PrizeType.CASH
    assert result["redemption_status"] == RedemptionStatus.PENDING
    assert payment.status.value == "pending"


def test_legacy_prize_in_pool_is_skipped(mysql_session: Session):
    consumer = Consumer(openid="legacy-prize-openid")
    activity = Activity(name="历史奖品过滤", start_at=utc_now() - timedelta(hours=1), end_at=utc_now() + timedelta(hours=1), status="active")
    batch = QrCodeBatch(batch_no="LEGACY001", batch_name="历史", prefix="L", total_count=1, status="active")
    mysql_session.add_all([consumer, activity, batch])
    mysql_session.commit()
    code = QrCode(code_no="L00000001", token_hash=token_hash("l" * 24), batch_id=batch.id, status=CodeStatus.ACTIVE)
    coupon = Prize(name="旧券", type=PrizeType.COUPON, total_stock=1)
    mysql_session.add_all([code, coupon])
    mysql_session.commit()
    mysql_session.add(ActivityPrize(activity_id=activity.id, prize_id=coupon.id, probability=1))
    mysql_session.commit()

    result = draw(mysql_session, consumer, validate_scan(mysql_session, consumer, "l" * 24)["draw_ticket"], "legacy-prize-1")

    assert result["prize_type"] == PrizeType.NONE


def test_dealer_general_redemption_endpoint_is_gone(mysql_session: Session):
    dealer = Dealer(dealer_no="RED-CASH", company_name="兑奖经销商", contact_name="测试", phone="13900000001", province="湖南", city="长沙")
    account = DealerAccount(dealer_id=1, username="dealer-cash", password_hash="unused")
    consumer = Consumer(openid="dealer-redemption-cash")
    activity = Activity(name="兑奖限制", start_at=utc_now() - timedelta(hours=1), end_at=utc_now() + timedelta(hours=1), status="active")
    batch = QrCodeBatch(batch_no="DR-CASH", batch_name="兑奖限制", prefix="D", total_count=1)
    mysql_session.add_all([dealer, consumer, activity, batch])
    mysql_session.commit()
    account.dealer_id = dealer.id
    code = QrCode(code_no="D00000001", token_hash=token_hash("dealer-cash"), batch_id=batch.id)
    prize = Prize(name="现金奖", type=PrizeType.CASH, cash_amount=Decimal("1.00"), total_stock=1)
    mysql_session.add_all([account, code, prize])
    mysql_session.commit()
    distribution = CodeDistribution(distribution_no="DIST-CASH", batch_id=batch.id, dealer_id=dealer.id, start_code_id=code.id, end_code_id=code.id, quantity=1)
    mysql_session.add(distribution)
    mysql_session.commit()
    record = LotteryRecord(consumer_id=consumer.id, qrcode_id=code.id, activity_id=activity.id, distribution_id=distribution.id, prize_id=prize.id, prize_name=prize.name, prize_type=PrizeType.CASH, redemption_status=RedemptionStatus.PENDING, idempotency_key="dealer-general-cash")
    mysql_session.add(record)
    mysql_session.commit()

    with pytest.raises(HTTPException) as exc:
        create_redemption(DealerRedemptionCreate(draw_number="00000001"), None, mysql_session, account)
    assert exc.value.status_code == 410
    mysql_session.refresh(record)
    assert record.redemption_status == RedemptionStatus.PENDING


def test_freezing_dealer_disables_existing_redemption_account(mysql_session: Session):
    admin = AdminUser(username="freeze-admin", password_hash="unused", role="super_admin")
    dealer = Dealer(dealer_no="FREEZE001", company_name="冻结经销商", contact_name="测试", phone="13900009999", province="湖南", city="长沙")
    mysql_session.add_all([admin, dealer])
    mysql_session.commit()
    account = DealerAccount(dealer_id=dealer.id, username="freeze-dealer", password_hash="unused")
    mysql_session.add(account)
    mysql_session.commit()

    freeze_dealer(dealer.id, mysql_session, admin)
    mysql_session.refresh(dealer)
    mysql_session.refresh(account)
    assert dealer.status == "frozen"
    assert account.is_active is False
    unfreeze_dealer(dealer.id, mysql_session, admin)
    mysql_session.refresh(dealer)
    mysql_session.refresh(account)
    assert dealer.status == "active"
    assert account.is_active is True


def test_operator_cannot_freeze_dealer(mysql_session: Session):
    operator = AdminUser(username="freeze-operator", password_hash="unused", role="operator")
    dealer = Dealer(dealer_no="FREEZE002", company_name="权限经销商", contact_name="测试", phone="13900009998", province="湖南", city="长沙")
    mysql_session.add_all([operator, dealer])
    mysql_session.commit()

    with pytest.raises(HTTPException, match="仅超级管理员"):
        freeze_dealer(dealer.id, mysql_session, operator)


def test_upgrade_winner_is_redeemed_only_by_dealer_upgrade_flow(mysql_session: Session):
    admin = AdminUser(username="upgrade-redemption-admin", password_hash="unused", role="super_admin")
    consumer = Consumer(openid="upgrade-redemption-consumer")
    activity = Activity(name="换购核销", start_at=utc_now() - timedelta(hours=1), end_at=utc_now() + timedelta(hours=1), status="active")
    batch = QrCodeBatch(batch_no="UPGRADE001", batch_name="换购核销", prefix="G", total_count=1, status="active")
    mysql_session.add_all([admin, consumer, activity, batch])
    mysql_session.commit()
    code = QrCode(code_no="G00000001", token_hash=token_hash("u" * 24), batch_id=batch.id, status=CodeStatus.ACTIVE)
    prize = Prize(name="换购礼盒", type=PrizeType.UPGRADE, upgrade_price=Decimal("10.00"), total_stock=1)
    mysql_session.add_all([code, prize])
    mysql_session.commit()
    dealer = Dealer(dealer_no="UPG-DEALER", company_name="换购经销商", contact_name="测试", phone="13800000011", province="湖南", city="长沙")
    mysql_session.add_all([ActivityPrize(activity_id=activity.id, prize_id=prize.id, probability=1), dealer])
    mysql_session.commit()
    account = DealerAccount(dealer_id=dealer.id, username="upgrade-dealer", password_hash="unused")
    distribution = CodeDistribution(distribution_no="UPG-DIST", batch_id=batch.id, dealer_id=dealer.id, start_code_id=code.id, end_code_id=code.id, quantity=1)
    mysql_session.add_all([account, distribution])
    mysql_session.commit()
    draw_result = draw(mysql_session, consumer, validate_scan(mysql_session, consumer, "u" * 24)["draw_ticket"], "upgrade-redemption-draw")
    record_id = draw_result["record_id"]
    upgrade_order = mysql_session.exec(select(UpgradeOrder).where(UpgradeOrder.lottery_record_id == record_id)).one()
    assert upgrade_order.status == "pending_store_redemption"
    assert upgrade_order.amount == Decimal("10.00")
    assert upgrade_order.redeem_code.startswith("UPG-")
    assert draw_result["upgrade_redeem_code"] == upgrade_order.redeem_code
    assert draw_result["upgrade_amount"] == "10.00"

    result = lookup_redemption_by_draw_number(DrawNumberLookup(draw_number="00000001"), mysql_session, admin)
    assert result["is_winner"] is True
    assert result["prize_name"] == "换购礼盒"

    redeemed = redeem_upgrade_order(DealerRedeemRequest(redeem_code=upgrade_order.redeem_code), None, mysql_session, account)
    assert redeemed["status"] == "redeemed"
    mysql_session.refresh(mysql_session.get(LotteryRecord, record_id))
    assert mysql_session.get(LotteryRecord, record_id).redemption_status == RedemptionStatus.REDEEMED


def test_all_batches_activity_does_not_require_explicit_batch_link(mysql_session: Session):
    consumer = Consumer(openid="all-batches-openid")
    activity = Activity(name="all batches", start_at=utc_now() - timedelta(hours=1), end_at=utc_now() + timedelta(hours=1), status="active", all_batches=True)
    batch = QrCodeBatch(batch_no="ALL001", batch_name="all", prefix="A", total_count=1, status="active")
    mysql_session.add_all([consumer, activity, batch])
    mysql_session.commit()
    code = QrCode(code_no="A00000001", token_hash=token_hash("d" * 24), batch_id=batch.id, status=CodeStatus.ACTIVE)
    prize = Prize(name="谢谢参与", type=PrizeType.NONE, total_stock=0)
    mysql_session.add_all([code, prize])
    mysql_session.commit()
    mysql_session.add(ActivityPrize(activity_id=activity.id, prize_id=prize.id, probability=1))
    mysql_session.commit()

    ticket = validate_scan(mysql_session, consumer, "d" * 24)
    assert ticket["activity_id"] == activity.id


def test_scan_locks_code_for_the_same_consumer_only(mysql_session: Session):
    first_consumer, second_consumer = Consumer(openid="lock-owner"), Consumer(openid="lock-other")
    activity = Activity(name="lock test", start_at=utc_now() - timedelta(hours=1), end_at=utc_now() + timedelta(hours=1), status="active", all_batches=True)
    batch = QrCodeBatch(batch_no="LOCK001", batch_name="lock", prefix="L", total_count=1, status="active")
    mysql_session.add_all([first_consumer, second_consumer, activity, batch])
    mysql_session.commit()
    raw_token = "e" * 24
    code = QrCode(code_no="L00000001", token_hash=token_hash(raw_token), batch_id=batch.id, status=CodeStatus.ACTIVE)
    prize = Prize(name="谢谢参与", type=PrizeType.NONE, total_stock=0)
    mysql_session.add_all([code, prize])
    mysql_session.commit()
    mysql_session.add(ActivityPrize(activity_id=activity.id, prize_id=prize.id, probability=1))
    mysql_session.commit()
    redis_client.delete(f"lottery:draw-lock:{code.id}")

    first_ticket = validate_scan(mysql_session, first_consumer, raw_token)
    refreshed_ticket = validate_scan(mysql_session, first_consumer, raw_token)
    assert first_ticket["code_no"] == "L00000001"
    assert refreshed_ticket["draw_ticket"] != ""
    with pytest.raises(HTTPException, match="正在被核验"):
        validate_scan(mysql_session, second_consumer, raw_token)

    draw(mysql_session, first_consumer, first_ticket["draw_ticket"], "lock-idempotency-0001")
    assert redis_client.get(f"lottery:draw-lock:{code.id}") is None


def test_print_package_contains_png_and_manifest(mysql_session: Session):
    batch = QrCodeBatch(batch_no="PRINT001", batch_name="印刷测试", prefix="P", total_count=1)
    mysql_session.add(batch)
    mysql_session.commit()
    mysql_session.add(QrCode(code_no="P00000001", token_hash=token_hash("b" * 24), token_ciphertext=encrypt_qr_token("b" * 24), redemption_code_hash=token_hash("1234"), redemption_code_ciphertext=encrypt_qr_token("1234"), batch_id=batch.id))
    mysql_session.commit()

    filename, package = export_print_package(mysql_session, batch.id)

    assert filename == "PRINT001-print-package.zip"
    with zipfile.ZipFile(BytesIO(package)) as archive:
        assert set(archive.namelist()) == {"qrcodes/P00000001.png", "manifest.csv", "README.txt"}
        assert archive.read("qrcodes/P00000001.png").startswith(b"\x89PNG")
        assert "P00000001" in archive.read("manifest.csv").decode("utf-8-sig")


def test_code_csv_contains_code_and_scan_url(mysql_session: Session):
    batch = QrCodeBatch(batch_no="CSV001", batch_name="CSV 测试", prefix="C", total_count=1)
    mysql_session.add(batch)
    mysql_session.commit()
    mysql_session.add(QrCode(code_no="C00000001", token_hash=token_hash("c" * 24), token_ciphertext=encrypt_qr_token("c" * 24), redemption_code_hash=token_hash("1234"), redemption_code_ciphertext=encrypt_qr_token("1234"), batch_id=batch.id))
    mysql_session.commit()
    filename, content = export_code_csv(mysql_session, batch.id)

    csv_text = content.decode("utf-8-sig")
    assert filename == "CSV001-codes.csv"
    assert "code_no,scan_url,redemption_code,batch_no,status" in csv_text
    assert ",1234,CSV001," in csv_text
    assert "C00000001" in csv_text and "?t=" in csv_text
