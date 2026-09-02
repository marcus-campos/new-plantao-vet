import re

import httpx
from pydantic import BaseModel

from app.core.errors import ERROR_CODES, AppError
from app.main import create_app

SNAKE_CASE = re.compile(r"^[a-z][a-z0-9_]*$")


def test_app_error_carries_code_status_and_params():
    error = AppError("pin_duplicate", 409, membership_id="abc")
    assert error.code == "pin_duplicate"
    assert error.status_code == 409
    assert error.params == {"membership_id": "abc"}


def test_app_error_defaults_to_status_400():
    error = AppError("forbidden")
    assert error.status_code == 400
    assert error.params == {}


def test_every_known_error_code_is_snake_case():
    assert ERROR_CODES
    for code in ERROR_CODES:
        assert SNAKE_CASE.fullmatch(code), code


async def test_app_error_becomes_error_envelope():
    app = create_app()

    @app.get("/boom")
    async def boom() -> None:
        raise AppError("task_already_processed", 409, task_id="t1")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/boom")

    assert response.status_code == 409
    assert response.json() == {
        "error": {"code": "task_already_processed", "params": {"task_id": "t1"}}
    }


async def test_no_error_response_contains_prose():
    app = create_app()

    @app.get("/raise/{code}")
    async def raise_code(code: str) -> None:
        raise AppError(code, 400)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for code in sorted(ERROR_CODES):
            response = await client.get(f"/raise/{code}")
            body = response.json()
            assert set(body) == {"error"}
            assert set(body["error"]) == {"code", "params"}
            assert SNAKE_CASE.fullmatch(body["error"]["code"])


async def test_request_validation_becomes_validation_error_envelope():
    app = create_app()

    class Payload(BaseModel):
        name: str

    @app.post("/echo")
    async def echo(payload: Payload) -> Payload:
        return payload

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/echo", json={})

    body = response.json()
    assert response.status_code == 422
    assert body["error"]["code"] == "validation_error"
    assert "detail" not in body
    assert all(set(field) == {"loc", "type"} for field in body["error"]["params"]["fields"])
