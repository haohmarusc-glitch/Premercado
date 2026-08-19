/**
 * POST /checkers/run -- executa o ciclo de checkers DENTRO de um request HTTP.
 *
 * Por que existe: no Autoscale, CPU só é garantida durante um request. Os
 * timers internos (setInterval) rodavam com o container estrangulado -- boot
 * de Python de 8-11s e imports de até 156s, estourando qualquer timeout
 * (medido em 04-05/08). Dentro de um request, o mesmo boot leva ~0,05s.
 *
 * Quem chama: um Scheduled Deployment a cada 5 min (scripts/trigger-checkers.sh),
 * autenticado com Bearer OPERATOR_API_KEY. A autorização aqui é ESTRITA:
 * requireAuth (routes/index.ts) aceita cookie de sessão OU a chave, mas esta
 * rota dispara trabalho caro sobre TODOS os usuários (subprocessos Python,
 * e-mails) -- um usuário logado comum não pode acioná-la, só o operador.
 *
 * Coordenação entre instâncias: o guard de sobreposição e a cadência das
 * etapas vivem numa linha única do Postgres (checker_lease), não em memória --
 * o Autoscale pode servir cada chamada numa instância diferente (inclusive
 * fantasmas de versões antigas durante trocas), e um estado in-process não as
 * enxergaria. A claim é um UPDATE atômico com prazo de expiração, então um
 * processo que morrer no meio do ciclo não deixa a trava presa pra sempre.
 *
 * Cadência: nem toda etapa roda a cada chamada -- cada uma tem seu intervalo
 * mínimo (os mesmos dos timers antigos: 5min/15min/1h/24h), persistido no
 * jsonb da mesma linha. Toda deduplicação de disparo (cooldowns, alertKeys)
 * continua nas tabelas próprias, então uma etapa rodar "cedo demais" é
 * inofensivo.
 */
import { Router, type IRouter, type Request, type Response, type NextFunction } from "express";
import { randomUUID } from "node:crypto";
import { sql } from "drizzle-orm";
import { db } from "@workspace/db";
import { checkAlerts, rodarCheckersDeMercado } from "../lib/alert-checker";
import { checkPortfolioAlerts } from "../lib/portfolio-alerts";
import { checkScenarioAlerts } from "../lib/scenario-alert-checker";
import { refreshScenarioParams } from "../lib/scenario-params-checker";
import { refreshEntryExitStudies } from "../lib/entry-exit-study-checker";
import { refreshRadarCorrelacoes } from "../lib/radar-correlacoes-checker";
import { refreshRadarEarnings } from "../lib/radar-earnings-checker";
import { state as agentState } from "../lib/runner";
import { logger } from "../lib/logger";

const MIN = 60_000;

// Teto do request inteiro: nenhuma etapa NOVA começa depois disto. As etapas
// têm timeouts internos próprios (120s/180s), então o pior caso real fica
// abaixo do --max-time 280s do script e de limites de proxy. Um ciclo
// saudável dentro de request leva ~50s (medido); este teto só protege o
// caso degenerado de várias etapas lentas se acumularem numa chamada.
const ORCAMENTO_DO_CICLO_MS = 240_000;

// A trava expira sozinha: cobre o orçamento acima + a etapa mais lenta que
// pode ter começado no limite (180s) + folga. Se o processo morrer no meio,
// a próxima chamada depois da expiração assume normalmente.
//
// Em minutos e interpolado no SQL, NÃO escrito à mão nos dois UPDATEs: a
// versão anterior tinha a constante só numa mensagem de log e `interval '8
// minutes'` literal na query, livres para divergir sem ninguém notar.
const VALIDADE_DA_TRAVA_MIN = 8;

// Margem subtraída do intervalo de cadência: o agendador externo não é
// pontual ao segundo, e sem folga uma chamada às 14:59:58 pularia a etapa de
// 15min que "venceria" às 15:00:00 -- ela só rodaria 5min depois, toda vez.
const MARGEM_MS = 90_000;

