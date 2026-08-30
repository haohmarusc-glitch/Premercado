#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
parametros_macro.py — camada macro/global sobre os parâmetros de vol
=====================================================================
Estende parametros_volatilidade.py com:

  1. Modo macro: em semana de FOMC a vol operacional é inflada -- mais
     ainda em nomes de beta alto quando a reunião tem dot plot (a projeção
     de juros bate mais forte em papel de duration longa/valuation esticado)
  2. Sinal overnight: a Ásia fecha HORAS antes do pré-mercado de NY, então o
     fechamento de Coreia/Taiwan/Japão é indício antecipado pro cluster de
     memória/semis -- estimado por correlação medida x movimento

## Adaptações na integração ao Premercado (vs. o pacote original)

  - O calendário FOMC NÃO é redeclarado aqui: vem de
    market_alerts.MACRO_EVENTS, que o repo já mantém (e que já alimenta
    check_macro_triggers). Duas listas da mesma coisa divergem sem avisar
    -- a segunda cópia envelhece calada. O import é feito DENTRO da função
    de propósito: market_alerts importa este módulo no topo, e um import
    recíproco no topo daqui fecharia um ciclo.
  - "Tem dot plot?" é DERIVADO do mês (o Fed publica o Summary of Economic
    Projections nas reuniões de março, junho, setembro e dezembro), não
    lido de uma lista paralela que precisaria ser mantida à mão.
  - Correlações dos proxies globais vêm de radar_ia_2026.correlacao() --
    ou seja, do overlay atualizável por atualizar_correlacoes.py -- em vez
    de hardcoded no módulo. Os ETFs proxy estão no universo do atualizador,
    então esses números se renovam junto com o resto.
  - `ref` default é HOJE em BRT, nunca o snapshot congelado.

Uso:
    python -m agent.parametros_macro                       # relatório
    python -m agent.parametros_macro --ticker MU --ref 2026-09-14
    python -m agent.parametros_macro --overnight EWY=-3.0 EWT=-2.1
