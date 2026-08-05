---
name: Schema drift no Publishing
description: db push do Publishing compara o banco com o schema Drizzle e propõe DROP do que não estiver declarado
---

Regra: toda tabela/coluna criada via SQL cru em runtime (ensure-schema.ts) precisa de uma declaração Drizzle equivalente em `lib/db/src/schema/premarket.ts`.

**Why:** em 05/08/2026 o painel Publishing propôs `DROP COLUMN owner_token/last_cycle_at` na `checker_lease` porque a tabela só existia via SQL cru — o db push trata o que não está no schema como candidato a apagar. As colunas eram essenciais (trava cross-instância e vigia).

**How to apply:** ao adicionar qualquer objeto no ensure-schema, espelhar a declaração no schema Drizzle na mesma mudança. Se o aviso de DROP aparecer numa publicação, é seguro prosseguir apenas para colunas de estado interno recriadas no boot; nunca para dados de usuário.

**Como VERIFICAR que a coluna é recriada no boot** — não decida de memória, procure a linha:

```
grep -n "ADD COLUMN IF NOT EXISTS <coluna>" artifacts/api-server/src/lib/ensure-schema.ts
```

Achou: a migração apaga e o próximo boot recria; o conteúdo perdido é estado efêmero. Não achou: **não aprove**, a coluna não volta sozinha.

Essa distinção é o que separa este caso dos dois incidentes anteriores documentados no `replit.md`, em que migrações do mesmo painel destruíram tabelas de produção. A regra nunca foi "confie no painel" nem "nunca publique" — é olhar o que a migração toca antes de clicar. Vinte minutos de conferência contra restaurar backup.
