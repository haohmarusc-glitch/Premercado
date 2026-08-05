import { sql } from "drizzle-orm";
import { db } from "@workspace/db";
import { logger } from "./logger";
import { shouldRunBackgroundCheckers } from "./background-checkers";

/**
 * Vigia dos checkers disparados por request.
 *
 * Existe por causa de um modo de falha que não produz erro nenhum: com os
 * timers desligados (o padrão desde que os ciclos passaram a rodar via
 * `POST /api/checkers/run`), quem executa os checkers é um gatilho EXTERNO --
 * um Scheduled Deployment do Replit. Se esse deployment não for criado, for
 * pausado, ficar sem o secret ou apontar para a URL errada, ninguém chama o
 * endpoint e **nenhum checker roda**. Sem spike, sem bounce, sem squeeze, sem
 * alerta de carteira.
 *
 * E o log diria apenas "Timers de checkers desligados", que parece intencional.
 * A funcionalidade inteira some em silêncio -- exatamente o tipo de falha que
 * só se descobre quando um alerta que devia ter chegado não chegou.
 *
 * O vigia é uma consulta só, sem Python e sem rede: mesmo num container
 * estrangulado ele roda. Atrasar não é problema; o que importa é que a
 * ausência vire uma linha de ERROR em vez de silêncio.
 */

// 5 min é a cadência do gatilho. O alarme espera 4 ciclos perdidos antes de
// falar: um 409 ocasional (agente diário rodando) ou um atraso do agendador
// são normais e não merecem alarme.
const INTERVALO_DA_CHECAGEM_MS = 5 * 60_000;
const LIMITE_SEM_CICLO_MS = 20 * 60_000;

let handle: ReturnType<typeof setInterval> | null = null;

export async function verificarUltimoCiclo(
  agora: number = Date.now(),
): Promise<{ alarmado: boolean; idadeMs: number | null }> {
  const result = await db.execute(sql`SELECT last_cycle_at FROM checker_lease WHERE id = 1`);
  const row = result.rows[0] as { last_cycle_at: string | Date | null } | undefined;
  const bruto = row?.last_cycle_at ?? null;

  // Nunca rodou. Não é o mesmo que "parou de rodar": logo depois de um deploy
  // é o estado esperado, e o próprio limite de 20 min cobre a diferença --
  // até lá o silêncio é normal, depois dele não é mais.
  if (bruto === null) {
    logger.warn(
      { limiteMs: LIMITE_SEM_CICLO_MS },
      "Vigia dos checkers: nenhum ciclo registrado ainda -- o gatilho externo já foi criado?",
    );
    return { alarmado: true, idadeMs: null };
  }

  const idadeMs = agora - new Date(bruto).getTime();
  if (idadeMs > LIMITE_SEM_CICLO_MS) {
    logger.error(
      { idadeMs, limiteMs: LIMITE_SEM_CICLO_MS, ultimoCiclo: new Date(bruto).toISOString() },
      "Vigia dos checkers: nenhum ciclo há tempo demais -- o gatilho externo parou (alertas NÃO estão rodando)",
    );
    return { alarmado: true, idadeMs };
  }

  return { alarmado: false, idadeMs };
}

export function iniciarVigiaDosCheckers(): void {
  // Com os timers ligados quem roda os ciclos é este processo, e a coluna
  // last_cycle_at nunca é preenchida -- vigiar aqui só produziria alarme falso.
  if (shouldRunBackgroundCheckers()) return;
  if (handle) return;

  handle = setInterval(() => {
    verificarUltimoCiclo().catch((err) => {
      logger.error({ err }, "Vigia dos checkers: falha ao consultar o último ciclo");
    });
  }, INTERVALO_DA_CHECAGEM_MS);
  handle.unref?.();

  logger.info(
    { intervaloMs: INTERVALO_DA_CHECAGEM_MS, limiteMs: LIMITE_SEM_CICLO_MS },
    "Vigia dos checkers iniciado (timers desligados, ciclos vêm de POST /api/checkers/run)",
  );
}
