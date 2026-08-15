#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
atualizar_correlacoes.py — refresh das correlações do Radar IA 2026,
calculadas localmente a partir do histórico do yfinance.

O "ponto fraco" documentado no guia do radar: as correlações embutidas em
radar_ia_2026.py são um snapshot de 14/08/2026. Este script recalcula a
janela de 6 meses e grava um OVERLAY JSON que radar_ia_2026.py carrega por
cima do snapshot no import -- sem tabela no Postgres (Python não acessa o
banco neste repo; a fonte de verdade continua no módulo, e rota/tela/
alertas herdam o dado novo no próximo processo).

## Por que yfinance e não a Alpha Vantage (mudança de 15/08/2026)

A versão original chamava ANALYTICS_FIXED_WINDOW (CALCULATIONS=CORRELATION)
em lotes de 5 símbolos. Em produção o endpoint devolveu 403 de forma
persistente com chave free válida -- o mesmo key funcionava normalmente em
GLOBAL_QUOTE, e o 403 continuou 6h depois do reset diário de cota, ou seja
não era limite de requisições: a Analytics API é uma das "certain premium
API functions" fora do plano gratuito.

Calcular aqui é melhor que pagar por isso:
  - sem chave, sem custo e sem rate limit -- pode rodar quantas vezes quiser
  - MESMA fonte de preço que já alimenta vol/beta/estudos (get_scenario_params,
    entry_exit_study, earnings_reaction_analysis) -- antes havia duas fontes
    de dado diferentes descrevendo o mesmo universo
  - uma dependência externa a menos pra virar paga de novo amanhã

Correlação é calculada sobre RETORNOS diários (pct_change), não sobre o nível
de preço: dois papéis em tendência de alta exibem correlação alta de nível
mesmo sem nenhuma co-movimentação diária real, e o que os consumidores
(alerta de contágio, dedup de sinal, regra de concentração do veredito)
precisam saber é justamente "esses dois andam juntos no dia a dia?".

Uso (no VPS, dentro do container):
    python -m agent.atualizar_correlacoes            # calcula e grava
    python -m agent.atualizar_correlacoes --dry-run  # calcula e mostra, não grava
    python -m agent.atualizar_correlacoes --meses 12 # janela maior

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
from datetime import timedelta

import pandas as pd
import yfinance as yf

# Import dos DOIS jeitos, mesmo padrão dos outros scripts do agente que
# rodam tanto standalone quanto como módulo do pacote.
try:
    from brt import today_brt
    from radar_ia_2026 import CORRELACOES, PORTFOLIO_DEFAULT, TEMA_IA
    from parametros_macro import INDICADORES_GLOBAIS
except ImportError:
    from agent.brt import today_brt
    from agent.radar_ia_2026 import CORRELACOES, PORTFOLIO_DEFAULT, TEMA_IA
    from agent.parametros_macro import INDICADORES_GLOBAIS

# Mínimo de pregões em comum pra um par valer. ~3 meses: abaixo disso a
# correlação vira ruído (papel recém-listado, ADR com feriado diferente),
# e um número ruim é pior que par ausente -- o consumidor sabe lidar com
# `correlacao() -> None`, mas não tem como desconfiar de um 0.9 espúrio.
MIN_PREGOES = 60

MESES_DEFAULT = 6

OVERLAY_PATH_DEFAULT = "/var/cache/premercado/radar_correlacoes.json"


def _overlay_path() -> str:
    return os.environ.get("RADAR_CORR_OVERLAY") or OVERLAY_PATH_DEFAULT


def universo() -> list[str]:
    """Todos os tickers que o radar já descreve: os dois lados de cada par
    medido, o tema IA, a carteira default e os ETFs proxy dos mercados
    asiáticos. Assim o refresh cobre pelo menos o que o snapshot cobria,
    sem lista paralela pra sair de sincronia.

    Os proxies (EWY/EWT/EWJ/FXI) entram porque parametros_macro.
    sinal_overnight lê a correlação deles com as posições pelo radar -- sem
    estarem aqui, ficariam presos ao que veio no snapshot (só EWY tinha)."""
    tickers: set[str] = set(PORTFOLIO_DEFAULT) | set(TEMA_IA) | set(INDICADORES_GLOBAIS)
    for a, b in CORRELACOES:
        tickers.add(a)
        tickers.add(b)
    return sorted(tickers)


def baixar_fechamentos(tickers: list[str], meses: int = MESES_DEFAULT) -> pd.DataFrame:
    """Fechamentos diários ajustados de todos os tickers numa chamada só.

    auto_adjust=True: split/dividendo cria um degrau artificial na série que
    vira co-movimento falso no dia do evento (mesma razão pela qual o resto
    do agente usa série ajustada pra retorno)."""
    dados = yf.download(
        tickers,
        period=f"{meses}mo",
        interval="1d",
        auto_adjust=True,
        progress=False,
        group_by="column",
        threads=True,
    )
    if dados is None or dados.empty:
        return pd.DataFrame()
    # Um ticker só devolve colunas simples; vários devolvem MultiIndex.
    if isinstance(dados.columns, pd.MultiIndex):
        if "Close" not in dados.columns.get_level_values(0):
            return pd.DataFrame()
        fech = dados["Close"]
    else:
        if "Close" not in dados.columns:
            return pd.DataFrame()
        fech = dados[["Close"]]
        fech.columns = tickers[:1]
    return fech.dropna(axis=1, how="all")


