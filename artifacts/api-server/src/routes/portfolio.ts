import { Router, type IRouter } from "express";
import { spawn } from "child_process";
import path from "path";
import { and, asc, eq } from "drizzle-orm";
import { db, portfolioPositionsTable, portfolioPurchasesTable, usersTable } from "@workspace/db";
import { getPythonBin, agentDir } from "../lib/runner";
import { computeOpenLotTotals } from "../lib/portfolio-math";
import {
  ListPortfolioPositionsResponse,
  UpdatePortfolioPositionResponse as PortfolioPositionSchema,
  CreatePortfolioPositionBody,
  UpdatePortfolioPositionBody,
  UpdatePortfolioPositionParams as PortfolioPositionParams,
  ListPortfolioPurchasesResponse,
  UpdatePortfolioPurchaseResponse as PortfolioPurchaseSchema,
  CreatePortfolioPurchaseBody,
  UpdatePortfolioPurchaseBody,
  UpdatePortfolioPurchaseParams as PortfolioPurchaseParams,
} from "@workspace/api-zod";
import { logger } from "../lib/logger";

const router: IRouter = Router();

// Aceita tanto `db` quanto o `tx` recebido dentro de db.transaction(...) --
// deixa recomputePosition reutilizavel nos dois contextos.
type DbOrTx = typeof db | Parameters<Parameters<typeof db.transaction>[0]>[0];

// Fetch real historical close prices for a ticker on a set of dates (via yfinance)
function fetchHistoricalPrices(ticker: string, dates: string[]): Promise<Record<string, number>> {
  return new Promise((resolve, reject) => {
    const scriptPath = path.join(agentDir, "agent", "get_historical_price.py");
    const py = spawn(getPythonBin(), [scriptPath]);
    py.stdin.write(JSON.stringify({ ticker, dates }));
    py.stdin.end();
    let out = "";
    let err = "";
    py.stdout.on("data", (d: Buffer) => { out += d.toString(); });
    py.stderr.on("data", (d: Buffer) => { err += d.toString(); });
    const t = setTimeout(() => { py.kill("SIGTERM"); reject(new Error("timeout")); }, 60_000);
    py.on("close", (code) => {
      clearTimeout(t);
      if (code !== 0) return reject(new Error(err || "Script failed"));
      try {
        const parsed = JSON.parse(out) as { prices?: Record<string, number> };
        resolve(parsed.prices ?? {});
      } catch { reject(new Error("Parse error")); }
    });
  });
}

function serPos(r: typeof portfolioPositionsTable.$inferSelect) {
  return { ...r, createdAt: r.createdAt.toISOString(), updatedAt: r.updatedAt.toISOString() };
}
function serPur(r: typeof portfolioPurchasesTable.$inferSelect) {
  return {
    ...r,
    purchasePrice: r.purchasePrice ?? null,
    saleDate: r.saleDate ?? null,
    salePrice: r.salePrice ?? null,
    createdAt: r.createdAt.toISOString(),
  };
}

// Recalcula quantity/avgCost/investedAmount de uma posicao a partir dos lotes
// de compra AINDA NAO VENDIDOS (saleDate IS NULL). Deve ser chamada sempre que
// uma compra for criada, tiver uma venda registrada, ou for excluida -- para
// a tabela de posicoes nunca ficar "congelada" desatualizada em relacao as
// compras reais. Se nao sobrar nenhum lote em aberto, a posicao e removida
// (fica so o historico em portfolio_purchases, incluindo os lotes vendidos).
//
// Recebe o executor (db ou uma tx) explicitamente -- os chamadores sempre
// rodam isso dentro de db.transaction() junto com a escrita em
// portfolio_purchases, senao um crash entre as duas escritas deixa
// portfolio_positions dessincronizada silenciosamente.
async function recomputePosition(executor: DbOrTx, positionId: number): Promise<void> {
  const purchases = await executor
    .select()
    .from(portfolioPurchasesTable)
    .where(eq(portfolioPurchasesTable.positionId, positionId));

  const open = purchases.filter((p) => p.saleDate == null);

  if (open.length === 0) {
    // Nenhum lote em aberto -- posicao totalmente vendida. NAO deletamos a
    // posicao (a FK portfolio_purchases -> portfolio_positions e' ON DELETE
    // CASCADE, entao deletar aqui apagaria o historico de compra/venda
    // junto). Em vez disso zeramos os campos: a posicao some da view de
    // "ativas" (ver filtro em GET /portfolio) mas a linha e o historico
    // continuam intactos no banco.
    await executor
      .update(portfolioPositionsTable)
      .set({ quantity: 0, avgCost: 0, investedAmount: 0, updatedAt: new Date() })
      .where(eq(portfolioPositionsTable.id, positionId));
    return;
  }

  const totals = computeOpenLotTotals(
    open.map((p) => ({ amount: Number(p.amount), purchasePrice: p.purchasePrice != null ? Number(p.purchasePrice) : null })),
  );

  await executor
    .update(portfolioPositionsTable)
    .set({ ...totals, updatedAt: new Date() })
    .where(eq(portfolioPositionsTable.id, positionId));
}

