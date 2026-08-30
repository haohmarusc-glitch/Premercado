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
import math
import json
import os
import sys
from datetime import timedelta

import pandas as pd

from agent import market_data_provider
from agent.brt import today_brt
from agent.radar_ia_2026 import (CORRELACOES, CORRELACOES_ATUALIZADO_EM,
                                 HOJE_SNAPSHOT, PORTFOLIO_DEFAULT, TEMA_IA)
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
    # Pelo provider, e não por um yf.download próprio. O que se ganha não é
    # só cache e fallback: é a MESMA série que o risk_manager.correlation usa.
    # As duas contas são idênticas (pct_change + Pearson, 6 meses, ajustado),
    # então a única coisa que podia separar os números que a tela mostra era a
    # fonte -- e separava, porque um lado filtrava a barra do dia corrente e o
    # outro não. Duas contas para o mesmo nome é o §2b do playbook, e aqui a
    # de baixo alimenta o validador do Veredito via radar_ia_2026.correlacao.
    #
    # permitir_externa=False pelo mesmo motivo do get_scenario_params: a série
    # é ajustada, e o degrau de um split vindo de outra fonte viraria
    # co-movimento falso -- exatamente o artefato que o auto_adjust evita.
    lote = market_data_provider.get_daily_closes_batch(
        tickers, f"{meses}mo", auto_adjust=True, permitir_externa=False
    )
    for aviso in lote.warnings:
        print(f"[atualizar_correlacoes] {aviso}", file=sys.stderr)
    if lote.degradadas:
        print(f"[atualizar_correlacoes] séries degradadas: {lote.degradadas}",
              file=sys.stderr)
    if not lote.ok:
        return pd.DataFrame()
    return lote.closes.dropna(axis=1, how="all")


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


def vol_semanal_de(fechamentos: pd.DataFrame,
                   min_pregoes: int = MIN_PREGOES) -> dict[str, float]:
    """Volatilidade SEMANAL realizada (%) por ticker, do mesmo download que
    já veio pras correlações -- zero chamada de rede a mais.

    Motivo de existir (visto em produção, 15/08/2026): a vol embutida em
    TEMA_IA é coleta manual de fonte externa e discordava da medição do
    próprio agente -- INTC aparecia com 31.7% anualizada no radar contra
    79.1% medidos por get_scenario_params no mesmo período, e NVDA saía com
    10.8% a.a., implausível pra qualquer janela recente. Como essa vol é a
    base do stop sugerido e da contribuição de risco, o erro se propagava
    pra decisão: NVDA vinha como a posição "mais segura" da carteira.

    Metodologia igual à do resto do agente: desvio-padrão dos RETORNOS
    diários, escalado pra semana por raiz do tempo (x sqrt(5)) -- mesma
    convenção de parametros_volatilidade.vol_diaria/vol_anualizada, então
    os números conversam entre módulos."""
    if fechamentos.empty:
        return {}
    retornos = fechamentos.pct_change().dropna(how="all")
    out: dict[str, float] = {}
    for col in retornos.columns:
        serie = retornos[col].dropna()
        if len(serie) < min_pregoes:
            continue
        desvio_diario = float(serie.std())
        if not desvio_diario or pd.isna(desvio_diario):
            continue
        out[str(col).upper()] = round(desvio_diario * math.sqrt(5) * 100, 2)
    return out


def gravar_overlay(pares: dict[tuple[str, str], float], meses: int = MESES_DEFAULT,
                   path: str | None = None,
                   vols: dict[str, float] | None = None) -> str:
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
        # Vol semanal realizada (%) por ticker -- substitui a coleta manual
        # de TEMA_IA, que discordava da medição do próprio agente.
        "vol_semanal": dict(sorted((vols or {}).items())),
    }
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    tmp = destino + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(blob, f, ensure_ascii=False, indent=1)
    os.replace(tmp, destino)
    return destino


def resumo_mudancas(pares: dict[tuple[str, str], float], corte: float = 0.15) -> dict:
    # NOTA sobre a base de comparação: CORRELACOES já vem com o overlay
    # anterior aplicado (radar_ia_2026 faz isso no import). Então na PRIMEIRA
    # execução isto compara contra o snapshot embutido de 14/08, e da segunda
    # em diante contra a MEDIÇÃO ANTERIOR -- que é o que interessa num job
    # semanal ("o que mudou desde a semana passada?"). O texto do relatório
    # reflete essa diferença em vez de dizer sempre "vs snapshot", que
    # passaria a ser mentira depois do primeiro overlay.
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
    # A base de comparação é o que está CARREGADO agora: o snapshot embutido
    # na primeira execução, e a medição anterior depois que existe overlay
    # (ver nota em resumo_mudancas). Nomear isso evita a leitura errada de
    # "estável desde 14/08" quando o que se mediu foi "estável desde a
    # semana passada".
    base = (f"a medição de {CORRELACOES_ATUALIZADO_EM}" if CORRELACOES_ATUALIZADO_EM
            else f"o snapshot de {HOJE_SNAPSHOT.isoformat()}")
    print(f"\npares calculados: {len(pares)} | comparando com {base}")
    print(f"novos: {len(res['novos'])} | sem dado agora: {len(res['ausentes'])}")
    if res["cruzaram_070"]:
        print("\nCRUZARAM o limiar de 0.70 (muda dedup/contágio/concentração):")
        for (a, b), antigo, novo in res["cruzaram_070"]:
            direcao = "virou MESMO TRADE" if novo >= 0.70 else "deixou de ser mesmo trade"
            print(f"  {a}-{b}: {antigo:.2f} -> {novo:.2f}  ({direcao})")
    if res["grandes"]:
        print(f"\nmudanças >= 0.15 ({len(res['grandes'])} pares, top 15):")
        for (a, b), antigo, novo in res["grandes"][:15]:
            print(f"  {a}-{b}: {antigo:.2f} -> {novo:.2f}")
    else:
        print("nenhuma mudança >= 0.15 -- regime estável.")


