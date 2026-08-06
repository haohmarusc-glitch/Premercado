#!/usr/bin/env python3
"""Aplica wiring de chat tools + rich context em agent.py e tools.py.

Uso (branch feat/chat-tools-rich-context, a partir da raiz do repo):
  python3 artifacts/api-server/src/agent/apply_chat_tools_wire.py

Idempotente.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent
AGENT = ROOT / "agent.py"
TOOLS = ROOT / "tools.py"


def _must(path: Path) -> str:
    if not path.exists():
        raise SystemExit(f"arquivo nao encontrado: {path}")
    return path.read_text(encoding="utf-8")


def wire_tools(src: str) -> str:
    import_line = "from .portfolio_snapshot import get_portfolio_snapshot\n"
    if "from .portfolio_snapshot import get_portfolio_snapshot" not in src:
        needle = "from .http_retry import"
        if needle in src:
            idx = src.find(needle)
            end = src.find("\n", idx)
            src = src[: end + 1] + import_line + src[end + 1 :]
        else:
            lines = src.splitlines(keepends=True)
            insert_at = 0
            for i, line in enumerate(lines):
                if line.startswith("from .") or line.startswith("import "):
                    insert_at = i + 1
            lines.insert(insert_at, import_line)
            src = "".join(lines)

    key = '"get_portfolio_snapshot": get_portfolio_snapshot'
    if key not in src:
        old = '"save_observation": save_observation,'
        if old not in src:
            raise SystemExit("DISPATCH: nao achei save_observation")
        src = src.replace(old, old + "\n    " + key + ",", 1)

    if '"name": "get_portfolio_snapshot"' not in src:
        schema_lines = [
            "{",
            '        "name": "get_portfolio_snapshot",',
            '        "description": (',
            '            "Snapshot das posicoes abertas da carteira: quantidade, custo medio, "',
            '            "investido e (por padrao) preco atual + P&L nao realizado. Use quando "',
            '            "o usuario perguntar sobre a carteira, patrimonio, quanto tem em um "',
            '            "ticker ou resultado nao realizado."',
            "        ),",
            '        "input_schema": {',
            '            "type": "object",',
            '            "properties": {',
            '                "include_prices": {',
            '                    "type": "boolean",',
            '                    "description": "True = preco ao vivo + P&L. False = so qty/custo.",',
            '                    "default": True,',
            "                },",
            "            },",
            '            "required": [],',
            "        },",
            "    },",
            "    ",
        ]
        schema = "\n".join(schema_lines)
        marker = '"name": "list_alerts"'
        pos = src.find(marker)
        if pos < 0:
            raise SystemExit("TOOLS: nao achei list_alerts")
        brace = src.rfind("{", 0, pos)
        src = src[:brace] + schema + src[brace:]

    return src


def _chat_tools_block() -> str:
    return "\n".join(
        [
            "_CHAT_TOOL_NAMES = {",
            "    # Analise",
            '    "get_stock_data", "get_news", "get_technical_indicators",',
            '    "detect_candle_patterns", "get_fear_greed_index", "get_sector_performance",',
            '    "get_short_interest", "get_analyst_ratings", "get_options_data",',
            '    "get_geopolitical_news", "check_squeeze_setup",',
            '    "get_macro_indicators", "get_retail_sentiment", "get_fundamentals_valuation",',
            '    "get_insider_trades",',
            "    # Acao / estado da carteira (liberadas no chat com guardrails no prompt)",
            '    "get_portfolio_snapshot",',
            '    "list_alerts", "create_alert", "delete_alert",',
            '    "get_scenario_status", "get_exit_plan_items",',
            "}",
        ]
    )


def _build_chat_prompt_fn() -> str:
    return (
        "def build_chat_prompt() -> str:\n"
        "    today = _today_brt_str()\n"
        "    now = _now_brt_str()\n"
        "    portfolio_tickers = list(config.PORTFOLIO_TICKERS)\n"
        "    portfolio_line = (\n"
        '        f"Posicoes da carteira (ABERTAS agora, e isso que \\"a carteira\\"/\\"minhas "\n'
        '        f"posicoes\\" significa quando o usuario perguntar): {\', \'.join(portfolio_tickers)}."\n'
        "        if portfolio_tickers\n"
        '        else "Posicoes da carteira: nenhuma posicao aberta no momento."\n'
        "    )\n"
        "    try:\n"
        "        rich = memory.rich_context_block()\n"
        "    except Exception as e:\n"
        '        rich = f"(contexto rico indisponivel: {e})"\n'
        "    try:\n"
        "        mem = memory.recent_context(portfolio_only=True, portfolio_tickers=portfolio_tickers)\n"
        "    except Exception as e:\n"
        '        mem = f"(memoria indisponivel: {e})"\n'
        "\n"
        '    return f"""Voce e um analista de acoes conversacional em {today} ({now} BRT).\n'
        'Ativos monitorados (cobertura geral, NAO e a carteira do usuario): {", ".join(config.TICKERS)}.\n'
        "{portfolio_line}\n"
        "\n"
        'Regra importante: se o usuario perguntar sobre "a carteira", "minha carteira",\n'
        '"minhas posicoes" ou similar, responda SOMENTE sobre os tickers listados acima\n'
        "como posicoes da carteira -- nunca inclua um ticker de cobertura geral (ou\n"
        "citado na memoria de dias anteriores, que cobre setores/cestas inteiras) que\n"
        "nao esteja nessa lista, mesmo que ele tenha aparecido em analises recentes.\n"
        "Se o usuario pedir um ticker especifico fora da carteira, responda normalmente\n"
        "sobre ele, so nao o rotule como posicao da carteira.\n"
        "\n"
        "Ferramentas disponiveis:\n"
        "- Analise: get_stock_data, get_news, get_technical_indicators, detect_candle_patterns,\n"
        "  get_fear_greed_index, get_sector_performance, get_short_interest, get_analyst_ratings,\n"
        "  get_options_data, get_geopolitical_news, check_squeeze_setup, get_macro_indicators,\n"
        "  get_retail_sentiment, get_fundamentals_valuation, get_insider_trades,\n"
        "  get_gamma_exposure (max 1x/resposta), get_earnings_transcript\n"
        "- Carteira / acoes: get_portfolio_snapshot (qty, custo, P&L), list_alerts, create_alert,\n"
        "  delete_alert, get_scenario_status (chance de empatar), get_exit_plan_items\n"
        "  (metas/janelas de venda cadastradas)\n"
        "\n"
        "Regras:\n"
        "- Responda a pergunta do usuario de forma direta e concisa.\n"
        "- Use ferramentas apenas quando necessario. Maximo 6 chamadas por resposta.\n"
        "- Preferencias de acao: antes de create_alert, chame list_alerts (evite duplicata).\n"
        "  No maximo 2 create_alert por resposta; sempre explique o motivo ao usuario.\n"
        "  delete_alert so com motivo claro (nivel superado / obsoleto).\n"
        "- NAO use: save_observation, search_edgar_filings, read_filing,\n"
        "  check_market_alerts, detect_sector_contagion, update_exit_plan_item,\n"
        "  create_exit_plan_item.\n"
        "- Formate em Markdown. Seja factual; cite numeros.\n"
        "\n"
        "=== ESTADO ATUAL (carteira / alertas / cenario) ===\n"
        "{rich}\n"
        "=== FIM DO ESTADO ===\n"
        "\n"
        "=== MEMORIA RECENTE (so tickers da carteira, 1 obs/ticker/dia) ===\n"
        "{mem}\n"
        '=== FIM DA MEMORIA ==="""\n'
        "\n"
    )


