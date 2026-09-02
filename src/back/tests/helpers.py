"""Helpers compartilhados pelos testes de API.

Tokens são emitidos direto por create_jwt (sem passar pelo endpoint) para que
cada teste dependa só do contrato de claims, não do fluxo de login inteiro.
"""

import uuid
from datetime import timedelta

from app.core.security import create_jwt
from app.models.clinic import Clinic
from app.models.membership import Membership


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def personal_token(membership: Membership, *, expires_in: timedelta = timedelta(hours=12)) -> str:
    return create_jwt(
        {
            "kind": "personal",
            "sub": str(membership.user_id),
            "clinic_id": str(membership.clinic_id),
            "membership_id": str(membership.id),
        },
        expires_in=expires_in,
    )


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


def operator_token(membership: Membership, *, expires_in: timedelta = timedelta(minutes=5)) -> str:
    """O token curto que o PIN emite na estação: identifica QUEM agiu."""
    return create_jwt(
        {
            "kind": "operator",
            "clinic_id": str(membership.clinic_id),
            "membership_id": str(membership.id),
        },
        expires_in=expires_in,
    )
