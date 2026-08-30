import { describe, it, expect } from "vitest";
import {
  extrairBlocoDoVeredito, rotuloDaRazao, razaoConhecida, acaoValida,
} from "@/lib/veredito-bloco";

const BLOCO = `\`\`\`json
{
  "tickers": [
    {"ticker": "NVDA", "action": "MANTER", "confidence": 0.55,
     "reason_codes": ["EARNINGS_PROXIMO", "VOLUME_FRACO"]},
    {"ticker": "ARM", "action": "VENDER", "confidence": 0.95,
     "reason_codes": ["PLANO_DE_SAIDA"]}
  ]
}
\`\`\``;

describe("extrairBlocoDoVeredito", () => {
  it("separa o bloco da prosa e devolve as decisões", () => {
    const { prosa, decisoes } = extrairBlocoDoVeredito(`**VEREDITO:** cauteloso.\n\n${BLOCO}`);
    expect(prosa).toBe("**VEREDITO:** cauteloso.");
    expect(prosa).not.toContain("tickers");
    expect(decisoes).toEqual([
      { ticker: "NVDA", action: "MANTER", confidence: 0.55,
        reasonCodes: ["EARNINGS_PROXIMO", "VOLUME_FRACO"] },
      { ticker: "ARM", action: "VENDER", confidence: 0.95,
        reasonCodes: ["PLANO_DE_SAIDA"] },
    ]);
  });

  it("pega o ÚLTIMO fence com tickers, e só ele", () => {
    // O formato pede o bloco no fim, mas a prosa pode citar payload antes.
    const antes = '```json\n{"exemplo": 1}\n```';
    const { prosa, decisoes } = extrairBlocoDoVeredito(`texto\n\n${antes}\n\nmais\n\n${BLOCO}`);
    expect(decisoes).toHaveLength(2);
    expect(prosa).toContain('"exemplo"');
  });

  it("texto sem bloco nenhum sai intacto", () => {
    const t = "**VEREDITO:** favorável.\n\nSó prosa.";
    expect(extrairBlocoDoVeredito(t)).toEqual({ prosa: t, decisoes: null });
  });
});

// ── o par que importa: bloco ilegível NÃO pode sumir da tela ────────────────
//
// Engolir em silêncio um bloco que ninguém conseguiu ler é pior que mostrar o
// fence cru: o `llm_runtime.py` ainda anexa o aviso de "leitura degradada" ao texto,
// e uma tela que apagasse o fence diria o contrário do aviso logo abaixo dele.

describe("bloco que o validador recusaria fica visível", () => {
  const degenerados: [string, string][] = [
    ["JSON quebrado", '```json\n{"tickers": [ {"ticker": "NVDA" ]}\n```'],
    ["tickers não é lista", '```json\n{"tickers": "NVDA"}\n```'],
    ["raiz é lista", '```json\n[{"tickers": []}]\n```'],
    ["lista vazia", '```json\n{"tickers": []}\n```'],
    ["itens sem ticker", '```json\n{"tickers": [{"action": "MANTER"}]}\n```'],
  ];
  for (const [nome, fence] of degenerados) {
    it(`${nome}: prosa intacta e decisoes null`, () => {
      const t = `prosa\n\n${fence}`;
      const r = extrairBlocoDoVeredito(t);
      expect(r.decisoes).toBeNull();
      expect(r.prosa).toBe(t);
    });
  }
});

// ── desvio de schema aparece, não é consertado em silêncio ──────────────────
//
// A tela não pode ser mais complacente que o validador. Cada caso abaixo é
// apontado por `validar_bloco_estruturado` como erro; se o front normalizasse,
// o leitor veria um quadro limpo enquanto a caixa de erros gritava.

describe("desvio de schema sobrevive até a tela", () => {
  const bloco = (item: string) => `\`\`\`json\n{"tickers": [${item}]}\n\`\`\``;

  it("action fora do vocabulário passa como veio", () => {
    const { decisoes } = extrairBlocoDoVeredito(
      bloco('{"ticker": "NVDA", "action": "SEGURAR", "confidence": 0.5, "reason_codes": ["X"]}'));
    expect(decisoes![0].action).toBe("SEGURAR");
    expect(acaoValida("SEGURAR")).toBe(false);
  });

  it("confidence fora de [0,1] ou não-número vira null", () => {
    for (const c of ["1.5", "-0.1", '"0.8"', "null", "true"]) {
      const { decisoes } = extrairBlocoDoVeredito(
        bloco(`{"ticker": "NVDA", "action": "MANTER", "confidence": ${c}, "reason_codes": ["X"]}`));
      expect(decisoes![0].confidence).toBeNull();
    }
    const { decisoes } = extrairBlocoDoVeredito(
      bloco('{"ticker": "NVDA", "action": "MANTER", "confidence": 0, "reason_codes": ["X"]}'));
    expect(decisoes![0].confidence).toBe(0);
  });

  it("reason_codes ausente vira lista vazia (a tela marca isso)", () => {
    const { decisoes } = extrairBlocoDoVeredito(
      bloco('{"ticker": "NVDA", "action": "MANTER", "confidence": 0.5}'));
    expect(decisoes![0].reasonCodes).toEqual([]);
  });

  it("ticker e action chegam em maiúsculas", () => {
    const { decisoes } = extrairBlocoDoVeredito(
      bloco('{"ticker": " nvda ", "action": "manter", "confidence": 0.5, "reason_codes": ["volume_fraco"]}'));
    expect(decisoes![0]).toMatchObject({
      ticker: "NVDA", action: "MANTER", reasonCodes: ["VOLUME_FRACO"],
    });
  });
});

describe("vocabulário de razões", () => {
  it("código conhecido vira rótulo legível", () => {
    expect(rotuloDaRazao("EARNINGS_PROXIMO")).toBe("earnings próximo");
    expect(razaoConhecida("EARNINGS_PROXIMO")).toBe(true);
  });

  it("código novo não some -- aparece marcado", () => {
    // O validador registra código fora do vocabulário como WARN, não ERROR:
    // o vocabulário evolui (RUNUP_ESTICADO nasceu assim). A tela faz igual.
    expect(razaoConhecida("TESE_NOVA")).toBe(false);
    expect(rotuloDaRazao("TESE_NOVA")).toBe("tese nova");
  });
});
