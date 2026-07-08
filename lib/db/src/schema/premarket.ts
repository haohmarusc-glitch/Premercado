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
  createdAt: timestamp("created_at").defaultNow().notNull(),
  updatedAt: timestamp("updated_at").defaultNow().notNull(),
});

export type User = typeof usersTable.$inferSelect;

export const reportsTable = pgTable("reports", {
  id: serial("id").primaryKey(),
  date: text("date").notNull(),
  content: text("content").notNull(),
  tickers: text("tickers").array().notNull().default([]),
  mode: text("mode").notNull().default("daily"), // daily | premarket
  createdAt: timestamp("created_at").defaultNow().notNull(),
  updatedAt: timestamp("updated_at").defaultNow().notNull(),
}, (t) => [
  index("idx_reports_date").on(t.date),
  index("idx_reports_mode").on(t.mode),
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
  trigger: text("trigger").notNull().default("manual"), // manual | scheduled | premarket
  mode: text("mode").notNull().default("daily"), // daily | premarket
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
