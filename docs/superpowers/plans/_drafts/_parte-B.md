<!-- Parte B do plano Semana 1 — Tasks 5 a 8. Concatenar após a Task 4. -->
<!-- Contratos exatos: docs/superpowers/plans/_drafting-brief-semana1.md (v3). -->

### Task 5: Auth pessoal e tenancy

**Files:**
- Create: `backend/app/schemas/auth.py`
- Create: `backend/app/api/routes/auth.py`
- Create: `backend/tests/helpers.py`
- Modify: `backend/app/api/deps.py` (adiciona `AuthContext`, `get_current_auth`, `get_operator`, `get_tenant_obj`; o `get_session` da Task 2 fica intacto)
- Modify: `backend/app/main.py` (registra o router de auth em `create_app()`)
- Test: `backend/tests/test_auth_login.py`, `backend/tests/test_deps_tenancy.py`

**Interfaces:**
- Consumes (Tasks 1–4 — se a assinatura implementada divergir, adapte a chamada aqui, nunca o contrato do brief):
  - `app.core.errors.AppError(code: str, status_code: int = 400, **params)` — expõe `.code`, `.status_code`, `.params`; handler registrado em `create_app()` devolve `{"error": {"code": ..., "params": {...}}}`.
  - `app.core.security`: `hash_password(password: str) -> str` · `verify_password(password: str, password_hash: str) -> bool` · `create_jwt(claims: dict[str, Any], *, expires_in: timedelta) -> str` (acrescenta `exp` a partir de `expires_in`) · `decode_jwt(token: str) -> dict[str, Any]` (expirado → `AppError("token_expired", 401)`; assinatura/estrutura inválida → `AppError("invalid_credentials", 401)`).
  - `app.api.deps.get_session() -> AsyncIterator[AsyncSession]`.
  - Models `app.models.clinic.Clinic`, `app.models.user.User`, `app.models.membership.Membership` (campos da spec §4).
  - `app.services.audit.ActorInfo(membership_id, name, license_number, license_authority)`.
  - Fixtures do conftest (Task 2): `client` (`httpx.AsyncClient` com `ASGITransport`, base_url `http://test`) e `session` (`AsyncSession`); factories `make_clinic(session, **overrides)`, `make_user(session, **overrides)`, `make_membership(session, *, clinic, user, **overrides)` — `overrides` são valores de coluna e persistem o objeto.
- Produces:
  - `POST /api/v1/auth/login` → 200 `{"access_token": str, "token_type": "bearer"}`; claims do JWT: `kind="personal"`, `sub` (user id), `clinic_id`, `membership_id`, `exp` (+12h). Credencial inválida (email desconhecido, senha errada, user/membership inativo) → `AppError("invalid_credentials", 401)`.
  - `GET /api/v1/auth/me` → `{"kind", "clinic_id", "membership_id"}` (superfície de teste de `get_current_auth`; a Task 6 a reusa para o modo estação).
  - `app.api.deps.AuthContext` — dataclass EXATA do brief: `kind: Literal["personal", "station"]`, `clinic_id: uuid.UUID`, `membership: Membership | None`.
  - `app.api.deps.get_current_auth(...) -> AuthContext` — caminho pessoal completo; token de estação é rejeitado até a Task 6 estender o branch.
  - `app.api.deps.get_operator(...) -> ActorInfo` — caminho pessoal completo; no modo estação levanta `AppError("operator_required", 403)` (a Task 6 insere a verificação do `X-Operator-Token` antes desse raise).
  - `app.api.deps.get_tenant_obj(session: AsyncSession, model: type, obj_id: uuid.UUID, clinic_id: uuid.UUID) -> Any` — assinatura EXATA do brief; 404 `AppError("not_found")` quando o objeto não existe naquele tenant. Tasks 6, 7, 8 dependem dela.
  - `backend/tests/helpers.py`: `bearer(token) -> dict`, `personal_token(membership, *, expires_in=timedelta(hours=12)) -> str`. A Task 6 acrescenta `station_token`.

- [ ] **Step 1: Escrever os helpers de teste e os testes de login (que falham)**

Crie `backend/tests/helpers.py`:

```python
"""Helpers compartilhados pelos testes de API.

Tokens são emitidos direto por create_jwt (sem passar pelo endpoint) para que
cada teste dependa só do contrato de claims, não do fluxo de login inteiro.
"""

from datetime import timedelta

from app.core.security import create_jwt
from app.models.membership import Membership


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def personal_token(
    membership: Membership, *, expires_in: timedelta = timedelta(hours=12)
) -> str:
    return create_jwt(
        {
            "kind": "personal",
            "sub": str(membership.user_id),
            "clinic_id": str(membership.clinic_id),
            "membership_id": str(membership.id),
        },
        expires_in=expires_in,
    )
```

Crie `backend/tests/test_auth_login.py`:

