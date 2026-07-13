---
name: OPERATOR_API_KEY — Secrets de Deployment vs. Shell
description: Por que o Bearer OPERATOR_API_KEY pode dar 401 mesmo com a secret "configurada" nos dois lugares
---

# OPERATOR_API_KEY dando 401 mesmo com a secret configurada

`requireAuth` (`artifacts/api-server/src/middleware/require-auth.ts`) aceita
`Authorization: Bearer <OPERATOR_API_KEY>` como caminho alternativo ao cookie
de sessão — usado pelo agente Python (`tools.py`, `_internal_headers()`) e por
`carteira.py`. Se isso der 401 mesmo com a secret aparentemente presente,
checar **nesta ordem** antes de suspeitar de bug no código:

1. **Replit Deployments têm Secrets separadas do workspace de dev.** A aba
   "Secrets" do editor não é a mesma coisa que "Production app secrets" na
   tela do Deployment — dá pra ter a chave certa num lugar e uma diferente
   (ou nenhuma) no outro. Sempre confirme os dois.
2. **Editar a secret de um Deployment exige republicar** (não só reiniciar o
   Run do editor) pra ela chegar no processo publicado.
3. **O Shell do Replit cacheia env vars no início da sessão.** Se você trocar
   uma secret com uma aba de Shell já aberta, `$OPERATOR_API_KEY` nela
   continua com o valor antigo até abrir uma aba nova. Isso gera o sintoma
   mais enganoso: você jura que os dois valores são "iguais" (comparando
   visualmente) e mesmo assim o teste falha, porque o Shell nunca pegou o
   valor novo pra comparar de verdade.
4. **Nunca comparar secrets "visualmente".** Compare por tamanho, sem nunca
   imprimir o valor: `echo -n "$OPERATOR_API_KEY" | wc -c`. Um mismatch de 1
   caractere é invisível a olho nu mas quebra a comparação exata (`===`) do
   `requireAuth`.

**Diagnóstico mais rápido** (caso o problema volte): adicionar um log
temporário em `requireAuth` logando só `operatorKey?.length` e
`authHeader?.length` (nunca os valores em si) no momento da tentativa —
resolve em 1-2 rodadas o "qual dos dois valores está errado" sem expor
segredo nenhum. Ver PR #48 no repositório pro precedente completo (log
adicionado, causa raiz encontrada — chave do Shell desatualizada —, log
removido depois de confirmado o fix).
