import type { PrescriptionCategory, PrescriptionKind } from "./types";

/** Cada categoria fala a própria língua.
 *
 *  O formulário de prescrição inteiro estava escrito no vocabulário da
 *  medicação: "primeira DOSE agora" numa aferição de pressão arterial,
 *  "Dipirona 25 mg/kg IV" como exemplo de nome numa troca de curativo, e
 *  "contínua · infusão" oferecida para um procedimento. Campo que não faz
 *  sentido na tela é decisão a mais para quem prescreve, e um convite ao erro.
 *
 *  `kinds` é a lista de tipos que a categoria admite: infusão contínua faz
 *  sentido em fármaco, fluido e nutrição por sonda; não faz em monitoramento,
 *  cuidado ou procedimento. `dosed` diz se a calculadora aparece: aferir
 *  glicemia não tem mg/kg.
 *
 *  Mora aqui, e não dentro de uma tela, porque a tabela de preços faz a MESMA
 *  pergunta ao cadastrar o item: concentração em mg/ml e posologia só existem
 *  no que se dosa. Duas cópias divergiriam, e o sintoma seria a calculadora
 *  pedindo mg/ml de uma diária de internação. */
export interface CategoryProfile {
  kinds: PrescriptionKind[];
  dosed: boolean;
}

export const CATEGORY_PROFILE: Record<PrescriptionCategory, CategoryProfile> = {
  medication: { kinds: ["recurring", "continuous", "prn"], dosed: true },
  fluids: { kinds: ["continuous", "recurring"], dosed: true },
  monitoring: { kinds: ["recurring", "prn"], dosed: false },
  nutrition: { kinds: ["recurring", "continuous", "prn"], dosed: false },
  care: { kinds: ["recurring", "prn"], dosed: false },
  procedure: { kinds: ["recurring", "prn"], dosed: false },
};

/** A ordem em que as categorias aparecem em toda a interface. */
export const CATEGORIES: PrescriptionCategory[] = [
  "medication",
  "fluids",
  "monitoring",
  "nutrition",
  "care",
  "procedure",
];
