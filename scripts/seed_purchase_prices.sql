-- Atualiza purchase_price de cada compra com base no avg_cost da posicao
-- Fonte: portfolio.py (avg_price por ticker)

UPDATE portfolio_purchases pp
SET purchase_price = pos.avg_cost
FROM portfolio_positions pos
WHERE pp.position_id = pos.id
  AND pp.purchase_price IS NULL;

-- Confirma resultado
SELECT pos.ticker, pp.purchase_date, pp.amount, pp.purchase_price
FROM portfolio_purchases pp
JOIN portfolio_positions pos ON pos.id = pp.position_id
ORDER BY pos.ticker, pp.purchase_date;
