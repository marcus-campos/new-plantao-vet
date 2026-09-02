from app.compliance import ComplianceProfile, IdentifierKind

# Saúde humana no Brasil. Mesmo produto, mesmo schema — o que muda é COMO o
# paciente é identificado e como se chama quem responde por ele.
#
# A retenção é maior (CFM Res. 1.821/2007 trata de 20 anos para o prontuário) e o
# registro profissional é o CRM, não o CRMV. Quando entrar um cliente de saúde
# humana de verdade, este é o arquivo que precisa de revisão jurídica — e só ele.
BR_HUMAN_PROFILE = ComplianceProfile(
    name="br_human",
    name_key="compliance.profile.br_human",
    license_authority_label_key="compliance.br_human.license_authority_label",
    requires_daily_progress_note=True,
    retention_years=20,
    patient_identifier_kinds=(
        IdentifierKind(kind="cpf", label_key="identifier.cpf", pattern=r"^\d{11}$"),
        IdentifierKind(kind="cns", label_key="identifier.cns", pattern=r"^\d{15}$"),
        IdentifierKind(kind="mrn", label_key="identifier.mrn"),
    ),
    responsible_label_key="responsible.guardian",
)
