#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
parametros_volatilidade.py — parâmetros operacionais derivados do Radar IA
==========================================================================
Transforma os dados do radar_ia_2026.py em números prontos pra decisão:

  1. Classe de regime de vol por ticker (extrema/alta/media/baixa)
  2. Conversões vol semanal -> diária -> anualizada
  3. Stop sugerido por ticker (múltiplo da vol, com modo earnings)
  4. Tamanho máximo de posição por orçamento de risco
  5. Vol de carteira COM covariância
  6. Contribuição de risco por posição
  7. Cenário de stress (correlações inflacionadas)

## Por que os itens 5-7 existem

radar_ia_2026.sizing_por_vol() dá pesos inversos à vol e IGNORA covariância
-- limitação que o próprio guia do pacote marcava como "usar só como teto
por posição, nunca como otimizador". Numa cesta em que quase tudo é do mesmo
cluster de memória/IA (correlações 0.7-0.8), ignorar covariância subestima
o risco justamente no cenário que importa: quando cai, cai tudo junto.
vol_carteira() abaixo usa a matriz completa de correlações medidas e é o
número que vale pra risco de carteira.

## Adaptações na integração ao Premercado (vs. o pacote original)

  - `ref` default é HOJE em BRT (brt.today_brt), não o HOJE_SNAPSHOT
    congelado de 14/08/2026 -- mesmo footgun já corrigido em
    radar_ia_2026.earnings_proximos: com o default velho, o "modo earnings"
    pararia de ligar sozinho depois que o snapshot envelhecesse.
  - Carteira default vem de config.PORTFOLIO_TICKERS (posições REAIS,
    injetadas pelo runner.ts a cada spawn) em vez do PORTFOLIO_DEFAULT
    hardcoded do pacote.

Uso:
    python -m agent.parametros_volatilidade                  # relatório
    python -m agent.parametros_volatilidade --ticker SNDK
    python -m agent.parametros_volatilidade --stop SMCI 45.50
    python -m agent.parametros_volatilidade --sizing SNDK 10000 1.0
    python -m agent.parametros_volatilidade --carteira MU=0.2 SMCI=0.2 --stress

AVISO: vol vem do snapshot do radar (marcadas est=True são estimativas de
setor, não medidas). Nada aqui é recomendação de investimento.
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import date

# Import dos DOIS jeitos: este arquivo roda como script solto (spawn por
# caminho) e como módulo do pacote -- mesmo padrão dos demais do agente.
try:
    from brt import today_brt
    from radar_ia_2026 import (CORR_ALTA, EARNINGS, PORTFOLIO_DEFAULT,
                               REACAO_EARNINGS, TEMA_IA, correlacao)
except ImportError:
    from agent.brt import today_brt
    from agent.radar_ia_2026 import (CORR_ALTA, EARNINGS, PORTFOLIO_DEFAULT,
                                     REACAO_EARNINGS, TEMA_IA, correlacao)

# Classes de vol semanal (%) -- cortes derivados da distribuição medida no
# snapshot: mediana ~6.0%, topo 18.72 (SNDK), piso 1.50 (NVDA).
CLASSES_VOL = [
    (10.0, "extrema"),   # SNDK 18.72, SMCI 12.30, WDC 11.31
    (6.0,  "alta"),      # STX 8.50, MU 7.89, CRWV 7.61, MRVL 6.58
    (3.0,  "media"),     # INTC 4.40, KLAC 4.24, LRCX 3.32
    (0.0,  "baixa"),     # ASML 2.40, NVDA 1.50
]

# Multiplicador do stop sobre a vol operacional, por classe. Repare que a
# classe EXTREMA tem o MENOR múltiplo: não é descuido -- num papel que já
# oscila 18% por semana, seguir alargando o stop proporcionalmente leva a
# um stop que só existe no papel. A resposta certa pra vol extrema é
# posição menor (ver tamanho_maximo), não stop infinito.
MULT_STOP = {"extrema": 1.2, "alta": 1.5, "media": 1.8, "baixa": 2.0}

# Correlação assumida quando o par não foi medido. Desde que
# atualizar_correlacoes.py passou a calcular a matriz COMPLETA do universo
# (561 pares), isso quase nunca entra -- fica como rede de segurança pra
# ticker fora do radar. Mediana intra-tema ~0.45.
CORR_DEFAULT = 0.45

