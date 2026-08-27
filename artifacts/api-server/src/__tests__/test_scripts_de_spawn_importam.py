"""Todo script que o servidor spawna tem de IMPORTAR no contexto do spawn.

Incidente real (27/08/2026): o /trend caiu em produção com

    ImportError: attempted relative import with no known parent package

A #410 fez o get_trend importar `fala_do_papel` de news_sources -- e
news_sources puxa `config` e `cache` por import RELATIVO, que só resolve
dentro do pacote. O get_trend roda por spawn (sys.path[0] = src/agent, sem
pacote); o braço de imports planos, que é o que realmente executa lá, morreu
no import. A suíte inteira não viu porque os testes importam via pacote --
o contexto do spawn não tinha teste nenhum.

O mesmo teste, no primeiro dia, achou um segundo quebrado: folego_de_caixa
(via config.py só-pacote), falhando em silêncio no folego-checker desde a
criação.

A armadilha estrutural que este arquivo vigia: `agent.py` (o MÓDULO) faz
sombra ao pacote `agent` quando src/agent está no sys.path -- então
`from agent import x` NUNCA funciona no spawn, e todo script spawnado
precisa da perna de imports planos, com dependências planas até o fundo.
"""

import os
import pathlib
import subprocess
import sys

_AGENT_DIR = pathlib.Path(__file__).resolve().parent.parent / "agent"

# A lista espelha os .py citados nos .ts do servidor (grep '"*.py"').
# get_risk.py fica de fora: só existe como rótulo num teste de spawn do lado
# TS, não é script real.
SCRIPTS_SPAWNADOS = [
    "atualizar_correlacoes", "atualizar_earnings", "backtest",
    "capex_hyperscalers", "ciclo_volatilidade", "confluence_engine",
    "earnings_reaction_analysis", "earnings_window", "entry_exit_study",
    "folego_de_caixa", "get_alt_data", "get_earnings", "get_fundamentals",
    "get_historical_price", "get_institutional_filings", "get_macro",
    "get_news_feed", "get_options_chain", "get_performance",
    "get_scenario_params", "get_technicals", "get_ticker_snapshot",
    "get_trend", "macro_risk_snapshot", "padroes_estatisticos",
    "risk_manager",
]


def test_todo_script_spawnado_importa_no_contexto_do_spawn():
    """Um subprocesso só, importando todos em sequência: pandas/yfinance
    carregam uma vez e o teste fica em segundos. O nome do primeiro que
    falhar sai no stderr. get_technicals redireciona o stdout do processo
    (os.dup2) no import -- por isso subprocess, nunca import direto."""
    faltando = [s for s in SCRIPTS_SPAWNADOS
                if not (_AGENT_DIR / f"{s}.py").exists()]
    assert not faltando, (
        f"scripts citados nos .ts que não existem: {faltando} -- ou o .ts "
        f"aponta pro nada, ou renomearam o arquivo sem atualizar a lista")

    codigo = "\n".join(
        f"import {s}\nprint('ok {s}', flush=True)" for s in SCRIPTS_SPAWNADOS)
    r = subprocess.run(
        [sys.executable, "-c", codigo],
        cwd=str(_AGENT_DIR),
        env={**os.environ, "PYTHONPATH": str(_AGENT_DIR)},
        capture_output=True, text=True, timeout=180,
    )
    assert r.returncode == 0, (
        "import quebrado em modo spawn -- último ok foi "
        f"'{(r.stdout.strip().splitlines() or ['nenhum'])[-1]}'.\n"
        f"stderr:\n{r.stderr[-1500:]}"
    )