// Verifica que a posição existe E pertence ao usuário logado -- usado antes
// de qualquer leitura/mutação em portfolio_purchases (que não tem user_id
// próprio, o dono é sempre checado transitivamente via position_id).
async function getOwnedPosition(positionId: number, userId: number) {
  const [pos] = await db
    .select()
    .from(portfolioPositionsTable)
    .where(and(eq(portfolioPositionsTable.id, positionId), eq(portfolioPositionsTable.userId, userId)));
  return pos ?? null;
}

// GET /portfolio
router.get("/portfolio", async (req, res): Promise<void> => {
  const rows = await db
    .select()
    .from(portfolioPositionsTable)
    .where(eq(portfolioPositionsTable.userId, req.userId!))
    .orderBy(asc(portfolioPositionsTable.createdAt));
  // Posicoes totalmente vendidas ficam com quantity = 0 (ver recomputePosition),
  // mas a linha continua no banco preservando o historico de compras/vendas --
  // e o frontend PRECISA dela aqui pra montar a seção "Ações Vendidas" (ele
  // busca os lotes de compra de cada posição desta lista; uma posição vendida
  // que sumisse daqui nunca apareceria como vendida, só desapareceria da
  // Carteira sem deixar rastro). O próprio frontend já separa ativas de
  // vendidas com sua própria lógica (baseada em saleDate/salePrice dos
  // lotes, não em quantity) -- não precisamos filtrar aqui.
  res.json(ListPortfolioPositionsResponse.parse(rows.map(serPos)));
});

// GET /portfolio/cash — caixa disponível (por modo) do usuário logado.
// Declarada antes das rotas /portfolio/:id pra "cash" não cair no param :id.
router.get("/portfolio/cash", async (req, res): Promise<void> => {
  const [u] = await db
    .select({ real: usersTable.cashReal, simulated: usersTable.cashSimulated })
    .from(usersTable)
    .where(eq(usersTable.id, req.userId!));
  res.json({ real: Number(u?.real ?? 0), simulated: Number(u?.simulated ?? 0) });
});

// PATCH /portfolio/cash — atualiza o caixa de um modo (real|simulated).
router.patch("/portfolio/cash", async (req, res): Promise<void> => {
  const mode = req.body?.mode;
  const amount = Number(req.body?.amount);
  if ((mode !== "real" && mode !== "simulated") || !Number.isFinite(amount) || amount < 0) {
    res.status(400).json({ error: "invalid input" });
    return;
  }
  const patch = mode === "real" ? { cashReal: amount } : { cashSimulated: amount };
  const [u] = await db
    .update(usersTable)
    .set({ ...patch, updatedAt: new Date() })
    .where(eq(usersTable.id, req.userId!))
    .returning({ real: usersTable.cashReal, simulated: usersTable.cashSimulated });
  if (!u) { res.status(404).json({ error: "user not found" }); return; }
  res.json({ real: Number(u.real), simulated: Number(u.simulated) });
});

// GET /portfolio/:id/purchases
router.get("/portfolio/:id/purchases", async (req, res): Promise<void> => {
  const p = PortfolioPositionParams.safeParse(req.params);
  if (!p.success) { res.status(400).json({ error: "invalid id" }); return; }
  const pos = await getOwnedPosition(p.data.id, req.userId!);
  if (!pos) { res.status(404).json({ error: "Position not found" }); return; }
  const rows = await db
    .select()
    .from(portfolioPurchasesTable)
    .where(eq(portfolioPurchasesTable.positionId, p.data.id))
    .orderBy(asc(portfolioPurchasesTable.purchaseDate));
  res.json(ListPortfolioPurchasesResponse.parse(rows.map(serPur)));
});

// POST /portfolio
router.post("/portfolio", async (req, res): Promise<void> => {
  const body = CreatePortfolioPositionBody.safeParse(req.body);
  if (!body.success) { res.status(400).json({ error: body.error.message }); return; }
  let notifyEmail = body.data.notifyEmail?.trim() || null;
  if (!notifyEmail) {
    const [me] = await db.select({ email: usersTable.email }).from(usersTable).where(eq(usersTable.id, req.userId!)).limit(1);
    notifyEmail = me?.email ?? null;
  }
  const [row] = await db
    .insert(portfolioPositionsTable)
    .values({ ...body.data, notifyEmail, ticker: body.data.ticker.toUpperCase(), userId: req.userId! })
    .returning();
  res.status(201).json(PortfolioPositionSchema.parse(serPos(row)));
});

