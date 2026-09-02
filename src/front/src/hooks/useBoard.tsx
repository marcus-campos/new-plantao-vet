import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "../api/client";
import type { Board } from "../api/types";
import { useApiErrorMessage } from "../components/ui";

/** A fila da clínica, lida de um lugar só.
 *
 *  Painel e Internados chamavam `GET /board` cada um por conta, com intervalos
 *  diferentes (5s e 10s) e layouts diferentes, sob dois itens de menu. Uma
 *  fonte, um intervalo, um contador, que é a promessa arquitetural mais
 *  repetida do projeto: painel e ficha nunca divergem.
 *
 *  O erro NÃO derruba os dados anteriores: uma falha de rede não pode fazer a
 *  enfermaria achar que a ala esvaziou. Mostra-se o último estado bom, com o
 *  aviso por cima e a hora da última leitura que de fato funcionou.
 */
const POLL_MS = 8000;

export interface BoardState {
  data: Board | null;
  error: string | null;
  /** Primeira carga: é o que decide entre esqueleto e conteúdo. */
  loading: boolean;
  /** Quando os dados na tela foram lidos com sucesso. O painel antigo imprimia
   *  `new Date()` a cada render, ao lado de dados que podiam estar velhos, e
   *  aquilo lia-se como "atualizado agora". */
  fetchedAt: Date | null;
  reload: () => Promise<void>;
}

export function useBoard(): BoardState {
  const describeError = useApiErrorMessage();
  const [data, setData] = useState<Board | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [fetchedAt, setFetchedAt] = useState<Date | null>(null);
  const alive = useRef(true);

  const reload = useCallback(async () => {
    try {
      const next = await api.board();
      if (!alive.current) return;
      setData(next);
      setFetchedAt(new Date());
      setError(null);
    } catch (err) {
      if (alive.current) setError(describeError(err));
    } finally {
      if (alive.current) setLoading(false);
    }
  }, [describeError]);

  useEffect(() => {
    alive.current = true;
    void reload();
    const timer = setInterval(() => void reload(), POLL_MS);
    return () => {
      alive.current = false;
      clearInterval(timer);
    };
  }, [reload]);

  return { data, error, loading, fetchedAt, reload };
}
