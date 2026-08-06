# Chat tools + rich context — wiring restante

`memory.py` já está no `main` (rich_context_block + recent_context filtrado).
`portfolio_snapshot.py` está nesta branch.

## Ainda falta aplicar no local / neste PR (arquivos grandes)

### 1. `tools.py`

Import e DISPATCH:

```python
from .portfolio_snapshot import get_portfolio_snapshot
```

Em `DISPATCH`:

```python
"get_portfolio_snapshot": get_portfolio_snapshot,
```

Schema em `TOOLS` (antes de `list_alerts`):

```python
{
    "name": "get_portfolio_snapshot",
    "description": (
        "Snapshot das posições abertas da carteira: quantidade, custo médio, "
        "investido e (por padrão) preço atual + P&L não realizado."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "include_prices": {
                "type": "boolean",
                "description": "True = preço ao vivo + P&L. False = só qty/custo.",
                "default": True,
            },
        },
        "required": [],
    },
},
```

### 2. `agent.py` — `_CHAT_TOOL_NAMES`

Adicionar:

```python
"get_portfolio_snapshot",
"list_alerts", "create_alert", "delete_alert",
"get_scenario_status", "get_exit_plan_items",
```

### 3. `agent.py` — `build_chat_prompt()`

- Chamar `memory.rich_context_block()`
- Chamar `memory.recent_context(portfolio_only=True, portfolio_tickers=...)`
- Incluir ferramentas de ação no texto do prompt
- Guardrails: max 6 tools, max 2 create_alert, list_alerts antes de create

Arquivos completos ficam em workspace local após as edições:
- `artifacts/api-server/src/agent/agent.py`
- `artifacts/api-server/src/agent/tools.py`
- `artifacts/api-server/src/agent/memory.py`
