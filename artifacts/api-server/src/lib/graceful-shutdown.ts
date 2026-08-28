/**
 * graceful-shutdown.ts — deixa as requisições em voo terminarem antes de sair.
 *
 * Por que existe: até 28/08/2026 o Express não tinha handler de SIGTERM
 * nenhum. Todos os `SIGTERM` do repo eram o Node MATANDO subprocessos Python
 * (routes/*.ts, alert-checker, runner) -- nenhum era o servidor se despedindo.
 * Então `docker compose up -d` cortava toda requisição em andamento no
 * instante do sinal.
 *
 * Visto em produção (28/08/2026 14:13 UTC), com o log dos três serviços lado
 * a lado:
 *
 *   14:13:19  POST /api/chat/message abre o stream SSE
 *   14:13:21  STEP:Turno 1 -- consultando anthropic...
 *   14:13:26  STEP:Turno 2 -- consultando anthropic...
 *   14:13:38  app-1 has been recreated          <- deploy
 *   14:13:38  caddy: aborting with incomplete response ... unexpected EOF
 *   14:13:39  app-1 exited with code 143        <- 128+15, SIGTERM
 *
 * O chat morreu no meio do turno 2 e o usuário viu erro. Para requisição
 * curta o corte é invisível; para o chat (SSE de dezenas de segundos) e para
 * as rotas que spawnam Python -- no mesmo log, /api/tickers/quotes levou 4,7s
 * e /api/technicals 2,5s -- é uma resposta perdida a cada deploy.
 *
 * ## O orçamento interno é MENOR que o externo, de propósito
 *
 * Mesma regra que `bounded_parallel.py` já segue do lado Python: quem drena
 * precisa terminar ANTES de quem mata. O Docker manda SIGTERM, espera
 * `stop_grace_period` e então manda SIGKILL -- um dreno mais longo que essa
 * folga seria morto no meio, entregando exatamente o corte que veio evitar,
 * só que mais tarde. DRENO_MAX_MS < stop_grace_period do docker-compose.yml,
 * e há teste fixando que os dois não divergem.
 */
import type { Server } from "node:http";

import { logger } from "./logger";

/**
 * Teto do dreno. Cobre o caso comum (turno de chat, rota que spawna Python)
 * sem transformar todo deploy numa espera longa: passado o teto, o que ainda
 * não terminou é cortado do mesmo jeito -- só que por escolha, e com log
 * dizendo quantas conexões pagaram a conta.
 *
 * Menor que o `stop_grace_period` do docker-compose.yml (ver cabeçalho).
 */
export const DRENO_MAX_MS = Number(
  process.env["SHUTDOWN_DRAIN_MS"] ?? 25_000,
);

/** Sinais que significam "encerre" -- SIGTERM do Docker, SIGINT do Ctrl-C. */
export const SINAIS_DE_PARADA = ["SIGTERM", "SIGINT"] as const;

export type Saida = (codigo: number) => void;

/**
 * Arma o desligamento gracioso no servidor HTTP.
 *
 * `sair` é injetado para o teste não derrubar o próprio pytest/vitest --
 * mesmo motivo do `dormir` injetado em earnings_dates.buscar().
 */
export function armarDesligamentoGracioso(
  server: Server,
  { sair = (c: number) => process.exit(c) }: { sair?: Saida } = {},
): void {
  let desligando = false;

  for (const sinal of SINAIS_DE_PARADA) {
    process.on(sinal, () => {
      // Segundo sinal enquanto drena = "não espere, mate agora". É o Ctrl-C
      // duas vezes de quem está com pressa, e o comportamento precisa ser o
      // que essa pessoa espera: sair na hora.
      if (desligando) {
        logger.warn({ sinal }, "Segundo sinal durante o dreno — encerrando já");
        sair(1);
        return;
      }
      desligando = true;
      logger.info(
        { sinal, drenoMaxMs: DRENO_MAX_MS },
        "Sinal de parada recebido — parando de aceitar conexões e drenando as em voo",
      );

      // Para de aceitar conexão NOVA; o callback só dispara quando as que já
      // existem terminarem.
      server.close(() => {
        clearTimeout(prazo);
        logger.info({ sinal }, "Dreno concluído — todas as requisições em voo terminaram");
        sair(0);
      });

      // Sem isto o dreno esbarra no teto em TODO deploy, mesmo com o servidor
      // ocioso: `server.close()` espera também os sockets keep-alive que os
      // navegadores deixam abertos sem requisição nenhuma em cima. Fechar só
      // os OCIOSOS não interrompe quem está no meio de uma resposta -- é
      // exatamente a distinção que separa "drenar" de "cortar".
      server.closeIdleConnections();

      const prazo = setTimeout(() => {
        logger.warn(
          { sinal, drenoMaxMs: DRENO_MAX_MS },
          "Dreno estourou o teto — cortando o que ainda não terminou",
        );
        sair(1);
      }, DRENO_MAX_MS);
      // O timer do prazo não pode ser o que segura o processo vivo: sem
      // unref(), um servidor já drenado ficaria esperando o relógio à toa.
      prazo.unref();
    });
  }
}