def correlacoes_de(fechamentos: pd.DataFrame,
                   min_pregoes: int = MIN_PREGOES) -> dict[tuple[str, str], float]:
    """Matriz de correlação dos RETORNOS diários -> {(A, B): corr}, A < B.

    Pares sem `min_pregoes` observações em comum ficam de fora (pandas
    devolve NaN com min_periods, e NaN não vira par)."""
    if fechamentos.empty or fechamentos.shape[1] < 2:
        return {}
    retornos = fechamentos.pct_change().dropna(how="all")
    matriz = retornos.corr(min_periods=min_pregoes)
    out: dict[tuple[str, str], float] = {}
    colunas = list(matriz.columns)
    for i, ca in enumerate(colunas):
        for cb in colunas[i + 1:]:
            valor = matriz.at[ca, cb]
            if pd.isna(valor):
                continue
            a, b = sorted([str(ca).upper(), str(cb).upper()])
            out[(a, b)] = round(float(valor), 2)
    return out


def gravar_overlay(pares: dict[tuple[str, str], float], meses: int = MESES_DEFAULT,
                   path: str | None = None) -> str:
    """Grava o overlay que radar_ia_2026.py carrega no import. Escrita
    atômica (tmp + rename) pra um leitor concorrente nunca ver JSON pela
    metade."""
    destino = path or _overlay_path()
    hoje = today_brt()
    blob = {
        "janela_inicio": (hoje - timedelta(days=int(meses * 30.4))).isoformat(),
        "janela_fim": hoje.isoformat(),
        "atualizado_em": hoje.isoformat(),
        "fonte": "yfinance_retornos_diarios",
        "correlacoes": {f"{a}|{b}": c for (a, b), c in sorted(pares.items())},
    }
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    tmp = destino + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(blob, f, ensure_ascii=False, indent=1)
    os.replace(tmp, destino)
    return destino


def resumo_mudancas(pares: dict[tuple[str, str], float], corte: float = 0.15) -> dict:
    """Compara com o snapshot embutido: pares novos, pares que sumiram e
    mudanças relevantes (regime de correlação pode ter virado)."""
    base = {tuple(sorted(k)): v for k, v in CORRELACOES.items()}
    novos = sorted(set(pares) - set(base))
    ausentes = sorted(set(base) - set(pares))
    grandes = sorted(
        ((par, base[par], c) for par, c in pares.items()
         if par in base and abs(c - base[par]) >= corte),
        key=lambda x: -abs(x[2] - x[1]))
    # Mudanças que cruzam CORR_ALTA nos dois sentidos são as que realmente
    # mexem no comportamento do agente (dedup, contágio, concentração).
    cruzaram = [(par, antigo, novo) for par, antigo, novo in
                (((a, b), base[(a, b)], c) for (a, b), c in pares.items() if (a, b) in base)
                if (antigo >= 0.70) != (novo >= 0.70)]
    return {"novos": novos, "ausentes": ausentes, "grandes": grandes, "cruzaram_070": cruzaram}


def _imprimir_resumo(pares: dict[tuple[str, str], float], res: dict) -> None:
    print(f"\npares calculados: {len(pares)} | novos vs snapshot: {len(res['novos'])} "
          f"| do snapshot sem dado agora: {len(res['ausentes'])}")
    if res["cruzaram_070"]:
        print("\nCRUZARAM o limiar de 0.70 (muda dedup/contágio/concentração):")
        for (a, b), antigo, novo in res["cruzaram_070"]:
            direcao = "virou MESMO TRADE" if novo >= 0.70 else "deixou de ser mesmo trade"
            print(f"  {a}-{b}: {antigo:.2f} -> {novo:.2f}  ({direcao})")
    if res["grandes"]:
        print(f"\nmudanças >= 0.15 vs snapshot 14/08 ({len(res['grandes'])} pares, top 15):")
        for (a, b), antigo, novo in res["grandes"][:15]:
            print(f"  {a}-{b}: {antigo:.2f} -> {novo:.2f}")
    else:
        print("\nnenhuma mudança >= 0.15 vs o snapshot -- regime estável.")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Recalcula as correlações do Radar IA a partir do yfinance")
    p.add_argument("--dry-run", action="store_true", help="calcula e mostra, não grava o overlay")
    p.add_argument("--meses", type=int, default=MESES_DEFAULT, metavar="N",
                   help=f"tamanho da janela em meses (default {MESES_DEFAULT})")
    p.add_argument("--min-pregoes", type=int, default=MIN_PREGOES, metavar="N")
    args = p.parse_args(argv)

    tickers = universo()
    print(f"baixando {len(tickers)} tickers ({args.meses} meses)...", file=sys.stderr)
    fech = baixar_fechamentos(tickers, args.meses)
    if fech.empty:
        print("yfinance não devolveu histórico -- overlay NÃO gravado "
              "(o snapshot embutido continua valendo)", file=sys.stderr)
        return 1

    faltando = sorted(set(tickers) - set(map(str, fech.columns)))
    if faltando:
        print(f"sem histórico para: {', '.join(faltando)}", file=sys.stderr)

    pares = correlacoes_de(fech, args.min_pregoes)
    if not pares:
        print("nenhum par com pregões suficientes -- overlay NÃO gravado", file=sys.stderr)
        return 1

    _imprimir_resumo(pares, resumo_mudancas(pares))

    if args.dry_run:
        print("\n--dry-run: overlay não gravado.")
        return 0
    destino = gravar_overlay(pares, args.meses)
    print(f"\noverlay gravado em {destino} -- novos processos do radar já leem daqui.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
