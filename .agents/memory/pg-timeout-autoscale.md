---
name: Timeouts de conexão do pool Postgres
description: Os erros de banco intermitentes em produção são o connectionTimeoutMillis do pool estourando numa rajada de conexões novas — não é falha de rede, do provedor, nem "CPU cortada fora de request"
---

# Erro de banco em produção = teto de conexão do pool, não rede

**O que se vê:** `_DrizzleQueryError` em queries simples, com

```
caused by: Error: Connection terminated due to connection timeout
caused by: Error: Connection terminated unexpectedly
```

**A leitura de dentro para fora está errada.** `Connection terminated
unexpectedly` NÃO é a rede caindo. Em `pg-pool/index.js:255`, quando
`connectionTimeoutMillis` vence, o próprio pool faz
`client.connection.stream.destroy()`; o `pg` emite "terminated unexpectedly"
porque **nós** matamos o socket, e só então o pg-pool embrulha aquilo como
"connection timeout". O provedor não fez nada.

**Causa medida (05/08/2026):** a run diária dispara 7 `save_observation` em
paralelo. Entre rajadas o pool esvazia (`idleTimeoutMillis` no default de 10s),
então são 7 conexões NOVAS simultâneas, cada uma com handshake TLS, num
container disputado. Na mesma rajada: 3 estouraram o teto de 5s e viraram 500;
as 5 que passaram levaram até 4,17s — encostando no teto. Corrigido subindo
`connectionTimeoutMillis` para 15s (PR #228).

**Não atribua a "CPU cortada fora de request".** Os 500 aconteceram DENTRO de
`POST /api/observations/internal`, num processo que estava servindo tráfego no
mesmo minuto. É disputa por CPU numa rajada, que acontece com ou sem request —
não ausência de CPU por estar fora de um. (No mesmo dia, um `run_checkers`
disparado por TIMER bootou em 0,05s: o modelo "request = CPU cheia, timer =
throttle" não descreve o que os logs mostram.)

**Sobre os erros antigos com `cause: {}` vazio:** não é possível afirmar que
são o mesmo fenômeno — `cause` vazio é literalmente a ausência do dado. Aquilo
era defeito de LOG, não de banco: os checkers logavam sob a chave `e`, e o
serializador de erro do pino só se aplica à chave `err` (corrigido na PR #226).
Depois que essa correção estiver em produção, os mesmos erros vão dizer o que
são. Até lá, tratar como desconhecido.

**How to apply:** ao ver erro de conexão no pool, comece perguntando quantas
conexões NOVAS foram pedidas ao mesmo tempo, não se a rede caiu. Rajada de N
inserts paralelos com pool frio = N handshakes concorrentes. E ao ler o
`cause`, lembre que o erro mais interno pode ser efeito do nosso próprio
timeout, não a causa raiz.

**Relacionado:** o mesmo padrão (teto abaixo do tempo real de partida) já tinha
mordido em `get_quotes` (60s) e no checker de carteira (30s), ambos corrigidos
para 120s na PR #227.
