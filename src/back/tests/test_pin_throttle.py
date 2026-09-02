from datetime import UTC, datetime, timedelta

import pytest

from app.core.errors import AppError
from app.services.pin import PinThrottle


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self.value = start

    def now(self) -> datetime:
        return self.value

    def advance(self, delta: timedelta) -> None:
        self.value += delta


def _clock() -> FakeClock:
    return FakeClock(datetime(2026, 8, 31, 12, 0, tzinfo=UTC))


def test_quatro_falhas_ainda_passam_quinta_bloqueia():
    clock = _clock()
    throttle = PinThrottle(now_fn=clock.now)
    for _ in range(4):
        throttle.register_failure("station-1")

    throttle.check("station-1")  # 4 falhas: ainda libera

    throttle.register_failure("station-1")
    with pytest.raises(AppError) as exc:
        throttle.check("station-1")
    assert exc.value.code == "pin_locked_out"
    assert exc.value.status_code == 429
    assert exc.value.params["retry_after_seconds"] > 0


def test_lockout_libera_apos_15_minutos():
    clock = _clock()
    throttle = PinThrottle(now_fn=clock.now)
    for _ in range(5):
        throttle.register_failure("station-1")
    with pytest.raises(AppError):
        throttle.check("station-1")

    clock.advance(timedelta(minutes=15, seconds=1))

    throttle.check("station-1")  # liberou: não levanta


def test_lockout_e_por_estacao_nao_por_clinica():
    clock = _clock()
    throttle = PinThrottle(now_fn=clock.now)
    for _ in range(5):
        throttle.register_failure("station-1")

    throttle.check("station-2")  # outra estação segue livre


def test_sucesso_zera_o_contador():
    clock = _clock()
    throttle = PinThrottle(now_fn=clock.now)
    for _ in range(4):
        throttle.register_failure("station-1")

    throttle.reset("station-1")

    for _ in range(4):
        throttle.register_failure("station-1")
    throttle.check("station-1")  # 4 de novo, não 8: não levanta
