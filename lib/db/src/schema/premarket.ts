import { pgTable, serial, text, timestamp, doublePrecision, boolean, integer } from "drizzle-orm/pg-core";
import { createInsertSchema } from "drizzle-zod";
import { z } from "zod/v4";

export const reportsTable = pgTable("reports", {
  id: serial("id").primaryKey(),
  date: text("date").notNull(),
  content: text("content").notNull(),
  tickers: text("tickers").array().notNull().default([]),
  createdAt: timestamp("created_at").defaultNow().notNull(),
});

export const insertReportSchema = createInsertSchema(reportsTable).omit({ id: true, createdAt: true });
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
});

export const insertObservationSchema = createInsertSchema(observationsTable).omit({ id: true, createdAt: true });
export type InsertObservation = z.infer<typeof insertObservationSchema>;
export type Observation = typeof observationsTable.$inferSelect;

export const agentRunsTable = pgTable("agent_runs", {
  id: serial("id").primaryKey(),
  startedAt: timestamp("started_at").defaultNow().notNull(),
  finishedAt: timestamp("finished_at"),
  status: text("status").notNull().default("running"), // running | success | failed
  trigger: text("trigger").notNull().default("manual"), // manual | scheduled
  durationMs: integer("duration_ms"),
  errorMessage: text("error_message"),
});

export type AgentRun = typeof agentRunsTable.$inferSelect;

export const settingsTable = pgTable("settings", {
  id: serial("id").primaryKey(),
  notifyEmail: text("notify_email").notNull(),
  scheduleEnabled: boolean("schedule_enabled").notNull().default(true),
  scheduleHour: integer("schedule_hour").notNull().default(8),
  scheduleMinute: integer("schedule_minute").notNull().default(30),
  tickers: text("tickers").array().notNull().default(["MU", "SMCI"]),
  updatedAt: timestamp("updated_at").defaultNow().notNull(),
});

export type Settings = typeof settingsTable.$inferSelect;

export const alertsTable = pgTable("alerts", {
  id: serial("id").primaryKey(),
  symbol: text("symbol").notNull(),
  condition: text("condition").notNull(), // 'above' | 'below'
  thresholdPct: doublePrecision("threshold_pct").notNull(),
  enabled: boolean("enabled").notNull().default(true),
  lastTriggeredAt: timestamp("last_triggered_at"),
  createdAt: timestamp("created_at").defaultNow().notNull(),
});

export type Alert = typeof alertsTable.$inferSelect;

export const alertFiringsTable = pgTable("alert_firings", {
  id: serial("id").primaryKey(),
  alertId: integer("alert_id").notNull(),
  symbol: text("symbol").notNull(),
  condition: text("condition").notNull(),
  thresholdPct: doublePrecision("threshold_pct").notNull(),
  changePctAtFiring: doublePrecision("change_pct_at_firing").notNull(),
  priceAtFiring: doublePrecision("price_at_firing"),
  firedAt: timestamp("fired_at").defaultNow().notNull(),
});

export type AlertFiring = typeof alertFiringsTable.$inferSelect;
