import {
  pgTable,
  serial,
  text,
  timestamp,
  numeric,
  boolean,
  integer,
  index,
  unique,
} from "drizzle-orm/pg-core";
import { createInsertSchema } from "drizzle-zod";
import { z } from "zod/v4";

// Helper: numeric(15,4) com tipo TypeScript `number` para compatibilidade
// com o código existente (operações aritméticas e comparações).
// O PostgreSQL armazena com precisão fixa; JS lê como string e coerce
// automaticamente em aritméticas, mas .$type<number>() sinaliza isso ao TS.
const money = (col: string) => numeric(col, { precision: 15, scale: 4 }).$type<number>();

export const usersTable = pgTable("users", {
  id: serial("id").primaryKey(),
  email: text("email").notNull().unique(),
  passwordHash: text("password_hash").notNull(),
  // Conta "seed" criada pelo backfill de migração pra dono do login original --
  // fica com senha aleatória inutilizável até o dono reivindicar via
  // /auth/claim-seed-account (ver ensure-schema.ts). Novos cadastros normais
  // já nascem com isClaimed=true.
  isClaimed: boolean("is_claimed").notNull().default(true),
  // Vê o menu Runs (histórico de execuções do agente) e a lista completa de
  // runs -- gerenciado só via SQL/backfill do dono seed por enquanto, sem
  // tela de administração pra promover outras contas.
  isAdmin: boolean("is_admin").notNull().default(false),
  // Rastreio de atividade pra tela de administração de usuários -- atualizado
  // a cada heartbeat do frontend (ver routes/activity.ts). lastPath é a rota
  // do FRONTEND (ex: "/portfolio"), não a rota da API.
  lastSeenAt: timestamp("last_seen_at"),
  lastPath: text("last_path"),
  // Caixa disponível (USD não investido) por modo de carteira, por usuário —
  // "Disponível para investir" da corretora. Entra no Patrimônio total, não no
  // investido. Fica no usuário (não no settings global) pra não vazar entre contas.
  cashReal: money("cash_real").notNull().default(0),
  cashSimulated: money("cash_simulated").notNull().default(0),
  createdAt: timestamp("created_at").defaultNow().notNull(),
  updatedAt: timestamp("updated_at").defaultNow().notNull(),
});

export type User = typeof usersTable.$inferSelect;

export const reportsTable = pgTable("reports", {
  id: serial("id").primaryKey(),
  date: text("date").notNull(),
  content: text("content").notNull(),
  tickers: text("tickers").array().notNull().default([]),
  mode: text("mode").notNull().default("daily"), // daily | premarket | portfolio | coal | ai | news | exit_plan | alerts | veredito
  // Dono do relatório -- null pros modos "de casa" (daily/premarket/coal/ai/
  // news/alerts/scheduled/manual), que seguem compartilhados por todo mundo
  // igual sempre foram. Preenchido só pros modos derivados da carteira de
  // QUEM disparou a run (portfolio/veredito), pra essas rotas não vazarem
  // holdings de um usuário pro relatório que outro usuário vê (ver runner.ts
  // getPortfolioTickers -- antes buscava a carteira de todo mundo junta).
  userId: integer("user_id").references(() => usersTable.id, { onDelete: "cascade" }),
  createdAt: timestamp("created_at").defaultNow().notNull(),
  updatedAt: timestamp("updated_at").defaultNow().notNull(),
}, (t) => [
  index("idx_reports_date").on(t.date),
  index("idx_reports_mode").on(t.mode),
  index("idx_reports_user_id").on(t.userId),
]);

export const insertReportSchema = createInsertSchema(reportsTable).omit({
  id: true,
  createdAt: true,
  updatedAt: true,
});
export type InsertReport = z.infer<typeof insertReportSchema>;
export type Report = typeof reportsTable.$inferSelect;

export const observationsTable = pgTable("observations", {
  id: serial("id").primaryKey(),
  ticker: text("ticker").notNull(),
  date: text("date").notNull(),
  summary: text("summary").notNull(),
  sentiment: text("sentiment").notNull().default("neutral"),
  priceAtObservation: money("price_at_observation"),
  userNotes: text("user_notes"),
  createdAt: timestamp("created_at").defaultNow().notNull(),
  updatedAt: timestamp("updated_at").defaultNow().notNull(),
}, (t) => [
  index("idx_observations_ticker").on(t.ticker),
  index("idx_observations_ticker_created").on(t.ticker, t.createdAt),
]);