def divergencias_de_vol(vols: dict[str, float], fator: float = 1.5) -> list[dict]:
    """Tickers cuja vol MEDIDA difere da coleta manual ORIGINAL por mais de
    `fator` (pra mais ou pra menos).

    Existe pra tornar visível o problema que motivou medir a vol: quando a
    diferença é de 2-3x, não é ruído de janela -- é erro de dado, e vinha
    contaminando stop e sizing em silêncio.

    Compara contra `vol_sem_snapshot` (o valor manual preservado por
    radar_ia_2026._aplicar_vol_medida) e não contra `vol_sem`, que a partir
    do primeiro refresh já É a medição anterior -- comparar com ela daria
    razão ~1 sempre e apagaria o diagnóstico depois de usá-lo uma vez."""
    fora = []
    for ticker, medida in vols.items():
        info = TEMA_IA.get(ticker) or {}
        manual = info.get("vol_sem_snapshot", info.get("vol_sem"))
        if not manual or not medida:
            continue
        razao = medida / manual
        if razao >= fator or razao <= 1 / fator:
            fora.append({"ticker": ticker, "manual": manual, "medida": medida,
                         "razao": round(razao, 2)})
    return sorted(fora, key=lambda x: -abs(x["razao"] - 1))


def atualizar_e_gravar(meses: int = MESES_DEFAULT,
                       min_pregoes: int = MIN_PREGOES) -> dict:
    """Ciclo completo (baixar -> calcular -> gravar) numa chamada, pro
    checker semanal (lib/radar-correlacoes-checker.ts) consumir como JSON.

    Nunca levanta: devolve {"ok": false, "erro": ...} pra qualquer falha, no
    mesmo espírito dos outros scripts chamados por checker -- um refresh que
    não deu certo deixa o overlay anterior (ou o snapshot embutido) valendo,
    que é sempre melhor que derrubar o ciclo de background."""
    try:
        tickers = universo()
        fech = baixar_fechamentos(tickers, meses)
        if fech.empty:
            return {"ok": False, "erro": "yfinance não devolveu histórico"}
        pares = correlacoes_de(fech, min_pregoes)
        if not pares:
            return {"ok": False, "erro": "nenhum par com pregões suficientes"}
        res = resumo_mudancas(pares)
        vols = vol_semanal_de(fech, min_pregoes)
        destino = gravar_overlay(pares, meses, vols=vols)
        return {
            "ok": True,
            "pares": len(pares),
            "tickers": len(tickers),
            "vols": len(vols),
            "vol_divergencias": divergencias_de_vol(vols),
            "sem_historico": sorted(set(tickers) - set(map(str, fech.columns))),
            "novos": len(res["novos"]),
            "mudancas_relevantes": len(res["grandes"]),
            # Os que cruzaram 0.70 mudam dedup/contágio/concentração -- é o
            # único item que merece log em nível de aviso do lado do Node.
            "cruzaram_070": [{"par": [a, b], "de": antigo, "para": novo}
                             for (a, b), antigo, novo in res["cruzaram_070"]],
            "overlay": destino,
        }
    except Exception as e:
        return {"ok": False, "erro": f"{type(e).__name__}: {e}"}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Recalcula as correlações do Radar IA a partir do yfinance")
    p.add_argument("--dry-run", action="store_true", help="calcula e mostra, não grava o overlay")
    p.add_argument("--meses", type=int, default=MESES_DEFAULT, metavar="N",
                   help=f"tamanho da janela em meses (default {MESES_DEFAULT})")
    p.add_argument("--min-pregoes", type=int, default=MIN_PREGOES, metavar="N")
    p.add_argument("--json", action="store_true",
                   help="ciclo completo, saída JSON (usado pelo checker semanal)")
    args = p.parse_args(argv)

    if args.json:
        # stdout fica EXCLUSIVO do JSON (o Node dá JSON.parse nele); o
        # progresso humano abaixo vai todo pra stderr, mesmo padrão dos
        # outros scripts spawnados.
        saida = atualizar_e_gravar(args.meses, args.min_pregoes)
        print(json.dumps(saida, ensure_ascii=False))
        return 0 if saida.get("ok") else 1

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

    vols = vol_semanal_de(fech, args.min_pregoes)
    print(f"\nvol semanal medida: {len(vols)} tickers")
    divs = divergencias_de_vol(vols)
    if divs:
        print(f"DIVERGEM da coleta manual do snapshot ({len(divs)} tickers) — "
              f"a medida passa a valer:")
        for d in divs[:15]:
            ann_manual = d["manual"] * (52 ** 0.5)
            ann_medida = d["medida"] * (52 ** 0.5)
            print(f"  {d['ticker']:<6} {d['manual']:>6.2f}%/sem ({ann_manual:>5.1f}% a.a.) "
                  f"-> {d['medida']:>6.2f}%/sem ({ann_medida:>5.1f}% a.a.)  x{d['razao']}")

    if args.dry_run:
        print("\n--dry-run: overlay não gravado.")
        return 0
    destino = gravar_overlay(pares, args.meses, vols=vols)
    print(f"\noverlay gravado em {destino} -- novos processos do radar já leem daqui.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