```python
import time

from app.core.security import decode_jwt, hash_password
from tests.factories import make_clinic, make_membership, make_user


async def _personal_setup(session):
    clinic = await make_clinic(session)
    user = await make_user(
        session, email="vet@plantao.vet", password_hash=hash_password("s3nh4-forte")
    )
    membership = await make_membership(
        session,
        clinic=clinic,
        user=user,
        role="vet",
        license_number="12345",
        license_authority="CRMV-SP",
    )
    return clinic, user, membership


async def test_login_ok_devolve_jwt_pessoal_de_12h(client, session):
    clinic, user, membership = await _personal_setup(session)

    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "vet@plantao.vet", "password": "s3nh4-forte"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["token_type"] == "bearer"
    claims = decode_jwt(data["access_token"])
    assert claims["kind"] == "personal"
    assert claims["sub"] == str(user.id)
    assert claims["clinic_id"] == str(clinic.id)
    assert claims["membership_id"] == str(membership.id)
    assert 11 * 3600 < claims["exp"] - time.time() <= 12 * 3600 + 60


async def test_login_senha_errada_devolve_codigo_nao_prosa(client, session):
    await _personal_setup(session)

    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "vet@plantao.vet", "password": "senha-errada"},
    )

    assert resp.status_code == 401
    assert resp.json() == {"error": {"code": "invalid_credentials", "params": {}}}


async def test_login_email_desconhecido_mesmo_codigo(client, session):
    # não vaza se o email existe: mesmo código e status da senha errada
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "ninguem@plantao.vet", "password": "qualquer"},
    )

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_credentials"


async def test_login_de_membership_inativo_e_recusado(client, session):
    clinic = await make_clinic(session)
    user = await make_user(
        session, email="ex@plantao.vet", password_hash=hash_password("s3nh4-forte")
    )
    await make_membership(session, clinic=clinic, user=user, role="tech", is_active=False)

    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "ex@plantao.vet", "password": "s3nh4-forte"},
    )

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_credentials"
```

- [ ] **Step 2: Rodar e ver falhar**

Run (em `backend/`): `uv run pytest tests/test_auth_login.py -q`
Expected: 4 FAILED — a rota `/api/v1/auth/login` não existe ainda (o app responde 404, os asserts de 200/401 falham).

- [ ] **Step 3: Implementar schemas e rota de login**

Crie `backend/app/schemas/auth.py`:

```python
import uuid
from typing import Literal

from pydantic import BaseModel


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class MeResponse(BaseModel):
    kind: Literal["personal", "station"]
    clinic_id: uuid.UUID
    membership_id: uuid.UUID | None
```

Crie `backend/app/api/routes/auth.py`:

```python
from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.core.errors import AppError
from app.core.security import create_jwt, verify_password
from app.models.membership import Membership
from app.models.user import User
from app.schemas.auth import LoginRequest, TokenResponse

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/login")
async def login(
    body: LoginRequest, session: AsyncSession = Depends(get_session)
) -> TokenResponse:
    user = (
        await session.execute(
            select(User).where(User.email == body.email, User.is_active.is_(True))
        )
    ).scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        raise AppError("invalid_credentials", 401)

    # v1: um usuário tem um vínculo ativo; múltiplas clínicas viram seletor na v2
    membership = (
        await session.execute(
            select(Membership)
            .where(Membership.user_id == user.id, Membership.is_active.is_(True))
            .limit(1)
        )
    ).scalar_one_or_none()
    if membership is None:
        raise AppError("invalid_credentials", 401)

    token = create_jwt(
        {
            "kind": "personal",
            "sub": str(user.id),
            "clinic_id": str(membership.clinic_id),
            "membership_id": str(membership.id),
        },
        expires_in=timedelta(hours=12),
    )
    return TokenResponse(access_token=token)
```

Em `backend/app/main.py`, dentro de `create_app()`, registre o router junto aos `include_router` existentes (import no topo do arquivo):

```python
from app.api.routes import auth as auth_routes

# ... dentro de create_app(), após os routers já registrados:
app.include_router(auth_routes.router)
```

- [ ] **Step 4: Rodar e ver passar**

Run (em `backend/`): `uv run pytest tests/test_auth_login.py -q`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas/auth.py backend/app/api/routes/auth.py backend/app/main.py backend/tests/helpers.py backend/tests/test_auth_login.py
git commit -m "feat(auth): login pessoal com JWT de 12h e codigo invalid_credentials"
```

- [ ] **Step 6: Escrever os testes de deps e tenancy (que falham)**

Crie `backend/tests/test_deps_tenancy.py`:

```python
from datetime import timedelta

import pytest

from app.api.deps import AuthContext, get_operator, get_tenant_obj
from app.core.errors import AppError
from app.models.membership import Membership
from tests.factories import make_clinic, make_membership, make_user
from tests.helpers import bearer, personal_token


async def test_me_com_token_pessoal(client, session):
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="vet")

    resp = await client.get("/api/v1/auth/me", headers=bearer(personal_token(membership)))

    assert resp.status_code == 200
    assert resp.json() == {
        "kind": "personal",
        "clinic_id": str(clinic.id),
        "membership_id": str(membership.id),
    }


async def test_token_expirado_devolve_token_expired(client, session):
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="vet")
    token = personal_token(membership, expires_in=timedelta(seconds=-1))

    resp = await client.get("/api/v1/auth/me", headers=bearer(token))

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "token_expired"


async def test_sem_header_authorization_e_invalid_credentials(client):
    resp = await client.get("/api/v1/auth/me")

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_credentials"


async def test_membership_desativado_nao_autentica_mais(client, session):
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="vet")
    token = personal_token(membership)

    membership.is_active = False
    await session.commit()

    resp = await client.get("/api/v1/auth/me", headers=bearer(token))

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_credentials"


