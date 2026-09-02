"""Quem pode fazer o quê.

Um lugar só. Espalhar `if role == "vet"` pelas rotas é como o sistema deixa
passar um técnico prescrevendo: a regra fica onde ninguém procura.

Duas classes de regra, que NÃO se misturam:

* **Reservado ao profissional habilitado** (`LICENSED_ONLY`): prescrever,
  assinar evolução, dar alta e declarar óbito são atos privativos de quem tem
  registro no conselho (CFMV Res. 1321/2020 na veterinária; o equivalente do
  CFM na saúde humana). A clínica não pode delegar isso a quem não é
  habilitado, nem que queira: é a lei, não a preferência dela.
* **Política da clínica**: o resto. Quem interna, quem mexe em preço, quem lê
  a conta. Aqui existe um padrão razoável, e é o que a clínica poderá
  customizar quando os papéis viraram configuração.
"""

from typing import Final

# ---- atos clínicos, privativos de quem tem registro -----------------------
PRESCRIPTION_CREATE: Final = "prescription.create"
PRESCRIPTION_ADJUST: Final = "prescription.adjust"
PRESCRIPTION_SUSPEND: Final = "prescription.suspend"
PROGRESS_NOTE_SIGN: Final = "progress_note.sign"
HOSPITALIZATION_DISCHARGE: Final = "hospitalization.discharge"

# ---- operação do plantão --------------------------------------------------
TASK_EXECUTE: Final = "task.execute"
TASK_AD_HOC: Final = "task.ad_hoc"
HOSPITALIZATION_ADMIT: Final = "hospitalization.admit"
PATIENT_REGISTER: Final = "patient.register"
OWNER_CONTACT: Final = "owner.contact"
#: Trabalhar NO plantão: escrever nota de beira de box, encerrar o próprio
#: turno, aprovar e aceitar boletim. É de quem está lá.
SHIFT_OPERATE: Final = "shift.operate"
#: MONTAR a escala é outro trabalho: planejamento de pessoas, feito uma ou duas
#: vezes por semana. As duas coisas viviam na mesma capacidade, e o resultado
#: era o avesso: um técnico montava a escala da clínica e o administrador não
#: conseguia escalar ninguém.
SHIFT_SCHEDULE: Final = "shift.schedule"
#: Abrir, renomear e desativar box é operação do plantão, não configuração:
#: o vet de madrugada precisa abrir um box sem esperar o administrador.
KENNEL_MANAGE: Final = "kennel.manage"

# ---- leituras que precisam de nome ----------------------------------------
#: Ler não é agir, mas ler dado de tutor, conta ou prontuário num aparelho
#: compartilhado sem ninguém identificado é vazamento, e nenhuma leitura do
#: sistema tinha capacidade nenhuma. Estas quatro exigem identificação; a
#: operação do plantão (fila, ficha, boxes, painel) segue aberta, que é
#: justamente para isso que o modo estação existe.
OWNER_READ: Final = "owner.read"
RECORD_READ: Final = "record.read"
TEAM_READ: Final = "team.read"

# ---- gestão ---------------------------------------------------------------
CLINIC_CONFIGURE: Final = "clinic.configure"
TEAM_MANAGE: Final = "team.manage"
PRICE_LIST_MANAGE: Final = "price_list.manage"
CHARGES_READ: Final = "charges.read"
#: Lançar item na conta é escrita, e estava sob a capacidade de LEITURA: o
#: técnico não via a conta na tabela de papéis e lia o extrato inteiro, enquanto
#: só o lançamento manual era barrado.
CHARGES_WRITE: Final = "charges.write"
AUDIT_READ: Final = "audit.read"

#: Nenhuma customização da clínica alcança estes: são privativos por lei.
LICENSED_ONLY: Final[frozenset[str]] = frozenset(
    {
        PRESCRIPTION_CREATE,
        PRESCRIPTION_ADJUST,
        PRESCRIPTION_SUSPEND,
        PROGRESS_NOTE_SIGN,
        HOSPITALIZATION_DISCHARGE,
    }
)

#: O papel que carrega registro no conselho. Na saúde humana é o médico; o
#: identificador continua "vet" porque renomear enum é migração, e o nome que
#: a pessoa lê vem do i18n, não daqui.
LICENSED_ROLE: Final = "vet"

_VET: Final[frozenset[str]] = LICENSED_ONLY | {
    TASK_EXECUTE,
    TASK_AD_HOC,
    HOSPITALIZATION_ADMIT,
    PATIENT_REGISTER,
    OWNER_CONTACT,
    OWNER_READ,
    RECORD_READ,
    SHIFT_OPERATE,
    SHIFT_SCHEDULE,
    KENNEL_MANAGE,
    CHARGES_READ,
    CHARGES_WRITE,
    AUDIT_READ,
}

# O técnico é quem executa à beira do box: administra, registra, contata o
# tutor. Não prescreve, não assina evolução, não dá alta.
#: Abrir e renomear box entra aqui de propósito: `KENNEL_MANAGE` está
#: documentado como "operação do plantão, não configuração: o profissional de
#: madrugada precisa abrir um box sem esperar o administrador", e às 3h quem
#: está ao lado do box é o técnico. Era o único papel sem essa capacidade.
_TECH: Final[frozenset[str]] = frozenset(
    {
        TASK_EXECUTE,
        TASK_AD_HOC,
        PATIENT_REGISTER,
        OWNER_CONTACT,
        OWNER_READ,
        RECORD_READ,
        SHIFT_OPERATE,
        KENNEL_MANAGE,
    }
)

# O administrador toca a clínica, não o paciente: configura, cobra, audita.
# Não executa tarefa clínica nem que seja dono da clínica.
_ADMIN: Final[frozenset[str]] = frozenset(
    {
        HOSPITALIZATION_ADMIT,
        PATIENT_REGISTER,
        OWNER_CONTACT,
        OWNER_READ,
        KENNEL_MANAGE,
        SHIFT_SCHEDULE,
        CLINIC_CONFIGURE,
        TEAM_MANAGE,
        TEAM_READ,
        PRICE_LIST_MANAGE,
        CHARGES_READ,
        CHARGES_WRITE,
        AUDIT_READ,
    }
)

DEFAULT_ROLE_CAPABILITIES: Final[dict[str, frozenset[str]]] = {
    "vet": _VET,
    "tech": _TECH,
    "admin": _ADMIN,
}


def can(role: str | None, capability: str) -> bool:
    if role is None:
        return False
    return capability in DEFAULT_ROLE_CAPABILITIES.get(role, frozenset())


def capabilities_of(role: str | None) -> frozenset[str]:
    """O que este papel pode. A interface usa para não oferecer o proibido."""
    if role is None:
        return frozenset()
    return DEFAULT_ROLE_CAPABILITIES.get(role, frozenset())
