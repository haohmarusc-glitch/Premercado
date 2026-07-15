import { drizzle } from "drizzle-orm/node-postgres";
import pg from "pg";
import * as schema from "./schema";

const { Pool } = pg;

if (!process.env.DATABASE_URL) {
  throw new Error(
    "DATABASE_URL must be set. Did you forget to provision a database?",
  );
}

export const pool = new Pool({ connectionString: process.env.DATABASE_URL });

// O pool emite 'error' quando o provedor do banco encerra uma conexao ociosa
// (ex.: "terminating connection due to administrator command", 57P01) --
// sem um listener aqui, o Node trata isso como excecao nao capturada e
// derruba o processo inteiro (visto em producao como crash loop repetido).
pool.on("error", (err) => {
  console.error("[db] erro no pool de conexoes Postgres (conexao ociosa encerrada?):", err);
});

export const db = drizzle(pool, { schema });

export * from "./schema";