export const insertObservationSchema = createInsertSchema(observationsTable).omit(
  {
    id: true,
    createdAt: true,
    updatedAt: true,
  },
);
export type InsertObservation = z.infer<typeof insertObservationSchema>;
export type Observation = typeof observationsTable.$inferSelect;

export const agentRunsTable = pgTable("agent_runs", {
  id: serial("id").primaryKey(),
  startedAt: timestamp("started_at").defaultNow().notNull(),
  finishedAt: timestamp("finished_at"),
  status: text("status").notNull().default("running"), // running | success | failed
  trigger: text("trigger").notNull().default("manual"), // manual | scheduled | premarket | portfolio | coal | ai | news | exit_plan | alerts
  mode: text("mode").notNull().default("daily"), // daily | premarket | portfolio | coal | ai | news | exit_plan | alerts
  durationMs: integer("duration_ms"),
  errorMessage: text("error_message"),
  // Uso de LLM da run (agregado de todos os provedores/modelos, via linha USAGE: do agente)
  inputTokens: integer("input_tokens"),
  outputTokens: integer("output_tokens"),
  cacheReadTokens: integer("cache_read_tokens"),
  cacheWriteTokens: integer("cache_write_tokens"),
  costUsd: numeric("cost_usd", { precision: 12, scale: 6 }).$type<number>(),
  llmProvider: text("llm_provider"),
  llmModel: text("llm_model"),
  createdAt: timestamp("created_at").defaultNow().notNull(),
}, (t) => [
  index("idx_agent_runs_status").on(t.status),
  index("idx_agent_runs_started_at").on(t.startedAt),
]);

export type AgentRun = typeof agentRunsTable.$inferSelect;

export const settingsTable = pgTable("settings", {
  id: serial("id").primaryKey(),
  notifyEmail: text("notify_email").notNull(),
  scheduleEnabled: boolean("schedule_enabled").notNull().default(true),
  scheduleHour: integer("schedule_hour").notNull().default(8),
  scheduleMinute: integer("schedule_minute").notNull().default(30),
  tickers: text("tickers")
    .array()
    .notNull()
    .default(["NVDA", "SMCI", "MU", "INTC", "GOOGL", "ARM", "TSLA"]),
  premarketEnabled: boolean("premarket_enabled").notNull().default(false),
  premarketIntervalMin: integer("premarket_interval_min").notNull().default(60),
  premarketWindowStartHour: integer("premarket_window_start_hour").notNull().default(8),
  premarketWindowEndHour: integer("premarket_window_end_hour").notNull().default(10),
  // Caixa disponível (USD não investido) por modo de carteira — "Disponível
  // para investir" da corretora. Entra no Patrimônio total, não no investido.
  cashReal: money("cash_real").notNull().default(0),
  cashSimulated: money("cash_simulated").notNull().default(0),
  // Controle de custo do agente LLM: provedor manual (null = ordem padrão,
  // anthropic primeiro), teto diário (USD, horário de Brasília) do provedor
  // primário e provedor barato usado depois que o teto é atingido.
  agentProvider: text("agent_provider"),
  dailyBudgetUsd: numeric("daily_budget_usd", { precision: 10, scale: 2 }).$type<number>(),
  cheapProvider: text("cheap_provider").notNull().default("gemini"),
  updatedAt: timestamp("updated_at").defaultNow().notNull(),
  createdAt: timestamp("created_at").defaultNow().notNull(),
});

export type Settings = typeof settingsTable.$inferSelect;

