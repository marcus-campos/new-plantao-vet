"""Planos comerciais: catálogo, atribuição e migração.

Quem vende cria, aposenta e migra planos sem mexer em código. O que este
serviço garante é o que a tabela sozinha não garante: que uma clínica nova só
entra em plano ativo, que um plano de teste já nasce com a data de fim, e que
migrar um plano inteiro deixa uma entrada na trilha de CADA clínica movida.
"""

import uuid
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.clinic import DEFAULT_PLANS, Clinic
from app.models.plan import Plan
from app.services.audit import ActorInfo, AuditService


class PlanService:
    @staticmethod
    async def ensure_defaults(session: AsyncSession) -> int:
        """Semeia o catálogo inicial quando a tabela está vazia.

        Só quando está VAZIA: depois que a plataforma criou ou aposentou um
        plano, recriar os três de origem seria desfazer uma decisão comercial."""
        existe = await session.scalar(sa.select(sa.func.count()).select_from(Plan))
        if existe:
            return 0
        for spec in DEFAULT_PLANS:
            session.add(Plan(**spec))
        await session.flush()
        return len(DEFAULT_PLANS)

    @staticmethod
    async def get(session: AsyncSession, code: str) -> Plan:
        plan = await session.scalar(sa.select(Plan).where(Plan.code == code))
        if plan is None:
            raise AppError("unknown_plan", 422, plan=code)
        return plan

    @staticmethod
    async def assignable(session: AsyncSession, code: str) -> Plan:
        """Um plano aposentado não recebe clínica nova. Quem já está nele fica."""
        plan = await PlanService.get(session, code)
        if not plan.is_active:
            raise AppError("plan_retired", 422, plan=code)
        return plan

    @staticmethod
    def apply(clinic: Clinic, plan: Plan, *, now: datetime | None = None) -> None:
        """Põe a clínica no plano: código, limite, e o teste quando é de teste.

        Um plano de teste é um plano com `trial_days`: a clínica entra em
        `trial` com a data de fim já calculada. Um plano PAGO não mexe no
        status: um teste de 30 dias do Pro é uma decisão comercial de quem
        vende, e é quem vende que marca `active` quando o pagamento chega.
        Pacote e ciclo de vida da assinatura são coisas diferentes."""
        now = now or datetime.now(UTC)
        clinic.plan_tier = plan.code
        clinic.bed_limit = plan.bed_limit
        if plan.trial_days > 0:
            clinic.subscription_status = "trial"
            clinic.trial_ends_at = now + timedelta(days=plan.trial_days)

    @staticmethod
    async def migrate(
        session: AsyncSession,
        *,
        source: Plan,
        target: Plan,
        actor: ActorInfo,
        retire_source: bool = True,
    ) -> list[uuid.UUID]:
        """Move TODAS as clínicas de um plano para outro, num ato.

        É o fim do plano "fundador": preço de lançamento para as primeiras
        clínicas, que depois vão para o definitivo. Cada clínica movida ganha
        uma entrada na própria trilha: o cliente vê que o plano mudou, e para
        qual. O plano de origem é aposentado por padrão, porque migrar e deixar
        a porta aberta para clínica nova seria migrar de novo daqui a um mês."""
        if source.code == target.code:
            raise AppError("validation_error", 422, field="to")
        if not target.is_active:
            raise AppError("plan_retired", 422, plan=target.code)
        clinics = list(
            (
                await session.execute(sa.select(Clinic).where(Clinic.plan_tier == source.code))
            ).scalars()
        )
        movidas: list[uuid.UUID] = []
        for clinic in clinics:
            before = AuditService.snapshot(clinic)
            PlanService.apply(clinic, target)
            await session.flush()
            await AuditService.record(
                session,
                clinic_id=clinic.id,
                actor=actor,
                action="plan_migrated",
                entity_type="clinic",
                entity_id=clinic.id,
                before=before,
                after=AuditService.snapshot(clinic),
                extra={"from": source.code, "to": target.code},
            )
            movidas.append(clinic.id)
        if retire_source and source.is_active:
            source.is_active = False
            source.retired_at = datetime.now(UTC)
            await session.flush()
        return movidas

    @staticmethod
    async def clinic_counts(session: AsyncSession) -> dict[str, int]:
        rows = await session.execute(
            sa.select(Clinic.plan_tier, sa.func.count())
            .where(Clinic.plan_tier.is_not(None))
            .group_by(Clinic.plan_tier)
        )
        return {code: int(n) for code, n in rows.all()}
