from datetime import timedelta

import pytest

from app.core.errors import AppError
from app.core.security import create_jwt, decode_jwt, hash_password, verify_password


def test_password_hash_roundtrip():
    hashed = hash_password("s3nh4-f0rte")
    assert hashed != "s3nh4-f0rte"
    assert verify_password("s3nh4-f0rte", hashed) is True
    assert verify_password("errada", hashed) is False


def test_jwt_roundtrip():
    token = create_jwt({"sub": "membership:abc", "kind": "personal"})
    claims = decode_jwt(token)
    assert claims["sub"] == "membership:abc"
    assert claims["kind"] == "personal"
    assert "exp" in claims


def test_expired_jwt_raises_token_expired():
    token = create_jwt({"sub": "x"}, expires_in=timedelta(seconds=-1))
    with pytest.raises(AppError) as exc_info:
        decode_jwt(token)
    assert exc_info.value.code == "token_expired"
    assert exc_info.value.status_code == 401


def test_garbage_jwt_raises_invalid_credentials():
    with pytest.raises(AppError) as exc_info:
        decode_jwt("not-a-token")
    assert exc_info.value.code == "invalid_credentials"
    assert exc_info.value.status_code == 401