export const alertsTable = pgTable("alerts", {
  id: serial("id").primaryKey(),
  symbol: text("symbol").notNull(),
  // 'price' (usa thresholdPrice ou thresholdPct, comportamento original) |
  // 'rsi' (usa thresholdValue como nivel de RSI 0-100) |
  // 'macd' (condition 'above' = histograma bullish, 'below' = bearish, sem threshold) |
  // 'sma20' | 'sma50' (condition 'above'/'below' = preco cruzou a media, sem threshold)
  indicator: text("indicator").notNull().default("price"),
  condition: text("condition").notNull(), // 'above' | 'below'
  thresholdPct: money("threshold_pct"),
  thresholdPrice: money("threshold_price"),
  thresholdValue: money("threshold_value"), // generico: nivel de RSI etc.
  enabled: boolean("enabled").notNull().default(true),
  lastTriggeredAt: timestamp("last_triggered_at"),
  // Dono do alerta -- nullable pra permitir o ALTER TABLE em cima de linhas
  // existentes; o backfill de migração preenche as linhas antigas com o
  // usuário seed (ver ensure-schema.ts).
  userId: integer("user_id").references(() => usersTable.id, { onDelete: "cascade" }),
  // E-mail que recebe a notificação deste alerta especificamente, definido
  // no momento da criação (default: e-mail de login do usuário) -- lido
  // direto daqui no disparo, sem consultar settings/users de novo.
  notifyEmail: text("notify_email"),
  createdAt: timestamp("created_at").defaultNow().notNull(),
  updatedAt: timestamp("updated_at").defaultNow().notNull(),
}, (t) => [
  index("idx_alerts_symbol").on(t.symbol),
  index("idx_alerts_enabled").on(t.enabled),
  index("idx_alerts_user_id").on(t.userId),
]);

export type Alert = typeof alertsTable.$inferSelect;

export const alertFiringsTable = pgTable("alert_firings", {
  id: serial("id").primaryKey(),
  alertId: integer("alert_id")
    .notNull()
    .references(() => alertsTable.id, { onDelete: "cascade" }),
  symbol: text("symbol").notNull(),
  indicator: text("indicator").notNull().default("price"),
  condition: text("condition").notNull(),
  thresholdPct: money("threshold_pct"),
  thresholdPrice: money("threshold_price"),
  thresholdValue: money("threshold_value"),
  valueAtFiring: money("value_at_firing"), // valor do indicador tecnico no momento do disparo (ex: RSI)
  changePctAtFiring: money("change_pct_at_firing"),
  priceAtFiring: money("price_at_firing"),
  firedAt: timestamp("fired_at").defaultNow().notNull(),
}, (t) => [
  index("idx_alert_firings_alert_id").on(t.alertId),
  index("idx_alert_firings_symbol").on(t.symbol),
  index("idx_alert_firings_fired_at").on(t.firedAt),
]);

export type AlertFiring = typeof alertFiringsTable.$inferSelect;

export const chatSessionsTable = pgTable("chat_sessions", {
  id: serial("id").primaryKey(),
  title: text("title").notNull().default("Nova conversa"),
  // Dono da conversa -- nullable pra permitir o ALTER TABLE em cima de linhas
  // existentes; o backfill de migração preenche as linhas antigas com o
  // usuário seed (ver ensure-schema.ts).
  userId: integer("user_id").references(() => usersTable.id, { onDelete: "cascade" }),
  createdAt: timestamp("created_at").defaultNow().notNull(),
  updatedAt: timestamp("updated_at").defaultNow().notNull(),
}, (t) => [
  index("idx_chat_sessions_user_id").on(t.userId),
]);

export type ChatSession = typeof chatSessionsTable.$inferSelect;

export const chatMessagesTable = pgTable("chat_messages", {
  id: serial("id").primaryKey(),
  sessionId: integer("session_id")
    .notNull()
    .references(() => chatSessionsTable.id, { onDelete: "cascade" }),
  role: text("role").notNull(),
  content: text("content").notNull(),
  // Uso de LLM da resposta (só preenchido em mensagens role=assistant, via
  // linha USAGE: emitida por run_chat_stream -- mesmo padrão de agent_runs).
  inputTokens: integer("input_tokens"),
  outputTokens: integer("output_tokens"),
  cacheReadTokens: integer("cache_read_tokens"),
  cacheWriteTokens: integer("cache_write_tokens"),
  costUsd: numeric("cost_usd", { precision: 12, scale: 6 }).$type<number>(),
  llmProvider: text("llm_provider"),
  llmModel: text("llm_model"),
  createdAt: timestamp("created_at").defaultNow().notNull(),
}, (t) => [
  index("idx_chat_messages_session_id").on(t.sessionId, t.createdAt),
]);

