-- Change up_alert_pcts column default to [10,15,20,30,40,50]
ALTER TABLE portfolio_positions
  ALTER COLUMN up_alert_pcts SET DEFAULT ARRAY[10,15,20,30,40,50]::integer[];

-- Backfill existing rows that still have the old default [15,20,30,40]
UPDATE portfolio_positions
SET up_alert_pcts = ARRAY[10,15,20,30,40,50]::integer[]
WHERE up_alert_pcts = ARRAY[15,20,30,40]::integer[];
