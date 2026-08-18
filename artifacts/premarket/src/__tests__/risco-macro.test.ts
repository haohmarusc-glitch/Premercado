/**
 * "Sem dado" precisa ser reconhecível de relance — inclusive no código.
 *
 * A correção do macro_risk.py separa três estados por sinal. Se a tela pintar
 * `sem_dado` igual a `inativo`, o conserto morre no último centímetro: quem
 * olha o painel vê seis cartões cinzas e conclui que o mercado está calmo,
 * quando o sistema não mediu nada.
 *
 * Rodar: pnpm --filter @workspace/premarket run test -- --run risco-macro
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "fs";
import { join } from "path";

const FONTE = readFileSync(
  join(__dirname, "..", "components", "risco-macro.tsx"), "utf-8");

describe("os três estados são visualmente distintos", () => {
  it("sem_dado tem tratamento próprio, não cai no ramo de inativo", () => {
    expect(FONTE).toContain("semDado");
    // listrado + tracejado: reconhecível sem ler o texto
    expect(FONTE).toContain("border-dashed");
    expect(FONTE).toContain("repeating-linear-gradient");
  });

  it("os ramos de ativo excluem explicitamente o sem_dado", () => {
    // `ativo && ...` sozinho bastaria só enquanto sem_dado garantisse
    // active=false. Amarrar o visual a essa garantia deixa a tela dependendo
    // de uma invariante de outro módulo.
    expect(FONTE).toContain("!semDado && ativo");
  });

  it("mostra o motivo quando não há dado", () => {
    // Sem o motivo o operador vê um cartão listrado e não sabe o que consertar.
    expect(FONTE).toContain("sinal?.motivo");
  });

  it("nao_aplicavel não é pintado como falha", () => {
    // "Não houve balanço hoje" é resposta completa, não buraco.
    expect(FONTE).toContain("naoAplicavel");
    expect(FONTE).toContain("não se aplica");
  });
});

describe("o score nunca aparece sozinho", () => {
  it("a cobertura é renderizada junto", () => {
    expect(FONTE).toContain("cobertura {cobertura}%");
  });

  it("score ausente vira traço, não zero", () => {
    // `score || 0` mostraria 0/100 num dia cego — a leitura oposta da verdade.
    expect(FONTE).toContain('typeof score === "number" ? score : "—"');
    expect(FONTE).not.toContain("score || 0");
  });

  it("explica por que o score sumiu", () => {
    expect(FONTE).toContain("cobertura baixa demais");
  });
});

describe("higiene de rede", () => {
  it("não refaz a coleta a cada foco de aba", () => {
    // A coleta bate em FRED + 3 tickers + notícias. O retrato é do PREGÃO, não
    // do minuto.
    expect(FONTE).toContain("refetchOnWindowFocus: false");
    expect(FONTE).toContain("staleTime");
  });
});
