import { describe, it, expect } from "vitest";
import {
  tabela, itens, pct, cabecalho, montarRelatorioSetor,
  ROTULO_POR_MODO_EXPORTADO, nomeArquivoMarkdown, blocoAnaliseIA, notaDaJanelaDeReacao,
} from "@/components/exportar-relatorio";

describe("nomeArquivoMarkdown", () => {
  const dia = new Date("2026-08-16T12:00:00Z");

  it("minúsculas, sem acento, hífens e data", () => {
    expect(nomeArquivoMarkdown("Previsão de vol — INTC, PDD", dia))
      .toBe("previsao-de-vol-intc-pdd-2026-08-16.md");
  });

  it("título vazio ou só símbolos cai no fallback", () => {
    expect(nomeArquivoMarkdown("", dia)).toBe("relatorio-2026-08-16.md");
    expect(nomeArquivoMarkdown("§§§", dia)).toBe("relatorio-2026-08-16.md");
  });

  it("título longo é cortado sem hífen pendurado", () => {
    const nome = nomeArquivoMarkdown("a".repeat(40) + " " + "b".repeat(40), dia);
    expect(nome.length).toBeLessThanOrEqual(60 + "-2026-08-16.md".length);
    expect(nome).not.toContain("--");
    expect(nome.replace("-2026-08-16.md", "")).not.toMatch(/-$/);
  });
});

describe("tabela", () => {
  it("monta cabeçalho, separador e corpo", () => {
    const t = tabela(["A", "B"], [["1", "2"], ["3", "4"]]);
    expect(t.split("\n")).toEqual([
      "| A | B |",
      "| --- | --- |",
      "| 1 | 2 |",
      "| 3 | 4 |",
    ]);
  });

  it("escapa pipe na célula para não quebrar o alinhamento", () => {
    // Nome de empresa e texto de sinal chegam sem sanitização; um `|` solto
    // desalinharia toda a tabela dali pra baixo.
    const t = tabela(["Sinal"], [["alta | volume forte"]]);
    expect(t).toContain("alta \\| volume forte");
  });

  it("troca vazio, null e undefined por travessão", () => {
    const t = tabela(["A", "B", "C"], [[null, undefined, ""]]);
    expect(t).toContain("| — | — | — |");
  });

  it("preserva o zero, que é valor e não ausência", () => {
    const t = tabela(["A"], [[0]]);
    expect(t).toContain("| 0 |");
    expect(t).not.toContain("| — |");
  });
});

describe("itens", () => {
  it("descarta linhas sem valor", () => {
    const s = itens([["Preço", "$10"], ["Beta", null], ["Vol", undefined], ["Nota", ""]]);
    expect(s).toBe("- **Preço:** $10");
  });

  it("mantém zero", () => {
    expect(itens([["Operações", 0]])).toBe("- **Operações:** 0");
  });
});

describe("pct", () => {
  it("põe sinal explícito no positivo", () => {
    expect(pct(3.456)).toBe("+3.46%");
  });

  it("mantém o sinal do negativo", () => {
    expect(pct(-8.9)).toBe("-8.90%");
  });

  it("devolve travessão para null, undefined e NaN", () => {
    expect(pct(null)).toBe("—");
    expect(pct(undefined)).toBe("—");
    expect(pct(NaN)).toBe("—");
  });

  it("respeita o número de casas", () => {
    expect(pct(12.345, 1)).toBe("+12.3%");
  });
});

describe("cabecalho", () => {
  it("inclui o título como h1 e a linha de contexto", () => {
    const c = cabecalho("Radar IA", "snapshot 14/08/2026");
    expect(c).toContain("# Radar IA");
    expect(c).toContain("snapshot 14/08/2026");
    expect(c).toContain("(BRT)");
  });

  it("funciona sem contexto", () => {
    expect(cabecalho("Backtest")).toContain("# Backtest");
  });
});

describe("montarRelatorioSetor", () => {
  const obs = [
    { ticker: "NVDA", date: "2026-08-15", createdAt: "2026-08-15T12:00:00Z", sentiment: "bullish", summary: "Demanda forte", priceAtObservation: 180.5 },
    { ticker: "AMD", date: "2026-08-14", createdAt: "2026-08-14T12:00:00Z", sentiment: "bearish", summary: "Margem em queda", priceAtObservation: null },
    { ticker: "ARM", date: "2026-08-15", createdAt: "2026-08-15T13:00:00Z", sentiment: "neutral", summary: "Sem novidade" },
  ];

  it("devolve null sem observações — não há relatório vazio pra salvar", () => {
    expect(montarRelatorioSetor("IA", ["NVDA"], [])).toBeNull();
  });

  it("conta o sentimento de cada tipo", () => {
    const r = montarRelatorioSetor("IA", ["NVDA", "AMD", "ARM"], obs)!;
    expect(r).toContain("- **Observações:** 3");
    expect(r).toContain("- **Bullish:** 1");
    expect(r).toContain("- **Bearish:** 1");
    expect(r).toContain("- **Neutras:** 1");
  });

  it("agrupa por dia, mais recente primeiro", () => {
    const r = montarRelatorioSetor("IA", ["NVDA"], obs)!;
    expect(r.indexOf("## 2026-08-15")).toBeLessThan(r.indexOf("## 2026-08-14"));
  });

  it("só mostra preço quando existe", () => {
    const r = montarRelatorioSetor("IA", ["NVDA"], obs)!;
    expect(r).toContain("**NVDA** (bullish · $180.50)");
    expect(r).toContain("**AMD** (bearish)");
  });

  it("cai pro createdAt quando date vem vazio", () => {
    const semData = [{ ticker: "X", createdAt: "2026-01-02T10:00:00Z", sentiment: "neutral", summary: "s" }];
    expect(montarRelatorioSetor("IA", ["X"], semData)!).toContain("## 2026-01-02");
  });
});

