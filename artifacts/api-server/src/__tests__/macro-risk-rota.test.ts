/**
 * A borda do banco não pode desfazer a distinção que o módulo protege.
 *
 * macro_risk.py devolve `aggregate_score: null` quando a cobertura fica abaixo
 * do mínimo — é assim que "não consegui medir" se distingue de "medi e está
 * calmo". Um `?? 0` na hora de gravar desfaria isso na última camada, onde
 * ninguém procuraria: a série histórica passaria a mostrar dias cegos como
 * dias de risco zero, e o gráfico ficaria bonito e errado.
 *
 * Rodar: pnpm --filter @workspace/api-server run test -- --run macro-risk-rota
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "fs";
import { join } from "path";

const ROTA = readFileSync(join(__dirname, "..", "routes", "macro-risk.ts"), "utf-8");
const SCHEMA = readFileSync(
  join(__dirname, "..", "..", "..", "..", "lib", "db", "src", "schema", "premarket.ts"), "utf-8");

describe("persistência do retrato macro", () => {
  it("score nulo é gravado como null, não coalescido para zero", () => {
    expect(ROTA).toContain("aggregateScore: r.aggregate_score ?? null");
    expect(ROTA).not.toContain("aggregate_score ?? 0");
  });

  it("a coluna do score é nullable no schema", () => {
    // `.notNull()` aqui forçaria o coalesce lá — o tipo do banco é a última
    // linha de defesa da distinção.
    const bloco = SCHEMA.slice(SCHEMA.indexOf("macroRiskSnapshotsTable"));
    const linha = bloco.split("\n").find((l) => l.includes("aggregate_score")) ?? "";
    expect(linha).toContain("integer(");
    expect(linha).not.toContain("notNull");
  });

  it("grava por upsert: reavaliar no mesmo dia sobrescreve", () => {
    // Duas linhas com a mesma data fariam o gráfico contar o dia duas vezes.
    expect(ROTA).toContain("onConflictDoUpdate");
    expect(ROTA).toContain("target: macroRiskSnapshotsTable.snapshotDate");
  });

  it("falha de escrita não derruba a resposta, mas vai para o log", () => {
    // O usuário pediu o retrato; devolvê-lo sem persistir é melhor que erro.
    // Silêncio, não: viraria série com buraco que ninguém explica depois.
    const bloco = ROTA.slice(ROTA.indexOf("await persistir"));
    expect(bloco).toContain("logger.error");
  });

  it("guarda o payload bruto do dia", () => {
    // Os thresholds vão mudar. Revisar um threshold sem os dados brutos do dia
    // que o motivou é adivinhação.
    expect(ROTA).toContain("raw: r as Record<string, unknown>");
  });
});

describe("a tabela é declarada no schema, não só criada em runtime", () => {
  it("existe em premarket.ts", () => {
    // O `db push` compara o banco com esse arquivo: tabela criada só por SQL
    // cru em runtime vira candidata a DROP. Aconteceu em 05/08 com
    // checker_lease — ver o comentário lá.
    expect(SCHEMA).toContain('pgTable("macro_risk_snapshots"');
  });

  it("e também no ensure-schema, para o boot não depender do push", () => {
    const ensure = readFileSync(join(__dirname, "..", "lib", "ensure-schema.ts"), "utf-8");
    expect(ensure).toContain("CREATE TABLE IF NOT EXISTS macro_risk_snapshots");
  });
});

describe("erro de parse nomeia o stdout", () => {
  it("não devolve só 'Parse error'", () => {
    // Sem o stdout no erro, achar a causa exige rodar o script à mão dentro do
    // container — foi o que custou horas no NaN de 18/08.
    expect(ROTA).toContain("stdout:");
  });
});