export type ChatMessage = typeof chatSessionsTable.$inferSelect;

export const portfolioPositionsTable = pgTable("portfolio_positions", {
  id: serial("id").primaryKey(),
  ticker: text("ticker").notNull(),
  quantity: money("quantity").notNull(),
  avgCost: money("avg_cost").notNull(),
  investedAmount: money("invested_amount").notNull(),
  // Dividendos recebidos acumulados nesta posição, informados manualmente.
  // Entram no patrimônio total e no P&L total como retorno realizado.
  dividends: money("dividends").notNull().default(0),
  // Marca a posição como ETF/fundo (vs ação). Usado só pra separar os valores
  // de "ações" e "ETFs" no Patrimônio total. Informado manualmente.
  isEtf: boolean("is_etf").notNull().default(false),
  firstPurchaseDate: text("first_purchase_date").notNull(),
  notes: text("notes"),
  isSimulated: boolean("is_simulated").notNull().default(false),
  downAlertPcts: integer("down_alert_pcts").array().notNull().default([10, 15, 20, 30]),
  upAlertPcts: integer("up_alert_pcts").array().notNull().default([10, 15, 20, 30, 40, 50]),
  // Dono da posição -- nullable pra permitir o ALTER TABLE em cima de linhas
  // existentes; o backfill de migração preenche as linhas antigas com o
  // usuário seed (ver ensure-schema.ts).
  userId: integer("user_id").references(() => usersTable.id, { onDelete: "cascade" }),
  // E-mail que recebe os alertas de ganho/perda/holding/recompra desta
  // posição, definido na criação (default: e-mail de login do usuário).
  notifyEmail: text("notify_email"),
  createdAt: timestamp("created_at").defaultNow().notNull(),
  updatedAt: timestamp("updated_at").defaultNow().notNull(),
}, (t) => [
  index("idx_portfolio_positions_ticker").on(t.ticker),
  index("idx_portfolio_positions_user_id").on(t.userId),
]);

export type PortfolioPosition = typeof portfolioPositionsTable.$inferSelect;

export const portfolioPurchasesTable = pgTable("portfolio_purchases", {
  id: serial("id").primaryKey(),
  positionId: integer("position_id")
    .notNull()
    .references(() => portfolioPositionsTable.id, { onDelete: "cascade" }),
  purchaseDate: text("purchase_date").notNull(),
  amount: money("amount").notNull(),
  purchasePrice: money("purchase_price"),
  priceManuallyEdited: boolean("price_manually_edited").notNull().default(false),
  saleDate: text("sale_date"),
  salePrice: money("sale_price"),
  createdAt: timestamp("created_at").defaultNow().notNull(),
}, (t) => [
  index("idx_portfolio_purchases_position_id").on(t.positionId),
]);

export type PortfolioPurchase = typeof portfolioPurchasesTable.$inferSelect;

export const portfolioAlertFiringsTable = pgTable("portfolio_alert_firings", {
  id: serial("id").primaryKey(),
  alertKey: text("alert_key").notNull().unique(),
  firedAt: timestamp("fired_at").defaultNow().notNull(),
}, (t) => [
  index("idx_portfolio_alert_firings_key").on(t.alertKey),
]);

export type PortfolioAlertFiring = typeof portfolioAlertFiringsTable.$inferSelect;

export const watchlistTable = pgTable("watchlist", {
  id: serial("id").primaryKey(),
  ticker: text("ticker").notNull(),
  notes: text("notes"),
  // Dono do item -- nullable pra permitir o ALTER TABLE em cima de linhas
  // existentes; o backfill de migração preenche as linhas antigas com o
  // usuário seed (ver ensure-schema.ts).
  userId: integer("user_id").references(() => usersTable.id, { onDelete: "cascade" }),
  addedAt: timestamp("added_at").defaultNow().notNull(),
}, (t) => [
  index("idx_watchlist_user_id").on(t.userId),
  // Antes era unique(ticker) sozinho -- agora cada usuário pode ter o mesmo
  // ticker na própria watchlist, só não duplicado para ELE.
  unique("uq_watchlist_user_ticker").on(t.userId, t.ticker),
]);
export type WatchlistItem = typeof watchlistTable.$inferSelect;

