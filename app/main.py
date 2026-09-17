from contextlib import asynccontextmanager

from pathlib import Path

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.httpsredirect import HTTPSRedirectMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from sqlmodel import Session, SQLModel, select
from sqlalchemy import inspect, text

from app.api import admin, dealer, h5, wechat_pay_notify
from app.cache import check_redis, ensure_redis
from app.core.config import get_settings
from app.db import engine
from app.models import AdminUser
from app.security import hash_password

settings = get_settings()
upload_directory = Path(__file__).resolve().parent.parent / "uploads"
upload_directory.mkdir(parents=True, exist_ok=True)


def bootstrap_data() -> None:
    SQLModel.metadata.create_all(engine)
    inspector = inspect(engine)
    with engine.begin() as connection:
        qrcode_indexes = {index["name"] for index in inspector.get_indexes("lottery_record")} | {constraint["name"] for constraint in inspector.get_unique_constraints("lottery_record")}
        if "uq_lottery_qrcode" in qrcode_indexes:
            connection.execute(text("ALTER TABLE lottery_record DROP INDEX uq_lottery_qrcode"))
        if "token_ciphertext" not in {column["name"] for column in inspector.get_columns("qrcode")}:
            connection.execute(text("ALTER TABLE qrcode ADD COLUMN token_ciphertext VARCHAR(512) NULL"))
        qrcode_columns = {column["name"] for column in inspector.get_columns("qrcode")}
        if "redemption_code_hash" not in qrcode_columns:
            connection.execute(text("ALTER TABLE qrcode ADD COLUMN redemption_code_hash VARCHAR(64) NULL"))
            connection.execute(text("CREATE INDEX ix_qrcode_redemption_code_hash ON qrcode (redemption_code_hash)"))
        if "redemption_code_ciphertext" not in qrcode_columns:
            connection.execute(text("ALTER TABLE qrcode ADD COLUMN redemption_code_ciphertext VARCHAR(512) NULL"))
        if "description" not in {column["name"] for column in inspector.get_columns("activity")}:
            connection.execute(text("ALTER TABLE activity ADD COLUMN description VARCHAR(512) NOT NULL DEFAULT ''"))
        if "rules" not in {column["name"] for column in inspector.get_columns("activity")}:
            connection.execute(text("ALTER TABLE activity ADD COLUMN rules TEXT NULL"))
        if "all_batches" not in {column["name"] for column in inspector.get_columns("activity")}:
            connection.execute(text("ALTER TABLE activity ADD COLUMN all_batches BOOLEAN NOT NULL DEFAULT TRUE"))
        prize_columns = {column["name"] for column in inspector.get_columns("prize")}
        prize_type_column = next((column for column in inspector.get_columns("prize") if column["name"] == "type"), None)
        if connection.dialect.name == "mysql" and prize_type_column and "UPGRADE" not in str(prize_type_column["type"]).upper():
            connection.execute(text("ALTER TABLE prize MODIFY COLUMN type ENUM('NONE','CASH','COUPON','PHYSICAL','POINTS','RETRY','UPGRADE') NOT NULL"))
        lottery_prize_type_column = next((column for column in inspector.get_columns("lottery_record") if column["name"] == "prize_type"), None)
        if connection.dialect.name == "mysql" and lottery_prize_type_column and "UPGRADE" not in str(lottery_prize_type_column["type"]).upper():
            connection.execute(text("ALTER TABLE lottery_record MODIFY COLUMN prize_type ENUM('NONE','CASH','COUPON','PHYSICAL','POINTS','RETRY','UPGRADE') NOT NULL"))
        for column_name, definition in {
            "points_amount": "INT NOT NULL DEFAULT 0",
            "upgrade_price": "DECIMAL(10,2) NOT NULL DEFAULT 0.00",
        }.items():
            if column_name not in prize_columns:
                connection.execute(text(f"ALTER TABLE prize ADD COLUMN {column_name} {definition}"))
        connection.execute(text("UPDATE prize SET status = 'inactive' WHERE type IN ('COUPON','PHYSICAL','POINTS','RETRY')"))
        lottery_columns = {column["name"] for column in inspector.get_columns("lottery_record")}
        if "redemption_status" not in lottery_columns:
            connection.execute(text("ALTER TABLE lottery_record ADD COLUMN redemption_status VARCHAR(16) NOT NULL DEFAULT 'not_required'"))
            connection.execute(text("UPDATE lottery_record SET redemption_status = 'pending' WHERE prize_type = 'cash'"))
        if "redeemed_at" not in lottery_columns:
            connection.execute(text("ALTER TABLE lottery_record ADD COLUMN redeemed_at DATETIME NULL"))
        if "redemption_note" not in lottery_columns:
            connection.execute(text("ALTER TABLE lottery_record ADD COLUMN redemption_note VARCHAR(512) NULL"))
        if "is_retry" not in lottery_columns:
            connection.execute(text("ALTER TABLE lottery_record ADD COLUMN is_retry BOOLEAN NOT NULL DEFAULT FALSE"))
        if "client_ip" not in lottery_columns:
            connection.execute(text("ALTER TABLE lottery_record ADD COLUMN client_ip VARCHAR(64) NULL"))
        if "client_location" not in lottery_columns:
            connection.execute(text("ALTER TABLE lottery_record ADD COLUMN client_location VARCHAR(255) NULL"))
        for column_name, definition in {
            "client_province": "VARCHAR(64) NULL",
            "client_city": "VARCHAR(64) NULL",
            "client_district": "VARCHAR(64) NULL",
            "client_address": "VARCHAR(512) NULL",
        }.items():
            if column_name not in lottery_columns:
                connection.execute(text(f"ALTER TABLE lottery_record ADD COLUMN {column_name} {definition}"))
        if "lottery_policy_id" not in lottery_columns:
            connection.execute(text("ALTER TABLE lottery_record ADD COLUMN lottery_policy_id INT NULL"))
        if "lottery_policy_version" not in lottery_columns:
            connection.execute(text("ALTER TABLE lottery_record ADD COLUMN lottery_policy_version INT NULL"))
        if "lottery_policy_scope" not in lottery_columns:
            connection.execute(text("ALTER TABLE lottery_record ADD COLUMN lottery_policy_scope VARCHAR(16) NULL"))
        consumer_columns = {column["name"] for column in inspector.get_columns("consumer")}
        if "risk_status" not in consumer_columns:
            connection.execute(text("ALTER TABLE consumer ADD COLUMN risk_status VARCHAR(16) NOT NULL DEFAULT 'normal'"))
        if "risk_note" not in consumer_columns:
            connection.execute(text("ALTER TABLE consumer ADD COLUMN risk_note VARCHAR(512) NOT NULL DEFAULT ''"))
        if "anonymized_at" not in consumer_columns:
            connection.execute(text("ALTER TABLE consumer ADD COLUMN anonymized_at DATETIME NULL"))
        if "win_probability_multiplier" not in consumer_columns:
            connection.execute(text("ALTER TABLE consumer ADD COLUMN win_probability_multiplier DECIMAL(5,4) NOT NULL DEFAULT 1.0000"))
        payment_columns = {column["name"] for column in inspector.get_columns("cash_payment")}
        for column_name, definition in {
            "provider": "VARCHAR(32) NOT NULL DEFAULT ''",
            "provider_out_bill_no": "VARCHAR(32) NULL",
            "transfer_bill_no": "VARCHAR(64) NULL",
            "transfer_state": "VARCHAR(32) NULL",
            "package_info": "VARCHAR(2048) NULL",
            "notify_event_id": "VARCHAR(64) NULL",
            "fail_reason": "VARCHAR(256) NULL",
            "provider_error_note": "VARCHAR(512) NULL",
            "attempts": "INT NOT NULL DEFAULT 0",
            "provider_created_at": "DATETIME NULL",
            "provider_updated_at": "DATETIME NULL",
            "processed_at": "DATETIME NULL",
            "operator_note": "VARCHAR(512) NOT NULL DEFAULT ''",
        }.items():
            if column_name not in payment_columns:
                connection.execute(text(f"ALTER TABLE cash_payment ADD COLUMN {column_name} {definition}"))
        replenishment_columns = {column["name"]: column for column in inspector.get_columns("replenishment_task")}
        store_id_column = replenishment_columns.get("store_id")
        if store_id_column and not store_id_column["nullable"] and connection.dialect.name == "mysql":
            connection.execute(text("ALTER TABLE replenishment_task MODIFY COLUMN store_id INT NULL"))
        dealer_redemption_columns = {column["name"] for column in inspector.get_columns("dealer_redemption")}
        if "request_ip" not in dealer_redemption_columns:
            connection.execute(text("ALTER TABLE dealer_redemption ADD COLUMN request_ip VARCHAR(64) NULL"))
        if "user_agent" not in dealer_redemption_columns:
            connection.execute(text("ALTER TABLE dealer_redemption ADD COLUMN user_agent VARCHAR(512) NULL"))
        upgrade_order_columns = {column["name"] for column in inspector.get_columns("upgrade_order")}
        if "payment_method" not in upgrade_order_columns:
            connection.execute(text("ALTER TABLE upgrade_order ADD COLUMN payment_method VARCHAR(32) NOT NULL DEFAULT 'manual'"))
    with Session(engine) as session:
        admin_user = session.exec(select(AdminUser).where(AdminUser.username == settings.admin_bootstrap_username)).first()
        if not admin_user:
            session.add(
                AdminUser(
                    username=settings.admin_bootstrap_username,
                    password_hash=hash_password(settings.admin_bootstrap_password),
                )
            )
            session.commit()


@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_redis()
    bootstrap_data()
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
logger = logging.getLogger("betel_lottery.request")
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_host_list)
if settings.force_https:
    app.add_middleware(HTTPSRedirectMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("request_failed id=%s method=%s path=%s", request_id, request.method, request.url.path)
        raise
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if settings.force_https:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    logger.info("request_completed id=%s method=%s path=%s status=%s", request_id, request.method, request.url.path, response.status_code)
    return response
app.include_router(admin.router)
app.include_router(dealer.router)
app.include_router(dealer.store_router)
app.include_router(h5.router)
app.include_router(wechat_pay_notify.router)
app.mount("/uploads", StaticFiles(directory=upload_directory), name="uploads")


@app.get("/health", tags=["system"])
def health():
    return {"ok": True}


@app.get("/ready", tags=["system"])
def ready():
    try:
        with Session(engine) as session:
            session.exec(text("SELECT 1")).one()
        redis_ok = check_redis()
    except Exception as exc:
        return JSONResponse(status_code=503, content={"ok": False, "detail": str(exc)})
    if not redis_ok:
        return JSONResponse(status_code=503, content={"ok": False, "detail": "Redis 不可用"})
    return {"ok": True, "database": True, "redis": True}
