from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt

from app.core.config import settings
from app.core.errors import AppError


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def create_jwt(claims: dict[str, Any], *, expires_in: timedelta = timedelta(hours=12)) -> str:
    payload = {**claims, "exp": datetime.now(UTC) + expires_in}
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_jwt(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise AppError("token_expired", 401) from None
    except jwt.InvalidTokenError:
        raise AppError("invalid_credentials", 401) from None