# Em selloff de setor as correlações medidas em período normal sobem muito
# (observado no selloff de jun/2026 liderado por AVGO: pares ~0.5 indo pra
# ~0.75-0.85). O cenário de stress multiplica por isso, com teto em 1.0.
STRESS_FATOR = 1.6

# Dias antes do balanço em que a vol implícita passa a mandar na histórica.
JANELA_EARNINGS_DIAS = 7
# Sem move implícito nos dados, infla a histórica por precaução -- earnings
# é evento binário, a vol de dia normal subestima sistematicamente.
MULT_EARNINGS_SEM_IMPLICITA = 1.5

RAIZ_5 = math.sqrt(5)     # pregões por semana
RAIZ_52 = math.sqrt(52)   # semanas por ano


def _carteira_default() -> list[str]:
    """Posições REAIS quando disponíveis (config.PORTFOLIO_TICKERS é
    preenchida pelo runner.ts a partir do banco a cada spawn); cai no
    PORTFOLIO_DEFAULT do radar só quando roda fora desse contexto."""
    try:
        try:
            from config import PORTFOLIO_TICKERS
        except ImportError:
            from agent.config import PORTFOLIO_TICKERS
        if PORTFOLIO_TICKERS:
            return list(PORTFOLIO_TICKERS)
    except Exception:
        pass
    return list(PORTFOLIO_DEFAULT)


def _vol_sem(ticker: str) -> float | None:
    d = TEMA_IA.get(ticker.upper())
    return d["vol_sem"] if d else None


def classe_vol(vol_semanal: float) -> str:
    for corte, nome in CLASSES_VOL:
        if vol_semanal >= corte:
            return nome
    return "baixa"


def vol_diaria(vol_semanal: float) -> float:
    """vol_sem / sqrt(5) -- escala de raiz do tempo, assume independência
    entre pregões (aproximação padrão; ignora autocorrelação/momentum)."""
    return vol_semanal / RAIZ_5


def vol_anualizada(vol_semanal: float) -> float:
    """vol_sem * sqrt(52), mesma escala de raiz do tempo."""
    return vol_semanal * RAIZ_52


def em_janela_earnings(ticker: str, ref: date | None = None,
                       janela: int = JANELA_EARNINGS_DIAS) -> dict | None:
    """Info do balanço se ele cai nos próximos `janela` dias a partir de
    `ref` (default: hoje em BRT)."""
    ref = ref or today_brt()
    info = EARNINGS.get(ticker.upper())
    if not info:
        return None
    d = date.fromisoformat(info["data"])
    if 0 <= (d - ref).days <= janela:
        re = REACAO_EARNINGS.get(ticker.upper(), {})
        return {"data": info["data"], "dias": (d - ref).days,
                "move_impl_sem": re.get("move_impl_sem"), "evr": re.get("evr")}
    return None


def parametros(ticker: str, ref: date | None = None) -> dict | None:
    """Parâmetros operacionais completos: vol (sem/dia/ano), classe, beta,
    stop sugerido e o modo em vigor (normal ou earnings). None quando o
    ticker não tem vol no radar."""
    t = ticker.upper()
    d = TEMA_IA.get(t)
    if not d or not d.get("vol_sem"):
        return None
    vs = d["vol_sem"]
    ev = em_janela_earnings(t, ref)
    vol_operacional, modo = vs, "normal"
    if ev:
        if ev["move_impl_sem"] and ev["move_impl_sem"] > vs:
            # A implícita é a vol que o mercado está PRECIFICANDO pro evento
            # -- domina a histórica de dia normal quando é maior.
            #
            # NOTA: no snapshot de 14/08/2026 este ramo nunca dispara com
            # dado real -- nenhum ticker tem vol_sem (TEMA_IA, tema de
            # chips) e move_impl_sem (REACAO_EARNINGS, screening de
            # varejo/China) ao mesmo tempo; os dois conjuntos são
            # disjuntos. Na prática todo ticker do tema cai no ramo de
            # precaução abaixo. O caminho fica porque a próxima coleta de
            # EVR pode cobrir os nomes de chips -- e está coberto por teste
            # com injeção, pra não apodrecer sem ninguém perceber.
            vol_operacional = ev["move_impl_sem"]
            modo = f"earnings em {ev['dias']}d (implícita {ev['move_impl_sem']}%)"
        else:
            vol_operacional = round(vs * MULT_EARNINGS_SEM_IMPLICITA, 2)
            modo = (f"earnings em {ev['dias']}d (sem implícita — "
                    f"vol x{MULT_EARNINGS_SEM_IMPLICITA} por precaução)")
    cls = classe_vol(vol_operacional)
    return {
        "ticker": t,
        "grupo": d.get("grupo"),
        "vol_semanal_pct": vs,
        "vol_diaria_pct": round(vol_diaria(vs), 2),
        "vol_anualizada_pct": round(vol_anualizada(vs), 1),
        "vol_estimada": d.get("est"),
        "beta": d.get("beta"),
        "classe": cls,
        "vol_operacional_pct": vol_operacional,
        "modo": modo,
        "stop_sugerido_pct": round(vol_operacional * MULT_STOP[cls], 2),
        "nota_stop": ("classe extrema: preferir reduzir posição a alargar o "
                      "stop além disso" if cls == "extrema" else None),
    }


