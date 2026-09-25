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
from sqlmodel import Session, select
from sqlalchemy import text

from app.api import admin, dealer, h5, wechat_pay_notify
from app.cache import check_redis, ensure_redis
from app.core.config import get_settings
from app.db import engine
from app.models import AdminUser
from app.security import hash_password

settings = get_settings()
upload_directory = Path(__file__).resolve().parent.parent / "uploads"
upload_directory.mkdir(parents=True, exist_ok=True)


def bootstrap_admin_user() -> None:
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
    bootstrap_admin_user()
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
