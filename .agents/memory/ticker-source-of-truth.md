---
name: Tickers — fonte da verdade
description: Decisão de onde vem a lista de ativos monitorados pelo agente
---

# Lista de tickers monitorados: settings DB é a fonte da verdade

A tabela `settings.tickers` (Postgres) é a fonte única da lista de ativos. O
runner Node lê essa lista e a repassa ao subprocess Python; cotações e relatório
salvo usam a mesma lista.

**Why:** antes havia descompasso — o agente lia uma constante hardcoded e
ignorava a UI de Settings, então adicionar tickers pela tela não tinha efeito.

**How to apply:** para mudar a cobertura de ativos, altere `settings.tickers`
(UI ou UPDATE no DB) — nunca volte a hardcodear tickers. Novos tickers que usam
EDGAR exigem o CIK oficial (a SMCI já esteve com CIK errado); confira em
https://www.sec.gov/files/company_tickers.json.