def stop_sugerido(ticker: str, preco_entrada: float,
                  ref: date | None = None) -> dict | None:
    """Converte o stop % sugerido em preço, dado o preço de entrada."""
    p = parametros(ticker, ref)
    if not p or not preco_entrada or preco_entrada <= 0:
        return None
    return {"ticker": p["ticker"], "entrada": preco_entrada,
            "stop_pct": p["stop_sugerido_pct"],
            "stop_preco": round(preco_entrada * (1 - p["stop_sugerido_pct"] / 100), 2),
            "classe": p["classe"], "modo": p["modo"]}


def tamanho_maximo(ticker: str, capital: float, risco_max_pct: float = 1.0,
                   ref: date | None = None) -> dict | None:
    """Posição máxima pra que, se o stop bater, a perda fique dentro de
    `risco_max_pct` do capital: posicao = capital * risco% / stop%.

    É o teto por posição ISOLADA -- não considera o que as outras posições
    fazem junto. Pra risco do conjunto, ver vol_carteira/contribuicao_risco."""
    p = parametros(ticker, ref)
    if not p or not capital or capital <= 0:
        return None
    risco_valor = capital * risco_max_pct / 100
    posicao = risco_valor / (p["stop_sugerido_pct"] / 100)
    return {"ticker": p["ticker"], "capital": capital,
            "risco_max_pct": risco_max_pct, "risco_valor": round(risco_valor, 2),
            "stop_pct": p["stop_sugerido_pct"],
            "posicao_maxima": round(min(posicao, capital), 2),
            "pct_do_capital": round(min(posicao / capital, 1.0) * 100, 1),
            "classe": p["classe"], "modo": p["modo"]}


def _corr(a: str, b: str, fator: float = 1.0) -> float:
    if a == b:
        return 1.0
    c = correlacao(a, b)
    if c is None:
        c = CORR_DEFAULT
    return min(1.0, c * fator)


def _normalizar(pesos: dict[str, float]) -> tuple[dict[str, float], dict[str, float], list[str]]:
    """Pesos normalizados só sobre tickers COM vol conhecida, mais a lista
    dos descartados -- carregar um ticker sem vol pra dentro da conta faria
    o peso dos outros mentir."""
    tk = {t.upper(): float(w) for t, w in pesos.items() if float(w) > 0}
    vols, sem_vol = {}, []
    for t in tk:
        v = _vol_sem(t)
        (sem_vol.append(t) if v is None else vols.__setitem__(t, v))
    tk = {t: w for t, w in tk.items() if t in vols}
    soma = sum(tk.values())
    if soma:
        tk = {t: w / soma for t, w in tk.items()}
    return tk, vols, sem_vol


