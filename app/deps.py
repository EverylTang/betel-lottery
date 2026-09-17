from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlmodel import Session

from app.db import get_session
from app.models import AdminUser, Consumer
from app.security import parse_token

SessionDep = Annotated[Session, Depends(get_session)]


def _bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少 Bearer Token")
    return authorization.removeprefix("Bearer ")


def current_admin(request: Request, session: SessionDep, authorization: Annotated[str | None, Header()] = None) -> AdminUser:
    payload = parse_token(_bearer_token(authorization), "admin")
    admin = session.get(AdminUser, int(payload["sub"]))
    if not admin or not admin.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="后台账号不可用")
    if request.method in {"POST", "PATCH", "PUT", "DELETE"} and admin.role != "super_admin":
        path = request.url.path
        finance_paths = ("/cash-payments/", "/lottery-records/", "/redemption-lookup")
        finance_coupon_path = "/coupon-codes/"
        if admin.role == "finance" and any(marker in path for marker in finance_paths):
            return admin
        if admin.role == "finance" and finance_coupon_path in path:
            return admin
        if admin.role == "operator" and not any(marker in path for marker in finance_paths + ("/users", "/dealer-accounts")):
            return admin
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="当前账号没有此操作权限")
    return admin


def current_consumer(session: SessionDep, authorization: Annotated[str | None, Header()] = None) -> Consumer:
    payload = parse_token(_bearer_token(authorization), "consumer")
    consumer = session.get(Consumer, int(payload["sub"]))
    if not consumer:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")
    return consumer
