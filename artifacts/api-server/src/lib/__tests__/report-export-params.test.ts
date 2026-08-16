import { describe, it, expect } from "vitest";
import {
  parseExportBody, MODOS_EXPORTAVEIS, LIMITE_MARKDOWN, LIMITE_TICKERS,
} from "../report-export-params";

const valido = {
  titulo: "Backtest NVDA",
  markdown: "# Backtest NVDA\n\nRetorno +12%",
  mode: "tela_backtest",
  tickers: ["nvda"],
  enviar: false,
};

describe("parseExportBody — caminho feliz", () => {
  it("aceita um corpo completo", () => {
    const r = parseExportBody(valido);
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.valor.titulo).toBe("Backtest NVDA");
    expect(r.valor.mode).toBe("tela_backtest");
    expect(r.valor.enviar).toBe(false);
  });

  it("normaliza ticker para maiúscula e sem espaço", () => {
    const r = parseExportBody({ ...valido, tickers: [" nvda ", "smci"] });
    expect(r.ok && r.valor.tickers).toEqual(["NVDA", "SMCI"]);
  });

  it("apara espaço do título", () => {
    const r = parseExportBody({ ...valido, titulo: "  Radar  " });
    expect(r.ok && r.valor.titulo).toBe("Radar");
  });

  it("só trata enviar como verdadeiro quando é o booleano true", () => {
    // "true" (string) e 1 chegam de cliente mal comportado; nenhum dos dois
    // pode disparar e-mail sozinho.
    for (const v of ["true", 1, "sim", {}]) {
      const r = parseExportBody({ ...valido, enviar: v });
      expect(r.ok && r.valor.enviar).toBe(false);
    }
    const r = parseExportBody({ ...valido, enviar: true });
    expect(r.ok && r.valor.enviar).toBe(true);
  });

  it("aceita todos os modos da lista", () => {
    for (const mode of MODOS_EXPORTAVEIS) {
      expect(parseExportBody({ ...valido, mode }).ok).toBe(true);
    }
  });
});

describe("parseExportBody — rejeições", () => {
  it("exige título", () => {
    const r = parseExportBody({ ...valido, titulo: "   " });
    expect(r.ok).toBe(false);
    if (r.ok) return;
    expect(r.status).toBe(400);
    expect(r.erro).toContain("titulo");
  });

  it("recusa markdown vazio com mensagem acionável", () => {
    const r = parseExportBody({ ...valido, markdown: "\n  \n" });
    expect(r.ok).toBe(false);
    if (r.ok) return;
    expect(r.status).toBe(400);
    expect(r.erro).toContain("rode a análise");
  });

  it("reclama do markdown antes do mode", () => {
    // Quem clica numa tela sem dados precisa ouvir "rode a análise", não uma
    // reclamação sobre um campo que a tela preenche sozinha.
    const r = parseExportBody({ titulo: "X", markdown: "", mode: "invalido" });
    expect(r.ok).toBe(false);
    if (r.ok) return;
    expect(r.erro).toContain("rode a análise");
  });

  it("recusa markdown acima do limite com 413", () => {
    const r = parseExportBody({ ...valido, markdown: "x".repeat(LIMITE_MARKDOWN + 1) });
    expect(r.ok).toBe(false);
    if (r.ok) return;
    expect(r.status).toBe(413);
  });

  it("aceita exatamente no limite", () => {
    expect(parseExportBody({ ...valido, markdown: "x".repeat(LIMITE_MARKDOWN) }).ok).toBe(true);
  });

  it("recusa modo fora da lista, inclusive os do agente", () => {
    for (const mode of ["daily", "premarket", "veredito", "portfolio", "qualquer"]) {
      const r = parseExportBody({ ...valido, mode });
      expect(r.ok).toBe(false);
      if (r.ok) continue;
      expect(r.status).toBe(400);
      expect(r.erro).toContain("mode inválido");
    }
  });

  it("nomeia o modo vazio em vez de imprimir string vazia", () => {
    const r = parseExportBody({ ...valido, mode: "" });
    expect(r.ok).toBe(false);
    if (r.ok) return;
    expect(r.erro).toContain("(vazio)");
  });

  it("sobrevive a corpo ausente ou não-objeto", () => {
    for (const body of [undefined, null, "texto", 42]) {
      expect(parseExportBody(body).ok).toBe(false);
    }
  });
});

describe("parseExportBody — tickers", () => {
  it("descarta entradas que não são string ou estão vazias", () => {
    const r = parseExportBody({ ...valido, tickers: ["NVDA", "", 42, null, "  ", "MU"] });
    expect(r.ok && r.valor.tickers).toEqual(["NVDA", "MU"]);
  });

  it("trata campo ausente ou não-array como lista vazia", () => {
    for (const tickers of [undefined, null, "NVDA", 42]) {
      const r = parseExportBody({ ...valido, tickers });
      expect(r.ok).toBe(true);
      if (r.ok) expect(r.valor.tickers).toEqual([]);
    }
  });

  it("corta no limite", () => {
    const muitos = Array.from({ length: LIMITE_TICKERS + 10 }, (_, i) => `T${i}`);
    const r = parseExportBody({ ...valido, tickers: muitos });
    expect(r.ok && r.valor.tickers.length).toBe(LIMITE_TICKERS);
  });
});

describe("MODOS_EXPORTAVEIS", () => {
  it("cobre as nove telas de análise", () => {
    expect(MODOS_EXPORTAVEIS).toHaveLength(9);
  });

  it("usa o prefixo tela_, que separa export manual de relatório do agente", () => {
    for (const m of MODOS_EXPORTAVEIS) {
      expect(m.startsWith("tela_")).toBe(true);
    }
  });
});
