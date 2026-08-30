#!/usr/bin/env python3
"""
provider_preflight.py — Checagem de saúde das fontes de dado ANTES de subir
uma versão nova (deploy manual ou `docker compose up -d --build`).

## Por que isto, e por que agora

O deploy de hoje sobe a imagem e assume que o yfinance vai responder — se o
Yahoo mudou algo (formato de resposta, bloqueio por IP do VPS, `fast_info`
quebrado) o problema só aparece depois, nos logs de produção, no primeiro
checker que rodar. Este script existe para descobrir isso ANTES, com um exit
code que o pipeline de deploy pode checar.

Custo baixo e conhecido: yfinance (sem cota) e UMA chamada da Alpha Vantage
com um ticker líquido e conhecido (AAPL), mais uma checagem de que o diretório
de cache em disco é gravável (mesmo requisito de `hist_cache.py`). A chamada da
Alpha Vantage sai do mesmo orçamento diário do fallback — rodar o preflight a
cada deploy custa 1 das 15 do dia.

Como consulta as duas fontes para o MESMO ticker, é também o único lugar do
sistema onde a comparação de fechamento entre elas roda no caminho feliz —
com yfinance vivo, `get_daily_history` retorna antes de tocar na fonte
externa.

## Uso

    python -m agent.provider_preflight
    echo $?   # 0 = pelo menos uma fonte de histórico E de cotação funcionam
              # 1 = degradado (uma das duas funciona, mas não as duas)
              # 2 = crítico (nenhuma fonte responde) — não implantar

Sugestão de integração: rodar como passo extra no CI ou como healthcheck
manual antes do `docker compose up -d --build` em produção. Não é bloqueante
por padrão fora de CI explícito — rede pode estar fora do ar no ambiente de
build sem que isso signifique nada sobre produção; o valor está no relatório,
não em travar o deploy sozinho.
"""
from __future__ import annotations

import json
import os
import sys
import time

from . import market_data_provider as mdp
from . import provider_health
from . import alpha_vantage_provider

PROBE_TICKER = os.environ.get("AGENT_PREFLIGHT_TICKER", "AAPL")


def _check_cache_dir_writable() -> tuple[bool, str]:
    path = os.environ.get("AGENT_HIST_CACHE_DIR", "/tmp/premercado_hist_cache")
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".preflight_probe")
        with open(probe, "w") as f:
            f.write(str(time.time()))
        os.remove(probe)
        return True, path
    except Exception as ex:
        return False, f"{path}: {ex}"


def run() -> dict:
    # Zera o disjuntor antes de medir — este script quer saber o estado REAL
    # da rede agora, não herdar um cooldown de uma falha de 4 minutos atrás.
    provider_health.reset("yfinance")
    provider_health.reset("alphavantage")
    # NÃO zera o orçamento diário (chave `_orcamento:alphavantage`): rodar o
    # preflight várias vezes tem que aparecer no contador, senão ele esconde
    # justamente o consumo que existe para vigiar.

    report: dict = {"ticker": PROBE_TICKER, "checks": {}}

    writable, detail = _check_cache_dir_writable()
    report["checks"]["cache_dir_writable"] = {"ok": writable, "detail": detail}

    t0 = time.time()
    hist = mdp.get_daily_history(PROBE_TICKER, period="3mo")
    report["checks"]["yfinance_history"] = {
        "ok": hist.source == "yfinance",
        "source_used": hist.source,
        "elapsed_s": round(time.time() - t0, 2),
        "warnings": hist.warnings,
    }

    t0 = time.time()
    externo_df = alpha_vantage_provider.fetch_daily_history(PROBE_TICKER, period="3mo")
    report["checks"]["fonte_externa_history"] = {
        "ok": externo_df is not None and not externo_df.empty,
        "provedor": "alphavantage",
        "elapsed_s": round(time.time() - t0, 2),
        "orcamento_usado_hoje": provider_health.orcamento_usado("alphavantage"),
    }

    # As duas fontes na mão ao mesmo tempo: compara o último fechamento. É a
    # única checagem de sanidade do sistema que enxerga divergência ANTES de
    # o número virar stop ou tamanho de posição.
    divergencia: list[str] = []
    if hist.ok and externo_df is not None and not externo_df.empty:
        mdp._cross_check_last_close(PROBE_TICKER, externo_df, hist.df, divergencia)  # noqa: SLF001
    report["checks"]["cross_check"] = {
        "ok": not divergencia,
        "comparado": bool(hist.ok and externo_df is not None and not externo_df.empty),
        "avisos": divergencia,
    }

    t0 = time.time()
    quote = mdp.get_quote(PROBE_TICKER)
    report["checks"]["quote"] = {
        "ok": quote.quote is not None,
        "source_used": quote.source,
        "delayed": quote.is_delayed,
        "elapsed_s": round(time.time() - t0, 2),
        "warnings": quote.warnings,
    }

    any_history_ok = (
        report["checks"]["yfinance_history"]["ok"]
        or report["checks"]["fonte_externa_history"]["ok"]
    )
    quote_ok = report["checks"]["quote"]["ok"]

    # Divergência entre fontes NÃO derruba o exit code: as duas responderam, o
    # sistema tem dado. É um aviso para olho humano, não motivo para barrar
    # deploy — decidir qual fonte está certa exige remedir, não um if.
    if any_history_ok and quote_ok and writable:
        status, exit_code = "ok", 0
    elif any_history_ok or quote_ok:
        status, exit_code = "degradado", 1
    else:
        status, exit_code = "critico", 2

    report["status"] = status
    report["exit_code"] = exit_code
    return report


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, indent=2, ensure_ascii=False))
    sys.exit(result["exit_code"])
