/**
 * A lista de sugestões do campo "Ativos monitorados" tem que cobrir o que o
 * usuário de fato acompanha.
 *
 * O que motivou (25/09/2026): "não estou conseguindo adicionar o ticker PDD".
 * A lista tinha 41 símbolos e NENHUMA ADR chinesa -- nem PDD, nem BABA, nem
 * BIDU, nem NTES -- enquanto carregava SQ, PYPL e MSTR. Digitar "PDD" não
 * abria sugestão alguma, e campo que não responde se lê como campo que
 * recusou. O Enter sempre funcionou; o que faltava era o sinal de que ia
 * funcionar.
 *
 * A invariante testada aqui é a que teria pegado isso sozinha: todo ticker
 * que o backend já usa como padrão precisa existir na sugestão. Sete dos
 * quinze defaults da rota de settings (SNDK, WDC, ALAB, CRDO, ANET, VRT)
 * também estavam de fora -- a lista tinha sido escrita antes deles e nunca
 * mais acompanhou.
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "fs";
import { join } from "path";
import { POPULAR_TICKERS } from "../pages/settings";

const simbolos = new Set(POPULAR_TICKERS.map((t) => t.symbol));

/**
 * Os defaults lidos do FONTE da rota, não copiados para cá: lista copiada
 * envelhece calada, que é exatamente o defeito que este arquivo testa.
 */
function defaultsDaRota(): string[] {
  const fonte = readFileSync(
    join(__dirname, "..", "..", "..", "api-server", "src", "routes", "settings.ts"),
    "utf-8",
  );
  const m = fonte.match(/tickers:\s*\[([^\]]+)\]/);
  if (!m) throw new Error("não achei o default de `tickers` em routes/settings.ts");
  return [...m[1].matchAll(/"([A-Z.^-]+)"/g)].map((x) => x[1]);
}

describe("sugestões de ticker", () => {
  it("cobre todos os tickers que a rota de settings já usa como padrão", () => {
    const faltando = defaultsDaRota().filter((t) => !simbolos.has(t));
    expect(faltando).toEqual([]);
  });

  it("cobre as ADRs chinesas da carteira", () => {
    // O caso do relato. Estão juntas porque quem acompanha uma acompanha as
    // outras -- o driver do dia delas é o mesmo (ver lib/benchmark-setor).
    for (const t of ["PDD", "BABA", "BIDU", "NTES", "JD"]) {
      expect(simbolos.has(t), `${t} fora da sugestão`).toBe(true);
    }
  });

  it("não tem símbolo repetido", () => {
    // Duplicata na lista viraria duas linhas idênticas no dropdown.
    expect(simbolos.size).toBe(POPULAR_TICKERS.length);
  });

  it("todo item tem símbolo em maiúsculas e um nome legível", () => {
    for (const t of POPULAR_TICKERS) {
      expect(t.symbol).toBe(t.symbol.toUpperCase());
      expect(t.name.trim().length).toBeGreaterThan(1);
    }
  });
});
