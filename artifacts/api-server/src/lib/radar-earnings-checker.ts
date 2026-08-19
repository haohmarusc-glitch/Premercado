/**
 * Background job que atualiza o calendário de earnings do Radar IA (ver
 * agent/atualizar_earnings.py). Sem ele, o dicionário EARNINGS fica preso ao
 * que alguém digitou à mão no snapshot de 14/08/2026.
 *
 * Por que DIÁRIO e não semanal como as correlações: são os dois extremos da
 * mesma escala. Correlação de janela de 6 meses se move devagar e o que
 * importa é mudança de regime, que leva semanas. Data de earnings vira
 * passado em DIAS, empresa remarca, e a confirmação oficial costuma sair na
 * semana anterior -- exatamente a janela em que o dado importa. O custo é 1
 * chamada de API por dia (o script pede o calendário inteiro de uma vez e
 * filtra local), contra a cota compartilhada com o feed de notícias.
 *
 * O script grava um overlay em disco (RADAR_EARNINGS_OVERLAY, default
 * /var/cache/premercado/radar_earnings.json) que radar_ia_2026.py carrega no
 * IMPORT. Mesma consequência do overlay de correlações: quem já está em
 * memória segue com as datas velhas até o próximo spawn -- o que se resolve
 * sozinho porque cada rota/checker spawna Python novo.
 */
import { runScript } from "../routes/scenarios";
import { state as agentState } from "./runner";
import { logger } from "./logger";

// Uma única requisição HTTP, mas o CSV do horizonte de 6 meses é grande e a
// Alpha Vantage não é rápida. Folga sobre o default de 60s do runScript pelo
// mesmo motivo do checker de correlações: timeout aqui custa um DIA sem
// refresh, e ninguém está esperando na frente da tela.
const TIMEOUT_MS = 120_000;

interface EarningsRefreshResult {
  ok: boolean;
  erro?: string;
  tickers?: number;
  confirmados?: number;
  mudaram?: Array<{ ticker: string; de: string | null; para: string }>;
  ausentes?: string[];
  overlay?: string;
}

export async function refreshRadarEarnings(): Promise<void> {
  // Mesmo motivo dos outros checkers: não competir por CPU/rede com o
  // agente diário.
  if (agentState.running) {
    logger.info("Radar earnings: pulando ciclo -- agente diário em execução");
    return;
  }

  let out: string;
  try {
    out = await runScript("atualizar_earnings.py", ["--json"], TIMEOUT_MS);
  } catch (err) {
    logger.error({ err }, "Radar earnings: falha ao rodar atualizar_earnings.py");
    return;
  }

  let parsed: EarningsRefreshResult;
  try {
    parsed = JSON.parse(out);
  } catch (err) {
    logger.error({ err, out: out.slice(0, 500) }, "Radar earnings: resposta inválida do script");
    return;
  }

  if (!parsed.ok) {
    // Falha aqui não é crítica: o overlay anterior (ou o calendário
    // embutido) segue valendo -- ninguém fica sem calendário, só sem o mais
    // novo. Mas vai a warn, não a info: cota estourada e chave inválida
    // entram por aqui, e são coisas que só se resolvem se alguém vir.
    logger.warn({ erro: parsed.erro }, "Radar earnings: refresh não concluído");
    return;
  }

  // Data que MUDOU é o evento que altera comportamento: o radar ordena por
  // proximidade, o alerta de contágio dispara pela data, e uma posição
  // montada para atravessar earnings depende de saber o dia certo. Sobe a
  // warn com os tickers nomeados em vez de sumir num info.
  if (parsed.mudaram?.length) {
    logger.warn(
      { tickers: parsed.mudaram },
      "Radar earnings: datas mudaram desde o refresh anterior",
    );
  }

  logger.info(
    {
      tickers: parsed.tickers,
      confirmados: parsed.confirmados,
      mudaram: parsed.mudaram?.length ?? 0,
      // Fora do horizonte da consulta -- mantêm a data embutida. Vale no log
      // porque é o conjunto que continua envelhecendo à mão.
      ausentes: parsed.ausentes,
    },
    "Radar earnings atualizados",
  );
}
