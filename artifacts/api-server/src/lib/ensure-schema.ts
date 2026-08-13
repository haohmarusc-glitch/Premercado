import { sql } from "drizzle-orm";
import { db } from "@workspace/db";
import { logger } from "./logger";

// Garante no boot as colunas exigidas por features novas quando o banco ainda
// não recebeu `pnpm --filter db push` (ex.: processo reiniciado sem o
// post-merge hook rodar). Statements idempotentes — espelham
// lib/db/migrations/0008_settings_cash.sql, 0009_agent_runs_usage.sql e
// 0009_alerts_technical_indicator.sql.
export async function ensureSchema(): Promise<void> {
  try {
    await db.execute(sql`ALTER TABLE settings ADD COLUMN IF NOT EXISTS cash_real numeric(15,4) NOT NULL DEFAULT 0`);
    await db.execute(sql`ALTER TABLE settings ADD COLUMN IF NOT EXISTS cash_simulated numeric(15,4) NOT NULL DEFAULT 0`);
    logger.info("Schema check ok (settings.cash_real/cash_simulated)");
  } catch (err) {
    logger.error({ err }, "Failed to ensure schema (cash columns)");
  }

  try {
    await db.execute(sql`ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS input_tokens integer`);
    await db.execute(sql`ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS output_tokens integer`);
    await db.execute(sql`ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS cache_read_tokens integer`);
    await db.execute(sql`ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS cache_write_tokens integer`);
    await db.execute(sql`ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS cost_usd numeric(12,6)`);
    await db.execute(sql`ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS llm_provider text`);
    await db.execute(sql`ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS llm_model text`);
    logger.info("Schema check ok (agent_runs usage/cost columns)");
  } catch (err) {
    logger.error({ err }, "Failed to ensure schema (agent_runs usage columns)");
  }

  try {
    await db.execute(sql`ALTER TABLE alerts ADD COLUMN IF NOT EXISTS indicator text NOT NULL DEFAULT 'price'`);
    await db.execute(sql`ALTER TABLE alerts ADD COLUMN IF NOT EXISTS threshold_value numeric(15,4)`);
    await db.execute(sql`ALTER TABLE alert_firings ADD COLUMN IF NOT EXISTS indicator text NOT NULL DEFAULT 'price'`);
    await db.execute(sql`ALTER TABLE alert_firings ADD COLUMN IF NOT EXISTS threshold_value numeric(15,4)`);
    await db.execute(sql`ALTER TABLE alert_firings ADD COLUMN IF NOT EXISTS value_at_firing numeric(15,4)`);
    logger.info("Schema check ok (alerts technical indicator columns)");
  } catch (err) {
    logger.error({ err }, "Failed to ensure schema (alerts technical indicator columns)");
  }

  try {
    await db.execute(sql`
      CREATE TABLE IF NOT EXISTS users (
        id serial PRIMARY KEY,
        email text NOT NULL UNIQUE,
        password_hash text NOT NULL,
        is_claimed boolean NOT NULL DEFAULT true,
        created_at timestamp NOT NULL DEFAULT now(),
        updated_at timestamp NOT NULL DEFAULT now()
      )
    `);
    await db.execute(sql`ALTER TABLE portfolio_positions ADD COLUMN IF NOT EXISTS user_id integer REFERENCES users(id) ON DELETE CASCADE`);
    await db.execute(sql`ALTER TABLE alerts ADD COLUMN IF NOT EXISTS user_id integer REFERENCES users(id) ON DELETE CASCADE`);
    await db.execute(sql`CREATE INDEX IF NOT EXISTS idx_portfolio_positions_user_id ON portfolio_positions(user_id)`);
    await db.execute(sql`CREATE INDEX IF NOT EXISTS idx_alerts_user_id ON alerts(user_id)`);
    logger.info("Schema check ok (users table + portfolio/alerts ownership columns)");
  } catch (err) {
    logger.error({ err }, "Failed to ensure schema (users/ownership columns)");
  }

  try {
    await db.execute(sql`ALTER TABLE alerts ADD COLUMN IF NOT EXISTS notify_email text`);
    await db.execute(sql`ALTER TABLE portfolio_positions ADD COLUMN IF NOT EXISTS notify_email text`);
    await db.execute(sql`ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin boolean NOT NULL DEFAULT false`);
    logger.info("Schema check ok (notify_email per-record + users.is_admin)");
  } catch (err) {
    logger.error({ err }, "Failed to ensure schema (notify_email/is_admin columns)");
  }

  try {
    await db.execute(sql`ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS user_id integer REFERENCES users(id) ON DELETE CASCADE`);
    await db.execute(sql`ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS user_id integer REFERENCES users(id) ON DELETE CASCADE`);
    await db.execute(sql`ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS user_id integer REFERENCES users(id) ON DELETE CASCADE`);
    await db.execute(sql`CREATE INDEX IF NOT EXISTS idx_chat_sessions_user_id ON chat_sessions(user_id)`);
    await db.execute(sql`CREATE INDEX IF NOT EXISTS idx_watchlist_user_id ON watchlist(user_id)`);
    await db.execute(sql`CREATE INDEX IF NOT EXISTS idx_trade_journal_user_id ON trade_journal(user_id)`);
    await db.execute(sql`ALTER TABLE watchlist DROP CONSTRAINT IF EXISTS watchlist_ticker_key`);
    await db.execute(sql`
      DO $$
      BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_watchlist_user_ticker') THEN
          ALTER TABLE watchlist ADD CONSTRAINT uq_watchlist_user_ticker UNIQUE (user_id, ticker);
        END IF;
      END $$;
    `);
    logger.info("Schema check ok (chat_sessions/watchlist/trade_journal ownership)");
  } catch (err) {
    logger.error({ err }, "Failed to ensure schema (chat/watchlist/journal ownership)");
  }

  try {
    await db.execute(sql`ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen_at timestamp`);
    await db.execute(sql`ALTER TABLE users ADD COLUMN IF NOT EXISTS last_path text`);
    logger.info("Schema check ok (users.last_seen_at/last_path)");
  } catch (err) {
    logger.error({ err }, "Failed to ensure schema (users activity tracking columns)");
  }

  try {
    await db.execute(sql`ALTER TABLE portfolio_purchases ADD COLUMN IF NOT EXISTS price_manually_edited boolean NOT NULL DEFAULT false`);
    logger.info("Schema check ok (portfolio_purchases.price_manually_edited)");
  } catch (err) {
    logger.error({ err }, "Failed to ensure schema (price_manually_edited column)");
  }

  try {
    await db.execute(sql`ALTER TABLE portfolio_positions ADD COLUMN IF NOT EXISTS dividends numeric(15,4) NOT NULL DEFAULT 0`);
    logger.info("Schema check ok (portfolio_positions.dividends)");
  } catch (err) {
    logger.error({ err }, "Failed to ensure schema (dividends column)");
  }

  try {
    await db.execute(sql`ALTER TABLE users ADD COLUMN IF NOT EXISTS cash_real numeric(15,4) NOT NULL DEFAULT 0`);
    await db.execute(sql`ALTER TABLE users ADD COLUMN IF NOT EXISTS cash_simulated numeric(15,4) NOT NULL DEFAULT 0`);
    logger.info("Schema check ok (users.cash_real/cash_simulated)");
  } catch (err) {
    logger.error({ err }, "Failed to ensure schema (user cash columns)");
  }

  try {
    await db.execute(sql`ALTER TABLE portfolio_positions ADD COLUMN IF NOT EXISTS is_etf boolean NOT NULL DEFAULT false`);
    logger.info("Schema check ok (portfolio_positions.is_etf)");
  } catch (err) {
    logger.error({ err }, "Failed to ensure schema (is_etf column)");
  }

  try {
    await db.execute(sql`ALTER TABLE settings ADD COLUMN IF NOT EXISTS agent_provider text`);
    await db.execute(sql`ALTER TABLE settings ADD COLUMN IF NOT EXISTS daily_budget_usd numeric(10,2)`);
    await db.execute(sql`ALTER TABLE settings ADD COLUMN IF NOT EXISTS cheap_provider text NOT NULL DEFAULT 'gemini'`);
    logger.info("Schema check ok (settings.agent_provider/daily_budget_usd/cheap_provider)");
  } catch (err) {
    logger.error({ err }, "Failed to ensure schema (agent provider/budget columns)");
  }

  try {
    await db.execute(sql`
      CREATE TABLE IF NOT EXISTS intraday_spikes (
        id serial PRIMARY KEY,
        ticker text NOT NULL,
        kind text NOT NULL,
        severity text NOT NULL,
        title text NOT NULL,
        detail text NOT NULL,
        value numeric(15, 4),
        fired_at timestamp NOT NULL DEFAULT now()
      )
    `);
    await db.execute(sql`CREATE INDEX IF NOT EXISTS idx_intraday_spikes_ticker ON intraday_spikes(ticker)`);
    await db.execute(sql`CREATE INDEX IF NOT EXISTS idx_intraday_spikes_fired_at ON intraday_spikes(fired_at)`);
    logger.info("Schema check ok (intraday_spikes table)");
  } catch (err) {
    logger.error({ err }, "Failed to ensure schema (intraday_spikes table)");
  }

  try {
    await db.execute(sql`
      CREATE TABLE IF NOT EXISTS exit_plan_items (
        id serial PRIMARY KEY,
        ticker text NOT NULL,
        phase integer NOT NULL,
        phase_label text NOT NULL,
        target_date text NOT NULL,
        action text NOT NULL,
        rationale text NOT NULL,
        event_date text,
        status text NOT NULL DEFAULT 'pending',
        sold_at text,
        sold_price numeric(15, 4),
        user_id integer REFERENCES users(id) ON DELETE CASCADE,
        created_at timestamp NOT NULL DEFAULT now(),
        updated_at timestamp NOT NULL DEFAULT now()
      )
    `);
    await db.execute(sql`CREATE INDEX IF NOT EXISTS idx_exit_plan_items_user_id ON exit_plan_items(user_id)`);
    await db.execute(sql`CREATE INDEX IF NOT EXISTS idx_exit_plan_items_ticker ON exit_plan_items(ticker)`);
    logger.info("Schema check ok (exit_plan_items table)");
  } catch (err) {
    logger.error({ err }, "Failed to ensure schema (exit_plan_items table)");
  }

  try {
    await db.execute(sql`ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS input_tokens integer`);
    await db.execute(sql`ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS output_tokens integer`);
    await db.execute(sql`ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS cache_read_tokens integer`);
    await db.execute(sql`ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS cache_write_tokens integer`);
    await db.execute(sql`ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS cost_usd numeric(12,6)`);
    await db.execute(sql`ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS llm_provider text`);
    await db.execute(sql`ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS llm_model text`);
    logger.info("Schema check ok (chat_messages usage/cost columns)");
  } catch (err) {
    logger.error({ err }, "Failed to ensure schema (chat_messages usage columns)");
  }

  try {
    await db.execute(sql`
      CREATE TABLE IF NOT EXISTS scenario_params (
        ticker text PRIMARY KEY,
        vol_annual numeric(15, 4) NOT NULL,
        beta_sector numeric(15, 4) NOT NULL,
        updated_at timestamp NOT NULL DEFAULT now()
      )
    `);
    await db.execute(sql`
      INSERT INTO scenario_params (ticker, vol_annual, beta_sector) VALUES
        ('NVDA', 0.50, 1.10),
        ('SMCI', 0.78, 1.60),
        ('ARM',  0.68, 1.45),
        ('AVGO', 0.42, 0.95),
        ('SKHY', 0.62, 1.30),
        ('MRVL', 0.66, 1.50)
      ON CONFLICT (ticker) DO NOTHING
    `);
    logger.info("Schema check ok (scenario_params table)");
  } catch (err) {
    logger.error({ err }, "Failed to ensure schema (scenario_params table)");
  }

  try {
    await db.execute(sql`
      CREATE TABLE IF NOT EXISTS scenario_alert_settings (
        user_id integer PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        data_alvo text NOT NULL,
        threshold_pct numeric(15, 4) NOT NULL DEFAULT 50,
        enabled boolean NOT NULL DEFAULT true,
        notify_email text,
        last_fired_at timestamp,
        updated_at timestamp NOT NULL DEFAULT now(),
        created_at timestamp NOT NULL DEFAULT now()
      )
    `);
    logger.info("Schema check ok (scenario_alert_settings table)");
  } catch (err) {
    logger.error({ err }, "Failed to ensure schema (scenario_alert_settings table)");
  }

  try {
    await db.execute(sql`ALTER TABLE scenario_alert_settings ADD COLUMN IF NOT EXISTS sector_move_pct numeric(15, 4)`);
    await db.execute(sql`ALTER TABLE scenario_alert_settings ADD COLUMN IF NOT EXISTS sector_move_updated_at timestamp`);
    logger.info("Schema check ok (scenario_alert_settings.sector_move_pct column)");
  } catch (err) {
    logger.error({ err }, "Failed to ensure schema (scenario_alert_settings.sector_move_pct column)");
  }

  try {
    await db.execute(sql`
      CREATE TABLE IF NOT EXISTS scenario_snapshots (
        id serial PRIMARY KEY,
        user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        snapshot_date text NOT NULL,
        data_alvo text NOT NULL,
        dias_restantes integer NOT NULL,
        p_empate numeric(15, 4) NOT NULL,
        valor_total_hoje numeric(15, 4) NOT NULL,
        custo_total numeric(15, 4) NOT NULL,
        p05 numeric(15, 4) NOT NULL,
        p50 numeric(15, 4) NOT NULL,
        p95 numeric(15, 4) NOT NULL,
        created_at timestamp NOT NULL DEFAULT now(),
        CONSTRAINT uq_scenario_snapshots_user_date UNIQUE (user_id, snapshot_date)
      )
    `);
    await db.execute(sql`CREATE INDEX IF NOT EXISTS idx_scenario_snapshots_user_id ON scenario_snapshots (user_id)`);
    logger.info("Schema check ok (scenario_snapshots table)");
  } catch (err) {
    logger.error({ err }, "Failed to ensure schema (scenario_snapshots table)");
  }

  try {
    await db.execute(sql`ALTER TABLE reports ADD COLUMN IF NOT EXISTS user_id integer REFERENCES users(id) ON DELETE CASCADE`);
    await db.execute(sql`CREATE INDEX IF NOT EXISTS idx_reports_user_id ON reports(user_id)`);
    logger.info("Schema check ok (reports.user_id ownership column)");
  } catch (err) {
    logger.error({ err }, "Failed to ensure schema (reports.user_id column)");
  }

  try {
    await db.execute(sql`
      CREATE TABLE IF NOT EXISTS scenario_resolutions (
        id serial PRIMARY KEY,
        user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        data_alvo text NOT NULL,
        valor_final numeric(15, 4) NOT NULL,
        custo_total numeric(15, 4) NOT NULL,
        p_empate_final numeric(15, 4) NOT NULL,
        bateu boolean NOT NULL,
        resolved_at timestamp NOT NULL DEFAULT now(),
        CONSTRAINT uq_scenario_resolutions_user_alvo UNIQUE (user_id, data_alvo)
      )
    `);
    await db.execute(sql`CREATE INDEX IF NOT EXISTS idx_scenario_resolutions_user_id ON scenario_resolutions (user_id)`);
    logger.info("Schema check ok (scenario_resolutions table)");
  } catch (err) {
    logger.error({ err }, "Failed to ensure schema (scenario_resolutions table)");
  }

  try {
    await db.execute(sql`
      CREATE TABLE IF NOT EXISTS sector_momentum (
        benchmark text PRIMARY KEY,
        momentum_annual_pct numeric(15, 4) NOT NULL,
        lookback_days integer NOT NULL,
        updated_at timestamp NOT NULL DEFAULT now()
      )
    `);
    logger.info("Schema check ok (sector_momentum table)");
  } catch (err) {
    logger.error({ err }, "Failed to ensure schema (sector_momentum table)");
  }

  try {
    await db.execute(sql`
      CREATE TABLE IF NOT EXISTS bounce_alert_firings (
        id serial PRIMARY KEY,
        alert_key text NOT NULL UNIQUE,
        fired_at timestamp NOT NULL DEFAULT now()
      )
    `);
    await db.execute(sql`CREATE INDEX IF NOT EXISTS idx_bounce_alert_firings_key ON bounce_alert_firings(alert_key)`);
    logger.info("Schema check ok (bounce_alert_firings table)");
  } catch (err) {
    logger.error({ err }, "Failed to ensure schema (bounce_alert_firings table)");
  }

  try {
    await db.execute(sql`
      CREATE TABLE IF NOT EXISTS squeeze_alert_firings (
        id serial PRIMARY KEY,
        alert_key text NOT NULL UNIQUE,
        fired_at timestamp NOT NULL DEFAULT now()
      )
    `);
    await db.execute(sql`CREATE INDEX IF NOT EXISTS idx_squeeze_alert_firings_key ON squeeze_alert_firings(alert_key)`);
    logger.info("Schema check ok (squeeze_alert_firings table)");
  } catch (err) {
    logger.error({ err }, "Failed to ensure schema (squeeze_alert_firings table)");
  }

  // Série diária de IV ATM por ticker: o gate de IV precisa julgar a IV de
  // hoje contra o histórico do PRÓPRIO papel, e o yfinance só devolve a cadeia
  // ao vivo -- não há série pra consultar nem como preencher retroativamente.
  // A única forma de ter IV Rank é começar a gravar.
  try {
    await db.execute(sql`
      CREATE TABLE IF NOT EXISTS iv_history (
        id serial PRIMARY KEY,
        ticker text NOT NULL,
        date text NOT NULL,
        atm_iv_pct numeric NOT NULL,
        atr_pct numeric,
        recorded_at timestamp NOT NULL DEFAULT now()
      )
    `);
    // Único por (ticker, dia): mais de uma run no mesmo dia acontece -- em
    // 31/07 saíram três -- e não pode duplicar a série.
    await db.execute(sql`CREATE UNIQUE INDEX IF NOT EXISTS idx_iv_history_ticker_date ON iv_history(ticker, date)`);
    await db.execute(sql`CREATE INDEX IF NOT EXISTS idx_iv_history_ticker ON iv_history(ticker)`);

    // Limpeza + trava de sanidade. A primeira run com gravação (03/08) escreveu
    // os sete ativos com atm_iv_pct entre 0,78 e 2,61 (NVDA em 2,08, sendo que
    // a IV real fica em 40-50) porque a média ATM engolia contrato com
    // impliedVolatility = 0 do yfinance como se fosse vol zero.
    //
    // Apagar em vez de conviver: iv_history existe pra virar IV Rank, que é
    // percentil contra o próprio histórico do papel -- uma linha absurda
    // desloca o percentil de todo dia futuro que olhar pra trás, e depois de
    // gravada não dá pra distinguir de leitura boa.
    //
    // O DELETE vem antes do CHECK de propósito: com linha fora da faixa ainda
    // na tabela, o ADD CONSTRAINT falha.
    await db.execute(sql`DELETE FROM iv_history WHERE atm_iv_pct < 5 OR atm_iv_pct > 500`);
    await db.execute(sql`
      DO $$
      BEGIN
        IF NOT EXISTS (
          SELECT 1 FROM pg_constraint WHERE conname = 'ck_iv_history_atm_iv_plausivel'
        ) THEN
          ALTER TABLE iv_history
            ADD CONSTRAINT ck_iv_history_atm_iv_plausivel
            CHECK (atm_iv_pct >= 5 AND atm_iv_pct <= 500);
        END IF;
      END $$;
    `);
    logger.info("Schema check ok (iv_history table)");
  } catch (err) {
    logger.error({ err }, "Failed to ensure schema (iv_history table)");
  }

  try {
    // Trava + cadência do ciclo de checkers via request (routes/checkers.ts).
    // Linha ÚNICA (id=1): a claim é um UPDATE atômico condicionado a
    // locked_until < now(), então só uma instância do Autoscale roda o ciclo
    // por vez -- estado in-process não serve, cada chamada pode cair numa
    // instância diferente (inclusive fantasmas de versões antigas).
    await db.execute(sql`
      CREATE TABLE IF NOT EXISTS checker_lease (
        id integer PRIMARY KEY CHECK (id = 1),
        locked_until timestamptz NOT NULL DEFAULT to_timestamp(0),
        cadence jsonb NOT NULL DEFAULT '{}'::jsonb
      )
    `);
    // owner_token: a trava expira sozinha, então quem solta precisa provar que
    // ainda é dono -- sem isso um ciclo que passou da validade liberava a trava
    // de OUTRA instância que já tinha assumido. Ver routes/checkers.ts.
    await db.execute(sql`ALTER TABLE checker_lease ADD COLUMN IF NOT EXISTS owner_token text`);
    // last_cycle_at: alimenta o vigia (lib/checker-watchdog.ts). Com os timers
    // desligados, um gatilho externo que pare de chamar não produz erro nenhum
    // -- os alertas simplesmente somem em silêncio. Este carimbo é o que
    // permite perceber.
    await db.execute(sql`ALTER TABLE checker_lease ADD COLUMN IF NOT EXISTS last_cycle_at timestamptz`);
    await db.execute(sql`INSERT INTO checker_lease (id) VALUES (1) ON CONFLICT (id) DO NOTHING`);
    logger.info("Schema check ok (checker_lease)");
  } catch (err) {
    logger.error({ err }, "Failed to ensure schema (checker_lease)");
  }

  try {
    await db.execute(sql`
      CREATE TABLE IF NOT EXISTS entry_exit_study_targets (
        id serial PRIMARY KEY,
        user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        ticker text NOT NULL,
        target_price numeric(15, 4) NOT NULL,
        target_date text NOT NULL,
        exit_alert_id integer REFERENCES alerts(id) ON DELETE SET NULL,
        entry_avg_low_alert_id integer REFERENCES alerts(id) ON DELETE SET NULL,
        entry_min_low_alert_id integer REFERENCES alerts(id) ON DELETE SET NULL,
        active boolean NOT NULL DEFAULT true,
        created_at timestamp NOT NULL DEFAULT now()
      )
    `);
    await db.execute(sql`CREATE INDEX IF NOT EXISTS idx_entry_exit_study_targets_user_id ON entry_exit_study_targets (user_id)`);
    await db.execute(sql`
      CREATE TABLE IF NOT EXISTS entry_exit_study_history (
        id serial PRIMARY KEY,
        target_id integer NOT NULL REFERENCES entry_exit_study_targets(id) ON DELETE CASCADE,
        calc_date text NOT NULL,
        current_price numeric(15, 4) NOT NULL,
        avg_low_1y numeric(15, 4),
        min_low_1y numeric(15, 4),
        avg_low_6m numeric(15, 4),
        min_low_6m numeric(15, 4),
        vol_annual numeric(15, 4),
        beta_sector numeric(15, 4),
        prob_reach_target numeric(15, 4),
        created_at timestamp NOT NULL DEFAULT now(),
        CONSTRAINT uq_entry_exit_study_history_target_date UNIQUE (target_id, calc_date)
      )
    `);
    await db.execute(sql`CREATE INDEX IF NOT EXISTS idx_entry_exit_study_history_target_id ON entry_exit_study_history (target_id)`);
    logger.info("Schema check ok (entry_exit_study_targets/history tables)");
  } catch (err) {
    logger.error({ err }, "Failed to ensure schema (entry_exit_study_targets/history tables)");
  }

  try {
    await db.execute(sql`ALTER TABLE entry_exit_study_history ADD COLUMN IF NOT EXISTS news jsonb`);
    await db.execute(sql`ALTER TABLE entry_exit_study_history ADD COLUMN IF NOT EXISTS earnings_date text`);
    await db.execute(sql`ALTER TABLE entry_exit_study_history ADD COLUMN IF NOT EXISTS news_sentiment text`);
    await db.execute(sql`ALTER TABLE entry_exit_study_history ADD COLUMN IF NOT EXISTS news_sentiment_reason text`);
    await db.execute(sql`
      CREATE TABLE IF NOT EXISTS entry_exit_study_resolutions (
        id serial PRIMARY KEY,
        target_id integer NOT NULL REFERENCES entry_exit_study_targets(id) ON DELETE CASCADE,
        ticker text NOT NULL,
        target_price numeric(15, 4) NOT NULL,
        target_date text NOT NULL,
        final_price numeric(15, 4) NOT NULL,
        bateu boolean NOT NULL,
        prob_final numeric(15, 4),
        resolved_at timestamp NOT NULL DEFAULT now(),
        CONSTRAINT uq_entry_exit_study_resolutions_target UNIQUE (target_id)
      )
    `);
    await db.execute(sql`CREATE INDEX IF NOT EXISTS idx_entry_exit_study_resolutions_target_id ON entry_exit_study_resolutions (target_id)`);
    logger.info("Schema check ok (entry_exit_study_history.news column + entry_exit_study_resolutions table)");
  } catch (err) {
    logger.error({ err }, "Failed to ensure schema (entry_exit_study_history.news column + entry_exit_study_resolutions table)");
  }
}
