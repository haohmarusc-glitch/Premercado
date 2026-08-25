/**
 * Background job que atualiza o fôlego de caixa dos tickers da carteira (ver
 * agent/folego_de_caixa.py) — quantos trimestres cada empresa aguenta na
 * queima média do último ano.
 *
 * Por que SEMANAL, e não diário: balanço é dado TRIMESTRAL. Entre uma
 * divulgação e a seguinte passam ~13 semanas e o número não muda no meio —
 * mesmo raciocínio do capex dos hiperescaladores. Semanal ainda pega qualquer
 * divulgação dentro de poucos dias.
 *
 * O script grava overlay em disco (FOLEGO_OVERLAY_PATH, default
 * /var/cache/premercado/folego_de_caixa.json), lido por agent.py no momento
 * do Veredito. O diretório inteiro está em volume nomeado desde 25/08/2026,
 * então o overlay sobrevive a `up --build` — e é dele que o coletor tira a
 * profundidade sem gastar cota.
 */
import { runScript } from "../routes/scenarios";
import { state as agentState } from "./runner";
import { logger } from "./logger";

// Um balanço + um fluxo de caixa por ticker, e a carteira tem dezenas.
// Timeout generoso pelo mesmo motivo dos outros checkers: falhar aqui custa
// uma SEMANA sem refresh, e ninguém está esperando na tela.
const TIMEOUT_MS = 300_000;

interface FolegoRefreshResult {
  ok: boolean;
  erro?: string;
  tickersComDado?: number;
  falhas?: string[];
  serieRasa?: string[];
  usandoGuardado?: string[];
  queimandoCaixa?: string[];
  fontes?: string[];
  overlay?: string | null;
}

export async function refreshFolegoDeCaixa(): Promise<void> {
  // Mesmo motivo dos outros checkers: não competir por CPU/rede com o
  // agente diário.
  if (agentState.running) {
    logger.info("Fôlego de caixa: pulando ciclo -- agente diário em execução");
    return;
  }

  let out: string;
  try {
    out = await runScript("folego_de_caixa.py", ["--json"], TIMEOUT_MS);
  } catch (err) {
    logger.error({ err }, "Fôlego de caixa: falha ao rodar folego_de_caixa.py");
    return;
  }

  let parsed: FolegoRefreshResult;
  try {
    parsed = JSON.parse(out);
  } catch (err) {
    logger.error({ err, out: out.slice(0, 500) },
                 "Fôlego de caixa: resposta inválida do script");
    return;
  }

  if (!parsed.ok) {
    logger.warn({ erro: parsed.erro, falhas: parsed.falhas,
                  tickersComDado: parsed.tickersComDado },
                "Fôlego de caixa: ciclo sem dado");
    return;
  }

  if (parsed.serieRasa?.length || parsed.usandoGuardado?.length) {
    // Não é falha do ciclo (a série guardada segue de pé), mas é o sintoma de
    // fonte secando — barulhento de propósito, mesma regra do capex.
    logger.warn({ serieRasa: parsed.serieRasa, usandoGuardado: parsed.usandoGuardado },
                "Fôlego de caixa: coleta incompleta, série mantida pelo balanço guardado");
  }

  logger.info({ tickersComDado: parsed.tickersComDado,
                queimandoCaixa: parsed.queimandoCaixa,
                falhas: parsed.falhas, fontes: parsed.fontes },
              "Fôlego de caixa atualizado");
}
