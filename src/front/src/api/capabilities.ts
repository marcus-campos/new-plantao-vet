/** Espelho de src/back/app/permissions.py.
 *
 *  A verdade é do servidor: isto existe só para a interface não OFERECER o
 *  que a API vai recusar. Nunca é a checagem em si. */
export const CAN = {
  prescriptionCreate: "prescription.create",
  prescriptionAdjust: "prescription.adjust",
  prescriptionSuspend: "prescription.suspend",
  progressNoteSign: "progress_note.sign",
  hospitalizationDischarge: "hospitalization.discharge",
  hospitalizationAdmit: "hospitalization.admit",
  taskExecute: "task.execute",
  taskAdHoc: "task.ad_hoc",
  patientRegister: "patient.register",
  ownerContact: "owner.contact",
  /** Trabalhar NO plantão: nota de beira de box, encerrar o turno, aprovar e
   *  aceitar boletim. */
  shiftOperate: "shift.operate",
  /** MONTAR a escala: planejamento de pessoas. Estavam na mesma capacidade, e
   *  o resultado era o avesso: um técnico montava a escala da clínica e o
   *  administrador não conseguia escalar ninguém. */
  shiftSchedule: "shift.schedule",
  kennelManage: "kennel.manage",
  clinicConfigure: "clinic.configure",
  teamManage: "team.manage",
  priceListManage: "price_list.manage",
  auditRead: "audit.read",
  /** Leituras que exigem alguém identificado. Não existiam: toda leitura do
   *  sistema era aberta, e um tablet de corredor sem PIN lia CPF de tutor,
   *  extrato e prontuário inteiros. */
  ownerRead: "owner.read",
  recordRead: "record.read",
  teamRead: "team.read",
  chargesRead: "charges.read",
  /** Lançar item na conta é escrita. Estava sob a capacidade de LEITURA. */
  chargesWrite: "charges.write",
} as const;