def wire_agent(src: str) -> str:
    chat_idx = src.find("_CHAT_TOOL_NAMES")
    if chat_idx >= 0:
        window = src[chat_idx : chat_idx + 900]
        if '"get_portfolio_snapshot"' not in window:
            old_start = src.find("_CHAT_TOOL_NAMES = {")
            old_end = src.find("}", old_start) + 1
            src = src[:old_start] + _chat_tools_block() + src[old_end:]

    if "rich_context_block" not in src:
        start = src.find("def build_chat_prompt()")
        if start < 0:
            raise SystemExit("nao achei build_chat_prompt")
        end = src.find("\ndef run_tool(", start)
        if end < 0:
            raise SystemExit("nao achei run_tool apos build_chat_prompt")
        src = src[:start] + _build_chat_prompt_fn() + src[end:]

    return src


def main() -> None:
    tools_src = _must(TOOLS)
    agent_src = _must(AGENT)

    tools_new = wire_tools(tools_src)
    agent_new = wire_agent(agent_src)

    changed = []
    if tools_new != tools_src:
        TOOLS.write_text(tools_new, encoding="utf-8")
        changed.append("tools.py")
    if agent_new != agent_src:
        AGENT.write_text(agent_new, encoding="utf-8")
        changed.append("agent.py")

    if not changed:
        print("OK — wiring ja aplicado (nada a fazer).")
    else:
        print("OK — atualizado:", ", ".join(changed))
        print("Proximo: git add + commit + push nesta branch.")


if __name__ == "__main__":
    main()