def vol_carteira(pesos: dict[str, float], fator_corr: float = 1.0) -> dict:
    """Vol semanal da carteira COM covariância completa:

        sigma_p^2 = sum_i sum_j w_i w_j s_i s_j rho_ij

    `pesos` não precisa somar 1 (normaliza). `fator_corr`: 1.0 normal,
    STRESS_FATOR pro cenário de stress. Compara com a vol média ponderada
    (o que se teria sem NENHUMA diversificação) pra medir o benefício real."""
    tk, vols, sem_vol = _normalizar(pesos)
    if not tk:
        return {"erro": "nenhum ticker com vol conhecida", "ignorados_sem_vol": sem_vol}
    lista = list(tk)
    var, nao_medidos = 0.0, []
    for i, a in enumerate(lista):
        for j, b in enumerate(lista):
            if i < j and correlacao(a, b) is None:
                nao_medidos.append((a, b))
            var += tk[a] * tk[b] * vols[a] * vols[b] * _corr(a, b, fator_corr)
    vol_p = math.sqrt(var)
    vol_ingenua = sum(tk[t] * vols[t] for t in tk)
    return {
        "pesos_normalizados": {t: round(w, 3) for t, w in tk.items()},
        "vol_carteira_semanal_pct": round(vol_p, 2),
        "vol_carteira_anualizada_pct": round(vol_p * RAIZ_52, 1),
        "vol_sem_diversificacao_pct": round(vol_ingenua, 2),
        "beneficio_diversificacao_pct": round((1 - vol_p / vol_ingenua) * 100, 1) if vol_ingenua else 0.0,
        "fator_corr_usado": fator_corr,
        "pares_nao_medidos_default": nao_medidos,
        "ignorados_sem_vol": sem_vol,
    }


def contribuicao_risco(pesos: dict[str, float], fator_corr: float = 1.0) -> list[dict]:
    """Quanto da vol da carteira vem de cada posição (contribuição marginal
    normalizada, soma 100%). Revela o descasamento entre peso em CAPITAL e
    peso em RISCO -- é comum uma posição de 20% do capital responder por 30%
    do risco só por ser mais volátil e mais correlacionada com o resto."""
    base = vol_carteira(pesos, fator_corr)
    if "erro" in base:
        return []
    tk = base["pesos_normalizados"]
    vol_p = base["vol_carteira_semanal_pct"]
    if not vol_p:
        return []
    out = []
    for t in tk:
        cov_t = sum(tk[b] * _vol_sem(t) * _vol_sem(b) * _corr(t, b, fator_corr) for b in tk)
        out.append({"ticker": t, "peso_pct": round(tk[t] * 100, 1),
                    "contribuicao_risco_pct": round(tk[t] * cov_t / (vol_p ** 2) * 100, 1)})
    return sorted(out, key=lambda x: -x["contribuicao_risco_pct"])


def stress_carteira(pesos: dict[str, float]) -> dict:
    """Vol normal vs. cenário de stress (correlações x STRESS_FATOR, teto
    1.0). O número que interessa não é só a vol subir -- é o BENEFÍCIO DE
    DIVERSIFICAÇÃO encolher: é exatamente no selloff que a proteção some."""
    normal = vol_carteira(pesos, 1.0)
    stress = vol_carteira(pesos, STRESS_FATOR)
    if "erro" in normal or "erro" in stress:
        return {"erro": "nenhum ticker com vol conhecida"}
    vn, vs = normal["vol_carteira_semanal_pct"], stress["vol_carteira_semanal_pct"]
    return {
        "vol_normal_sem_pct": vn,
        "vol_stress_sem_pct": vs,
        "aumento_pct": round((vs / vn - 1) * 100, 1) if vn else 0.0,
        "beneficio_diversificacao_normal_pct": normal["beneficio_diversificacao_pct"],
        "beneficio_diversificacao_stress_pct": stress["beneficio_diversificacao_pct"],
        "leitura": ("em stress a diversificação encolhe -- se a carteira "
                    "precisa sobreviver a um selloff de setor, dimensionar "
                    "pela vol de stress, não pela normal"),
    }


def pares_concentrados(tickers: list[str]) -> list[dict]:
    """Pares da carteira com correlação >= CORR_ALTA -- o mesmo critério que
    o veredito_validator usa pra flagar concentração, aqui exposto pra quem
    estiver dimensionando posição."""
    tk = sorted({t.upper() for t in tickers})
    out = []
    for i, a in enumerate(tk):
        for b in tk[i + 1:]:
            c = correlacao(a, b)
            if c is not None and c >= CORR_ALTA:
                out.append({"par": (a, b), "correlacao": c})
    return sorted(out, key=lambda x: -x["correlacao"])


