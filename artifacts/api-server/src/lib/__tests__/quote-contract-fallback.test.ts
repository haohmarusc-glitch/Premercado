import { describe, it, expect } from "vitest";
import { GetTickerQuotesResponse } from "@workspace/api-zod";

// A rota devolve `GetTickerQuotesResponse.parse(data)`, e o zod DESCARTA
// chaves que o schema não declara. Se `isDelayed` sair do schema, o Python
// continua mandando o campo, a rota continua respondendo 200, e a faixa de
// aviso da UI simplesmente nunca aparece — um preço de ontem exibido como
// atual, sem nenhum erro em lugar nenhum. É a armadilha nº 7 do README na
// forma mais silenciosa possível, então vale um teste só para ela.

const cotacaoAtrasada = {
  symbol: "NVDA",
  currency: "USD",
  price: 180.5,
  change: 1.5,
  changePct: 0.84,
  open: null,
  previousClose: 179,
  dayHigh: null,
  dayLow: null,
  volume: 1000000,
  marketCap: null,
  marketState: null,
  preMarketPrice: null,
  preMarketChangePct: null,
  postMarketPrice: null,
  postMarketChangePct: null,
  regularMarketPrice: null,
  isDelayed: true,
  source: "alphavantage_eod",
  sourceWarnings: ["Cotação ao vivo indisponível — mostrando fechamento de 2026-08-14"],
  error: null,
};

describe("contrato da cotação com fallback", () => {
  it("preserva isDelayed no parse da resposta", () => {
    const [q] = GetTickerQuotesResponse.parse([cotacaoAtrasada]);
    expect(q.isDelayed).toBe(true);
  });

  it("preserva source e sourceWarnings", () => {
    const [q] = GetTickerQuotesResponse.parse([cotacaoAtrasada]);
    expect(q.source).toBe("alphavantage_eod");
    expect(q.sourceWarnings).toEqual([
      "Cotação ao vivo indisponível — mostrando fechamento de 2026-08-14",
    ]);
  });

  it("aceita cotação ao vivo com os campos no estado normal", () => {
    const [q] = GetTickerQuotesResponse.parse([
      { ...cotacaoAtrasada, isDelayed: false, source: "yfinance", sourceWarnings: [] },
    ]);
    expect(q.isDelayed).toBe(false);
    expect(q.source).toBe("yfinance");
  });

  it("aceita resposta antiga sem os campos novos", () => {
    // Durante um deploy o Node novo pode ler cache do formato anterior; os
    // campos são opcionais justamente para isso não virar erro 500.
    const { isDelayed, source, sourceWarnings, ...antiga } = cotacaoAtrasada;
    void isDelayed; void source; void sourceWarnings;
    const [q] = GetTickerQuotesResponse.parse([antiga]);
    expect(q.symbol).toBe("NVDA");
    expect(q.isDelayed).toBeUndefined();
  });
});
