"""O nascimento de uma clínica: um caminho, dois portões.

Existiam duas portas possíveis — o back-office, onde você faz o onboarding à
mão, e o site, onde a clínica se cadastra sozinha — e o miolo é o mesmo:
clínica, plano, administrador, vínculo e uma entrada na trilha. Duas cópias
divergiriam no dia em que o onboarding ganhar um passo, e a que ficasse para
trás seria justamente a que ninguém olha.

O que separa as duas portas é o ATOR na trilha: "Suporte PlantãoVet · <nome>"
de um lado, "Cadastro pelo site" do outro. O cliente vê de onde a clínica veio.
"""

import secrets
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.security import hash_password
from app.models.clinic import Clinic
from app.models.membership import Membership
from app.models.user import User
from app.services.audit import ActorInfo, AuditService
from app.services.plans import PlanService

#: O que o back-office já exige de um slug (PlatformClinicCreate).
#: Repetido como constante porque agora quem GERA slug é este serviço.
SLUG_MIN = 3
SLUG_MAX = 40


@dataclass(slots=True)
class ClinicSpec:
    """Uma clínica a nascer. Os defaults servem ao cadastro pelo site; o
    back-office preenche o resto quando a negociação pediu outra coisa."""

    name: str
    admin_name: str
    admin_email: str
    admin_password: str | None = None
    #: Vazio: gerado do nome. O back-office manda o dele.
    slug: str | None = None
    plan_code: str = "trial"
    subscription_status: str = "trial"
    #: Só vale quando o plano NÃO é de teste; um plano de teste traz a duração.
    trial_days: int | None = None
    bed_limit: int | None = None
    locale: str = "pt-BR"
    currency: str = "BRL"
    timezone: str = "America/Sao_Paulo"
    compliance_profile: str = "br"
    contact_name: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None


class OnboardingService:
    @staticmethod
    def temporary_password() -> str:
        """Legível ao telefone: sem 0/O, 1/l, e em grupos.

        Uma senha ditada por voz precisa sobreviver à ligação ruim."""
        alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
        raw = "".join(secrets.choice(alphabet) for _ in range(12))
        return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"

    @staticmethod
    async def unique_slug(session: AsyncSession, name: str) -> str:
        """O slug sai do nome. Nunca é a razão de um cadastro falhar.

        "Slug" é jargão e a porta pública não pergunta. O nome da clínica vira
        `clinica-vida-animal`; nome curto demais, vazio ou só de emoji ganha um
        sufixo aleatório em vez de 422 — o padrão do back-office exige de 3 a
        40 caracteres com as pontas alfanuméricas, e um cadastro perdido por
        causa disso seria um cliente perdido por causa de uma regra interna."""
        sem_acento = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
        base = "".join(c if c.isalnum() else "-" for c in sem_acento.lower())
        base = "-".join(p for p in base.split("-") if p)[:SLUG_MAX].strip("-")
        if len(base) < SLUG_MIN:
            base = f"clinica-{secrets.token_hex(3)}"

        candidato = base
        n = 1
        while await session.scalar(sa.select(Clinic.id).where(Clinic.slug == candidato)):
            n += 1
            sufixo = f"-{n}"
            candidato = f"{base[: SLUG_MAX - len(sufixo)]}{sufixo}"
        return candidato

    @staticmethod
    async def create_clinic(
        session: AsyncSession, *, spec: ClinicSpec, actor: ActorInfo
    ) -> tuple[Clinic, User, Membership, str]:
        """A clínica, o primeiro administrador e o vínculo, num ato.

        NÃO faz commit: quem chama decide a transação. A rota do back-office
        precisa montar a ficha de detalhe antes de fechar, e a do site precisa
        emitir o token.

        Devolve a senha EM CLARO, uma vez. Depois disto só existe o hash. O
        vínculo sai junto porque o cadastro pelo site precisa dele para montar
        o token, e reconsultá-lo seria buscar o que acabou de ser criado."""
        plan = await PlanService.assignable(session, spec.plan_code)
        slug = spec.slug or await OnboardingService.unique_slug(session, spec.name)
        if spec.slug and await session.scalar(sa.select(Clinic.id).where(Clinic.slug == slug)):
            raise AppError("slug_taken", 409)

        email = spec.admin_email.strip().lower()
        if await session.scalar(sa.select(User.id).where(User.email == email)):
            raise AppError("email_taken", 409)

        clinic = Clinic(
            name=spec.name.strip(),
            slug=slug,
            subscription_status=spec.subscription_status,
            locale=spec.locale,
            currency=spec.currency.upper(),
            timezone=spec.timezone,
            compliance_profile=spec.compliance_profile,
            contact_name=spec.contact_name,
            contact_email=spec.contact_email,
            contact_phone=spec.contact_phone,
        )
        # O plano decide limite e, quando é de teste, o próprio teste. Um
        # limite informado por fora (negociação) vence o do plano.
        PlanService.apply(clinic, plan)
        if spec.bed_limit is not None:
            clinic.bed_limit = spec.bed_limit
        # Teste com prazo negociado num plano PAGO: o plano não traz duração,
        # então quem vende diz quantos dias.
        if spec.subscription_status == "trial" and clinic.trial_ends_at is None:
            dias = spec.trial_days if spec.trial_days is not None else 30
            clinic.trial_ends_at = datetime.now(UTC) + timedelta(days=dias)
        session.add(clinic)
        await session.flush()

        password = spec.admin_password or OnboardingService.temporary_password()
        admin = User(
            name=spec.admin_name.strip(), email=email, password_hash=hash_password(password)
        )
        session.add(admin)
        await session.flush()
        membership = Membership(clinic_id=clinic.id, user_id=admin.id, role="admin")
        session.add(membership)
        await session.flush()

        await AuditService.record(
            session,
            clinic_id=clinic.id,
            actor=actor,
            action="clinic_created",
            entity_type="clinic",
            entity_id=clinic.id,
            after=AuditService.snapshot(clinic),
        )
        return clinic, admin, membership, password