describe("ROTULO_POR_MODO_EXPORTADO", () => {
  it("cobre as dez telas de análise", () => {
    expect(Object.keys(ROTULO_POR_MODO_EXPORTADO)).toHaveLength(10);
  });

  it("usa o prefixo tela_ em todos os modos, que é o que o Histórico filtra", () => {
    for (const m of Object.keys(ROTULO_POR_MODO_EXPORTADO)) {
      expect(m.startsWith("tela_")).toBe(true);
    }
  });

  it("não colide com os modos do agente", () => {
    const doAgente = ["daily", "premarket", "portfolio", "veredito", "news", "alerts", "coal", "ai", "exit_plan"];
    for (const m of doAgente) {
      expect(ROTULO_POR_MODO_EXPORTADO[m]).toBeUndefined();
    }
  });
});

// ── O veredito do validador acompanha o documento validado ──────────────────
//
// Ver o cabeçalho de `blocoAnaliseIA`: os relatórios de ADI e MRVL de
// 21/09/2026 saíram do app com um erro que o validador tinha pego (a
// distância até a máxima de 52 semanas medida sobre o preço em vez de sobre
// a máxima) e o .md não trazia uma linha disso.
describe("blocoAnaliseIA", () => {
  it("sem aviso, é o texto e mais nada", () => {
    expect(blocoAnaliseIA("## Quadro geral\n\ntexto")).toBe(
      "## Análise com IA\n\n## Quadro geral\n\ntexto");
    expect(blocoAnaliseIA("texto", [])).not.toContain("⚠");
    expect(blocoAnaliseIA("texto", null, false)).not.toContain("⚠");
  });

  it("os avisos vêm ANTES do texto, e todos", () => {
    const b = blocoAnaliseIA("o texto da análise", [
      "ANALISE_DISTANCIA_DA_FAIXA: diz 17.29% em relação à máxima de 52 semanas",
      "ANALISE_POSICAO_NA_FAIXA: metade superior",
    ]);
    expect(b).toContain("2 problema(s)");
    expect(b).toContain("ANALISE_DISTANCIA_DA_FAIXA");
    expect(b).toContain("ANALISE_POSICAO_NA_FAIXA");
    // Quem abre o arquivo tem que ver o aviso na primeira tela, não no fim.
    expect(b.indexOf("⚠")).toBeLessThan(b.indexOf("o texto da análise"));
    // Citação em bloco: sobrevive a qualquer renderizador de markdown.
    for (const linha of b.split("\n").filter((l) => l.includes("ANALISE_"))) {
      expect(linha.startsWith(">")).toBe(true);
    }
  });

  it("o corte por tamanho também é aviso", () => {
    // Corpo com marca própria: o próprio aviso contém a palavra "texto", e
    // procurá-la acusaria o aviso de estar depois de si mesmo.
    const b = blocoAnaliseIA("## Quadro geral", null, true);
    expect(b).toContain("cortado por tamanho");
    expect(b.indexOf("cortado")).toBeLessThan(b.indexOf("## Quadro geral"));
  });
});

// Ver o cabeçalho de `notaDaJanelaDeReacao`: num emissor que divulga após o
// fechamento, a tabela de eventos mostra o gap de D0 e o resumo traz a média
// de D+1 — sem esta linha, os dois se leem como contradição.
describe("notaDaJanelaDeReacao", () => {
  it("aponta a coluna certa em cada caso", () => {
    expect(notaDaJanelaDeReacao("seguinte")).toContain("Fech. D+1");
    expect(notaDaJanelaDeReacao("seguinte")).toContain("SEGUINTE");
    expect(notaDaJanelaDeReacao("anuncio")).toContain("Fech. dia");
    expect(notaDaJanelaDeReacao("anuncio")).toContain("antes da abertura");
  });

  it("sem a janela, não inventa uma", () => {
    // `itens` descarta null, então a linha simplesmente não sai — melhor que
    // afirmar a janela errada num payload antigo que não traz o campo.
    expect(notaDaJanelaDeReacao(undefined)).toBeNull();
    expect(notaDaJanelaDeReacao(null)).toBeNull();
  });
});