interface Etapa {
  nome: string;
  intervaloMs: number;
  run: () => Promise<void>;
}

const ETAPAS: Etapa[] = [
  { nome: "alerts", intervaloMs: 0, run: checkAlerts },
  { nome: "market", intervaloMs: 0, run: rodarCheckersDeMercado },
  { nome: "portfolio", intervaloMs: 15 * MIN, run: checkPortfolioAlerts },
  { nome: "scenario_alerts", intervaloMs: 60 * MIN, run: checkScenarioAlerts },
  { nome: "scenario_params", intervaloMs: 24 * 60 * MIN, run: refreshScenarioParams },
  { nome: "entry_exit_study", intervaloMs: 24 * 60 * MIN, run: refreshEntryExitStudies },
  // Semanal: correlação de janela de 6 meses se move devagar -- o que
  // importa capturar é mudança de regime (par cruzando 0.70), não a
  // terceira casa decimal. Ver lib/radar-correlacoes-checker.ts.
  { nome: "radar_correlacoes", intervaloMs: 7 * 24 * 60 * MIN, run: refreshRadarCorrelacoes },
  // Diário, ao contrário das correlações: data de earnings vira passado em
  // dias e a confirmação oficial sai na semana anterior -- justo a janela
  // em que o dado importa. Custa 1 chamada de API por dia.
  { nome: "radar_earnings", intervaloMs: 24 * 60 * MIN, run: refreshRadarEarnings },
];

type Cadencia = Record<string, number>;

interface Posse {
  token: string;
  cadencia: Cadencia;
}

/**
 * Tenta assumir a trava. UPDATE atômico: só UMA instância consegue por vez,
 * qualquer que seja o processo. Devolve a cadência persistida e um token de
 * posse, ou null se outra instância está com o ciclo em andamento.
 *
 * O token existe porque a trava EXPIRA. Sem ele, o release era incondicional
 * (`WHERE id = 1`), e este cenário quebrava a exclusão mútua que a trava
 * inteira existe para garantir:
 *
 *   1. A passa dos 8 minutos e a trava vence (o orçamento de 240s só barra o
 *      INÍCIO de uma etapa nova -- uma que comece em 239s e leve 180s termina
 *      em ~420s, contra 480s de validade: sobra ~1 minuto, e num container sem
 *      CPU é esse minuto que some).
 *   2. B assume e começa o ciclo dele.
 *   3. A termina e solta -- liberando a trava de B e sobrescrevendo a cadência
 *      com a cópia velha que A carregava.
 *
 * Com o token, o release de A não casa e vira no-op: a trava continua com B, e
 * a cadência de B continua valendo.
 */
async function assumirTrava(): Promise<Posse | null> {
  const token = randomUUID();
  const result = await db.execute(sql`
    UPDATE checker_lease
    SET locked_until = now() + make_interval(mins => ${VALIDADE_DA_TRAVA_MIN}),
        owner_token = ${token}
    WHERE id = 1 AND locked_until < now()
    RETURNING cadence
  `);
  const row = result.rows[0] as { cadence: Cadencia } | undefined;
  if (!row) return null;
  return { token, cadencia: row.cadence ?? {} };
}

/**
 * Solta a trava SÓ se ela ainda for nossa. Devolve false quando a posse já
 * passou para outra instância -- caso em que não há nada a fazer além de
 * registrar, porque quem manda agora é a outra.
 */
async function soltarTrava(token: string, cadencia: Cadencia): Promise<boolean> {
  const result = await db.execute(sql`
    UPDATE checker_lease
    SET locked_until = now(),
        cadence = ${JSON.stringify(cadencia)}::jsonb,
        last_cycle_at = now(),
        owner_token = NULL
    WHERE id = 1 AND owner_token = ${token}
    RETURNING id
  `);
  return result.rows.length > 0;
}