"""
from __future__ import annotations

import argparse
import json
from datetime import date, timedelta

from agent.brt import today_brt
from agent.radar_ia_2026 import correlacao
from agent.parametros_volatilidade import (MULT_STOP, _carteira_default,
                                           classe_vol, parametros)

# Multiplicadores do modo macro sobre a vol operacional.
MULT_FOMC = 1.25            # semana de FOMC sem projeções
MULT_FOMC_SEP = 1.40        # com dot plot (Summary of Economic Projections)
MULT_EXTRA_BETA_ALTO = 1.15  # adicional, só em dot plot, pra beta >= BETA_ALTO
JANELA_MACRO_DIAS = 3       # dias antes do comunicado em que o modo liga
BETA_ALTO = 2.5

# Meses em que a reunião do FOMC vem acompanhada do Summary of Economic
# Projections (dot plot) -- regra fixa do Fed, não lista mantida à mão.
MESES_COM_DOT_PLOT = {3, 6, 9, 12}

# Proxies observáveis pro fechamento asiático. Os ETFs são US-listed, então
# a correlação deles com os semis é medida na MESMA sessão (é o número que
# radar_ia_2026 tem); o índice real é o que fecha antes e dá o sinal
# antecipado. Usar a correlação do ETF pra ler o movimento do índice é
# aproximação deliberada: o ETF dilui o país (dezenas de nomes), então o
# sinal real do índice tende a ser >= ao medido aqui.
#
# `composicao` descreve o que o índice CONTÉM -- fato estrutural, estável.
# Deliberadamente NÃO diz qual proxy é o "melhor indicador": isso depende da
# carteira e muda com o dado, então sai de melhor_proxy() sobre as
# correlações vivas. A versão anterior afirmava que EWY era o melhor
# indicador overnight; ao medir EWT/EWJ/FXI (ago/2026) o dado mostrou o
# contrário pra uma carteira sem nomes de memória -- Taiwan lidera NVDA
# (0.58 vs 0.52), ARM (0.66 vs 0.60) e AVGO (0.61 vs 0.58), porque a TSMC
# fabrica pra eles. Descrição que faz ranking envelhece errado; a que
# descreve composição, não.
INDICADORES_GLOBAIS: dict[str, dict] = {
    "EWY": {"pais": "Coreia do Sul", "indice": "^KS11",
            "composicao": "Samsung ~25-30% do ETF; SK Hynix pesado — "
                          "concentra o cluster de MEMÓRIA/HBM"},
    "EWT": {"pais": "Taiwan", "indice": "^TWII",
            "composicao": "TSMC domina — FUNDIÇÃO, logo lidera quem fabrica "
                          "lá (NVDA, ARM, AVGO, MRVL)"},
    "EWJ": {"pais": "Japão", "indice": "^N225",
            "composicao": "amplo, não é puro-semi; equipamento japonês "
                          "(TEL, Advantest) conversa com AMAT/LRCX"},
    "FXI": {"pais": "China", "indice": "000001.SS",
            "composicao": "large caps chinesas, quase sem semis — fator "
                          "próprio, relevante pros ADRs chineses"},
}

# Limiares de leitura do impacto estimado, em pontos percentuais.
IMPACTO_FORTE = 1.5
IMPACTO_MODERADO = 0.7


def _fomc_datas() -> list[str]:
    """Datas do FOMC vindas do calendário único do repo (market_alerts).
    Import tardio: market_alerts importa este módulo no topo, então importar
    de volta no topo fecharia um ciclo. Lista vazia se indisponível -- o
    modo macro simplesmente não liga, em vez de derrubar o chamador."""
    try:
        try:
            from market_alerts import MACRO_EVENTS
        except ImportError:
            from agent.market_alerts import MACRO_EVENTS
        return list(MACRO_EVENTS.get("FOMC") or [])
    except Exception:
        return []


def tem_dot_plot(iso_data: str) -> bool:
    """Reunião de março/junho/setembro/dezembro publica projeções."""
    try:
        return date.fromisoformat(iso_data).month in MESES_COM_DOT_PLOT
    except ValueError:
        return False


def em_janela_macro(ref: date | None = None,
                    janela: int = JANELA_MACRO_DIAS) -> dict | None:
    """Primeiro FOMC dentro dos próximos `janela` dias a partir de `ref`
    (default: hoje em BRT), ou None."""
    ref = ref or today_brt()
    for iso in sorted(_fomc_datas()):
        try:
            d = date.fromisoformat(iso)
        except ValueError:
            continue
        dias = (d - ref).days
        if 0 <= dias <= janela:
            return {"data": iso, "dias": dias, "sep": tem_dot_plot(iso)}
    return None


def parametros_completos(ticker: str, ref: date | None = None) -> dict | None:
    """parametros() + camada macro: em semana de FOMC a vol operacional é
    inflada e o stop recalculado sobre ela. Fora da janela, devolve exatamente
    o que parametros() devolveria -- sem efeito colateral."""
    p = parametros(ticker, ref)
    if not p:
        return None
    ev = em_janela_macro(ref)
    if not ev:
        return p
    mult = MULT_FOMC_SEP if ev["sep"] else MULT_FOMC
    # Beta ausente NÃO conta como beta zero: sem o dado não dá pra afirmar
    # que o papel é de beta alto, então ele simplesmente não leva o
    # multiplicador extra -- falha pro lado seguro (não infla vol sem base).
    beta = p.get("beta")
    beta_pesa = ev["sep"] and isinstance(beta, (int, float)) and beta >= BETA_ALTO
    if beta_pesa:
        mult *= MULT_EXTRA_BETA_ALTO
    vol_final = round(p["vol_operacional_pct"] * mult, 2)
    cls = classe_vol(vol_final)
    p.update({
        "vol_operacional_pct": vol_final,
        "classe": cls,
        "stop_sugerido_pct": round(vol_final * MULT_STOP[cls], 2),
        "modo": (f"{p['modo']} + " if p["modo"] != "normal" else "")
                + f"FOMC {ev['data']} em {ev['dias']}d"
                + (" [dot plot]" if ev["sep"] else "")
                + (f" [beta alto {beta}]" if beta_pesa else ""),
        "mult_macro": round(mult, 3),
    })
    return p


def melhor_proxy(ticker: str) -> list[tuple[str, float]]:
    """Proxies com correlação medida contra `ticker`, do mais forte pro mais
    fraco. É isto -- e não texto fixo no módulo -- que responde "qual mercado
    asiático antecipa melhor esta posição?", porque a resposta depende do
    papel e muda quando as correlações são recalculadas."""
    t = ticker.upper()
    pares = [(p, correlacao(p, t)) for p in INDICADORES_GLOBAIS]
    return sorted(((p, c) for p, c in pares if c is not None), key=lambda x: -x[1])


def lideres_por_posicao(portfolio: list[str] | None = None) -> list[dict]:
    """Pra cada posição, qual proxy lidera e por quanto sobre o segundo.
    Margem pequena entre o 1º e o 2º significa que os dois mercados dizem
    quase a mesma coisa -- não vale tratar o vencedor como sinal exclusivo."""
    out = []
    for t in [x.upper() for x in (portfolio or _carteira_default())]:
        ranking = melhor_proxy(t)
        if not ranking:
            continue
        lider, corr = ranking[0]
        margem = corr - ranking[1][1] if len(ranking) > 1 else None
        out.append({"posicao": t, "proxy": lider, "correlacao": corr,
                    "indice": INDICADORES_GLOBAIS[lider]["indice"],
                    "margem_sobre_2o": round(margem, 2) if margem is not None else None,
                    "ranking": ranking})
    return sorted(out, key=lambda x: -x["correlacao"])


def sinal_overnight(fechamentos: dict[str, float],
                    portfolio: list[str] | None = None) -> list[dict]:
    """Impacto estimado por posição a partir do fechamento asiático.

    `fechamentos`: {proxy: variação %} -- ex.: {"EWY": -3.0, "EWT": -2.1}.
    Estimativa linear (corr x movimento), média entre os proxies que têm
    correlação medida com a posição. É indício de pré-mercado, não previsão:
    correlação descreve o passado e sobe justamente em dia de stress."""
    tk = [t.upper() for t in (portfolio or _carteira_default())]
    out = []
    for t in tk:
        componentes, soma = [], 0.0
        for proxy, mov in fechamentos.items():
            proxy = proxy.upper()
            if proxy not in INDICADORES_GLOBAIS:
                continue
            c = correlacao(proxy, t)
            if c is None:
                continue
            soma += c * float(mov)
            componentes.append(f"{proxy}({float(mov):+.1f}%)x{c:.2f}")
        if not componentes:
            continue
        impacto = soma / len(componentes)
        out.append({
            "posicao": t,
            "impacto_esperado_pct": round(impacto, 2),
            "componentes": componentes,
            "alerta": ("FORTE" if abs(impacto) >= IMPACTO_FORTE
                       else "moderado" if abs(impacto) >= IMPACTO_MODERADO
                       else "leve"),
        })
    return sorted(out, key=lambda x: -abs(x["impacto_esperado_pct"]))


def relatorio(ref: date | None = None) -> None:
    ref = ref or today_brt()
    print("=" * 74)
    print(f"CAMADA MACRO/GLOBAL — ref {ref}")
    print("=" * 74)

    datas = sorted(d for d in _fomc_datas() if d >= ref.isoformat())
    print("\n--- FOMC restante (calendário de market_alerts.MACRO_EVENTS) ---")
    if not datas:
        print("  nenhuma reunião futura no calendário — hora de atualizar MACRO_EVENTS")
    for iso in datas:
        dias = (date.fromisoformat(iso) - ref).days
        print(f"  {iso} ({dias:+5d}d) {'[DOT PLOT]' if tem_dot_plot(iso) else ''}")

    ev = em_janela_macro(ref)
    print(f"\nmodo macro agora: {'ATIVO — ' + ev['data'] if ev else 'inativo'}")

    if datas:
        proxima = date.fromisoformat(datas[0])
        print(f"\n--- Parâmetros na véspera do próximo FOMC ({proxima}) ---")
        ref_fomc = proxima - timedelta(days=1)
        for t in ["SNDK", "MU", "NVDA", "SMCI"]:
            p = parametros_completos(t, ref_fomc)
            if p:
                print(f"  {t:<5} vol_oper {p['vol_operacional_pct']:>6.2f}%  "
                      f"stop {p['stop_sugerido_pct']:>6.2f}%  [{p['modo']}]")

    print("\n--- Indicadores overnight (correlações vivas do radar) ---")
    carteira = _carteira_default()
    for proxy, info in INDICADORES_GLOBAIS.items():
        corrs = [(t, correlacao(proxy, t)) for t in carteira]
        txt = ", ".join(f"{t} {c:.2f}" for t, c in corrs if c is not None) or "sem correlação medida"
        print(f"  {proxy} ({info['pais']}, {info['indice']}): {txt}")
        print(f"     composição: {info['composicao']}")

    print("\n--- Quem antecipa cada posição (do dado, não de texto fixo) ---")
    lideres = lideres_por_posicao(carteira)
    if not lideres:
        print("  nenhuma posição com correlação medida contra os proxies")
    for l in lideres:
        margem = (f"  (+{l['margem_sobre_2o']:.2f} sobre o 2º)"
                  if l["margem_sobre_2o"] is not None else "")
        empatado = (l["margem_sobre_2o"] is not None and l["margem_sobre_2o"] < 0.05)
        print(f"  {l['posicao']:<6} <- {l['proxy']} ({l['indice']}) "
              f"corr {l['correlacao']:.2f}{margem}"
              + ("  [praticamente empatado — olhar os dois]" if empatado else ""))

    print("\n--- Simulação: Ásia caiu forte (EWY -3%, EWT -2%) ---")
    for s in sinal_overnight({"EWY": -3.0, "EWT": -2.0}):
        print(f"  {s['posicao']:<5} impacto estimado {s['impacto_esperado_pct']:+.2f}% "
              f"[{s['alerta']}]  ({'; '.join(s['componentes'])})")
    print()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Camada macro dos parâmetros de vol")
    ap.add_argument("--ticker", metavar="T")
    ap.add_argument("--ref", metavar="YYYY-MM-DD")
    ap.add_argument("--overnight", nargs="+", metavar="PROXY=MOV")
    args = ap.parse_args(argv)
    ref = date.fromisoformat(args.ref) if args.ref else None

    if args.ticker:
        print(json.dumps(parametros_completos(args.ticker, ref), ensure_ascii=False, indent=2))
    elif args.overnight:
        fech = {}
        for item in args.overnight:
            k, v = item.split("=")
            fech[k] = float(v)
        print(json.dumps(sinal_overnight(fech), ensure_ascii=False, indent=2))
    else:
        relatorio(ref)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
