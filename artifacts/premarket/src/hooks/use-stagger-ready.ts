import { useEffect, useState } from "react";

/**
 * Atraso deliberado antes de uma query ficar `enabled`.
 *
 * Existe pra evitar que vários cards do dashboard disparem seu subprocesso
 * Python no mesmo instante. Medido em produção (04-05/08): um mount só do
 * dashboard soltava sete interpretadores em 148ms -- alt-data, trend,
 * technicals, news, chart, market-alerts (x2, chaves diferentes: o card geral
 * e o do plano de saída). O teto do servidor (vaga-python.ts) contém o dano
 * de qualquer forma, mas quem sente a fila é sempre o card mais azarado.
 * Isto ataca a causa em vez de só conter o efeito: espaça a largada em vez de
 * deixar todo mundo competir pela mesma vaga no mesmo milissegundo.
 *
 * `delayMs = 0` retorna `true` de cara -- sem re-render extra pro que já era
 * pra ser imediato (cotações, o relatório, o gráfico principal).
 */
export function useStaggerReady(delayMs: number): boolean {
  const [ready, setReady] = useState(delayMs <= 0);

  useEffect(() => {
    if (delayMs <= 0) return;
    const t = setTimeout(() => setReady(true), delayMs);
    return () => clearTimeout(t);
  }, [delayMs]);

  return ready;
}
