/**
 * Guardas de CALL-SITE da carteira.
 *
 * O padrão que mais se repetiu neste repo não é código errado — é código certo
 * que ninguém chama. `isPositionActiveFromLots` existia, estava correto e
 * tinha teste; `portfolio-alerts.ts` simplesmente não o usava, e isso custou
 * 98 e-mails falsos de 204 disparos. Teste de função pura não pega isso: a
 * função passa, o chamador erra.
 *
 * Então estes testes leem o FONTE. São chatos de propósito: se alguém
 * reescrever um dos blocos "do jeito que lembra", o teste quebra apontando o
 * arquivo. Mesmo padrão de `alerts-guardas-de-tela.test.ts`.
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const aqui = dirname(fileURLToPath(import.meta.url));
const src = resolve(aqui, "../..");

function ler(rel: string): string {
  return readFileSync(resolve(src, rel), "utf8");
}
/** O fonte sem comentários, para a guarda não casar com a própria explicação. */
function semComentarios(s: string): string {
  return s
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .split("\n")
    .filter((l) => !l.trim().startsWith("//"))
    .join("\n");
}

describe("portfolio-alerts.ts", () => {
  const fonte = ler("lib/portfolio-alerts.ts");
  const codigo = semComentarios(fonte);

  it("não divide pelo avg_cost armazenado", () => {
    // A linha exata que produzia Infinity:
    //   const pct = ((price - pos.avgCost) / pos.avgCost) * 100.0;
    expect(codigo).not.toMatch(/\/\s*pos\.avgCost/);
  });

  it("usa variacaoContraCusto, que nunca devolve Infinity nem NaN", () => {
    expect(codigo).toMatch(/variacaoContraCusto\s*\(/);
  });

  it("filtra os lotes em aberto antes de decidir ganho/perda", () => {
    expect(codigo).toMatch(/\.filter\(loteEmAberto\)/);
  });

  it("deriva o custo médio dos lotes", () => {
    expect(codigo).toMatch(/totaisDosLotesAbertos\s*\(/);
  });

  it("pula lote vendido no laço de holding", () => {
    expect(codigo).toMatch(/if\s*\(vendaRegistrada\(purchase\)\)\s*continue/);
  });

  it("todas as chaves de alerta são v2 e carregam um id", () => {
    const chaves = [...codigo.matchAll(/`(gain|loss|holding|recompra):[^`]*`/g)].map((m) => m[0]);
    expect(chaves.length).toBeGreaterThanOrEqual(4);
    for (const c of chaves) {
      expect(c, `chave sem v2: ${c}`).toMatch(/^`(gain|loss|holding|recompra):v2:\$\{/);
    }
  });

  it("a chave de holding usa o id da COMPRA, não ticker+data", () => {
    // Dois lotes do mesmo ticker na mesma data existem (SMCI 14/05 e 22/07,
    // SKHY 15/07 -- lotes divididos para venda parcial).
    expect(codigo).toMatch(/`holding:v2:\$\{purchase\.id\}/);
  });

  it("busca os lotes ANTES do laço de preço", () => {
    // Eram buscados só na seção de holding, lá embaixo -- tarde demais para o
    // bloco de ganho/perda, que por isso decidia pelo campo armazenado.
    // A ordem é lida no fonte CRU: o marcador da seção é um comentário.
    const iLotes = fonte.indexOf("from(portfolioPurchasesTable)");
    const iPreco = fonte.indexOf("Price threshold alerts");
    expect(iLotes).toBeGreaterThan(0);
    expect(iPreco).toBeGreaterThan(0);
    expect(iLotes).toBeLessThan(iPreco);
  });

  it("deduplica os tickers antes de pedir cotação", () => {
    expect(codigo).toMatch(/new Set\(positions\.map\(\(p\) => p\.ticker\)\)/);
  });
});

describe("routes/portfolio.ts", () => {
  const codigo = semComentarios(ler("routes/portfolio.ts"));

  it("recomputePosition usa loteEmAberto, não saleDate == null", () => {
    expect(codigo).toMatch(/purchases\.filter\(loteEmAberto\)/);
    expect(codigo).not.toMatch(/saleDate\s*==\s*null/);
  });

  it("as DUAS rotas que gravam venda validam o estado resultante", () => {
    // POST /portfolio/:id/purchases aceita saleDate/salePrice no corpo, então
    // o lote meio-vendido nascia pelos dois caminhos.
    const chamadas = [...codigo.matchAll(/erroNoEstadoDeVenda\s*\(/g)];
    expect(chamadas).toHaveLength(2);
  });

  it("o PATCH valida o estado MESCLADO, não só o corpo", () => {
    // Editar apenas a data de um lote que já tem preço é legítimo; um
    // validador que olhasse só o corpo recusaria.
    expect(codigo).toMatch(/"saleDate" in update \? update\.saleDate : existing\.saleDate/);
    expect(codigo).toMatch(/"salePrice" in update \? update\.salePrice : existing\.salePrice/);
  });

  it("recusa preço de compra zerado ou negativo", () => {
    expect(codigo).toMatch(/purchasePrice deve ser um número finito maior que zero/);
  });
});

describe("migração 0041 e ensure-schema em sincronia", () => {
  const migracao = readFileSync(
    resolve(src, "../../../lib/db/migrations/0041_portfolio_alert_firings_invalidacao.sql"),
    "utf8",
  );
  const ensure = ler("lib/ensure-schema.ts");

  it("cada coluna da migração está no ensure-schema", () => {
    // Playbook §9: coluna só na migração aparece em produção como erro de
    // query num contêiner recém-subido, porque o boot não roda `db push`.
    const colunas = [...migracao.matchAll(/ADD COLUMN IF NOT EXISTS (\w+)/g)].map((m) => m[1]);
    expect(colunas).toEqual(["invalidated_at", "invalidation_reason"]);
    for (const c of colunas) {
      expect(ensure, `${c} está na migração mas não no ensure-schema`)
        .toContain(`ADD COLUMN IF NOT EXISTS ${c}`);
    }
  });

  it("a migração não apaga nenhum disparo", () => {
    // A decisão é explícita: os 98 disparos falsos são a evidência do defeito.
    expect(migracao).not.toMatch(/\bDELETE\b/i);
    expect(migracao).not.toMatch(/\bTRUNCATE\b/i);
    expect(migracao).not.toMatch(/\bDROP\b/i);
  });

  it("o ensure-schema não repete a marcação a cada boot", () => {
    // Um UPDATE em massa a cada reinício é o oposto de idempotente-inofensivo.
    const bloco = ensure.slice(
      ensure.indexOf("portfolio_alert_firings ADD COLUMN"),
      ensure.indexOf("invalidation columns)"),
    );
    expect(bloco).not.toMatch(/UPDATE/i);
  });

  it("só marca o que a chave antiga permite identificar", () => {
    // Conservador de propósito: a chave v1 não diz de qual posição o disparo
    // veio, então a rajada de gain:ARM de 21/09 (ARM #12 encerrada, ARM #22
    // aberta) não é marcável. Deixar de marcar um falso é erro menor que
    // marcar um legítimo.
    expect(migracao).toMatch(/NOT LIKE 'gain:v2:%'/);
    expect(migracao).toMatch(/NOT LIKE 'holding:v2:%'/);
  });
});

describe("nenhum consumidor reescreve o predicado à mão", () => {
  const consumidores = [
    "lib/portfolio-alerts.ts",
    "lib/scenario-params-checker.ts",
    "lib/runner.ts",
    "routes/portfolio.ts",
    "routes/performance.ts",
    "routes/scenarios.ts",
  ];

  it.each(consumidores)("%s não tem `saleDate && salePrice` solto", (rel) => {
    const codigo = semComentarios(ler(rel));
    // A forma antiga, em qualquer nome de variável: `!(x.saleDate && x.salePrice)`
    expect(codigo).not.toMatch(/!\(\s*\w+\.saleDate\s*&&\s*\w+\.salePrice/);
  });

  it.each(consumidores)("%s decide posição ativa pelos lotes", (rel) => {
    const codigo = semComentarios(ler(rel));
    const lePosicoes = /from\(portfolioPositionsTable\)/.test(codigo);
    if (!lePosicoes) return;
    // Quem lê portfolio_positions tem que consultar os lotes de algum jeito:
    // posicaoAtivaPelosLotes, loteEmAberto ou vendaRegistrada.
    expect(
      /posicaoAtivaPelosLotes|loteEmAberto|vendaRegistrada/.test(codigo),
      `${rel} lê portfolio_positions sem olhar os lotes -- foi exatamente o defeito de portfolio-alerts.ts`,
    ).toBe(true);
  });
});