// Só o operador. requireAuth (mais acima na cadeia) também aceita cookie de
// sessão, o que serve pras rotas normais -- mas disparar o ciclo inteiro não
// é uma ação de usuário.
function requireOperatorKey(req: Request, res: Response, next: NextFunction): void {
  const key = process.env["OPERATOR_API_KEY"];
  if (!key || req.headers.authorization !== `Bearer ${key}`) {
    res.status(403).json({ error: "Esta rota exige a chave de operador" });
    return;
  }
  next();
}

const router: IRouter = Router();

router.post("/checkers/run", requireOperatorKey, async (_req, res) => {
  // Mesmo motivo dos guards internos dos checkers: o agente diário sozinho já
  // satura CPU/rede do container; rodar por cima só faria os dois falharem.
  if (agentState.running) {
    res.status(409).json({ error: "Agente diário em execução -- ciclo pulado" });
    return;
  }

  const posse = await assumirTrava();
  if (posse === null) {
    res.status(409).json({ error: "Ciclo de checkers já em andamento em outra instância" });
    return;
  }
  const { token, cadencia } = posse;

  const inicio = Date.now();
  const executados: { nome: string; ok: boolean; duracaoMs: number; erro?: string }[] = [];
  const pulados: string[] = [];
  const adiados: string[] = [];
  try {
    for (const etapa of ETAPAS) {
      if (Date.now() - inicio > ORCAMENTO_DO_CICLO_MS) {
        // Sem punição: a cadência não avança, então a etapa adiada é a
        // primeira candidata da próxima chamada (5 min depois).
        adiados.push(etapa.nome);
        continue;
      }
      const ultima = cadencia[etapa.nome] ?? 0;
      if (etapa.intervaloMs > 0 && Date.now() - ultima < etapa.intervaloMs - MARGEM_MS) {
        pulados.push(etapa.nome);
        continue;
      }
      const t0 = Date.now();
      try {
        await etapa.run();
        cadencia[etapa.nome] = Date.now();
        executados.push({ nome: etapa.nome, ok: true, duracaoMs: Date.now() - t0 });
      } catch (err) {
        // Avança a cadência MESMO em falha: reexecutar a cada 5min uma etapa
        // de 24h que está quebrada não conserta nada, só gasta ciclo.
        cadencia[etapa.nome] = Date.now();
        executados.push({
          nome: etapa.nome,
          ok: false,
          duracaoMs: Date.now() - t0,
          erro: err instanceof Error ? err.message : String(err),
        });
        logger.error({ err, etapa: etapa.nome }, "Checkers via request: etapa falhou, as demais seguem");
      }
    }
  } finally {
    try {
      const aindaNossa = await soltarTrava(token, cadencia);
      if (!aindaNossa) {
        // O ciclo passou da validade e outra instância assumiu no meio. A
        // cadência daqui foi descartada de propósito -- a de quem está com a
        // trava agora é a mais nova. Vale WARN e não ERROR: nada quebrou, mas
        // se isto virar rotina o orçamento do ciclo está grande demais para a
        // validade da trava.
        logger.warn(
          { validadeMin: VALIDADE_DA_TRAVA_MIN, duracaoMs: Date.now() - inicio },
          "Checkers via request: a trava expirou durante o ciclo e outra instância assumiu",
        );
      }
    } catch (err) {
      // Não fatal: a trava expira sozinha em VALIDADE_DA_TRAVA_MIN.
      logger.error({ err, validadeMin: VALIDADE_DA_TRAVA_MIN }, "Checkers via request: falha ao soltar a trava (expira sozinha)");
    }
  }

  const duracaoMs = Date.now() - inicio;
  logger.info({ executados, pulados, adiados, duracaoMs }, "Checkers via request: ciclo concluído");
  res.json({ ok: executados.every((e) => e.ok), duracaoMs, executados, pulados, adiados });
});

export default router;
