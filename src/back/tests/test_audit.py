import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.models import AuditEntry
from app.services.audit import ActorInfo, AuditService
from tests.factories import make_clinic, make_membership


class _SnapshotBase(DeclarativeBase):
    # Base separada: a tabela-sonda não entra no metadata do app
    # (nunca é criada no banco; snapshot só inspeciona o objeto mapeado).
    pass


class SnapshotProbe(_SnapshotBase):
    __tablename__ = "snapshot_probe"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(sa.Text)
    phone_e164: Mapped[str] = mapped_column(sa.Text)
    tax_id: Mapped[str] = mapped_column(sa.Text)
    password_hash: Mapped[str] = mapped_column(sa.Text)
    pin_hash: Mapped[str] = mapped_column(sa.Text)
    station_key_hash: Mapped[str] = mapped_column(sa.Text)
    secret_hash: Mapped[str] = mapped_column(sa.Text)
    enrollment_code_hash: Mapped[str] = mapped_column(sa.Text)


def test_redacted_set_covers_all_sensitive_columns():
    """A lista é explícita de propósito: coluna sensível nova só some da
    trilha quando alguém a acrescenta aqui, e este teste é o lembrete."""
    assert AuditService.REDACTED == {
        "phone_e164",
        "tax_id",
        "password_hash",
        "pin_hash",
        "station_key_hash",
        # Segredo do aparelho e código de liberação: a trilha registra QUE um
        # aparelho entrou, nunca com o quê.
        "secret_hash",
        "enrollment_code_hash",
    }


def test_snapshot_redacts_sensitive_columns_and_stringifies_uuid():
    probe_id = uuid.uuid4()
    probe = SnapshotProbe(
        id=probe_id,
        name="Rex",
        phone_e164="+5511999998888",
        tax_id="123.456.789-00",
        password_hash="x",
        pin_hash="y",
        station_key_hash="z",
    )
    snap = AuditService.snapshot(probe)
    assert snap == {"id": str(probe_id), "name": "Rex"}


async def test_record_stores_before_and_after(db_session):
    clinic = await make_clinic(db_session)
    membership = await make_membership(db_session, clinic=clinic)
    actor = ActorInfo(
        membership_id=membership.id,
        name="Dra. Ana",
        license_number="12345",
        license_authority="CRMV-SP",
    )
    entity_id = uuid.uuid4()
    await AuditService.record(
        db_session,
        clinic_id=clinic.id,
        actor=actor,
        action="task_executed",
        entity_type="task",
        entity_id=entity_id,
        before={"status": "pending"},
        after={"status": "done"},
        extra={"early": False},
    )
    entry = (
        await db_session.execute(sa.select(AuditEntry).where(AuditEntry.clinic_id == clinic.id))
    ).scalar_one()
    assert entry.payload == {
        "before": {"status": "pending"},
        "after": {"status": "done"},
        "extra": {"early": False},
    }
    assert entry.actor_membership_id == membership.id
    assert entry.actor_name == "Dra. Ana"
    assert entry.actor_license == "12345"
    assert entry.actor_license_authority == "CRMV-SP"
    assert entry.action == "task_executed"
    assert entry.entity_type == "task"
    assert entry.entity_id == entity_id
    assert entry.prev_hash == ""
    assert len(entry.entry_hash) == 64


async def test_hash_chain_links_entries_of_same_clinic(db_session):
    clinic = await make_clinic(db_session)
    await AuditService.record(
        db_session,
        clinic_id=clinic.id,
        actor=None,
        action="hospitalization_admitted",
        entity_type="hospitalization",
        entity_id=None,
    )
    await AuditService.record(
        db_session,
        clinic_id=clinic.id,
        actor=None,
        action="prescription_created",
        entity_type="prescription",
        entity_id=None,
    )
    entries = (
        (
            await db_session.execute(
                sa.select(AuditEntry)
                .where(AuditEntry.clinic_id == clinic.id)
                .order_by(AuditEntry.id)
            )
        )
        .scalars()
        .all()
    )
    assert entries[0].prev_hash == ""
    assert entries[0].actor_name == "system"
    assert entries[1].prev_hash == entries[0].entry_hash
    assert entries[1].entry_hash != entries[0].entry_hash


async def test_hash_chains_are_independent_per_clinic(db_session):
    clinic_a = await make_clinic(db_session)
    clinic_b = await make_clinic(db_session)
    await AuditService.record(
        db_session,
        clinic_id=clinic_a.id,
        actor=None,
        action="hospitalization_admitted",
        entity_type="hospitalization",
        entity_id=None,
    )
    await AuditService.record(
        db_session,
        clinic_id=clinic_b.id,
        actor=None,
        action="hospitalization_admitted",
        entity_type="hospitalization",
        entity_id=None,
    )
    entry_b = (
        await db_session.execute(sa.select(AuditEntry).where(AuditEntry.clinic_id == clinic_b.id))
    ).scalar_one()
    # A cadeia da clínica B começa do zero: não herda o hash da clínica A.
    assert entry_b.prev_hash == ""


async def test_direct_update_on_audit_entries_is_blocked(db_session):
    clinic = await make_clinic(db_session)
    await AuditService.record(
        db_session,
        clinic_id=clinic.id,
        actor=None,
        action="hospitalization_admitted",
        entity_type="hospitalization",
        entity_id=None,
    )
    with pytest.raises(DBAPIError, match="append-only"):
        await db_session.execute(sa.text("UPDATE audit_entries SET action = 'tampered'"))


async def test_direct_delete_on_audit_entries_is_blocked(db_session):
    clinic = await make_clinic(db_session)
    await AuditService.record(
        db_session,
        clinic_id=clinic.id,
        actor=None,
        action="hospitalization_admitted",
        entity_type="hospitalization",
        entity_id=None,
    )
    with pytest.raises(DBAPIError, match="append-only"):
        await db_session.execute(sa.text("DELETE FROM audit_entries"))
