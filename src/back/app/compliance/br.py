from app.compliance import ComplianceProfile, IdentifierKind

# Perfil brasileiro (CFMV Res. 1321/2020 + 1653/2025). Nenhuma regra
# específica de país vive fora deste módulo.
BR_PROFILE = ComplianceProfile(
    name="br",
    name_key="compliance.profile.br",
    license_authority_label_key="compliance.br.license_authority_label",
    requires_daily_progress_note=True,
    retention_years=5,
    patient_identifier_kinds=(
        IdentifierKind(kind="microchip", label_key="identifier.microchip", pattern=r"^\d{9,15}$"),
        IdentifierKind(kind="rga", label_key="identifier.rga"),
    ),
    responsible_label_key="responsible.owner",
)