export const tradeJournalTable = pgTable("trade_journal", {
  id: serial("id").primaryKey(),
  ticker: text("ticker").notNull(),
  entryDate: text("entry_date").notNull(),
  entryPrice: money("entry_price"),
  stopLoss: money("stop_loss"),
  targetPrice: money("target_price"),
  thesis: text("thesis"),
  emotionalState: text("emotional_state").notNull().default("neutral"),
  exitDate: text("exit_date"),
  exitPrice: money("exit_price"),
  result: text("result"),
  notes: text("notes"),
  // Dono da anotação -- nullable pra permitir o ALTER TABLE em cima de linhas
  // existentes; o backfill de migração preenche as linhas antigas com o
  // usuário seed (ver ensure-schema.ts).
  userId: integer("user_id").references(() => usersTable.id, { onDelete: "cascade" }),
  createdAt: timestamp("created_at").defaultNow().notNull(),
  updatedAt: timestamp("updated_at").defaultNow().notNull(),
}, (t) => [
  index("idx_trade_journal_user_id").on(t.userId),
]);
export type TradeJournalEntry = typeof tradeJournalTable.$inferSelect;

// Picos intraday de volume/preço (candles de 1min) detectados pelo poller de
// background (alert-checker.ts, a cada 5min) via agent/get_intraday_spikes.py
// -- persistido pra sobreviver entre polls e aparecer no card "Alertas de
// Mercado" mesmo se o usuário não estiver com a página aberta no minuto exato
// do spike. Sem userId: é sinal de mercado (mesmos tickers monitorados pra
// todo mundo), não uma preferência por usuário.
export const intradaySpikesTable = pgTable("intraday_spikes", {
  id: serial("id").primaryKey(),
  ticker: text("ticker").notNull(),
  kind: text("kind").notNull(), // 'volume' | 'price'
  severity: text("severity").notNull(), // 'info' | 'atencao' | 'critico'
  title: text("title").notNull(),
  detail: text("detail").notNull(),
  value: money("value"),
  firedAt: timestamp("fired_at").defaultNow().notNull(),
}, (t) => [
  index("idx_intraday_spikes_ticker").on(t.ticker),
  index("idx_intraday_spikes_fired_at").on(t.firedAt),
]);
export type IntradaySpike = typeof intradaySpikesTable.$inferSelect;

// Dedup de e-mails do alerta de "repique" (dead-cat bounce / possível
// realização de lucro, ver market_alerts.py::check_dead_cat_bounce),
// disparado em background por alert-checker.ts via agent/get_bounce_alerts.py.
// Diferente de intraday_spikes (candle de 1min, sem e-mail, cooldown de 15min
// só pro card): esse sinal é baseado em fechamento diário (hoje vs. mesmo dia
// da semana passada), então uma chave por (ticker, direção, dia BRT) evita
// reenviar e-mail a cada poll de 5min enquanto o preço intradiário oscila em
// torno do limiar dentro do mesmo pregão. Mesmo padrão de dedup por chave
// única de portfolio_alert_firings -- sem propósito de exibição, só idempotência.
export const bounceAlertFiringsTable = pgTable("bounce_alert_firings", {
  id: serial("id").primaryKey(),
  alertKey: text("alert_key").notNull().unique(),
  firedAt: timestamp("fired_at").defaultNow().notNull(),
}, (t) => [
  index("idx_bounce_alert_firings_key").on(t.alertKey),
]);
export type BounceAlertFiring = typeof bounceAlertFiringsTable.$inferSelect;

// Dedup de e-mails do alerta de "setup de squeeze" (risco de short squeeze +
// reversão técnica, ver tools.py::check_squeeze_setup), disparado em
// background por alert-checker.ts via agent/get_squeeze_alerts.py -- dois
// níveis: "near" (falta só 1-2 dos 4 requisitos, avisa o que falta) e
// "confirmed" (squeeze_setup_detected=true). Chave por (ticker, tier, dia
// BRT): evita reenviar e-mail a cada poll de 5min enquanto o nível não
// muda, mas dispara de novo assim que "near" vira "confirmed" (chave
// diferente) mesmo no mesmo dia. Mesmo padrão de bounce_alert_firings --
// sem propósito de exibição, só idempotência.
export const squeezeAlertFiringsTable = pgTable("squeeze_alert_firings", {
  id: serial("id").primaryKey(),
  alertKey: text("alert_key").notNull().unique(),
  firedAt: timestamp("fired_at").defaultNow().notNull(),
}, (t) => [
  index("idx_squeeze_alert_firings_key").on(t.alertKey),
]);
export type SqueezeAlertFiring = typeof squeezeAlertFiringsTable.$inferSelect;

