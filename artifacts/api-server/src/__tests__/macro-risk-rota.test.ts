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
const LIB = readFileSync(join(__dirname, "..", "lib", "macro-risk.ts"), "utf-8");
const SCHED = readFileSync(join(__dirname, "..", "lib", "scheduler.ts"), "utf-8");
const SCHEMA = readFileSync(
  join(__dirname, "..", "..", "..", "..", "lib", "db", "src", "schema", "premarket.ts"), "utf-8");

describe("persistência do retrato macro", () => {
  it("score nulo é gravado como null, não coalescido para zero", () => {
    expect(LIB).toContain("aggregateScore: r.aggregate_score ?? null");
    expect(LIB).not.toContain("aggregate_score ?? 0");
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
    expect(LIB).toContain("onConflictDoUpdate");
    expect(LIB).toContain("target: macroRiskSnapshotsTable.snapshotDate");
  });

  it("falha de escrita não derruba a resposta, mas vai para o log", () => {
    // O usuário pediu o retrato; devolvê-lo sem persistir é melhor que erro.
    // Silêncio, não: viraria série com buraco que ninguém explica depois.
    const bloco = ROTA.slice(ROTA.indexOf("await persistirMacroRisk"));
    expect(bloco).toContain("logger.error");
  });

  it("guarda o payload bruto do dia", () => {
    // Os thresholds vão mudar. Revisar um threshold sem os dados brutos do dia
    // que o motivou é adivinhação.
    expect(LIB).toContain("raw: r as Record<string, unknown>");
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
    expect(LIB).toContain("stdout:");
  });
});


// ── o retrato diário ────────────────────────────────────────────────────────
//
// Sem agendamento a série só cresce quando alguém abre a tela — e o valor
// inteiro da persistência está em ter TODO pregão, inclusive os que ninguém
// olhou. Um buraco no histórico só aparece meses depois, quando se tenta
// comparar um dia ruim com o padrão.

describe("agendamento diário", () => {
  it("existe, em dia útil e antes do pré-mercado", () => {
    // 07:50 BRT = 06:50 ET: a Ásia já fechou (é isso que dá as 6-8h de
    // dianteira ao sinal de contágio) e o FRED já publicou o dia anterior.
    expect(SCHED).toContain('const MACRO_RISK_CRON = "50 7 * * 1-5"');
  });

  it("é ligado nos DOIS caminhos de boot", () => {
    // startScheduler retorna cedo quando acha settings no banco. Ligar só no
    // fallback faria o agendamento existir apenas em instalação nova — e
    // ninguém notaria, porque a tela continua funcionando pelo botão.
    const chamadas = SCHED.split("scheduleMacroRiskTask()").length - 1;
    expect(chamadas).toBeGreaterThanOrEqual(3);  // definição + 2 call sites
  });

  it("não passa pelo runAgent", () => {
    // runAgent serializa por state.running para não rodar duas análises de LLM
    // ao mesmo tempo. A coleta é só rede: passar por ali faria o retrato ser
    // PULADO nos dias em que o diário atrasa, que são os dias movimentados.
    const bloco = SCHED.slice(SCHED.indexOf("MACRO_RISK_CRON"), SCHED.indexOf("Unified settings"));
    expect(bloco).not.toContain("runAgent");
    expect(bloco).toContain("coletarEPersistir");
  });

  it("a coleta agendada nunca levanta", () => {
    // Exceção não tratada dentro de um task do node-cron derruba o agendamento
    // em silêncio até o próximo boot: o sintoma seria a série parando de
    // crescer sem nenhum erro aparecer.
    const fn = LIB.slice(LIB.indexOf("export async function coletarEPersistir"));
    expect(fn).toContain("try {");
    expect(fn).toContain("catch (err)");
    expect(fn).toContain("return null");
  });

  it("rota e cron compartilham a mesma sequência", () => {
    // Duas cópias divergentes é exatamente como a ordem de provedores passou a
    // divergir entre provider.py e agent-budget.ts.
    expect(ROTA).toContain('from "../lib/macro-risk"');
    expect(LIB).toContain("export async function coletarEPersistir");
  });
});
