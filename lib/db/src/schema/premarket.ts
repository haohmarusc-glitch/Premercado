import {
  pgTable,
  serial,
  text,
  timestamp,
  doublePrecision,
  boolean,
  integer,
  index,
} from "drizzle-orm/pg-core";
import { createInsertSchema } from "drizzle-zod";
import { z } from "zod/v4";

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
  priceAtObservation: doublePrecision("price_at_observation"),
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
  // Pré-mercado intradiário automático
  premarketEnabled: boolean("premarket_enabled").notNull().default(false),
  premarketIntervalMin: integer("premarket_interval_min").notNull().default(30),
  premarketWindowStartHour: integer("premarket_window_start_hour").notNull().default(6),
  premarketWindowEndHour: integer("premarket_window_end_hour").notNull().default(9),
  updatedAt: timestamp("updated_at").defaultNow().notNull(),
  createdAt: timestamp("created_at").defaultNow().notNull(),
});

export type Settings = typeof settingsTable.$inferSelect;

export const alertsTable = pgTable("alerts", {
  id: serial("id").primaryKey(),
  symbol: text("symbol").notNull(),
  condition: text("condition").notNull(), // 'above' | 'below'
  thresholdPct: doublePrecision("threshold_pct"),
  thresholdPrice: doublePrecision("threshold_price"),
  enabled: boolean("enabled").notNull().default(true),
  lastTriggeredAt: timestamp("last_triggered_at"),
  createdAt: timestamp("created_at").defaultNow().notNull(),
  updatedAt: timestamp("updated_at").defaultNow().notNull(),
}, (t) => [
  index("idx_alerts_symbol").on(t.symbol),
  index("idx_alerts_enabled").on(t.enabled),
]);

export type Alert = typeof alertsTable.$inferSelect;

export const alertFiringsTable = pgTable("alert_firings", {
  id: serial("id").primaryKey(),
  alertId: integer("alert_id")
    .notNull()
    .references(() => alertsTable.id, { onDelete: "cascade" }),
  symbol: text("symbol").notNull(),
  condition: text("condition").notNull(),
  thresholdPct: doublePrecision("threshold_pct"),
  thresholdPrice: doublePrecision("threshold_price"),
  changePctAtFiring: doublePrecision("change_pct_at_firing"),
  priceAtFiring: doublePrecision("price_at_firing"),
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
  createdAt: timestamp("created_at").defaultNow().notNull(),
  updatedAt: timestamp("updated_at").defaultNow().notNull(),
});

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

export type ChatMessage = typeof chatMessagesTable.$inferSelect;

export const portfolioPositionsTable = pgTable("portfolio_positions", {
  id: serial("id").primaryKey(),
  ticker: text("ticker").notNull(),
  quantity: doublePrecision("quantity").notNull(),
  avgCost: doublePrecision("avg_cost").notNull(),
  investedAmount: doublePrecision("invested_amount").notNull(),
  firstPurchaseDate: text("first_purchase_date").notNull(),
  notes: text("notes"),
  downAlertPcts: integer("down_alert_pcts").array().notNull().default([10, 15, 20, 30]),
  upAlertPcts: integer("up_alert_pcts").array().notNull().default([10, 15, 20, 30, 40, 50]),
  createdAt: timestamp("created_at").defaultNow().notNull(),
  updatedAt: timestamp("updated_at").defaultNow().notNull(),
}, (t) => [
  index("idx_portfolio_positions_ticker").on(t.ticker),
]);

export type PortfolioPosition = typeof portfolioPositionsTable.$inferSelect;

export const portfolioPurchasesTable = pgTable("portfolio_purchases", {
  id: serial("id").primaryKey(),
  positionId: integer("position_id")
    .notNull()
    .references(() => portfolioPositionsTable.id, { onDelete: "cascade" }),
  purchaseDate: text("purchase_date").notNull(),
  amount: doublePrecision("amount").notNull(),
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