async def test_get_operator_pessoal_monta_actor_info_do_membership(session):
    clinic = await make_clinic(session)
    user = await make_user(session, name="Dra. Ana Souza")
    membership = await make_membership(
        session,
        clinic=clinic,
        user=user,
        role="vet",
        license_number="4321",
        license_authority="CRMV-SP",
    )
    auth = AuthContext(kind="personal", clinic_id=clinic.id, membership=membership)

    actor = await get_operator(auth=auth, session=session, x_operator_token=None)

    assert actor.membership_id == membership.id
    assert actor.name == "Dra. Ana Souza"
    assert actor.license_number == "4321"
    assert actor.license_authority == "CRMV-SP"


async def test_get_tenant_obj_cross_tenant_e_404(session):
    clinic_a = await make_clinic(session, slug="clinica-a")
    clinic_b = await make_clinic(session, slug="clinica-b")
    user_b = await make_user(session, email="b@plantao.vet")
    membership_b = await make_membership(session, clinic=clinic_b, user=user_b, role="vet")

    # no tenant certo, devolve o objeto
    obj = await get_tenant_obj(session, Membership, membership_b.id, clinic_b.id)
    assert obj.id == membership_b.id

    # no tenant errado, 404 not_found — nunca 403, para não vazar existência
    with pytest.raises(AppError) as exc:
        await get_tenant_obj(session, Membership, membership_b.id, clinic_a.id)
    assert exc.value.code == "not_found"
    assert exc.value.status_code == 404
```

- [ ] **Step 7: Rodar e ver falhar**

Run (em `backend/`): `uv run pytest tests/test_deps_tenancy.py -q`
Expected: erro de coleta — `ImportError: cannot import name 'AuthContext' from 'app.api.deps'`.

- [ ] **Step 8: Implementar AuthContext, get_current_auth, get_operator (pessoal) e get_tenant_obj**

Acrescente ao `backend/app/api/deps.py` (o `get_session` existente fica como está; adicione os imports novos ao topo):

```python
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.security import decode_jwt
from app.models.membership import Membership
from app.models.user import User
from app.services.audit import ActorInfo


@dataclass
class AuthContext:
    kind: Literal["personal", "station"]
    clinic_id: uuid.UUID
    membership: Membership | None  # None quando kind == "station"


def _decode_bearer(authorization: str | None) -> dict[str, Any]:
    if authorization is None or not authorization.startswith("Bearer "):
        raise AppError("invalid_credentials", 401)
    return decode_jwt(authorization.removeprefix("Bearer "))


