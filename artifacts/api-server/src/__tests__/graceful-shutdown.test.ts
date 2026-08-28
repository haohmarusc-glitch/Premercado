import { describe, it, expect, vi, afterEach } from "vitest";
import { readFileSync } from "fs";
import { join } from "path";
import type { Server } from "node:http";

import {
  DRENO_MAX_MS,
  SINAIS_DE_PARADA,
  armarDesligamentoGracioso,
} from "../lib/graceful-shutdown";

// ─── Deploy não pode cortar requisição em voo ───────────────────────────────
//
// Até 28/08/2026 o Express não tinha handler de SIGTERM nenhum: todos os
// `SIGTERM` do repo eram o Node MATANDO subprocessos Python, nenhum era o
// servidor se despedindo. O log de produção daquele dia, com os três serviços
// lado a lado:
//
//   14:13:19  POST /api/chat/message abre o stream SSE
//   14:13:26  STEP:Turno 2 -- consultando anthropic...
//   14:13:38  app-1 has been recreated       <- deploy
//   14:13:38  caddy: aborting with incomplete response ... unexpected EOF
//   14:13:39  app-1 exited with code 143     <- 128+15, SIGTERM
//
// O chat morreu no meio do turno 2 e o usuário viu erro.

const RAIZ = join(__dirname, "..");
const fonte = (rel: string) => readFileSync(join(RAIZ, rel), "utf-8");

/** Servidor de mentira: registra o que foi chamado e deixa o teste decidir
 *  QUANDO o dreno termina, que é a variável que todos estes casos exercitam. */
function servidorFalso() {
  let terminarDreno: (() => void) | undefined;
  const chamadas = { close: 0, closeIdleConnections: 0 };
  const server = {
    close(cb?: () => void) {
      chamadas.close += 1;
      terminarDreno = cb;
      return server;
    },
    closeIdleConnections() {
      chamadas.closeIdleConnections += 1;
    },
  } as unknown as Server;
  return {
    server,
    chamadas,
    /** simula "todas as conexões em voo terminaram" */
    concluirDreno: () => terminarDreno?.(),
  };
}

afterEach(() => {
  for (const sinal of SINAIS_DE_PARADA) process.removeAllListeners(sinal);
  vi.useRealTimers();
});

describe("o dreno espera as requisições em voo", () => {
  it.each(SINAIS_DE_PARADA)("para de aceitar conexões nova ao receber %s", (sinal) => {
    const { server, chamadas } = servidorFalso();
    const sair = vi.fn();
    armarDesligamentoGracioso(server, { sair });

    process.emit(sinal as NodeJS.Signals);

    expect(chamadas.close).toBe(1);
    // Não saiu ainda: as em voo continuam correndo.
    expect(sair).not.toHaveBeenCalled();
  });

  it("só sai depois que a última requisição termina, e com código 0", () => {
    const { server, concluirDreno } = servidorFalso();
    const sair = vi.fn();
    armarDesligamentoGracioso(server, { sair });

    process.emit("SIGTERM");
    expect(sair).not.toHaveBeenCalled();

    concluirDreno();
    expect(sair).toHaveBeenCalledWith(0);
  });

  it("fecha os keep-alive OCIOSOS, senão todo deploy espera o teto à toa", () => {
    // `server.close()` sozinho também espera os sockets que os navegadores
    // deixam abertos sem requisição nenhuma em cima -- o dreno bateria no teto
    // mesmo com o servidor ocioso. Fechar só os ociosos não interrompe quem
    // está no meio de uma resposta: é a distinção entre drenar e cortar.
    const { server, chamadas } = servidorFalso();
    armarDesligamentoGracioso(server, { sair: vi.fn() });

    process.emit("SIGTERM");

    expect(chamadas.closeIdleConnections).toBe(1);
  });
});

describe("o teto do dreno", () => {
  it("corta o que não terminou quando estoura, saindo com código 1", () => {
    vi.useFakeTimers();
    const { server } = servidorFalso();
    const sair = vi.fn();
    armarDesligamentoGracioso(server, { sair });

    process.emit("SIGTERM");
    vi.advanceTimersByTime(DRENO_MAX_MS - 1);
    expect(sair).not.toHaveBeenCalled();

    vi.advanceTimersByTime(1);
    expect(sair).toHaveBeenCalledWith(1);
  });

  it("não dispara depois de um dreno que terminou a tempo", () => {
    vi.useFakeTimers();
    const { server, concluirDreno } = servidorFalso();
    const sair = vi.fn();
    armarDesligamentoGracioso(server, { sair });

    process.emit("SIGTERM");
    concluirDreno();
    expect(sair).toHaveBeenCalledWith(0);

    // O prazo tem que ter sido cancelado -- senão o processo que já saiu
    // "limpo" ganharia um segundo sair(1) depois.
    vi.advanceTimersByTime(DRENO_MAX_MS * 2);
    expect(sair).toHaveBeenCalledTimes(1);
  });
});

describe("segundo sinal é pressa, não repetição", () => {
  it("mata na hora em vez de reiniciar o dreno", () => {
    const { server, chamadas } = servidorFalso();
    const sair = vi.fn();
    armarDesligamentoGracioso(server, { sair });

    process.emit("SIGTERM");
    expect(sair).not.toHaveBeenCalled();

    process.emit("SIGTERM");
    expect(sair).toHaveBeenCalledWith(1);
    // E não abriu um segundo dreno por cima do primeiro.
    expect(chamadas.close).toBe(1);
  });
});

describe("o orçamento interno é menor que o externo", () => {
  it("DRENO_MAX_MS cabe no stop_grace_period do compose", () => {
    // Mesma regra que bounded_parallel.py já segue do lado Python: quem drena
    // precisa terminar ANTES de quem mata. Um dreno mais longo que a folga do
    // Docker seria morto no meio pelo SIGKILL, entregando exatamente o corte
    // que veio evitar -- só que mais tarde.
    const compose = readFileSync(join(RAIZ, "../../../docker-compose.yml"), "utf-8");
    const m = compose.match(/stop_grace_period:\s*(\d+)s/);
    expect(m, "o serviço app precisa declarar stop_grace_period").not.toBeNull();

    const folgaMs = Number(m![1]) * 1000;
    expect(DRENO_MAX_MS).toBeLessThan(folgaMs);
  });

  it("o padrão do Docker (10s) não bastaria para o teto escolhido", () => {
    // Se algum dia alguém remover a linha do compose, o teste acima falha --
    // este documenta POR QUE ela precisa existir.
    expect(DRENO_MAX_MS).toBeGreaterThan(10_000);
  });
});

describe("o handler está armado onde precisa", () => {
  it("index.ts arma FORA do callback do listen", () => {
    // `app.listen()` devolve o servidor na hora, mas o callback só roda depois
    // de ensureSchema()/bootstrap -- armar lá dentro deixaria o boot inteiro,
    // o trecho mais lento, sem handler.
    const index = fonte("index.ts");
    const posListen = index.indexOf("app.listen(");
    const posArmar = index.indexOf("armarDesligamentoGracioso(server)");
    expect(posArmar).toBeGreaterThan(-1);
    // Depois do `});` que fecha o listen, não aninhado dentro dele.
    expect(index.slice(posListen, posArmar)).toContain("});");
  });
});
