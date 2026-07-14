"""
Entry point called as: python3 -m agent.run_agent
Writes STEP: lines to stdout for progress tracking, and REPORT: <content> at the end.

AGENT_MODE env var controls the run type:
  daily     (default) — full pre-market analysis
  premarket           — fast intraday flash scan
"""

import json
import os
import signal
import sys

from . import agent as a
from . import config
from .provider import get_run_usage


def progress(step: str) -> None:
    print(f"STEP:{step}", flush=True)


def emit_usage() -> None:
    """Emite USAGE:{json} no stdout com tokens/custo acumulados da run.
    Impressa ANTES de REPORT: (o runner captura tudo após 'REPORT:'), e também
    em caso de erro — chamadas parciais já custaram dinheiro."""
    try:
        usage = get_run_usage()
        if usage["calls"] > 0:
            print("USAGE:" + json.dumps(usage, ensure_ascii=False), flush=True)
    except Exception:
        pass


def _handle_sigterm(signum, frame) -> None:
    """runner.ts mata o processo com SIGTERM ao estourar o timeout de 10 min.
    Sem este handler, o except mais abaixo nunca roda e o custo já gasto nas
    chamadas parciais (o run pode ter feito 20+ turnos antes de travar) some
    silenciosamente — a run fica registrada como falha sem custo nenhum."""
    emit_usage()
    print("ERROR: agent killed (timeout)", file=sys.stderr, flush=True)
    sys.exit(1)


signal.signal(signal.SIGTERM, _handle_sigterm)


if __name__ == "__main__":
    # Fail-fast: se a chave Anthropic está presente mas claramente malformada,
    # erra aqui com mensagem clara em vez de deixar o provider.py estourar no
    # meio de um turno do agente (e só então cair no fallback chain, mascarando
    # o problema real). Não bloqueia se ANTHROPIC_API_KEY estiver vazia — nesse
    # caso o FallbackClient segue para o próximo provider configurado.
    _key = os.environ.get("ANTHROPIC_API_KEY", "")
    if _key and not config.validate_anthropic_key():
        print(
            "ERROR: ANTHROPIC_API_KEY presente mas não passa validação de formato "
            "(esperado prefixo '' e tamanho mínimo). Verifique a env var.",
            file=sys.stderr,
            flush=True,
        )
        sys.exit(1)

    mode = os.environ.get("AGENT_MODE", "daily")
    try:
        if mode == "premarket":
            report = a.run_premarket(progress_callback=progress)
        elif mode in ("portfolio", "coal", "ai"):
            # Garante os tickers corretos mesmo que o Node.js não os passe via env var
            if mode == "coal" and not os.environ.get("AGENT_PORTFOLIO_TICKERS"):
                os.environ["AGENT_PORTFOLIO_TICKERS"] = "HCC,AMR,ARCH,CEIX,BTU"
            elif mode == "ai" and not os.environ.get("AGENT_PORTFOLIO_TICKERS"):
                os.environ["AGENT_PORTFOLIO_TICKERS"] = "NVDA,ARM,GOOGL,META,MSFT,AMD,PLTR,SMCI"
            report = a.run_portfolio(progress_callback=progress)
        else:
            report = a.run(progress_callback=progress)
        emit_usage()
        print("REPORT:" + report, flush=True)
        sys.exit(0)
    except Exception as e:
        emit_usage()
        print(f"ERROR: {e}", file=sys.stderr, flush=True)
        sys.exit(1)