async def get_current_auth(
    session: AsyncSession = Depends(get_session),
    authorization: str | None = Header(default=None),
) -> AuthContext:
    claims = _decode_bearer(authorization)
    if claims.get("kind") == "personal":
        membership = (
            await session.execute(
                select(Membership).where(
                    Membership.id == uuid.UUID(claims["membership_id"]),
                    Membership.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()
        if membership is None:
            raise AppError("invalid_credentials", 401)
        return AuthContext(
            kind="personal", clinic_id=membership.clinic_id, membership=membership
        )
    # kind == "station": branch adicionado na Task 6 (valida station_key_version);
    # até lá token de estação não autentica.
    raise AppError("invalid_credentials", 401)


async def get_operator(
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    x_operator_token: str | None = Header(default=None, alias="X-Operator-Token"),
) -> ActorInfo:
    if auth.kind == "personal":
        membership = auth.membership
        user = await session.get(User, membership.user_id)
        return ActorInfo(
            membership_id=membership.id,
            name=user.name,
            license_number=membership.license_number,
            license_authority=membership.license_authority,
        )
    # Modo estação: a Task 6 insere aqui a troca do X-Operator-Token por ActorInfo.
    raise AppError("operator_required", 403)


async def get_tenant_obj(session: AsyncSession, model: type, obj_id: uuid.UUID,
                         clinic_id: uuid.UUID) -> Any:
    obj = (
        await session.execute(
            select(model).where(model.id == obj_id, model.clinic_id == clinic_id)
        )
    ).scalar_one_or_none()
    if obj is None:
        raise AppError("not_found", 404)
    return obj
```

Acrescente a rota `/me` em `backend/app/api/routes/auth.py` (imports novos: `get_current_auth`, `AuthContext`, `MeResponse`):

```python
from app.api.deps import AuthContext, get_current_auth
from app.schemas.auth import MeResponse


@router.get("/me")
async def me(auth: AuthContext = Depends(get_current_auth)) -> MeResponse:
    return MeResponse(
        kind=auth.kind,
        clinic_id=auth.clinic_id,
        membership_id=auth.membership.id if auth.membership else None,
    )
```

- [ ] **Step 9: Rodar e ver passar**

Run (em `backend/`): `uv run pytest tests/test_deps_tenancy.py tests/test_auth_login.py -q`
Expected: `10 passed`

- [ ] **Step 10: Suíte inteira + lint e commit**

Run (em `backend/`): `uv run pytest -q && uv run ruff check .`
Expected: todos os testes verdes, lint sem apontamentos.

```bash
git add backend/app/api/deps.py backend/app/api/routes/auth.py backend/tests/test_deps_tenancy.py
git commit -m "feat(auth): AuthContext, get_operator pessoal e get_tenant_obj com 404 por tenant"
```

### Task 6: Modo estação endurecido

**Files:**
- Create: `backend/app/services/pin.py` (`PinThrottle` com clock injetável, singleton `pin_throttle`, `PinService`)
- Create: `backend/app/api/routes/memberships.py` (definição de PIN, admin-only)
- Modify: `backend/app/schemas/auth.py` (`StationLoginRequest`, `PinRequest`, `OperatorTokenResponse`, `SetPinRequest`)
- Modify: `backend/app/api/routes/auth.py` (`POST /station`, `POST /pin`)
- Modify: `backend/app/api/deps.py` (branch station em `get_current_auth`, `_validate_station`, `get_station_claims`, `get_operator` completo)
- Modify: `backend/app/main.py` (registra router de memberships)
- Modify: `backend/tests/helpers.py` (adiciona `station_token`)
- Test: `backend/tests/test_pin_throttle.py`, `backend/tests/test_auth_station.py`

**Interfaces:**
- Consumes:
  - Task 5: `AuthContext`, `get_current_auth`, `get_operator`, `get_tenant_obj`, `_decode_bearer`, rota `/api/v1/auth/me`, `tests/helpers.py` (`bearer`, `personal_token`).
  - Tasks 1–4: `AppError`, `hash_password`/`verify_password`/`create_jwt`/`decode_jwt` (contrato declarado na Task 5), `AuditService.record`, models `Clinic` (`slug`, `station_key_hash`, `station_key_version`), `Membership` (`pin_hash`, `role`, `is_active`), `AuditEntry` (`payload`, `action`, `clinic_id`), factories.
- Produces:
  - `POST /api/v1/auth/station` → 200 `TokenResponse`; claims: `kind="station"`, `clinic_id`, `station_key_version`, `station_id` (uuid4 por login — identifica a estação no throttle), `exp` +12h. Slug desconhecido ou chave errada → `invalid_credentials` 401.
  - `get_current_auth` agora aceita `kind="station"`: valida `claims["station_key_version"] != clinics.station_key_version` → `AppError("station_key_rotated", 401)`; devolve `AuthContext(kind="station", clinic_id, membership=None)`.
  - `POST /api/v1/auth/pin` (só token de estação; token pessoal → `forbidden` 403) → 200 `{"operator_token": str}` (claims `kind="operator"`, `clinic_id`, `membership_id`, `exp` +5min). PIN errado → `invalid_credentials` 401 + auditoria `action="pin_failed"`. 5 falhas → `pin_locked_out` 429 com `params.retry_after_seconds` por 15 min, por estação.
  - `app.services.pin.PinThrottle(now_fn)` — `check(station_id)`, `register_failure(station_id)`, `reset(station_id)`; singleton `pin_throttle` usado pela rota.
  - `app.services.pin.PinService.set_pin(session, *, membership, pin, actor) -> None` — PIN já usado por outro membership ativo da clínica → `AppError("pin_duplicate", 409)`; audita `action="pin_set"`.
  - `POST /api/v1/memberships/{membership_id}/pin` → 204; exige token pessoal de `role="admin"` (senão `forbidden` 403); membership de outro tenant → 404.
  - `get_operator` completo: estação sem `X-Operator-Token`, com token expirado, de outro kind ou de outra clínica → `AppError("operator_required", 403)`; válido → `ActorInfo` do membership do operador. Tasks 7 e 8 usam em toda mutação.
  - `tests/helpers.py::station_token(clinic, *, station_id=None, station_key_version=None, expires_in=timedelta(hours=12)) -> str`.

- [ ] **Step 1: Escrever os testes unitários do PinThrottle (que falham)**

Crie `backend/tests/test_pin_throttle.py`:

```python
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
```

- [ ] **Step 2: Rodar e ver falhar**

Run (em `backend/`): `uv run pytest tests/test_pin_throttle.py -q`
Expected: erro de coleta — `ModuleNotFoundError: No module named 'app.services.pin'`.

- [ ] **Step 3: Implementar PinThrottle e PinService**

Crie `backend/app/services/pin.py`:

```python
"""Rate limit de PIN por estação e definição de PIN único por clínica.

O relógio é injetável (now_fn) para o teste de liberação após 15 minutos não
depender de sleep. O singleton pin_throttle guarda estado em memória por
processo — suficiente para a v1 (uma instância de API); o scheduler da GCP na
semana 4 já roda single-instance pelo mesmo motivo.
"""

from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.security import hash_password, verify_password
from app.models.membership import Membership
from app.services.audit import ActorInfo, AuditService


class PinThrottle:
    max_failures = 5
    lockout = timedelta(minutes=15)

    def __init__(self, now_fn: Callable[[], datetime] | None = None) -> None:
        self._now_fn = now_fn or (lambda: datetime.now(UTC))
        self._failures: dict[str, list[datetime]] = defaultdict(list)

    def check(self, station_id: str) -> None:
        now = self._now_fn()
        recent = [t for t in self._failures[station_id] if now - t < self.lockout]
        self._failures[station_id] = recent
        if len(recent) >= self.max_failures:
            newest = max(recent)
            retry_after = int((self.lockout - (now - newest)).total_seconds())
            raise AppError("pin_locked_out", 429, retry_after_seconds=retry_after)

    def register_failure(self, station_id: str) -> None:
        self._failures[station_id].append(self._now_fn())

    def reset(self, station_id: str) -> None:
        self._failures.pop(station_id, None)


pin_throttle = PinThrottle()


class PinService:
    @staticmethod
    async def set_pin(
        session: AsyncSession,
        *,
        membership: Membership,
        pin: str,
        actor: ActorInfo,
    ) -> None:
        # PIN é único por clínica: dois PINs iguais atribuiriam o ato clínico
        # à pessoa errada. bcrypt não permite busca por igualdade, então
        # verificamos contra cada membership ativo com PIN definido.
        others = (
            await session.execute(
                select(Membership).where(
                    Membership.clinic_id == membership.clinic_id,
                    Membership.id != membership.id,
                    Membership.is_active.is_(True),
                    Membership.pin_hash.is_not(None),
                )
            )
        ).scalars().all()
        if any(verify_password(pin, other.pin_hash) for other in others):
            raise AppError("pin_duplicate", 409)

        membership.pin_hash = hash_password(pin)
        await session.flush()
        # pin_hash está em AuditService.REDACTED: o snapshot nunca o carrega.
        await AuditService.record(
            session,
            clinic_id=membership.clinic_id,
            actor=actor,
            action="pin_set",
            entity_type="membership",
            entity_id=membership.id,
        )
```

- [ ] **Step 4: Rodar e ver passar**

Run (em `backend/`): `uv run pytest tests/test_pin_throttle.py -q`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/pin.py backend/tests/test_pin_throttle.py
git commit -m "feat(auth): PinThrottle com clock injetavel e PinService com PIN unico por clinica"
```

- [ ] **Step 6: Escrever os testes do login de estação e da rotação de chave (que falham)**

Adicione `station_token` a `backend/tests/helpers.py` (imports novos: `uuid`, `Clinic`):

```python
import uuid

from app.models.clinic import Clinic


def station_token(
    clinic: Clinic,
    *,
    station_id: str | None = None,
    station_key_version: int | None = None,
    expires_in: timedelta = timedelta(hours=12),
) -> str:
    return create_jwt(
        {
            "kind": "station",
            "clinic_id": str(clinic.id),
            "station_key_version": (
                station_key_version
                if station_key_version is not None
                else clinic.station_key_version
            ),
            "station_id": station_id or str(uuid.uuid4()),
        },
        expires_in=expires_in,
    )
```

Crie `backend/tests/test_auth_station.py` com o primeiro bloco de testes:

```python
import time
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.api.deps import AuthContext, get_operator
from app.core.errors import AppError
from app.core.security import create_jwt, decode_jwt, hash_password
from app.models.audit import AuditEntry
from tests.factories import make_clinic, make_membership, make_user
from tests.helpers import bearer, personal_token, station_token

STATION_KEY = "chave-da-estacao"


async def _station_setup(session, *, pin="1234"):
    clinic = await make_clinic(
        session, station_key_hash=hash_password(STATION_KEY), station_key_version=1
    )
    user = await make_user(session, email="tech@plantao.vet", name="Tec. Joao")
    membership = await make_membership(
        session, clinic=clinic, user=user, role="tech", pin_hash=hash_password(pin)
    )
    return clinic, membership


async def _station_login(client, clinic) -> str:
    resp = await client.post(
        "/api/v1/auth/station",
        json={"clinic_slug": clinic.slug, "station_key": STATION_KEY},
    )
    assert resp.status_code == 200
    return resp.json()["access_token"]


async def test_login_de_estacao_emite_token_com_versao_e_station_id(client, session):
    clinic, _ = await _station_setup(session)

    token = await _station_login(client, clinic)

    claims = decode_jwt(token)
    assert claims["kind"] == "station"
    assert claims["clinic_id"] == str(clinic.id)
    assert claims["station_key_version"] == 1
    assert claims["station_id"]
    assert 11 * 3600 < claims["exp"] - time.time() <= 12 * 3600 + 60

    resp = await client.get("/api/v1/auth/me", headers=bearer(token))
    assert resp.status_code == 200
    assert resp.json() == {
        "kind": "station",
        "clinic_id": str(clinic.id),
        "membership_id": None,
    }


async def test_station_key_errada_e_invalid_credentials(client, session):
    clinic, _ = await _station_setup(session)

    resp = await client.post(
        "/api/v1/auth/station",
        json={"clinic_slug": clinic.slug, "station_key": "chave-errada"},
    )

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_credentials"


async def test_rotacao_da_station_key_revoga_tokens_emitidos(client, session):
    clinic, _ = await _station_setup(session)
    token = station_token(clinic)  # carrega station_key_version=1

    resp = await client.get("/api/v1/auth/me", headers=bearer(token))
    assert resp.status_code == 200

    clinic.station_key_version = 2
    await session.commit()

    resp = await client.get("/api/v1/auth/me", headers=bearer(token))
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "station_key_rotated"
```

- [ ] **Step 7: Rodar e ver falhar**

Run (em `backend/`): `uv run pytest tests/test_auth_station.py -q`
Expected: 3 FAILED — `/api/v1/auth/station` não existe (404) e `/auth/me` com token de estação devolve `invalid_credentials` (o branch station da Task 5 ainda rejeita tudo).

- [ ] **Step 8: Implementar login de estação e validação de station_key_version**

Adicione a `backend/app/schemas/auth.py`:

```python
from pydantic import Field


class StationLoginRequest(BaseModel):
    clinic_slug: str
    station_key: str


class PinRequest(BaseModel):
    pin: str = Field(pattern=r"^\d{4}$")


class SetPinRequest(BaseModel):
    pin: str = Field(pattern=r"^\d{4}$")


class OperatorTokenResponse(BaseModel):
    operator_token: str
```

Em `backend/app/api/deps.py`, adicione `_validate_station` e `get_station_claims`, e troque o final de `get_current_auth` (import novo: `from app.models.clinic import Clinic`):

```python
async def _validate_station(session: AsyncSession, claims: dict[str, Any]) -> Clinic:
    clinic = await session.get(Clinic, uuid.UUID(claims["clinic_id"]))
    if clinic is None:
        raise AppError("invalid_credentials", 401)
    if claims.get("station_key_version") != clinic.station_key_version:
        # rotacionar a chave da estação revoga todo token emitido antes
        raise AppError("station_key_rotated", 401)
    return clinic


async def get_station_claims(
    session: AsyncSession = Depends(get_session),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    claims = _decode_bearer(authorization)
    if claims.get("kind") != "station":
        raise AppError("forbidden", 403)
    await _validate_station(session, claims)
    return claims
```

Em `get_current_auth`, substitua o bloco final

```python
    # kind == "station": branch adicionado na Task 6 (valida station_key_version);
    # até lá token de estação não autentica.
    raise AppError("invalid_credentials", 401)
```

por:

```python
    if claims.get("kind") == "station":
        clinic = await _validate_station(session, claims)
        return AuthContext(kind="station", clinic_id=clinic.id, membership=None)
    raise AppError("invalid_credentials", 401)
```

Adicione a rota em `backend/app/api/routes/auth.py` (imports novos: `uuid`, `Clinic`, `StationLoginRequest`):

```python
import uuid

from app.models.clinic import Clinic
from app.schemas.auth import StationLoginRequest


@router.post("/station")
async def station_login(
    body: StationLoginRequest, session: AsyncSession = Depends(get_session)
) -> TokenResponse:
    clinic = (
        await session.execute(select(Clinic).where(Clinic.slug == body.clinic_slug))
    ).scalar_one_or_none()
    if (
        clinic is None
        or clinic.station_key_hash is None
        or not verify_password(body.station_key, clinic.station_key_hash)
    ):
        raise AppError("invalid_credentials", 401)

    token = create_jwt(
        {
            "kind": "station",
            "clinic_id": str(clinic.id),
            "station_key_version": clinic.station_key_version,
            # station_id identifica ESTA sessão de estação: é a chave do
            # rate limit de PIN (lockout por estação, não por clínica)
            "station_id": str(uuid.uuid4()),
        },
        expires_in=timedelta(hours=12),
    )
    return TokenResponse(access_token=token)
```

- [ ] **Step 9: Rodar e ver passar**

Run (em `backend/`): `uv run pytest tests/test_auth_station.py tests/test_deps_tenancy.py -q`
Expected: `9 passed` (os testes da Task 5 continuam verdes com o novo branch).

- [ ] **Step 10: Commit**

```bash
git add backend/app/schemas/auth.py backend/app/api/deps.py backend/app/api/routes/auth.py backend/tests/helpers.py backend/tests/test_auth_station.py
git commit -m "feat(auth): login de estacao com station_key_version e revogacao por rotacao"
```

- [ ] **Step 11: Escrever os testes do fluxo de PIN, lockout e get_operator (que falham)**

Adicione a `backend/tests/test_auth_station.py`:

```python
async def test_fluxo_completo_estacao_pin_operator_token(client, session):
    clinic, membership = await _station_setup(session, pin="1234")
    station_jwt = await _station_login(client, clinic)

    resp = await client.post(
        "/api/v1/auth/pin", json={"pin": "1234"}, headers=bearer(station_jwt)
    )

    assert resp.status_code == 200
    operator_jwt = resp.json()["operator_token"]
    claims = decode_jwt(operator_jwt)
    assert claims["kind"] == "operator"
    assert claims["clinic_id"] == str(clinic.id)
    assert claims["membership_id"] == str(membership.id)
    assert claims["exp"] - time.time() <= 5 * 60 + 5  # operator token vive 5 min

    # o operator token vira ActorInfo no get_operator
    auth = AuthContext(kind="station", clinic_id=clinic.id, membership=None)
    actor = await get_operator(auth=auth, session=session, x_operator_token=operator_jwt)
    assert actor.membership_id == membership.id
    assert actor.name == "Tec. Joao"


async def test_pin_errado_e_401_e_audita_pin_failed(client, session):
    clinic, _ = await _station_setup(session, pin="1234")
    station_jwt = await _station_login(client, clinic)

    resp = await client.post(
        "/api/v1/auth/pin", json={"pin": "9999"}, headers=bearer(station_jwt)
    )

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_credentials"

    entry = (
        await session.execute(
            select(AuditEntry)
            .where(AuditEntry.clinic_id == clinic.id, AuditEntry.action == "pin_failed")
            .order_by(AuditEntry.id.desc())
        )
    ).scalars().first()
    assert entry is not None
    assert entry.payload["extra"]["station_id"] == decode_jwt(station_jwt)["station_id"]


async def test_lockout_apos_5_falhas_no_endpoint(client, session):
    clinic, _ = await _station_setup(session, pin="1234")
    station_jwt = await _station_login(client, clinic)

    for _ in range(5):
        resp = await client.post(
            "/api/v1/auth/pin", json={"pin": "0000"}, headers=bearer(station_jwt)
        )
        assert resp.status_code == 401

    # até o PIN CERTO é recusado durante o lockout
    resp = await client.post(
        "/api/v1/auth/pin", json={"pin": "1234"}, headers=bearer(station_jwt)
    )
    assert resp.status_code == 429
    body = resp.json()
    assert body["error"]["code"] == "pin_locked_out"
    assert body["error"]["params"]["retry_after_seconds"] > 0
    # a liberação após 15 min é coberta em tests/test_pin_throttle.py com clock injetado


async def test_pin_com_token_pessoal_e_forbidden(client, session):
    clinic, _ = await _station_setup(session)
    user = await make_user(session, email="vet2@plantao.vet")
    vet = await make_membership(session, clinic=clinic, user=user, role="vet")

    resp = await client.post(
        "/api/v1/auth/pin", json={"pin": "1234"}, headers=bearer(personal_token(vet))
    )

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


async def test_estacao_sem_operator_token_e_operator_required(session):
    clinic, _ = await _station_setup(session)
    auth = AuthContext(kind="station", clinic_id=clinic.id, membership=None)

    with pytest.raises(AppError) as exc:
        await get_operator(auth=auth, session=session, x_operator_token=None)

    assert exc.value.code == "operator_required"
    assert exc.value.status_code == 403


async def test_operator_token_expirado_e_operator_required(session):
    clinic, membership = await _station_setup(session)
    expired = create_jwt(
        {
            "kind": "operator",
            "clinic_id": str(clinic.id),
            "membership_id": str(membership.id),
        },
        expires_in=timedelta(seconds=-1),
    )
    auth = AuthContext(kind="station", clinic_id=clinic.id, membership=None)

    with pytest.raises(AppError) as exc:
        await get_operator(auth=auth, session=session, x_operator_token=expired)

    assert exc.value.code == "operator_required"


async def test_operator_token_de_outra_clinica_e_operator_required(client, session):
    clinic_a, _ = await _station_setup(session)
    clinic_b = await make_clinic(
        session, slug="clinica-b", station_key_hash=hash_password(STATION_KEY)
    )
    user_b = await make_user(session, email="tech-b@plantao.vet")
    membership_b = await make_membership(
        session, clinic=clinic_b, user=user_b, role="tech", pin_hash=hash_password("4321")
    )
    operator_b = create_jwt(
        {
            "kind": "operator",
            "clinic_id": str(clinic_b.id),
            "membership_id": str(membership_b.id),
        },
        expires_in=timedelta(minutes=5),
    )
    auth_a = AuthContext(kind="station", clinic_id=clinic_a.id, membership=None)

    with pytest.raises(AppError) as exc:
        await get_operator(auth=auth_a, session=session, x_operator_token=operator_b)

    assert exc.value.code == "operator_required"
```

- [ ] **Step 12: Rodar e ver falhar**

Run (em `backend/`): `uv run pytest tests/test_auth_station.py -q`
Expected: os 3 testes anteriores passam; os 7 novos FALHAM (`/api/v1/auth/pin` responde 404; `get_operator` com operator token válido levanta `operator_required` porque o caminho estação ainda é o stub da Task 5).

- [ ] **Step 13: Implementar POST /auth/pin e completar get_operator**

Adicione a rota em `backend/app/api/routes/auth.py` (imports novos: `get_station_claims`, `pin_throttle`, `AuditService`, `PinRequest`, `OperatorTokenResponse`):

```python
from app.api.deps import get_station_claims
from app.schemas.auth import OperatorTokenResponse, PinRequest
from app.services.audit import AuditService
from app.services.pin import pin_throttle


@router.post("/pin")
async def exchange_pin(
    body: PinRequest,
    claims: dict = Depends(get_station_claims),
    session: AsyncSession = Depends(get_session),
) -> OperatorTokenResponse:
    station_id: str = claims["station_id"]
    clinic_id = uuid.UUID(claims["clinic_id"])

    pin_throttle.check(station_id)  # 429 pin_locked_out durante o lockout

    memberships = (
        await session.execute(
            select(Membership).where(
                Membership.clinic_id == clinic_id,
                Membership.is_active.is_(True),
                Membership.pin_hash.is_not(None),
            )
        )
    ).scalars().all()
    match = next(
        (m for m in memberships if verify_password(body.pin, m.pin_hash)), None
    )

    if match is None:
        pin_throttle.register_failure(station_id)
        await AuditService.record(
            session,
            clinic_id=clinic_id,
            actor=None,
            action="pin_failed",
            entity_type="station",
            entity_id=None,
            extra={"station_id": station_id},
        )
        # commit ANTES do raise: o rastro da falha sobrevive ao rollback do erro
        await session.commit()
        raise AppError("invalid_credentials", 401)

    pin_throttle.reset(station_id)
    token = create_jwt(
        {
            "kind": "operator",
            "clinic_id": str(clinic_id),
            "membership_id": str(match.id),
        },
        expires_in=timedelta(minutes=5),
    )
    return OperatorTokenResponse(operator_token=token)
```

Em `backend/app/api/deps.py`, substitua o final de `get_operator`

```python
    # Modo estação: a Task 6 insere aqui a troca do X-Operator-Token por ActorInfo.
    raise AppError("operator_required", 403)
```

por:

```python
    # Modo estação: cada ação exige operator token de 5 min obtido via PIN.
    if x_operator_token is None:
        raise AppError("operator_required", 403)
    try:
        claims = decode_jwt(x_operator_token)
    except AppError as exc:
        raise AppError("operator_required", 403) from exc
    if claims.get("kind") != "operator" or claims.get("clinic_id") != str(auth.clinic_id):
        raise AppError("operator_required", 403)
    membership = (
        await session.execute(
            select(Membership).where(
                Membership.id == uuid.UUID(claims["membership_id"]),
                Membership.clinic_id == auth.clinic_id,
                Membership.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        raise AppError("operator_required", 403)
    user = await session.get(User, membership.user_id)
    return ActorInfo(
        membership_id=membership.id,
        name=user.name,
        license_number=membership.license_number,
        license_authority=membership.license_authority,
    )
```

- [ ] **Step 14: Rodar e ver passar**

Run (em `backend/`): `uv run pytest tests/test_auth_station.py -q`
Expected: `10 passed`

- [ ] **Step 15: Commit**

```bash
git add backend/app/api/routes/auth.py backend/app/api/deps.py backend/tests/test_auth_station.py
git commit -m "feat(auth): troca de PIN por operator token de 5 min com lockout e auditoria"
```

- [ ] **Step 16: Escrever os testes de definição de PIN (que falham)**

Adicione a `backend/tests/test_auth_station.py`:

```python
async def _admin_headers(session, clinic):
    admin_user = await make_user(session, email="admin@plantao.vet")
    admin = await make_membership(session, clinic=clinic, user=admin_user, role="admin")
    return bearer(personal_token(admin))


async def test_pin_duplicado_na_clinica_e_recusado(client, session):
    clinic, _tech_com_1234 = await _station_setup(session, pin="1234")
    headers = await _admin_headers(session, clinic)
    other_user = await make_user(session, email="vet3@plantao.vet")
    other = await make_membership(session, clinic=clinic, user=other_user, role="vet")

    resp = await client.post(
        f"/api/v1/memberships/{other.id}/pin", json={"pin": "1234"}, headers=headers
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "pin_duplicate"

    resp = await client.post(
        f"/api/v1/memberships/{other.id}/pin", json={"pin": "5678"}, headers=headers
    )
    assert resp.status_code == 204


async def test_definir_pin_exige_admin(client, session):
    clinic, tech = await _station_setup(session, pin="1234")
    vet_user = await make_user(session, email="vet4@plantao.vet")
    vet = await make_membership(session, clinic=clinic, user=vet_user, role="vet")

    resp = await client.post(
        f"/api/v1/memberships/{tech.id}/pin",
        json={"pin": "2222"},
        headers=bearer(personal_token(vet)),
    )

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


async def test_definir_pin_de_membership_de_outra_clinica_e_404(client, session):
    clinic_a, _ = await _station_setup(session)
    headers_a = await _admin_headers(session, clinic_a)
    clinic_b = await make_clinic(session, slug="clinica-b-pin")
    user_b = await make_user(session, email="alvo-b@plantao.vet")
    membership_b = await make_membership(session, clinic=clinic_b, user=user_b, role="tech")

    resp = await client.post(
        f"/api/v1/memberships/{membership_b.id}/pin",
        json={"pin": "3333"},
        headers=headers_a,
    )

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"
```

- [ ] **Step 17: Rodar e ver falhar**

Run (em `backend/`): `uv run pytest tests/test_auth_station.py -q`
Expected: os 3 novos testes FALHAM com 404 em `/api/v1/memberships/{id}/pin` (rota não existe; o teste de tenant falha com o assert do código `not_found`).

- [ ] **Step 18: Implementar o endpoint de definição de PIN**

Crie `backend/app/api/routes/memberships.py`:

```python
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    AuthContext,
    get_current_auth,
    get_operator,
    get_session,
    get_tenant_obj,
)
from app.core.errors import AppError
from app.models.membership import Membership
from app.schemas.auth import SetPinRequest
from app.services.audit import ActorInfo
from app.services.pin import PinService

router = APIRouter(prefix="/api/v1/memberships", tags=["memberships"])


@router.post("/{membership_id}/pin", status_code=204)
async def set_pin(
    membership_id: uuid.UUID,
    body: SetPinRequest,
    auth: AuthContext = Depends(get_current_auth),
    actor: ActorInfo = Depends(get_operator),
    session: AsyncSession = Depends(get_session),
) -> None:
    # v1: só o admin da clínica define/troca PINs (tela de gestão da semana 2)
    if auth.kind != "personal" or auth.membership.role != "admin":
        raise AppError("forbidden", 403)
    membership = await get_tenant_obj(session, Membership, membership_id, auth.clinic_id)
    await PinService.set_pin(session, membership=membership, pin=body.pin, actor=actor)
    await session.commit()
```

Em `backend/app/main.py`, registre o router (import no topo, `include_router` em `create_app()`):

```python
from app.api.routes import memberships as membership_routes

# ... dentro de create_app():
app.include_router(membership_routes.router)
```

- [ ] **Step 19: Rodar e ver passar + suíte inteira**

Run (em `backend/`): `uv run pytest tests/test_auth_station.py -q && uv run pytest -q && uv run ruff check .`
Expected: `13 passed` no arquivo, suíte inteira verde, lint limpo.

- [ ] **Step 20: Commit**

```bash
git add backend/app/api/routes/memberships.py backend/app/main.py backend/tests/test_auth_station.py
git commit -m "feat(auth): definicao de PIN admin-only com recusa de PIN duplicado"
```

<!-- CONTINUA: Task 7 -->

