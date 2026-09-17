import base64
import hashlib
from datetime import datetime, timedelta, timezone

import jwt
from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException, status
from passlib.context import CryptContext

from app.core.config import get_settings

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
settings = get_settings()


def _qr_token_cipher() -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(settings.jwt_secret.encode()).digest())
    return Fernet(key)


def encrypt_qr_token(token: str) -> str:
    return _qr_token_cipher().encrypt(token.encode()).decode()


def decrypt_qr_token(ciphertext: str) -> str:
    try:
        return _qr_token_cipher().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="二维码印刷凭证无法解密，请重新生成批次") from exc


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def make_token(subject: str, token_type: str, expires_minutes: int, **claims: str | int) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": subject, "typ": token_type, "iat": now, "exp": now + timedelta(minutes=expires_minutes), **claims}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def parse_token(token: str, expected_type: str) -> dict:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token 无效或已过期") from exc
    if payload.get("typ") != expected_type:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token 类型错误")
    return payload
