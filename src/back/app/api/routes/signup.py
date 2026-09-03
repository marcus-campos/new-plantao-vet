"""A porta pública: uma clínica se cadastra sozinha e entra na hora.

Aberta de propósito, e é a única rota do sistema que CRIA um tenant sem
credencial nenhuma. O que a protege é o limite por IP daqui e o fato de o
plano de entrada ser um teste que vence: uma clínica falsa não custa nada
além de uma linha, e some da lista em 14 dias.

Não fica em `auth.py` de propósito: aquilo é sessão, isto é o nascimento de
uma clínica.
"""

from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.core.errors import AppError
from app.core.security import create_jwt
from app.schemas.auth import TokenResponse
from app.schemas.signup import SignupRequest
from app.services.audit import ActorInfo
from app.services.onboarding import ClinicSpec, OnboardingService

router = APIRouter(prefix="/api/v1", tags=["signup"])

#: Como o cadastro pelo site aparece na trilha da clínica. `membership_id=None`
#: porque não há ninguém da equipe agindo: a clínica ainda não existia.
SITE_ACTOR = ActorInfo(
    membership_id=None,
    name="Cadastro pelo site",
    role=None,
    license_number=None,
    license_authority=None,
)


class SignupThrottle:
    """Cinco cadastros por hora por IP.

    Em memória, como o `PinThrottle` que já resolve o mesmo tipo de problema.
    Serve a uma VM só, que é o deploy de hoje; com duas instâncias isto vira
    Redis, e esta classe é o único ponto de troca.

    Sem captcha de propósito: no lançamento, o custo de um cadastro perdido é
    maior que o de uma clínica falsa que vence em 14 dias.
    """

    max_signups = 5
    window = timedelta(hours=1)

    def __init__(self, now_fn: Callable[[], datetime] | None = None) -> None:
        self._now_fn = now_fn or (lambda: datetime.now(UTC))
        self._hits: dict[str, list[datetime]] = defaultdict(list)

    def check(self, ip: str) -> None:
        now = self._now_fn()
        recent = [t for t in self._hits[ip] if now - t < self.window]
        self._hits[ip] = recent
        if len(recent) >= self.max_signups:
            retry_after = int((self.window - (now - min(recent))).total_seconds())
            raise AppError("signup_rate_limited", 429, retry_after_seconds=retry_after)

    def register(self, ip: str) -> None:
        self._hits[ip].append(self._now_fn())

    def reset_all(self) -> None:
        self._hits.clear()


signup_throttle = SignupThrottle()


def client_ip(request: Request) -> str:
    """Quem está do outro lado, atrás do proxy.

    Em produção o app roda atrás do Caddy da VM, e `request.client.host` seria
    sempre o do proxy: um único IP para o mundo inteiro, e o limite barraria
    todo mundo depois do quinto cadastro do dia."""
    encaminhado = request.headers.get("X-Forwarded-For")
    if encaminhado:
        return encaminhado.split(",")[0].strip()
    return request.client.host if request.client else "desconhecido"


@router.post("/signup", response_model=TokenResponse, status_code=201)
async def signup(
    payload: SignupRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    """Cria a clínica e devolve a sessão, num ato.

    Devolve o MESMO token do login de propósito: uma segunda tela pedindo o
    e-mail e a senha que a pessoa acabou de digitar é onde ela desiste."""
    ip = client_ip(request)
    signup_throttle.check(ip)

    clinic, admin, membership, _ = await OnboardingService.create_clinic(
        session,
        spec=ClinicSpec(
            name=payload.clinic_name,
            admin_name=payload.admin_name,
            admin_email=payload.email,
            admin_password=payload.password,
            plan_code="trial",
            contact_name=payload.admin_name.strip(),
            contact_email=payload.email.strip().lower(),
            contact_phone=payload.phone,
        ),
        actor=SITE_ACTOR,
    )
    await session.commit()
    # Só conta depois de dar certo: um e-mail repetido não gasta a cota de
    # quem digitou errado e vai tentar de novo.
    signup_throttle.register(ip)

    token = create_jwt(
        {
            "kind": "personal",
            "sub": str(admin.id),
            "clinic_id": str(clinic.id),
            "membership_id": str(membership.id),
        },
        expires_in=timedelta(hours=12),
    )
    return TokenResponse(access_token=token)
