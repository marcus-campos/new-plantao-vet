import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models import (
    Clinic,
    Hospitalization,
    Kennel,
    Membership,
    Owner,
    Patient,
    Prescription,
    Role,
    Task,
    User,
)


async def make_clinic(session: AsyncSession, **overrides: Any) -> Clinic:
    suffix = uuid.uuid4().hex[:8]
    fields: dict[str, Any] = {"name": f"Clinic {suffix}", "slug": f"clinic-{suffix}"}
    fields.update(overrides)
    clinic = Clinic(**fields)
    session.add(clinic)
    await session.flush()
    return clinic


async def make_user(session: AsyncSession, **overrides: Any) -> User:
    suffix = uuid.uuid4().hex[:8]
    fields: dict[str, Any] = {
        "name": f"User {suffix}",
        "email": f"user-{suffix}@example.com",
        "password_hash": hash_password("secret123"),
    }
    fields.update(overrides)
    user = User(**fields)
    session.add(user)
    await session.flush()
    return user


async def make_membership(
    session: AsyncSession,
    *,
    clinic: Clinic | None = None,
    user: User | None = None,
    **overrides: Any,
) -> Membership:
    clinic = clinic or await make_clinic(session)
    user = user or await make_user(session)
    fields: dict[str, Any] = {"clinic_id": clinic.id, "user_id": user.id, "role": Role.vet}
    fields.update(overrides)
    membership = Membership(**fields)
    session.add(membership)
    await session.flush()
    return membership


async def make_kennel(session: AsyncSession, *, clinic: Clinic, **overrides: Any) -> Kennel:
    kennel = Kennel(clinic_id=clinic.id, **{"name": "Box 01", **overrides})
    session.add(kennel)
    await session.flush()
    return kennel


async def make_owner(session: AsyncSession, *, clinic: Clinic, **overrides: Any) -> Owner:
    owner = Owner(
        clinic_id=clinic.id,
        **{"name": "Tutor Teste", "phone_e164": "+5511999990000", **overrides},
    )
    session.add(owner)
    await session.flush()
    return owner


async def make_patient(
    session: AsyncSession, *, clinic: Clinic, owner: Owner | None = None, **overrides: Any
) -> Patient:
    owner = owner or await make_owner(session, clinic=clinic)
    patient = Patient(
        clinic_id=clinic.id,
        owner_id=owner.id,
        **{"name": "Thor", "species": "dog", **overrides},
    )
    session.add(patient)
    await session.flush()
    return patient


async def make_hospitalization(
    session: AsyncSession,
    *,
    clinic: Clinic,
    patient: Patient | None = None,
    membership: Membership | None = None,
    **overrides: Any,
) -> Hospitalization:
    patient = patient or await make_patient(session, clinic=clinic)
    if membership is None:
        user = await make_user(session)
        membership = await make_membership(session, clinic=clinic, user=user, role="vet")
    hospitalization = Hospitalization(
        clinic_id=clinic.id,
        patient_id=patient.id,
        vet_membership_id=membership.id,
        **{"consent_status": "consent_recorded", **overrides},
    )
    session.add(hospitalization)
    await session.flush()
    return hospitalization


async def make_prescription(
    session: AsyncSession,
    *,
    clinic: Clinic,
    hospitalization: Hospitalization | None = None,
    **overrides: Any,
) -> Prescription:
    hospitalization = hospitalization or await make_hospitalization(session, clinic=clinic)
    values: dict[str, Any] = {
        "kind": "recurring",
        "category": "medication",
        "name": "Dipirona 25 mg/kg IV",
        "details": {"drug": "dipirona"},
        "frequency_minutes": 480,
        "criticality": "normal",
        "tolerance_minutes": 60,
        "starts_at": datetime.now(UTC),
        **overrides,
    }
    prescription = Prescription(
        clinic_id=clinic.id, hospitalization_id=hospitalization.id, **values
    )
    session.add(prescription)
    await session.flush()
    return prescription


async def make_task(
    session: AsyncSession,
    *,
    clinic: Clinic,
    hospitalization: Hospitalization | None = None,
    **overrides: Any,
) -> Task:
    hospitalization = hospitalization or await make_hospitalization(session, clinic=clinic)
    values: dict[str, Any] = {
        "title": "Dipirona 500 mg",
        "category": "medication",
        "scheduled_for": datetime.now(UTC),
        "criticality": "normal",
        "tolerance_minutes": 60,
        "status": "pending",
        "price_minor": 1800,
        **overrides,
    }
    task = Task(clinic_id=clinic.id, hospitalization_id=hospitalization.id, **values)
    session.add(task)
    await session.flush()
    return task
