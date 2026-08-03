/**
 * Checklist executada antes de mandar o relatório por e-mail.
 *
 * Cada caso aqui é um defeito que chegou de verdade à caixa de entrada:
 * KOSPI +17,91% num pregão, "SKHY desceu -3,54% hoje" escrito num sábado,
 * HCC com 🟡 no cabeçalho e 🔴 três linhas abaixo, três e-mails do mesmo dia
 * em 31/07 (um deles só um fragmento com GOOGL/TSLA).
 *
 * O banco é mockado: as duas checagens que o consultam (envio duplicado e
 * preços congelados) precisam de estado controlado, e o resto da checklist é
 * texto puro.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

const linhasContagem: { n: number }[] = [{ n: 1 }];
const linhasAnterior: { content: string }[] = [];

// A contagem de duplicados faz .where() e aguarda ali; a busca do relatório
// anterior segue com .orderBy().limit(). O objeto devolvido por where()
// precisa ser as duas coisas: thenable e encadeável.
vi.mock("@workspace/db", () => {
  const posWhere = {
    then: (res: (v: unknown) => unknown) => Promise.resolve(linhasContagem).then(res),
    orderBy: () => ({ limit: () => Promise.resolve(linhasAnterior) }),
  };
  const chain = { from: () => chain, where: () => posWhere };
  return { db: { select: () => chain }, reportsTable: { date: "date", mode: "mode", id: "id", content: "content" } };
});
vi.mock("../logger", () => ({ logger: { warn: () => {}, error: () => {}, info: () => {} } }));

const { preflightRelatorio, bannerDeAvisos } = await import("../report-preflight");

const SEGUNDA = new Date(Date.UTC(2026, 7, 3, 15, 0)); // 03/08/2026
const DOMINGO = new Date(Date.UTC(2026, 7, 2, 15, 0)); // 02/08/2026

const CORPO = "Relatório do dia com análise detalhada. ".repeat(40);

function base(extra = "", opts: Partial<{ mode: string; tickers: string[]; agora: Date }> = {}) {
  return preflightRelatorio({
    content: `# Relatório\n\n${CORPO}\n${extra}`,
    date: "2026-08-03",
    mode: opts.mode ?? "daily",
    tickers: opts.tickers ?? [],
    agora: opts.agora ?? SEGUNDA,
  });
}

beforeEach(() => {
  linhasContagem[0] = { n: 1 };
  linhasAnterior.length = 0;
});

const codigos = (r: { achados: { code: string }[] }) => r.achados.map((a) => a.code);

describe("preflight", () => {
  it("relatório normal passa limpo", async () => {
    const r = await base();
    expect(r.achados).toEqual([]);
    expect(r.bloqueado).toBe(false);
  });

  it("bloqueia relatório curto demais", async () => {
    const r = await preflightRelatorio({
      content: "Análise incompleta.", date: "2026-08-03", mode: "daily", tickers: [], agora: SEGUNDA,
    });
    expect(codigos(r)).toContain("RELATORIO_VAZIO");
    expect(r.bloqueado).toBe(true);
  });

  it("bloqueia segundo e-mail do mesmo modo no mesmo dia", async () => {
    linhasContagem[0] = { n: 2 };
    const r = await base();
    expect(codigos(r)).toContain("SEGUNDO_EMAIL_HOJE");
    expect(r.bloqueado).toBe(true);
  });

  it("avisa quando é fim de semana e o texto não diz", async () => {
    const r = await base("", { agora: DOMINGO });
    expect(codigos(r)).toContain("FIM_DE_SEMANA_NAO_SINALIZADO");
    expect(r.bloqueado).toBe(false);
  });

  it("não avisa quando o texto sinaliza o fim de semana", async () => {
    const r = await base("Hoje não há pregão; os números são do último pregão de 31/07.", { agora: DOMINGO });
    expect(codigos(r)).not.toContain("FIM_DE_SEMANA_NAO_SINALIZADO");
  });

  it("pega índice com variação implausível (o caso KOSPI)", async () => {
    const r = await base("Global: KOSPI +17,91%, Nikkei +4,03%.");
    expect(codigos(r)).toContain("INDICE_IMPLAUSIVEL");
    // Nikkei +4,03% é plausível e não pode gerar achado
    expect(r.achados.filter((a) => a.code === "INDICE_IMPLAUSIVEL")).toHaveLength(1);
  });

  it("pega preços congelados em dia útil", async () => {
    const precos = "NVDA $200,75 ARM $239,69 SMCI $28,40 MRVL $187,56";
    linhasAnterior.push({ content: `anterior ${precos}` });
    const r = await base(precos);
    expect(codigos(r)).toContain("PRECOS_CONGELADOS");
  });

  it("não acusa congelamento quando os preços mudaram", async () => {
    linhasAnterior.push({ content: "NVDA $200,75 ARM $239,69 SMCI $28,40 MRVL $187,56" });
    const r = await base("NVDA $204,10 ARM $241,00 SMCI $29,05 MRVL $190,20");
    expect(codigos(r)).not.toContain("PRECOS_CONGELADOS");
  });

  it("pega earnings iminente sem seção do ativo", async () => {
    const r = await base("HCC tem earnings em 3 dias e merece atenção.");
    expect(codigos(r)).toContain("EARNINGS_IMINENTE_SEM_SECAO");
  });

  it("não acusa quando o ativo com earnings tem seção", async () => {
    const r = await base("HCC tem earnings em 3 dias.\n\n## HCC\n\nAnálise do ativo.");
    expect(codigos(r)).not.toContain("EARNINGS_IMINENTE_SEM_SECAO");
  });

  it("pega rótulo contraditório na mesma seção (o caso HCC)", async () => {
    const r = await base("\n## HCC\n\n🟡 leitura inicial.\n\nCorreção: reclassificando para 🔴.");
    expect(codigos(r)).toContain("ROTULO_CONTRADITORIO");
  });

  it("pega verbo de direção contra o sinal do percentual", async () => {
    const r = await base("A ação subiu -3,54% no pregão.");
    expect(codigos(r)).toContain("DIRECAO_INCOERENTE");
  });

  it("aceita verbo coerente com o sinal", async () => {
    const r = await base("A ação caiu -3,54% no pregão.");
    expect(codigos(r)).not.toContain("DIRECAO_INCOERENTE");
  });

  it("pega ticker da carteira ausente do relatório", async () => {
    const r = await base("Análise de NVDA e ARM.", { tickers: ["NVDA", "ARM", "HCC"] });
    const achado = r.achados.find((a) => a.code === "TICKER_AUSENTE");
    expect(achado?.message).toContain("HCC");
  });

  it("pega marcador de execução truncada", async () => {
    const r = await base("[Aviso: limite de turnos atingido — análise pode estar incompleta.]");
    expect(codigos(r)).toContain("EXECUCAO_TRUNCADA");
  });

  it("banner lista só os avisos, não os bloqueios", async () => {
    const banner = bannerDeAvisos([
      { code: "A", severity: "WARN", message: "aviso" },
      { code: "B", severity: "BLOCK", message: "bloqueio" },
    ]);
    expect(banner).toContain("A");
    expect(banner).not.toContain("bloqueio");
  });

  it("banner é vazio quando não há aviso", () => {
    expect(bannerDeAvisos([])).toBe("");
  });
});
