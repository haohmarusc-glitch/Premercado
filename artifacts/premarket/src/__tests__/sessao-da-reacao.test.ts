import { describe, it, expect } from "vitest";

// ─── Qual coluna é a reação ─────────────────────────────────────────────────
//
// Incidente de leitura (SNDK, 26/08/2026) -- meu, não do modelo. O painel
// mostrava "correlação run-up × reação 0.85". Recalculando com a coluna
// "Fech. dia" da mesma tabela dava 0,66, e isso foi registrado como
// divergência a investigar.
//
// Não era. SNDK divulga depois do fechamento, então a reação medida é o dia
// SEGUINTE -- a coluna "Fech. D+1". Com ela, r = 0,85 exato. O código estava
// certo; a tabela é que não dizia qual das duas colunas ela estava usando.
//
// O teste fixa a aritmética das duas leituras porque é a distância entre elas
// que justifica marcar a coluna: 0,66 e 0,85 são ambos "correlação positiva
// forte", e um leitor que pegue o número errado não percebe que pegou.

function pearson(x: number[], y: number[]): number {
  const n = x.length;
  const mx = x.reduce((a, b) => a + b, 0) / n;
  const my = y.reduce((a, b) => a + b, 0) / n;
  const dx = x.map((v) => v - mx);
  const dy = y.map((v) => v - my);
  const num = dx.reduce((a, _, i) => a + dx[i] * dy[i], 0);
  const den = Math.sqrt(
    dx.reduce((a, v) => a + v * v, 0) * dy.reduce((a, v) => a + v * v, 0),
  );
  return num / den;
}

// A tabela real de SNDK, sem o evento mais antigo (que não tem run-up e por
// isso fica fora da correlação).
const RUNUP = [-18.16, 85.89, 111.01, 79.0, 10.04];
const FECH_DIA = [-5.4, 3.04, 2.21, -4.07, -0.7];
const FECH_D1 = [-6.81, 8.25, 6.85, 15.31, -4.58];

describe("a correlação publicada vem da sessão da reação", () => {
  it("com a sessão certa (AMC -> dia seguinte) dá o 0,85 do painel", () => {
    expect(Number(pearson(RUNUP, FECH_D1).toFixed(2))).toBe(0.85);
  });

  it("com a sessão do anúncio dá outro número igualmente plausível", () => {
    // 0,66 não é absurdo -- é o que torna o erro invisível. Se a leitura
    // errada produzisse -0,3 ou 1,4 o leitor pararia sozinho.
    expect(Number(pearson(RUNUP, FECH_DIA).toFixed(2))).toBe(0.66);
  });

  it("as duas leituras contam a mesma história qualitativa", () => {
    // Ambas "positiva forte". Por isso a tabela precisa DIZER qual coluna
    // usou, em vez de contar com o leitor conferir.
    expect(pearson(RUNUP, FECH_DIA)).toBeGreaterThan(0.5);
    expect(pearson(RUNUP, FECH_D1)).toBeGreaterThan(0.5);
  });
});

describe("qual coluna marcar", () => {
  const colunaDaReacao = (janela?: "anuncio" | "seguinte") =>
    janela === "seguinte" ? "Fech. D+1" : janela === "anuncio" ? "Fech. dia" : null;

  it("divulgação depois do fechamento reage no dia seguinte", () => {
    expect(colunaDaReacao("seguinte")).toBe("Fech. D+1");
  });

  it("divulgação antes da abertura reage no próprio dia", () => {
    expect(colunaDaReacao("anuncio")).toBe("Fech. dia");
  });

  it("sem janela declarada, nenhuma coluna é marcada", () => {
    // Marcar por palpite seria pior que não marcar: a marca é uma afirmação
    // sobre o dado, e afirmar sem base é o defeito que ela existe pra evitar.
    expect(colunaDaReacao(undefined)).toBeNull();
  });
});
