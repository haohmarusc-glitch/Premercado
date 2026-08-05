import { drizzle } from "drizzle-orm/node-postgres";
import pg from "pg";
import * as schema from "./schema";

const { Pool } = pg;

if (!process.env.DATABASE_URL) {
  throw new Error(
    "DATABASE_URL must be set. Did you forget to provision a database?",
  );
}

export const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  // O default do node-postgres e 0: espera PARA SEMPRE por uma conexao livre.
  // Sob pressao isso vira uma request pendurada sem limite em vez de um erro
  // que alguem consegue ver e tratar. Por isso existe um teto -- mas 5s era
  // baixo demais.
  //
  // 05/08, run diaria: o agente dispara 7 save_observation EM PARALELO. O pool
  // esta frio (idleTimeoutMillis de 10s entre rajadas), entao sao 7 conexoes
  // NOVAS ao mesmo tempo, cada uma com handshake TLS, num container onde o
  // interpretador Python leva 10s so para subir. Tres estouraram os 5s e
  // viraram 500; as cinco que passaram levaram ate 4.17s -- encostando no teto.
  //
  // O erro interno engana: pg-pool/index.js:255 faz
  // `client.connection.stream.destroy()` quando o prazo vence, o pg emite
  // "Connection terminated unexpectedly", e so DEPOIS isso e embrulhado como
  // "Connection terminated due to connection timeout". Ou seja, a conexao nao
  // caiu sozinha -- nos a matamos. Quem ler de dentro para fora vai cacar um
  // problema de rede ou do Neon que nao existe.
  //
  // 15s pelo mesmo criterio ja aplicado aos timeouts de subprocesso: um teto
  // abaixo do tempo real de partida nao protege nada, so garante a falha.
  connectionTimeoutMillis: 15_000,
  // Keepalive de TCP: sem ele, um proxy/NAT no caminho pode derrubar a conexao
  // ociosa em silencio, e o pool so descobre ao entregar um socket ja morto
  // para a proxima query -- que e exatamente como um 08P01 (protocol
  // violation) aparece do lado do cliente.
  keepAlive: true,
  // idleTimeoutMillis fica no default (10s) DE PROPOSITO. Aumenta-lo segura a
  // conexao ociosa por mais tempo e AMPLIA a janela em que o servidor pode
  // mata-la pelas costas -- e o lado errado para quem esta tentando reduzir
  // erro de conexao morta.
});

// Sem um listener aqui, o Node trata o 'error' do pool como excecao nao
// capturada e derruba o processo inteiro (visto em producao como crash loop).
//
// A mensagem nomeia o codigo em vez de assumir a causa: a versao anterior
// dizia sempre "conexao ociosa encerrada?", o que e um bom palpite para 57P01
// (shutdown administrativo) e um palpite ERRADO para o 08P01 que apareceu em
// 03/08. Um log que nomeia a causa errada custa mais que um log sem hipotese.
const CAUSA_POR_CODIGO: Record<string, string> = {
  "57P01": "o servidor encerrou a conexao (shutdown/comando administrativo)",
  "08P01": "violacao de protocolo -- socket provavelmente ja estava morto ao ser reusado",
  "08006": "falha de conexao",
  "08003": "conexao inexistente",
};

pool.on("error", (err) => {
  const codigo = (err as NodeJS.ErrnoException & { code?: string }).code;
  const causa = codigo ? CAUSA_POR_CODIGO[codigo] : undefined;
  console.error(
    `[db] erro no pool de conexoes Postgres` +
      (codigo ? ` [${codigo}]` : "") +
      (causa ? `: ${causa}` : ": causa nao mapeada"),
    err,
  );
});

export const db = drizzle(pool, { schema });

export * from "./schema";