// PUT /portfolio/:id
router.put("/portfolio/:id", async (req, res): Promise<void> => {
  const params = PortfolioPositionParams.safeParse(req.params);
  const body = UpdatePortfolioPositionBody.safeParse(req.body);
  if (!params.success || !body.success) { res.status(400).json({ error: "invalid input" }); return; }
  const update: Record<string, unknown> = { ...body.data, updatedAt: new Date() };
  if (body.data.ticker) update.ticker = body.data.ticker.toUpperCase();
  const [row] = await db
    .update(portfolioPositionsTable)
    .set(update)
    .where(and(eq(portfolioPositionsTable.id, params.data.id), eq(portfolioPositionsTable.userId, req.userId!)))
    .returning();
  if (!row) { res.status(404).json({ error: "Position not found" }); return; }
  res.json(PortfolioPositionSchema.parse(serPos(row)));
});

// DELETE /portfolio/:id
router.delete("/portfolio/:id", async (req, res): Promise<void> => {
  const p = PortfolioPositionParams.safeParse(req.params);
  if (!p.success) { res.status(400).json({ error: "invalid id" }); return; }
  await db
    .delete(portfolioPositionsTable)
    .where(and(eq(portfolioPositionsTable.id, p.data.id), eq(portfolioPositionsTable.userId, req.userId!)));
  res.status(204).send();
});

// POST /portfolio/:id/purchases
router.post("/portfolio/:id/purchases", async (req, res): Promise<void> => {
  const params = PortfolioPositionParams.safeParse(req.params);
  const body = CreatePortfolioPurchaseBody.safeParse(req.body);
  if (!params.success || !body.success) { res.status(400).json({ error: "invalid input" }); return; }

  const pos = await getOwnedPosition(params.data.id, req.userId!);
  if (!pos) { res.status(404).json({ error: "Position not found" }); return; }

  // Se o cliente nao mandou preco, tenta buscar o fechamento real da data
  // (yfinance) aqui tambem -- o frontend ja tenta isso antes de chamar essa
  // rota, mas essa e' a defesa de verdade: sem isso o lote fica com
  // purchasePrice null e distorce avgCost/quantity da posicao (ver
  // recomputePosition). Falha ao buscar preco nao bloqueia a compra --
  // fica null e o usuario pode rodar backfill-prices depois.
  let purchasePrice = body.data.purchasePrice ?? null;
  if (purchasePrice == null) {
    try {
      const prices = await fetchHistoricalPrices(pos.ticker, [body.data.purchaseDate]);
      purchasePrice = prices[body.data.purchaseDate] ?? null;
    } catch (err) {
      logger.warn({ err, ticker: pos.ticker }, "Failed to backfill purchase price at insert time");
    }
  }

  // priceManuallyEdited so' e' true quando o cliente sinaliza explicitamente
  // que o preco (ou a quantidade) foi informado a mao a partir da confirmacao
  // real da corretora. O preco que o frontend busca por estimativa (yfinance)
  // ao adicionar sem preco NAO conta -- senao "Corrigir precos reais" ficaria
  // permanentemente bloqueado num valor apenas aproximado.
  const priceManuallyEdited = body.data.priceManuallyEdited === true;

  const row = await db.transaction(async (tx) => {
    const [inserted] = await tx
      .insert(portfolioPurchasesTable)
      .values({ positionId: params.data.id, ...body.data, purchasePrice, priceManuallyEdited })
      .returning();
    await recomputePosition(tx, params.data.id);
    return inserted;
  });
  res.status(201).json(PortfolioPurchaseSchema.parse(serPur(row)));
});

