#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
atualizar_correlacoes.py — refresh das correlações do Radar IA 2026 via
Alpha Vantage ANALYTICS_FIXED_WINDOW (CALCULATIONS=CORRELATION).

O "ponto fraco" documentado no guia do radar: as correlações embutidas em
radar_ia_2026.py são um snapshot de 14/08/2026. Este script recalcula a
janela de 6 meses direto da Alpha Vantage e grava um OVERLAY JSON que
radar_ia_2026.py carrega por cima do snapshot no import -- sem tabela no
Postgres (Python não acessa o banco neste repo; a fonte de verdade continua
no módulo, e rota/tela/alertas herdam o dado novo no próximo processo).

Rate limit do plano free (por que o script é "lento" de propósito):
  - 5 símbolos por request  -> os 12 lotes do guia cobrem o universo
  - ~5 requests/min         -> pausa default de 15s entre lotes
  - 25 requests/dia         -> 12 lotes cabem com folga (1 rodada/dia)

Uso (no VPS, dentro do container):
    export ALPHAVANTAGE_API_KEY=...   # ou ALPHA_VANTAGE_API_KEY
    python -m agent.atualizar_correlacoes            # roda os 12 lotes e grava
    python -m agent.atualizar_correlacoes --dry-run  # busca e mostra, não grava
    python -m agent.atualizar_correlacoes --lotes 2  # só os N primeiros lotes

O overlay vai em RADAR_CORR_OVERLAY (default
/var/cache/premercado/radar_correlacoes.json). ATENÇÃO: um rebuild da
imagem apaga esse cache -- depois de `docker compose up -d --build`, rode o
script de novo (ou monte um volume pro diretório).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, timedelta

# Import dos DOIS jeitos, mesmo padrão dos outros scripts do agente que
# rodam tanto standalone quanto como módulo do pacote.
try:
    from http_retry import SESSION
    from brt import today_brt
except ImportError:
    from agent.http_retry import SESSION
    from agent.brt import today_brt

ANALYTICS_URL = "https://alphavantageapi.co/timeseries/analytics"
RANGE = "6month"

# Lotes de 5 símbolos validados na coleta original (ver Passo 5 do guia) --
# todos incluem uma âncora repetida (NVDA/MU/SNDK) de propósito, pra costurar
# correlações entre lotes diferentes através do símbolo comum.
LOTES: list[list[str]] = [
    ["NVDA", "SNDK", "STX", "WDC", "MU"],
    ["NVDA", "SMCI", "DELL", "HPE", "CRWV"],
    ["NVDA", "AMD", "AVGO", "MRVL", "ARM"],
    ["NVDA", "AMAT", "LRCX", "KLAC", "ASML"],
    ["NVDA", "VRT", "GEV", "ETN", "ANET"],
    ["NVDA", "CEG", "VST", "CSCO", "TSM"],
    ["NVDA", "MSFT", "GOOGL", "AMZN", "META"],
    ["NVDA", "PLTR", "ORCL", "QCOM", "INTC"],
    ["MU", "SMCI", "ARM", "MRVL", "AVGO"],
    ["SNDK", "AMAT", "DELL", "VRT", "TSM"],
    ["MU", "LRCX", "SMCI", "CRWV", "CEG"],
    ["EWY", "MU", "SNDK", "NVDA", "SMCI"],
]

PAUSA_DEFAULT_S = 15  # ~4 req/min, abaixo do teto de 5/min do plano free

OVERLAY_PATH_DEFAULT = "/var/cache/premercado/radar_correlacoes.json"


def _overlay_path() -> str:
    return os.environ.get("RADAR_CORR_OVERLAY") or OVERLAY_PATH_DEFAULT


def _api_key() -> str | None:
    return os.environ.get("ALPHAVANTAGE_API_KEY") or os.environ.get("ALPHA_VANTAGE_API_KEY")


def _achar_matriz(obj) -> dict | None:
    """Procura recursivamente o bloco {"index": [...], "correlation": [[...]]}
    na resposta -- defensivo de propósito: o envelope exato da Analytics API
    (payload/RETURNED_DATA/...) já variou entre exemplos da documentação, e
    o que interessa é só a matriz com seu índice de símbolos."""
    if isinstance(obj, dict):
        idx, corr = obj.get("index"), obj.get("correlation")
        if (isinstance(idx, list) and idx and all(isinstance(s, str) for s in idx)
                and isinstance(corr, list) and corr):
            return {"index": idx, "correlation": corr}
        for v in obj.values():
            achado = _achar_matriz(v)
            if achado:
                return achado
    elif isinstance(obj, list):
        for v in obj:
            achado = _achar_matriz(v)
            if achado:
                return achado
    return None


def extrair_correlacoes(resposta: dict) -> dict[tuple[str, str], float]:
    """Extrai {(A, B): corr} (A < B) da resposta da Analytics API. Aceita
    matriz completa (n x n) ou triangular inferior (linha i com i+1 colunas,
    como a API costuma devolver). Ignora a diagonal (corr 1.0 consigo)."""
    bloco = _achar_matriz(resposta)
    if not bloco:
        return {}
    index = [str(s).upper() for s in bloco["index"]]
    matriz = bloco["correlation"]
    out: dict[tuple[str, str], float] = {}
    for i, linha in enumerate(matriz):
        if not isinstance(linha, list):
            continue
        for j, valor in enumerate(linha):
            if j >= i or valor is None:  # triangular: só j < i interessa
                continue
            try:
                c = round(float(valor), 2)
            except (TypeError, ValueError):
                continue
            a, b = sorted([index[i], index[j]])
            out[(a, b)] = c
    return out