// Itens de um plano de saída de carteira: cada linha é "vender TICKER até
// targetDate, motivo X", opcionalmente amarrado a um evento (earnings) que
// justifica o prazo. Gerado manualmente (por análise no chat) ou por uma
// futura rotina do agente -- não é recálculo automático de nível de preço,
// é o registro do plano combinado com o usuário, que a UI cruza com a data
// de hoje pra sinalizar "vencido"/"prazo perto".
export const exitPlanItemsTable = pgTable("exit_plan_items", {
  id: serial("id").primaryKey(),
  ticker: text("ticker").notNull(),
  phase: integer("phase").notNull(),
  phaseLabel: text("phase_label").notNull(),
  targetDate: text("target_date").notNull(),
  action: text("action").notNull(),
  rationale: text("rationale").notNull(),
  eventDate: text("event_date"),
  status: text("status").notNull().default("pending"),
  soldAt: text("sold_at"),
  soldPrice: money("sold_price"),
  userId: integer("user_id").references(() => usersTable.id, { onDelete: "cascade" }),
  createdAt: timestamp("created_at").defaultNow().notNull(),
  updatedAt: timestamp("updated_at").defaultNow().notNull(),
}, (t) => [
  index("idx_exit_plan_items_user_id").on(t.userId),
  index("idx_exit_plan_items_ticker").on(t.ticker),
]);
export type ExitPlanItem = typeof exitPlanItemsTable.$inferSelect;

// Parâmetros de risco (vol/beta) usados no Painel de Cenários -- seed manual
// na migração 0022 só como valor inicial; recalculado automaticamente todo
// dia por scenario-params-checker.ts a partir do histórico OHLCV real
// (desvio-padrão dos log-retornos × √252 pra vol, regressão contra um índice
// setorial pra beta -- ver get_scenario_params.py). Global (não por usuário)
// -- vol/beta de um ticker é a mesma pra todo mundo que o possui, diferente
// de portfolio_positions.
export const scenarioParamsTable = pgTable("scenario_params", {
  ticker: text("ticker").primaryKey(),
  volAnnual: money("vol_annual").notNull(),
  betaSector: money("beta_sector").notNull(),
  updatedAt: timestamp("updated_at").defaultNow().notNull(),
});
export type ScenarioParams = typeof scenarioParamsTable.$inferSelect;

// Momentum recente do benchmark setorial (SMH), recalculado 1x por dia junto
// com scenario_params (mesmo histórico já baixado por get_scenario_params.py,
// sem chamada de rede extra) -- retorno anualizado dos últimos `lookback_days`
// pregões. Alimenta a SUGESTÃO (não trava, o usuário sempre pode sobrescrever)
// do slider "Movimento do setor até a data-alvo" em /cenarios: sugestão =
// momentum_annual_pct × (dias até a data-alvo / 365) -- como os dias restantes
// são recalculados a cada render, a sugestão encolhe sozinha conforme a
// data-alvo se aproxima, sem precisar recalcular nada aqui além de 1x/dia.
// Global (não por usuário) -- 1 linha só, chave é o benchmark.
export const sectorMomentumTable = pgTable("sector_momentum", {
  benchmark: text("benchmark").primaryKey(), // ex.: "SMH"
  momentumAnnualPct: money("momentum_annual_pct").notNull(),
  lookbackDays: integer("lookback_days").notNull(),
  updatedAt: timestamp("updated_at").defaultNow().notNull(),
});
export type SectorMomentum = typeof sectorMomentumTable.$inferSelect;