// PATCH /portfolio/purchases/:purchaseId — corrigir valor/preco de compra ou registrar venda
router.patch("/portfolio/purchases/:purchaseId", async (req, res): Promise<void> => {
  const p = PortfolioPurchaseParams.safeParse(req.params);
  const body = UpdatePortfolioPurchaseBody.safeParse(req.body);
  if (!p.success || !body.success) { res.status(400).json({ error: "invalid input" }); return; }

  const [existing] = await db
    .select({ positionId: portfolioPurchasesTable.positionId })
    .from(portfolioPurchasesTable)
    .where(eq(portfolioPurchasesTable.id, p.data.purchaseId));
  if (!existing || !(await getOwnedPosition(existing.positionId, req.userId!))) {
    res.status(404).json({ error: "Not found" });
    return;
  }

  // So mexe em cada campo se ele veio explicitamente no corpo da requisicao --
  // do contrario, editar so o preco/valor de uma compra ja vendida apagaria
  // a venda registrada (saleDate/salePrice), ja que ambos sao nullish no schema.
  const update: Record<string, unknown> = {};
  if ("saleDate" in req.body) update.saleDate = body.data.saleDate ?? null;
  if ("salePrice" in req.body) update.salePrice = body.data.salePrice ?? null;
  if (body.data.amount !== undefined) update.amount = body.data.amount;
  if (body.data.purchasePrice !== undefined) {
    update.purchasePrice = body.data.purchasePrice;
    // Preco corrigido a mao (ex.: confirmacao real da corretora) -- nunca mais
    // deixa o backfill (mesmo forcado) sobrescrever com estimativa historica.
    update.priceManuallyEdited = body.data.purchasePrice != null;
  }

  if (Object.keys(update).length === 0) {
    res.status(400).json({ error: "no fields to update" });
    return;
  }

  const row = await db.transaction(async (tx) => {
    const [updated] = await tx
      .update(portfolioPurchasesTable)
      .set(update)
      .where(eq(portfolioPurchasesTable.id, p.data.purchaseId))
      .returning();
    await recomputePosition(tx, updated.positionId);
    return updated;
  });
  res.json(PortfolioPurchaseSchema.parse(serPur(row)));
});

// DELETE /portfolio/purchases/:purchaseId
router.delete("/portfolio/purchases/:purchaseId", async (req, res): Promise<void> => {
  const p = PortfolioPurchaseParams.safeParse(req.params);
  if (!p.success) { res.status(400).json({ error: "invalid id" }); return; }
  const [existing] = await db
    .select({ positionId: portfolioPurchasesTable.positionId })
    .from(portfolioPurchasesTable)
    .where(eq(portfolioPurchasesTable.id, p.data.purchaseId));
  if (!existing || !(await getOwnedPosition(existing.positionId, req.userId!))) {
    res.status(404).json({ error: "Not found" });
    return;
  }
  await db.transaction(async (tx) => {
    await tx.delete(portfolioPurchasesTable).where(eq(portfolioPurchasesTable.id, p.data.purchaseId));
    await recomputePosition(tx, existing.positionId);
  });
  res.status(204).send();
});

// GET /portfolio/historical-price?ticker=NVDA&date=2026-03-20 — single real close price
// Utilitário sem estado próprio de usuário (proxy do yfinance) -- não precisa
// de checagem de dono, só de estar logado (já garantido pelo requireAuth).
router.get("/portfolio/historical-price", async (req, res, next): Promise<void> => {
  const ticker = String(req.query.ticker ?? "").toUpperCase();
  const date = String(req.query.date ?? "");
  if (!ticker || !/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    res.status(400).json({ error: "ticker and valid date (YYYY-MM-DD) required" });
    return;
  }
  try {
    const prices = await fetchHistoricalPrices(ticker, [date]);
    res.json({ ticker, date, price: prices[date] ?? null });
  } catch (e: unknown) {
    next(e);
  }
});

// POST /portfolio/:id/backfill-prices — corrige purchasePrice de cada compra
// usando o fechamento real da data (yfinance). Por padrão só preenche compras
// sem preço; passe { force: true } para sobrescrever as demais também.
// Nunca mexe em compras marcadas como priceManuallyEdited (preço já corrigido
// à mão com o valor real de execução da corretora é sempre mais confiável do
// que a estimativa por fechamento do dia).
router.post("/portfolio/:id/backfill-prices", async (req, res, next): Promise<void> => {
  const params = PortfolioPositionParams.safeParse(req.params);
  if (!params.success) { res.status(400).json({ error: "invalid id" }); return; }
  const force = req.body?.force === true;

  const pos = await getOwnedPosition(params.data.id, req.userId!);
  if (!pos) { res.status(404).json({ error: "Position not found" }); return; }

  const purchases = await db
    .select()
    .from(portfolioPurchasesTable)
    .where(eq(portfolioPurchasesTable.positionId, params.data.id));

  const editable = purchases.filter((p) => !p.priceManuallyEdited);
  const targets = force ? editable : editable.filter((p) => p.purchasePrice == null);
  const skippedManual = purchases.length - editable.length;
  if (targets.length === 0) {
    res.json({ updated: 0, skippedManual, message: "Nenhuma compra para atualizar" });
    return;
  }

  try {
    const dates = [...new Set(targets.map((p) => p.purchaseDate))];
    const prices = await fetchHistoricalPrices(pos.ticker, dates);

    let updated = 0;
    for (const p of targets) {
      const price = prices[p.purchaseDate];
      if (price == null) continue;
      await db
        .update(portfolioPurchasesTable)
        .set({ purchasePrice: price })
        .where(eq(portfolioPurchasesTable.id, p.id));
      updated++;
    }
    res.json({ updated, total: targets.length, skippedManual, prices });
  } catch (e: unknown) {
    next(e);
  }
});

export default router;