def buscar_lote(simbolos: list[str], api_key: str, timeout_s: int = 30) -> dict[tuple[str, str], float]:
    """Uma chamada ANALYTICS_FIXED_WINDOW pra um lote de até 5 símbolos."""
    resp = SESSION.get(ANALYTICS_URL, params={
        "SYMBOLS": ",".join(simbolos),
        "RANGE": RANGE,
        "INTERVAL": "DAILY",
        "OHLC": "close",
        "CALCULATIONS": "CORRELATION",
        "apikey": api_key,
    }, timeout=timeout_s)
    resp.raise_for_status()
    dados = resp.json()
    # Rate limit/erro vem como 200 com corpo de aviso em vez de HTTP != 2xx.
    for chave in ("Note", "Information", "Error Message"):
        if isinstance(dados, dict) and dados.get(chave):
            raise RuntimeError(f"Alpha Vantage: {dados[chave]}")
    pares = extrair_correlacoes(dados)
    if not pares:
        raise RuntimeError(f"resposta sem matriz de correlação pro lote {simbolos}")
    return pares


def atualizar(api_key: str, lotes: list[list[str]] | None = None,
              pausa_s: float = PAUSA_DEFAULT_S) -> tuple[dict[tuple[str, str], float], list[str]]:
    """Roda os lotes em sequência respeitando o rate limit. Devolve
    (pares_acumulados, erros) -- resultado parcial vale mais que nada: um
    lote que falhar (rate limit do dia estourado, símbolo delistado) não
    derruba o que os anteriores já trouxeram."""
    lotes = lotes if lotes is not None else LOTES
    acumulado: dict[tuple[str, str], float] = {}
    erros: list[str] = []
    for i, lote in enumerate(lotes):
        try:
            pares = buscar_lote(lote, api_key)
            acumulado.update(pares)
            print(f"[{i + 1}/{len(lotes)}] {','.join(lote)}: {len(pares)} pares", file=sys.stderr)
        except Exception as e:
            erros.append(f"lote {','.join(lote)}: {e}")
            print(f"[{i + 1}/{len(lotes)}] FALHOU: {e}", file=sys.stderr)
        if i < len(lotes) - 1:
            time.sleep(pausa_s)
    return acumulado, erros


def gravar_overlay(pares: dict[tuple[str, str], float], path: str | None = None) -> str:
    """Grava o overlay que radar_ia_2026.py carrega no import. Escrita
    atômica (tmp + rename) pra um leitor concorrente nunca ver JSON pela
    metade."""
    destino = path or _overlay_path()
    hoje = today_brt()
    blob = {
        "janela_inicio": (hoje - timedelta(days=182)).isoformat(),
        "janela_fim": hoje.isoformat(),
        "atualizado_em": hoje.isoformat(),
        "fonte": "alpha_vantage_analytics",
        "correlacoes": {f"{a}|{b}": c for (a, b), c in sorted(pares.items())},
    }
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    tmp = destino + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(blob, f, ensure_ascii=False, indent=1)
    os.replace(tmp, destino)
    return destino


def _resumo_mudancas(pares: dict[tuple[str, str], float]) -> None:
    """Compara com o snapshot embutido e mostra o que mudou de verdade."""
    try:
        from radar_ia_2026 import CORRELACOES as EMBUTIDAS
    except ImportError:
        from agent.radar_ia_2026 import CORRELACOES as EMBUTIDAS
    base = {tuple(sorted(k)): v for k, v in EMBUTIDAS.items()}
    novos = sorted(set(pares) - set(base))
    grandes = sorted(
        ((par, base[par], c) for par, c in pares.items()
         if par in base and abs(c - base[par]) >= 0.15),
        key=lambda x: -abs(x[2] - x[1]))
    print(f"\npares medidos: {len(pares)} | novos vs snapshot: {len(novos)}")
    if grandes:
        print("mudanças >= 0.15 vs snapshot 14/08 (regime pode ter virado):")
        for (a, b), antigo, novo in grandes[:15]:
            print(f"  {a}-{b}: {antigo:.2f} -> {novo:.2f}")
    else:
        print("nenhuma mudança >= 0.15 vs o snapshot -- regime estável.")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Atualiza correlações do Radar IA via Alpha Vantage")
    p.add_argument("--dry-run", action="store_true", help="busca e mostra, não grava o overlay")
    p.add_argument("--lotes", type=int, metavar="N", help="roda só os N primeiros lotes")
    p.add_argument("--pausa", type=float, default=PAUSA_DEFAULT_S, metavar="SEG")
    p.add_argument("--api-key", default=None, help="override de ALPHAVANTAGE_API_KEY")
    args = p.parse_args(argv)

    api_key = args.api_key or _api_key()
    if not api_key:
        print("defina ALPHAVANTAGE_API_KEY (ou passe --api-key)", file=sys.stderr)
        return 2

    lotes = LOTES[: args.lotes] if args.lotes else LOTES
    pares, erros = atualizar(api_key, lotes, args.pausa)
    if not pares:
        print("nenhum par obtido -- overlay NÃO gravado (o snapshot embutido continua valendo)",
              file=sys.stderr)
        return 1

    _resumo_mudancas(pares)
    if erros:
        print(f"\n{len(erros)} lote(s) falharam (resultado parcial gravado mesmo assim):", file=sys.stderr)
        for e in erros:
            print(f"  - {e}", file=sys.stderr)

    if args.dry_run:
        print("\n--dry-run: overlay não gravado.")
        return 0
    destino = gravar_overlay(pares)
    print(f"\noverlay gravado em {destino} -- novos processos do radar já leem daqui.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
