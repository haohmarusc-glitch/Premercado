/**
 * Background job que atualiza o capex dos hiperescaladores (ver
 * agent/capex_hyperscalers.py) — a tese de IA/data center como fato datado
 * no snapshot do Veredito, em vez de opinião do modelo.
 *
 * Por que SEMANAL, e não diário como o calendário de earnings: capex é dado
 * TRIMESTRAL. Entre uma divulgação e a seguinte passam ~13 semanas, e o
 * número não muda no meio. Rodar diariamente gastaria rede para reconfirmar
 * o mesmo valor 90 vezes. Semanal ainda pega qualquer divulgação dentro de
 * poucos dias — folga de sobra para um dado que se move quatro vezes por ano.
 *
 * O script grava overlay em disco (CAPEX_OVERLAY_PATH, default
 * /var/cache/premercado/capex_hyperscalers.json), lido por agent.py no
 * momento do Veredito. Mesma consequência dos outros overlays: um rebuild da
 * imagem apaga o cache, e o primeiro ciclo do checker o reconstrói.
 *
 * O script mescla a coleta nova com o histórico já guardado no overlay, então
 * um ciclo com a cota da Alpha Vantage esgotada não encolhe a série — mas os
 * campos `historicoRaso` e `usandoGuardado` ficam no log justamente para o
 * caso de a coleta parar de trazer dado novo semana após semana sem que nada
 * quebre visivelmente.
 */
import { runScript } from "../routes/scenarios";
import { state as agentState } from "./runner";
import { logger } from "./logger";

// Cinco tickers, cada um com uma busca de fundamentos — mais lento que uma
// cotação. Timeout generoso pelo mesmo motivo dos outros checkers: falhar
// aqui custa uma SEMANA sem refresh, e ninguém está esperando na tela.
const TIMEOUT_MS = 180_000;

interface CapexRefreshResult {
  ok: boolean;
  erro?: string;
  trimestre?: string | null;
  totalUsdBi?: number | null;
  direcao?: string | null;
  variacaoQoQPct?: number | null;
  empresasComDado?: number;
  falhas?: string[];
  historicoRaso?: string[];
  usandoGuardado?: string[];
  fontes?: string[];
  overlay?: string | null;
}

export async function refreshCapexHyperscalers(): Promise<void> {
  // Mesmo motivo dos outros checkers: não competir por CPU/rede com o
  // agente diário.
  if (agentState.running) {
    logger.info("Capex hyperscalers: pulando ciclo -- agente diário em execução");
    return;
  }

  let out: string;
  try {
    out = await runScript("capex_hyperscalers.py", ["--json"], TIMEOUT_MS);
  } catch (err) {
    logger.error({ err }, "Capex hyperscalers: falha ao rodar capex_hyperscalers.py");
    return;
  }

  let parsed: CapexRefreshResult;
  try {
    parsed = JSON.parse(out);
  } catch (err) {
    logger.error({ err, out: out.slice(0, 500) },
                 "Capex hyperscalers: resposta inválida do script");
    return;
  }

  if (!parsed.ok) {
    // Cobertura parcial NÃO é sucesso silencioso: o agregado com três das
    // cinco empresas parece uma queda de capex, e é só calendário.
    logger.warn({ erro: parsed.erro, falhas: parsed.falhas,
                  historicoRaso: parsed.historicoRaso,
                  empresasComDado: parsed.empresasComDado },
                "Capex hyperscalers: ciclo sem trimestre completo");
    return;
  }

  if (parsed.historicoRaso?.length || parsed.usandoGuardado?.length) {
    // Não é falha do ciclo (a série guardada segue de pé), mas é o sintoma
    // de fonte secando — barulhento de propósito.
    logger.warn({ historicoRaso: parsed.historicoRaso,
                  usandoGuardado: parsed.usandoGuardado },
                "Capex hyperscalers: coleta incompleta, série mantida pelo histórico guardado");
  }

  logger.info({ trimestre: parsed.trimestre, totalUsdBi: parsed.totalUsdBi,
                direcao: parsed.direcao, variacaoQoQPct: parsed.variacaoQoQPct,
                fontes: parsed.fontes, falhas: parsed.falhas,
                historicoRaso: parsed.historicoRaso },
              "Capex hyperscalers atualizado");
}
