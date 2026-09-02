import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Role
from tests.factories import make_clinic, make_membership, make_user


async def test_clinic_defaults(db_session):
    clinic = await make_clinic(db_session)
    assert clinic.locale == "pt-BR"
    assert clinic.currency == "BRL"
    assert clinic.unit_system == "metric"
    assert clinic.compliance_profile == "br"
    assert clinic.timezone == "America/Sao_Paulo"
    assert clinic.station_key_version == 1
    # Âncoras chaveadas por MINUTOS (nunca horas) — default UFMS do brief.
    assert clinic.anchors == {
        "1440": ["10:00"],
        "720": ["10:00", "22:00"],
        "480": ["10:00", "18:00", "02:00"],
        "360": ["10:00", "16:00", "22:00", "04:00"],
    }
    # Cerimônias default por name_key (conteúdo NOSSO: traduzido na criação).
    assert [p["name_key"] for p in clinic.default_prescriptions] == [
        "ceremony.owner_contact",
        "ceremony.daily_progress_note",
    ]
    assert all(p["frequency_minutes"] == 1440 for p in clinic.default_prescriptions)


async def test_membership_unique_per_clinic_and_user(db_session):
    clinic = await make_clinic(db_session)
    user = await make_user(db_session)
    await make_membership(db_session, clinic=clinic, user=user)
    with pytest.raises(IntegrityError):
        await make_membership(db_session, clinic=clinic, user=user, role=Role.tech)


async def test_same_user_can_join_two_clinics(db_session):
    user = await make_user(db_session)
    first = await make_membership(db_session, user=user)
    second = await make_membership(db_session, user=user)
    assert first.clinic_id != second.clinic_id
    assert first.user_id == second.user_id
