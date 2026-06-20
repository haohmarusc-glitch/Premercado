"""
Entry point called as: python3 -m agent.run_agent
Writes STEP: lines to stdout for progress tracking, and REPORT: <content> at the end.

AGENT_MODE env var controls the run type:
  daily     (default) — full pre-market analysis
  premarket           — fast intraday flash scan
"""

import os
import sys

from . import agent as a
from . import config


def progress(step: str) -> None:
    print(f"STEP:{step}", flush=True)


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
        elif mode in ("portfolio", "coal"):
            report = a.run_portfolio(progress_callback=progress)
        else:
            report = a.run(progress_callback=progress)
        print("REPORT:" + report, flush=True)
        sys.exit(0)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr, flush=True)
        sys.exit(1)