// Config do alerta por e-mail do Painel de Cenários -- uma linha por usuário
// (não uma lista de múltiplos alertas, ao contrário de `alerts`, porque o
// painel só tem UMA data-alvo por vez). O checker em background
// (lib/scenario-alert-checker.ts) roda o mesmo cálculo de
// @workspace/scenario-math com o cenário neutro (sem venda manual, setor
// parado, vol 1x) contra esta data-alvo/threshold, e dispara e-mail quando
// a probabilidade de empatar cai abaixo do limiar -- com cooldown via
// lastFiredAt pra não reenviar a cada ciclo enquanto a condição persiste.
export const scenarioAlertSettingsTable = pgTable("scenario_alert_settings", {
  userId: integer("user_id").primaryKey().references(() => usersTable.id, { onDelete: "cascade" }),
  dataAlvo: text("data_alvo").notNull(), // YYYY-MM-DD
  thresholdPct: money("threshold_pct").notNull().default(50), // dispara quando pEmpate*100 < thresholdPct
  enabled: boolean("enabled").notNull().default(true),
  notifyEmail: text("notify_email"),
  lastFiredAt: timestamp("last_fired_at"),
  updatedAt: timestamp("updated_at").defaultNow().notNull(),
  createdAt: timestamp("created_at").defaultNow().notNull(),
});
export type ScenarioAlertSettings = typeof scenarioAlertSettingsTable.$inferSelect;

// Snapshot diário da leitura do Painel de Cenários (uma linha por usuário por
// dia, upsert idempotente) -- gerado pelo mesmo checker em background que já
// roda de hora em hora (lib/scenario-alert-checker.ts), usando o cenário
// neutro contra a data-alvo configurada em scenario_alert_settings. Alimenta
// o termômetro de confirmação na tela /cenarios: cada snapshot registra a
// pEmpate do dia (calculada com preço de mercado real daquele dia), o que dá
// o histórico "quantos dias, desde que comecei a acompanhar, a chance de
// empatar ficou acima do limiar".
export const scenarioSnapshotsTable = pgTable("scenario_snapshots", {
  id: serial("id").primaryKey(),
  userId: integer("user_id").notNull().references(() => usersTable.id, { onDelete: "cascade" }),
  snapshotDate: text("snapshot_date").notNull(), // YYYY-MM-DD, data real do snapshot
  dataAlvo: text("data_alvo").notNull(), // data-alvo vigente no momento do snapshot
  diasRestantes: integer("dias_restantes").notNull(),
  pEmpate: money("p_empate").notNull(), // 0-1
  valorTotalHoje: money("valor_total_hoje").notNull(),
  custoTotal: money("custo_total").notNull(),
  p05: money("p05").notNull(),
  p50: money("p50").notNull(),
  p95: money("p95").notNull(),
  createdAt: timestamp("created_at").defaultNow().notNull(),
}, (t) => [
  index("idx_scenario_snapshots_user_id").on(t.userId),
  unique("uq_scenario_snapshots_user_date").on(t.userId, t.snapshotDate),
]);
export type ScenarioSnapshot = typeof scenarioSnapshotsTable.$inferSelect;

// Resultado final de um ciclo de acompanhamento (uma linha por data-alvo já
// vencida, uma vez resolvida nunca muda -- histórico de acurácia do modelo).
// `bateu` = valorFinal >= custoTotal, ou seja, a carteira realmente empatou
// (ou superou) o custo total até a data-alvo, o mesmo evento que pEmpate
// estimava a probabilidade de acontecer.
export const scenarioResolutionsTable = pgTable("scenario_resolutions", {
  id: serial("id").primaryKey(),
  userId: integer("user_id").notNull().references(() => usersTable.id, { onDelete: "cascade" }),
  dataAlvo: text("data_alvo").notNull(),
  valorFinal: money("valor_final").notNull(),
  custoTotal: money("custo_total").notNull(),
  pEmpateFinal: money("p_empate_final").notNull(), // última pEmpate estimada antes da resolução
  bateu: boolean("bateu").notNull(),
  resolvedAt: timestamp("resolved_at").defaultNow().notNull(),
}, (t) => [
  index("idx_scenario_resolutions_user_id").on(t.userId),
  unique("uq_scenario_resolutions_user_alvo").on(t.userId, t.dataAlvo),
]);
export type ScenarioResolution = typeof scenarioResolutionsTable.$inferSelect;
