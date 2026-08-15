/**
 * Background job que recalcula as correlações do Radar IA uma vez por
 * semana (ver agent/atualizar_correlacoes.py). Sem ele, a matriz fica
 * presa ao snapshot de 14/08/2026 embutido em radar_ia_2026.py -- que é
 * exatamente a limitação que o guia do pacote marcava como "o ponto fraco".
 *
 * Por que SEMANAL e não diário: correlação de janela de 6 meses se move
 * devagar -- um dia a mais ou a menos muda a terceira casa decimal. O que
 * importa capturar é mudança de REGIME (um par cruzando 0.70 e virando "o
 * mesmo trade"), e isso leva semanas pra acontecer. Rodar diariamente
 * gastaria ~37 downloads de histórico por dia pra reescrever praticamente
 * os mesmos números.
 *
 * O script grava um overlay em disco (RADAR_CORR_OVERLAY, default
 * /var/cache/premercado/radar_correlacoes.json) que radar_ia_2026.py
 * carrega no IMPORT. Ou seja: quem já está em memória (este processo Node e
 * os subprocessos Python vivos) continua com os números velhos até o
 * próximo spawn. Na prática isso se resolve sozinho, porque cada checker/
 * rota spawna Python novo -- mas é a razão de o refresh manual no VPS vir
 * acompanhado de `docker compose restart app`.
 */
import { runScript } from "../routes/scenarios";
import { state as agentState } from "./runner";
import { logger } from "./logger";

// Acima do default de 60s do runScript: o script baixa ~37 históricos numa
// chamada batelada e ninguém está esperando na frente da tela. Um timeout
// aqui custa uma SEMANA sem refresh, então vale dar folga.
const TIMEOUT_MS = 180_000;

interface CorrelacoesRefreshResult {
  ok: boolean;
  erro?: string;
  pares?: number;
  tickers?: number;
  sem_historico?: string[];
  novos?: number;
  mudancas_relevantes?: number;
  cruzaram_070?: Array<{ par: [string, string]; de: number; para: number }>;
  overlay?: string;
}

export async function refreshRadarCorrelacoes(): Promise<void> {
  // Mesmo motivo dos outros checkers: o script baixa histórico de ~37
  // tickers via yfinance, não vale competir por CPU/rede com o agente.
  if (agentState.running) {
    logger.info("Radar correlações: pulando ciclo -- agente diário em execução");
    return;
  }

  let out: string;
  try {
    out = await runScript("atualizar_correlacoes.py", ["--json"], TIMEOUT_MS);
  } catch (err) {
    logger.error({ err }, "Radar correlações: falha ao rodar atualizar_correlacoes.py");
    return;
  }

  let parsed: CorrelacoesRefreshResult;
  try {
    parsed = JSON.parse(out);
  } catch (err) {
    logger.error({ err, out: out.slice(0, 500) }, "Radar correlações: resposta inválida do script");
    return;
  }

  if (!parsed.ok) {
    // Falha aqui não é crítica: o overlay anterior (ou o snapshot embutido)
    // segue valendo -- ninguém fica sem correlação, só sem a mais nova.
    logger.warn({ erro: parsed.erro }, "Radar correlações: refresh não concluído");
    return;
  }

  // Cruzar 0.70 é o único evento que muda COMPORTAMENTO (dedup de sinal,
  // contágio de earnings, regra de concentração do veredito) -- por isso
  // sobe pra warn, com os pares nomeados, em vez de sumir num info.
  if (parsed.cruzaram_070?.length) {
    logger.warn(
      { pares: parsed.cruzaram_070 },
      "Radar correlações: pares cruzaram o limiar de 0.70 -- dedup/contágio/concentração mudam de leitura",
    );
  }

  logger.info(
    {
      pares: parsed.pares,
      tickers: parsed.tickers,
      novos: parsed.novos,
      mudancasRelevantes: parsed.mudancas_relevantes,
      semHistorico: parsed.sem_historico,
    },
    "Radar correlações atualizadas",
  );
}
