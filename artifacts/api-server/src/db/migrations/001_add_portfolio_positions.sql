CREATE TABLE IF NOT EXISTS portfolio_positions (
    id              SERIAL PRIMARY KEY,
    ticker          VARCHAR(10)  NOT NULL,
    shares          INTEGER      NOT NULL,
    purchase_price  REAL         NOT NULL,
    purchase_date   TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    high_since_purchase REAL,
    low_since_purchase  REAL,
    high_date       TIMESTAMP,
    low_date        TIMESTAMP,
    alert_high_pct  REAL,
    alert_low_pct   REAL,
    notes           VARCHAR(500),
    created_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_portfolio_ticker        ON portfolio_positions (ticker);
CREATE INDEX IF NOT EXISTS idx_portfolio_purchase_date ON portfolio_positions (purchase_date);