def relatorio(ref: date | None = None) -> None:
    ref = ref or today_brt()
    print("=" * 74)
    print(f"PARÂMETROS DE VOLATILIDADE — ref {ref}")
    print("=" * 74)
    print(f"\n{'TICKER':<7}{'CLASSE':<9}{'VOL SEM':>8}{'VOL DIA':>8}"
          f"{'VOL ANO':>8}{'BETA':>6}{'STOP%':>7}  MODO")
    med = sorted(((t, d) for t, d in TEMA_IA.items() if d.get("vol_sem")),
                 key=lambda x: -x[1]["vol_sem"])
    for t, _ in med:
        p = parametros(t, ref)
        est = "*" if p["vol_estimada"] else " "
        print(f"{t:<7}{p['classe']:<9}{p['vol_semanal_pct']:>7.2f}{est}"
              f"{p['vol_diaria_pct']:>8.2f}{p['vol_anualizada_pct']:>8.1f}"
              f"{p['beta'] or 0:>6.2f}{p['stop_sugerido_pct']:>7.2f}  {p['modo']}")
    print("\n(*) vol estimada de setor, não medida")

    carteira = _carteira_default()
    print(f"\n--- CARTEIRA ({', '.join(carteira)}, pesos iguais) ---")
    pesos = {t: 1 for t in carteira}
    vc = vol_carteira(pesos)
    if "erro" in vc:
        print(f"  {vc['erro']}")
        return
    print(f"vol semanal: {vc['vol_carteira_semanal_pct']}% | anualizada: "
          f"{vc['vol_carteira_anualizada_pct']}% | sem diversificação seria "
          f"{vc['vol_sem_diversificacao_pct']}% (benefício "
          f"{vc['beneficio_diversificacao_pct']}%)")
    if vc["ignorados_sem_vol"]:
        # Sem isso o número parece cobrir a carteira inteira quando na
        # verdade cobre só parte dela -- pior que não ter número.
        print(f"⚠ FORA DA CONTA (sem vol no radar): "
              f"{', '.join(vc['ignorados_sem_vol'])} — os números acima "
              f"descrevem só as demais posições")
    st = stress_carteira(pesos)
    print(f"stress (corr x{STRESS_FATOR}): {st['vol_stress_sem_pct']}% "
          f"(+{st['aumento_pct']}%) | diversificação cai de "
          f"{st['beneficio_diversificacao_normal_pct']}% para "
          f"{st['beneficio_diversificacao_stress_pct']}%")
    print("\ncontribuição de risco (peso em capital -> peso em risco):")
    for c in contribuicao_risco(pesos):
        print(f"  {c['ticker']:<6} {c['peso_pct']:>5.1f}%  ->  "
              f"{c['contribuicao_risco_pct']:>5.1f}%")
    conc = pares_concentrados(carteira)
    if conc:
        print("\npares no mesmo trade (corr >= 0.70):")
        for c in conc:
            print(f"  {c['par'][0]}-{c['par'][1]}: {c['correlacao']:.2f}")
    print()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Parâmetros operacionais de volatilidade")
    ap.add_argument("--ticker", metavar="T")
    ap.add_argument("--stop", nargs=2, metavar=("T", "PRECO"))
    ap.add_argument("--sizing", nargs=3, metavar=("T", "CAPITAL", "RISCO%"))
    ap.add_argument("--carteira", nargs="+", metavar="T=PESO")
    ap.add_argument("--stress", action="store_true", help="com --carteira: cenário de stress")
    args = ap.parse_args(argv)

    if args.ticker:
        print(json.dumps(parametros(args.ticker), ensure_ascii=False, indent=2))
    elif args.stop:
        t, preco = args.stop
        print(json.dumps(stop_sugerido(t, float(preco)), ensure_ascii=False, indent=2))
    elif args.sizing:
        t, cap, r = args.sizing
        print(json.dumps(tamanho_maximo(t, float(cap), float(r)), ensure_ascii=False, indent=2))
    elif args.carteira:
        pesos = {}
        for item in args.carteira:
            t, w = item.split("=")
            pesos[t] = float(w)
        saida = stress_carteira(pesos) if args.stress else vol_carteira(pesos)
        print(json.dumps(saida, ensure_ascii=False, indent=2))
        print(json.dumps(contribuicao_risco(pesos), ensure_ascii=False, indent=2))
    else:
        relatorio()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
