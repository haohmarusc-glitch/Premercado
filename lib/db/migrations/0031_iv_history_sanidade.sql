-- Limpa a IV implausível já gravada e impede que volte a entrar.
--
-- A primeira run com gravação de série (03/08) escreveu os sete ativos com
-- atm_iv_pct entre 0,78 e 2,61 -- NVDA em 2,08 quando a IV real fica na casa
-- de 40-50%. Causa em tools.py: o yfinance devolve impliedVolatility = 0 para
-- contrato sem cotação, e a média dos strikes ATM tratava esse zero como
-- observação real de volatilidade zero (corrigido em _atm_iv_pct).
--
-- Por que apagar em vez de deixar: iv_history existe pra virar IV Rank, que é
-- percentil contra o próprio histórico do papel. Uma linha absurda não é só
-- ruído -- ela desloca o percentil de TODO dia futuro que olhar pra trás, e
-- depois de gravada não há como distinguir de uma leitura boa. Sete linhas
-- perdidas custam sete dias de série; sete linhas erradas custam o rank.
DELETE FROM iv_history WHERE atm_iv_pct < 5 OR atm_iv_pct > 500;

-- Terceira barreira (além de _atm_iv_pct na origem e _registrar_iv na
-- gravação). Está no banco porque é o único ponto que nenhum caminho de
-- escrita futuro consegue contornar por esquecimento.
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
