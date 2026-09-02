from dataclasses import dataclass


@dataclass(frozen=True)
class IdentifierKind:
    """Um jeito de identificar o paciente fora do sistema."""

    kind: str
    label_key: str
    #: Regex de validação (None = qualquer texto).
    pattern: str | None = None


@dataclass(frozen=True)
class ComplianceProfile:
    name: str
    #: Chave de tradução do nome da área ("Veterinária", "Saúde humana").
    name_key: str
    license_authority_label_key: str
    requires_daily_progress_note: bool
    retention_years: int
    #: Como o paciente é identificado nesta área/país. Veterinária: microchip.
    #: Saúde humana: CPF e CNS. Mesmo schema, perfis diferentes.
    patient_identifier_kinds: tuple["IdentifierKind", ...] = ()
    #: Quem responde pelo paciente: "tutor" na veterinária, "responsável" na
    #: saúde humana (onde muitas vezes é o próprio paciente).
    responsible_label_key: str = "responsible.owner"


def list_profiles() -> tuple[ComplianceProfile, ...]:
    """Tudo que a clínica pode escolher nas configurações.

    Um perfil novo (outro país, outra área) entra aqui e aparece na tela sem
    tocar em rota, schema ou front."""
    # Import tardio para evitar ciclo (br.py importa ComplianceProfile daqui).
    from app.compliance.br import BR_PROFILE
    from app.compliance.br_human import BR_HUMAN_PROFILE

    return (BR_PROFILE, BR_HUMAN_PROFILE)


def get_profile(name: str) -> ComplianceProfile:
    profiles = {profile.name: profile for profile in list_profiles()}
    try:
        return profiles[name]
    except KeyError:
        raise KeyError(f"unknown compliance profile: {name}") from None
