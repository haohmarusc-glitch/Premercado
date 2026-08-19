#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
radar_ia_2026.py — Módulo de análise consolidada do tema IA (ago/set 2026)
============================================================================
Dados coletados em 12-14/08/2026 (fontes: Alpha Vantage Analytics, OptionSlam,
ChartRow, TradingView/StockAnalysis, Yahoo/Investing — ver seção FONTES no fim).

Uso standalone:
    python radar_ia_2026.py                  # relatório completo
    python radar_ia_2026.py --portfolio      # análise do portfólio
    python radar_ia_2026.py --earnings 14    # earnings nos próximos 14 dias
    python radar_ia_2026.py --corr MU SNDK   # correlação entre dois tickers
    python radar_ia_2026.py --cluster MU     # cluster e vizinhos do ticker
    python radar_ia_2026.py --json           # exporta tudo em JSON

Ou importe no Premercado:
    from radar_ia_2026 import (correlacao, cluster_de, risco_portfolio,
                               earnings_proximos, alerta_contagio, DADOS)

AVISO: dados estáticos — snapshot de 14/08/2026. Revalidar antes de operar.

Adaptações feitas na integração ao Premercado (vs. o pacote original):
  - earnings_proximos() usa HOJE em BRT como referência default, não o
    HOJE_SNAPSHOT congelado — o guia original alertava que esquecer de passar
    ref=date.today() faria os alertas "congelarem" em 14/08/2026; aqui o
    default seguro é o comportamento padrão e o snapshot só é usado se pedido
    explicitamente (ref=HOJE_SNAPSHOT).
  - ajustar_scores_por_cluster() (Passo 1 do guia) mora aqui no módulo, não
    num "itci_engine.py" (que não existe neste repo) — consumidores reais:
    market_alerts.check_sinais_correlacionados e qualquer ranking futuro.
  - Sem carregar_radar_db.py/psql: neste repo Python não acessa o Postgres
    (todo acesso a banco é via Node/Drizzle) — a tela Radar IA lê estes dados
    via rota que spawna `--json` (mesmo padrão de /confluence, /backtest).
"""

from __future__ import annotations
import argparse
import json
import os
import sys
from datetime import date, timedelta
from itertools import combinations
# Serializacao que nao emite NaN/Infinity -- ver json_seguro.py. Import
# duplo porque estes scripts rodam dos DOIS jeitos: spawn por caminho
# (imports planos) e como membro do pacote agent.
try:
    import json_seguro
except ImportError:
    from agent import json_seguro


# brt.today_brt: import dos DOIS jeitos porque este script roda dos dois
# jeitos (flat via spawn direto por caminho, e como módulo do pacote agent) --
# mesmo padrão documentado em earnings_reaction_analysis.py.
try:
    from brt import today_brt
    import market_data_provider
    import radar_overrides as _radar_overrides
except ImportError:
    from agent.brt import today_brt
    from agent import market_data_provider
    from agent import radar_overrides as _radar_overrides

HOJE_SNAPSHOT = date(2026, 8, 14)  # data de referência dos dados

# ============================================================================
# 1. CALENDÁRIO DE EARNINGS (ago-set/2026)  "BO"=antes da abertura, "AC"=após
# ============================================================================
EARNINGS = {
    # -- semicondutores --
    "ADI":  {"data": "2026-08-19", "quando": None, "setor": "semis"},
    "WOLF": {"data": "2026-08-19", "quando": None, "setor": "semis"},
    "NVDA": {"data": "2026-08-26", "quando": "AC", "setor": "semis"},
    "SNPS": {"data": "2026-08-26", "quando": None, "setor": "semis"},
    "AVGO": {"data": "2026-09-03", "quando": "AC", "setor": "semis"},
    "MU":   {"data": "2026-09-22", "quando": "AC", "setor": "semis"},
    # -- software/tech --
    "INTU": {"data": "2026-08-25", "quando": None, "setor": "software"},
    "CRM":  {"data": "2026-08-26", "quando": None, "setor": "software"},
    "CRWD": {"data": "2026-08-26", "quando": None, "setor": "software",
             "nota": "fontes divergem: pode ser 01/09"},
    "S":    {"data": "2026-08-27", "quando": None, "setor": "software"},
    "ADSK": {"data": "2026-08-27", "quando": "AC", "setor": "software"},
    "PANW": {"data": "2026-09-01", "quando": None, "setor": "software"},
    "MDB":  {"data": "2026-09-01", "quando": None, "setor": "software"},
    "AI":   {"data": "2026-09-02", "quando": None, "setor": "software"},
    "SNOW": {"data": "2026-09-02", "quando": None, "setor": "software"},
    "ZS":   {"data": "2026-09-03", "quando": None, "setor": "software"},
    "ORCL": {"data": "2026-09-08", "quando": None, "setor": "software",
             "nota": "outra fonte: 14/09"},
    "ADBE": {"data": "2026-09-10", "quando": None, "setor": "software"},
    "CSCO": {"data": "2026-09-30", "quando": None, "setor": "networking"},
    # -- varejo --
    "HD":   {"data": "2026-08-18", "quando": "BO", "setor": "varejo"},
    "LOW":  {"data": "2026-08-19", "quando": "BO", "setor": "varejo"},
    "TJX":  {"data": "2026-08-19", "quando": None, "setor": "varejo"},
    "TGT":  {"data": "2026-08-19", "quando": None, "setor": "varejo"},
    "WMT":  {"data": "2026-08-20", "quando": None, "setor": "varejo"},
    "ROST": {"data": "2026-08-20", "quando": None, "setor": "varejo"},
    "BBY":  {"data": "2026-08-27", "quando": None, "setor": "varejo"},
    "ULTA": {"data": "2026-08-27", "quando": "AC", "setor": "varejo"},
    "DLTR": {"data": "2026-09-02", "quando": None, "setor": "varejo"},
    "LULU": {"data": "2026-09-03", "quando": None, "setor": "varejo"},
    "GME":  {"data": "2026-09-08", "quando": None, "setor": "varejo"},
    "KR":   {"data": "2026-09-10", "quando": None, "setor": "varejo"},
    "COST": {"data": "2026-09-24", "quando": None, "setor": "varejo"},
    # -- China ADRs --
    "BIDU": {"data": "2026-08-18", "quando": None, "setor": "china"},
    "NTES": {"data": "2026-08-20", "quando": "BO", "setor": "china"},
    "XPEV": {"data": "2026-08-24", "quando": "BO", "setor": "china"},
    "PDD":  {"data": "2026-08-24", "quando": "BO", "setor": "china",
             "nota": "~24-25/08, não confirmado oficialmente"},
    "BILI": {"data": "2026-08-27", "quando": None, "setor": "china"},
    "BABA": {"data": "2026-09-04", "quando": None, "setor": "china"},
    # -- EV --
    "LI":   {"data": "2026-08-27", "quando": None, "setor": "ev"},
    "NIO":  {"data": "2026-09-01", "quando": None, "setor": "ev"},
    # -- outros --
    "EL":   {"data": "2026-08-19", "quando": None, "setor": "outros"},
    "DE":   {"data": "2026-08-20", "quando": None, "setor": "outros"},
    "TOL":  {"data": "2026-08-18", "quando": None, "setor": "outros"},
    "NKE":  {"data": "2026-09-29", "quando": None, "setor": "outros"},
    # -- tema IA (datas citadas na análise de volatilidade) --
    "SNDK": {"data": "2026-11-05", "quando": None, "setor": "memoria"},
    "STX":  {"data": "2026-10-28", "quando": None, "setor": "memoria"},
    "LRCX": {"data": "2026-10-21", "quando": None, "setor": "equipamento"},
    "KLAC": {"data": "2026-10-28", "quando": None, "setor": "equipamento"},
    "ASML": {"data": "2026-10-14", "quando": None, "setor": "equipamento"},
    "TSM":  {"data": "2026-10-15", "quando": None, "setor": "foundry"},
    "MRVL": {"data": "2026-08-27", "quando": None, "setor": "semis",
             "nota": "fontes divergem: 20 ou 27/08"},
}

# ============================================================================
# 2. SCREENING MÍNIMA 52 SEMANAS (preços de 12-14/08/2026)
# ============================================================================
MIN52 = {
    # dentro da faixa (<= 10% acima da mínima)
    "PDD":  {"preco": 84.5,   "min52": 87.11,  "status": "dentro"},
    "XPEV": {"preco": 11.75,  "min52": 11.49,  "status": "dentro"},
    "ADSK": {"preco": 233.51, "min52": 214.10, "status": "dentro"},
    "LOW":  {"preco": 218.0,  "min52": 199.40, "status": "dentro"},
    # borderline (10-20%)
    "SNPS": {"preco": 417.22, "min52": 366.00, "status": "borderline"},
    "NTES": {"preco": 122.65, "min52": 106.06, "status": "borderline"},
    "ULTA": {"preco": 515.57, "min52": 443.60, "status": "borderline"},
    "COST": {"preco": 935.03, "min52": 844.06, "status": "borderline"},
    "EL":   {"preco": 77.0,   "min52": 66.22,  "status": "borderline"},
    "HD":   {"preco": 341.70, "min52": 289.10, "status": "borderline"},
}

# --- Busca viva de preço e faixa de 52 semanas -------------------------------
#
# Os valores acima são o SNAPSHOT de 12-14/08/2026 e servem só de fallback.
# Deixá-los como única fonte produziu o erro que a auditoria de 17/08/2026
# pegou: PDD com preco 84,5 ABAIXO da própria min52 87,11 -- impossível por
# construção, e a min52 real era 71,94. Dado velho não erra só de magnitude;
# erra de forma, e forma impossível passa despercebida quando ninguém olha.
#
# auto_adjust=True e permitir_externa=False pela mesma razão de get_trend e
# get_technicals: a série é ajustada, a fonte externa devolve "as traded", e
# um desdobramento dentro da janela viraria uma mínima de 52 semanas que nunca
# existiu.
#
# Extremos vêm de Low/High (intradiário), não do fechamento -- é a definição
# que fast_info.year_low usa e que o resto do app já mostra. Usar fechamento
# aqui daria dois "min52" diferentes com o mesmo nome nas telas.
MIN52_PERIODO = "1y"
MIN52_DENTRO_PCT = 10.0      # <= 10% acima da mínima
MIN52_BORDERLINE_PCT = 20.0  # 10-20%


def _status_min52(preco: float, min52: float) -> str:
    # round antes de comparar: 110/100-1 dá 0.10000000000000009 em float, e
    # sem isso um papel EXATAMENTE a +10% da mínima cai em "borderline" quando
    # a regra escrita é "<= 10% = dentro". Erro de uma casa em ponto flutuante
    # decidindo classificação é o tipo de coisa que ninguém encontra olhando o
    # número final.
    acima = round((preco / min52 - 1) * 100, 6)
    if acima <= MIN52_DENTRO_PCT:
        return "dentro"
    return "borderline" if acima <= MIN52_BORDERLINE_PCT else "fora"


def validar_min52(linha: dict) -> list[str]:
    """Inconsistências que não podem existir num dado bom.

    Mesma ideia do veredito_validator: conferir ANTES de servir, porque um
    número impossível com cara de número calculado não levanta suspeita em
    quem lê.
    """
    avisos = []
    preco, mn, mx = linha.get("preco"), linha.get("min52"), linha.get("max52")
    if preco is not None and mn is not None and preco < mn:
        avisos.append(f"preço {preco} abaixo da mínima de 52 semanas {mn}")
    if preco is not None and mx is not None and preco > mx:
        avisos.append(f"preço {preco} acima da máxima de 52 semanas {mx}")
    if mn is not None and mx is not None and mn > mx:
        avisos.append(f"mínima {mn} maior que a máxima {mx}")
    return avisos


def atualizar_min52_vivo(tickers: list[str] | None = None) -> dict:
    """Substitui preço/mínima/máxima do snapshot por valores vivos.

    Devolve `{"atualizados": [...], "fontesDegradadas": {...}, "avisos": [...]}`.
    Ticker cuja série não vier NÃO é sobrescrito: mantém o valor do snapshot,
    mas entra em fontesDegradadas. Degradar em silêncio para o valor velho é
    exatamente o que se está corrigindo aqui.
    """
    alvos = tickers or list(MIN52)
    resultado: dict = {"atualizados": [], "fontesDegradadas": {}, "avisos": []}

    # Aquece o hist_cache com UMA chamada em lote antes do laço. Os tickers do
    # screening (PDD, XPEV, ULTA, HD...) não estão na carteira, então nenhum
    # outro checker os deixou no cache -- sem isto seriam ~10 downloads
    # sequenciais, e o custo cairia inteiro no primeiro acesso pós-deploy.
    #
    # O lote devolve só fechamentos; os extremos precisam de Low/High. Por isso
    # ele serve de aquecimento (get_daily_closes_batch grava o OHLCV completo
    # por ticker no hist_cache) e a leitura real vem do get_daily_history
    # abaixo, que então acerta o cache quente.
    try:
        market_data_provider.get_daily_closes_batch(
            alvos, MIN52_PERIODO, auto_adjust=True, permitir_externa=False
        )
    except Exception as e:  # noqa: BLE001 — aquecimento é otimização, não requisito
        print(f"[radar] lote de aquecimento falhou ({type(e).__name__}); "
              f"seguindo ticker a ticker", file=sys.stderr, flush=True)

    for t in alvos:
        try:
            r = market_data_provider.get_daily_history(
                t, MIN52_PERIODO, auto_adjust=True, permitir_externa=False
            )
        except Exception as e:  # noqa: BLE001 — fonte fora do ar não derruba o radar
            resultado["fontesDegradadas"][t] = f"erro: {type(e).__name__}"
            continue

        if not r.ok or r.df.empty:
            resultado["fontesDegradadas"][t] = "sem série utilizável"
            continue

        df = r.df
        try:
            preco = round(float(df["Close"].iloc[-1]), 2)
            mn = round(float(df["Low"].min()), 2)
            mx = round(float(df["High"].max()), 2)
        except (KeyError, IndexError, ValueError) as e:
            resultado["fontesDegradadas"][t] = f"série incompleta: {type(e).__name__}"
            continue

        linha = {"preco": preco, "min52": mn, "max52": mx,
                 "status": _status_min52(preco, mn), "fonte": r.source}
        avisos = validar_min52(linha)
        if avisos:
            # Dado vivo inconsistente é pior que dado velho: não sobrescreve,
            # avisa. O snapshot ao menos é reconhecidamente antigo.
            resultado["avisos"].extend(f"{t}: {a}" for a in avisos)
            resultado["fontesDegradadas"][t] = "dado vivo inconsistente, mantido o snapshot"
            continue

        if r.is_stale or r.source not in ("yfinance", "yfinance_cache"):
            resultado["fontesDegradadas"][t] = r.source

        MIN52[t] = linha
        resultado["atualizados"].append(t)

    return resultado


# ============================================================================
# 3. REAÇÃO HISTÓRICA A EARNINGS (coleta MANUAL, OptionSlam)
# ============================================================================
# Estes números não têm API: alguém abre o OptionSlam e transcreve. Antes eles
# viviam embutidos aqui, indistinguíveis do resto -- dado manual disfarçado de
# vivo, que envelhece em silêncio.
#
# Agora moram em dados/radar_overrides.json, versionados e com `coletado_em`
# obrigatório, e o relatório imprime a idade. A auditoria de 17/08/2026 pegou o
# custo de não fazer isso: o snapshot de 13/08 seguia sendo servido como se
# fosse de hoje.
#
# A leitura em si vive em radar_overrides.py: o earnings_window também precisa
# destes números (fallback do move implícito quando a cadeia de opções não
# responde), e duas cópias da leitura divergiriam na primeira mudança de
# formato -- o padrão de bug do playbook §2b.
REACAO_EARNINGS, OVERRIDES_COLETADO_EM, OVERRIDES_FONTE = _radar_overrides.carregar()


def idade_overrides_dias(ref: date | None = None) -> int | None:
    """Há quantos dias os dados manuais foram coletados. None sem carimbo."""
    return _radar_overrides.idade_dias(OVERRIDES_COLETADO_EM, ref)


# ============================================================================
# 4. RISCOS POR TICKER (dos 7 priorizados + AOSL)
# ============================================================================
RISCOS = {
    "XPEV": ["guerra de preços EV China (vendas domésticas caindo 10 meses)",
             "concorrência BYD", "risco ADR/delisting (cauda)",
             "tese physical AI ainda não monetizada",
             "positivo: 1º trimestre lucrativo no Q4/2025"],
    "PDD":  ["última reação -10.37% mesmo com fundamentos ok",
             "GMV 6.18 cresceu só +0.9% YoY (fadiga do consumidor)",
             "tarifas EUA no Temu (de minimis)", "guerra de preços vs BABA/JD",
             "P/E ~8.7x já precifica pessimismo (proteção ou armadilha)"],
    "ADSK": ["medo de disrupção IA no CAD", "aquisição MaintainX $3.6B (cautela)",
             "sensível a juros/CAPEX industrial", "positivo: volatilidade baixa"],
    "LOW":  ["imobiliário travado por juros", "Zacks 4/Sell (barra baixa)",
             "risco setorial indireto (CEO HD afastado)", "perfil defensivo"],
    "NTES": ["dependência de títulos específicos de games",
             "regulação de games na China",
             "positivo: Goldman APAC Conviction List"],
    "SNPS": ["overhang IA: Kimi K3 projetou chip em 48h, derrubou SNPS/CDNS ~10%",
             "corte de 10% do quadro", "receita de IP -8% YoY",
             "integração Ansys em andamento",
             "padrão: beats de EPS não seguram o papel"],
    "ULTA": ["Sephora/Kohl's e Amazon comendo share em beleza",
             "controvérsia de marketing (ruído)",
             "consumo discricionário sensível",
             "duas últimas reações negativas"],
    "AOSL": ["guidance Q1 FY27 $176M abaixo do consenso $181M",
             "non-GAAP anual virou prejuízo -$12.9M",
             "sem catalisador de earnings até novembro"],
}

# ============================================================================
# 5. TEMA IA — YTD 2026, VOLATILIDADE E BETA (fechamento 13/08/2026)
#    vol_sem = volatilidade semanal % (TradingView); est=True quando estimado
# ============================================================================
TEMA_IA = {
    "SNDK": {"ytd": 543.5, "vol_sem": 18.72, "beta": 3.79, "est": False,
             "grupo": "memoria", "driver": "NAND, datacenter +437% receita YoY"},
    "DELL": {"ytd": 292.8, "vol_sem": None,  "beta": None, "est": True,
             "grupo": "hardware", "driver": "servidores AI"},
    "STX":  {"ytd": 234.6, "vol_sem": 8.50,  "beta": 2.59, "est": False,
             "grupo": "memoria", "driver": "storage AI/datacenter"},
    "MU":   {"ytd": 232.8, "vol_sem": 7.89,  "beta": 3.02, "est": False,
             "grupo": "memoria", "driver": "HBM, demanda > oferta"},
    "INTC": {"ytd": 183.6, "vol_sem": 4.40,  "beta": 3.39, "est": False,
             "grupo": "chips", "driver": "turnaround"},
    "WDC":  {"ytd": 182.8, "vol_sem": 11.31, "beta": 2.28, "est": False,
             "grupo": "memoria", "driver": "storage"},
    "MRVL": {"ytd": 161.6, "vol_sem": 6.58,  "beta": 1.78, "est": False,
             "grupo": "chips", "driver": "ASIC customizado, óptica"},
    "ARM":  {"ytd": 154.7, "vol_sem": 11.0,  "beta": 3.77, "est": True,
             "grupo": "chips", "driver": "licenciamento; litígio QCOM Q4/26"},
    "HPE":  {"ytd": 149.0, "vol_sem": 3.21,  "beta": 0.89, "est": False,
             "grupo": "hardware", "driver": "servidores"},
    "AMD":  {"ytd": 125.6, "vol_sem": 6.3,   "beta": 1.78, "est": True,
             "grupo": "chips", "driver": "MI300, alternativa NVDA"},
    "AMAT": {"ytd": 108.4, "vol_sem": 6.01,  "beta": 1.50, "est": False,
             "grupo": "equipamento", "driver": "equipamento fabricação"},
    "LRCX": {"ytd": 97.0,  "vol_sem": 3.32,  "beta": 2.37, "est": False,
             "grupo": "equipamento", "driver": "equipamento"},
    "VRT":  {"ytd": 77.3,  "vol_sem": 5.5,   "beta": 1.90, "est": True,
             "grupo": "energia", "driver": "resfriamento datacenter"},
    "ASML": {"ytd": 72.8,  "vol_sem": 2.40,  "beta": 2.20, "est": False,
             "grupo": "equipamento", "driver": "litografia EUV"},
    "KLAC": {"ytd": 72.4,  "vol_sem": 4.24,  "beta": 1.83, "est": False,
             "grupo": "equipamento", "driver": "equipamento"},
    "GEV":  {"ytd": 60.5,  "vol_sem": 5.5,   "beta": 1.50, "est": True,
             "grupo": "energia", "driver": "energia datacenter"},
    "ANET": {"ytd": 55.4,  "vol_sem": 4.5,   "beta": 1.50, "est": True,
             "grupo": "networking", "driver": "networking datacenter"},
    "CRWV": {"ytd": 48.5,  "vol_sem": 7.61,  "beta": 3.07, "est": False,
             "grupo": "neocloud", "driver": "aluguel de GPU"},
    "CSCO": {"ytd": 47.3,  "vol_sem": 2.5,   "beta": 0.95, "est": True,
             "grupo": "networking", "driver": "networking maduro"},
    "ETN":  {"ytd": 42.3,  "vol_sem": 3.0,   "beta": 1.20, "est": True,
             "grupo": "energia", "driver": "elétrica industrial"},
    "TSM":  {"ytd": 41.7,  "vol_sem": 3.5,   "beta": 1.30, "est": True,
             "grupo": "foundry", "driver": "fundição líder; geopolítica Taiwan"},
    "SMCI": {"ytd": 33.7,  "vol_sem": 12.30, "beta": 2.99, "est": False,
             "grupo": "hardware", "driver": "servidores AI; marca contábil 2024"},
    "NVDA": {"ytd": 20.8,  "vol_sem": 1.50,  "beta": 1.63, "est": False,
             "grupo": "chips", "driver": "líder; já precificado"},
    "AVGO": {"ytd": 20.7,  "vol_sem": 4.0,   "beta": 2.73, "est": False,
             "grupo": "chips", "driver": "ASIC p/ hyperscalers"},
    "AMZN": {"ytd": 14.9,  "vol_sem": 2.5,   "beta": 1.20, "est": True,
             "grupo": "hyperscaler", "driver": "AWS"},
    "GOOGL": {"ytd": 10.7, "vol_sem": 2.0,   "beta": 1.00, "est": True,
              "grupo": "hyperscaler", "driver": "TPU/cloud"},
    "MSFT": {"ytd": 2.7,   "vol_sem": 1.75,  "beta": 0.95, "est": True,
             "grupo": "hyperscaler", "driver": "maduro/diversificado"},
    "PLTR": {"ytd": 0.7,   "vol_sem": 5.7,   "beta": 2.0,  "est": True,
             "grupo": "software", "driver": "valuation extremo ~150x"},
    "QCOM": {"ytd": -3.6,  "vol_sem": 3.0,   "beta": 1.25, "est": True,
             "grupo": "chips", "driver": "smartphone fraco domina"},
    "VST":  {"ytd": -9.2,  "vol_sem": 5.5,   "beta": 1.60, "est": True,
             "grupo": "energia", "driver": "geração; preço spot"},
    "META": {"ytd": -9.9,  "vol_sem": 3.0,   "beta": 1.25, "est": True,
             "grupo": "hyperscaler", "driver": "CAPEX pesa no FCF"},
    "ORCL": {"ytd": -19.8, "vol_sem": 3.6,   "beta": 2.66, "est": False,
             "grupo": "software", "driver": "dívida p/ CAPEX, ceticismo"},
    "CEG":  {"ytd": -21.1, "vol_sem": 4.5,   "beta": 0.80, "est": True,
             "grupo": "energia", "driver": "nuclear; PPAs; maior perdedor"},
}

# ============================================================================
# 6. CORRELAÇÕES MEDIDAS (Alpha Vantage, retornos diários 13/02-14/08/2026)
#    Pares simétricos — usar correlacao(a, b) para lookup.
#    EWY = ETF iShares South Korea (proxy Samsung, ~25-30% do peso).
# ============================================================================
CORRELACOES = {
    # cluster memória
    ("WDC", "SNDK"): 0.71, ("WDC", "MU"): 0.73, ("WDC", "STX"): 0.88,
    ("SNDK", "MU"): 0.82, ("SNDK", "STX"): 0.71, ("MU", "STX"): 0.69,
    # cluster equipamento
    ("LRCX", "KLAC"): 0.89, ("LRCX", "ASML"): 0.87, ("LRCX", "AMAT"): 0.92,
    ("KLAC", "ASML"): 0.82, ("KLAC", "AMAT"): 0.90, ("ASML", "AMAT"): 0.82,
    # pontes memória <-> equipamento <-> foundry
    ("MU", "LRCX"): 0.80, ("SNDK", "AMAT"): 0.73, ("TSM", "AMAT"): 0.72,
    ("SNDK", "TSM"): 0.56, ("VRT", "AMAT"): 0.66, ("VRT", "TSM"): 0.63,
    ("VRT", "SNDK"): 0.53, ("CRWV", "LRCX"): 0.48, ("SMCI", "LRCX"): 0.49,
    ("CEG", "LRCX"): 0.30, ("MU", "CRWV"): 0.44, ("CEG", "MU"): 0.22,
    ("CEG", "CRWV"): 0.13, ("CEG", "SMCI"): 0.45,
    # cluster energia
    ("VRT", "ETN"): 0.78, ("VRT", "GEV"): 0.64, ("VRT", "ANET"): 0.52,
    ("ETN", "GEV"): 0.67, ("ETN", "ANET"): 0.52, ("ANET", "GEV"): 0.46,
    ("CEG", "VST"): 0.76, ("CEG", "TSM"): 0.30, ("CEG", "CSCO"): 0.03,
    ("VST", "TSM"): 0.39, ("VST", "CSCO"): 0.07, ("TSM", "CSCO"): 0.32,
    # cluster chips
    ("ARM", "AVGO"): 0.51, ("ARM", "AMD"): 0.68, ("ARM", "MRVL"): 0.56,
    ("AVGO", "AMD"): 0.51, ("AVGO", "MRVL"): 0.55, ("AMD", "MRVL"): 0.56,
    # cluster hardware/neocloud
    ("DELL", "HPE"): 0.64, ("DELL", "SMCI"): 0.47, ("HPE", "SMCI"): 0.49,
    ("SMCI", "CRWV"): 0.38, ("DELL", "CRWV"): 0.26, ("HPE", "CRWV"): 0.35,
    ("DELL", "SNDK"): 0.34, ("DELL", "TSM"): 0.27, ("DELL", "AMAT"): 0.33,
    ("DELL", "VRT"): 0.27,
    # hyperscalers
    ("AMZN", "MSFT"): 0.43, ("AMZN", "GOOGL"): 0.62, ("AMZN", "META"): 0.50,
    ("MSFT", "GOOGL"): 0.18, ("MSFT", "META"): 0.22, ("GOOGL", "META"): 0.38,
    # software/perdedores/turnaround
    ("INTC", "QCOM"): 0.54, ("INTC", "ORCL"): 0.20, ("INTC", "PLTR"): 0.04,
    ("QCOM", "ORCL"): 0.21, ("QCOM", "PLTR"): 0.13, ("ORCL", "PLTR"): 0.39,
    # portfólio cruzado
    ("AVGO", "SMCI"): 0.45, ("AVGO", "MU"): 0.50, ("ARM", "SMCI"): 0.41,
    ("ARM", "MU"): 0.49, ("MRVL", "SMCI"): 0.42, ("MRVL", "MU"): 0.55,
    ("SMCI", "MU"): 0.43,
    # NVDA vs todos (âncora)
    ("NVDA", "TSM"): 0.66, ("NVDA", "ASML"): 0.54, ("NVDA", "KLAC"): 0.52,
    ("NVDA", "SMCI"): 0.51, ("NVDA", "CRWV"): 0.50, ("NVDA", "LRCX"): 0.50,
    ("NVDA", "VRT"): 0.49, ("NVDA", "AMD"): 0.48, ("NVDA", "AVGO"): 0.48,
    ("NVDA", "AMAT"): 0.47, ("NVDA", "MU"): 0.44, ("NVDA", "INTC"): 0.43,
    ("NVDA", "ARM"): 0.42, ("NVDA", "ETN"): 0.40, ("NVDA", "ORCL"): 0.39,
    ("NVDA", "GEV"): 0.38, ("NVDA", "ANET"): 0.38, ("NVDA", "SNDK"): 0.37,
    ("NVDA", "META"): 0.37, ("NVDA", "MRVL"): 0.37, ("NVDA", "WDC"): 0.36,
    ("NVDA", "STX"): 0.34, ("NVDA", "AMZN"): 0.32, ("NVDA", "GOOGL"): 0.27,
    ("NVDA", "CSCO"): 0.25, ("NVDA", "VST"): 0.24, ("NVDA", "MSFT"): 0.24,
    ("NVDA", "PLTR"): 0.19, ("NVDA", "QCOM"): 0.15, ("NVDA", "CEG"): 0.14,
    ("NVDA", "DELL"): 0.21, ("NVDA", "HPE"): 0.32,
    # proxy Samsung (EWY)
    ("EWY", "MU"): 0.81, ("EWY", "SNDK"): 0.74, ("EWY", "NVDA"): 0.52,
    ("EWY", "SMCI"): 0.51,
}

PORTFOLIO_DEFAULT = ["MU", "SMCI", "ARM", "MRVL", "AVGO"]

# Limiares de interpretação
CORR_ALTA = 0.70      # mesmo trade
CORR_MODERADA = 0.40  # fator compartilhado

# ── Overlay de correlações atualizadas (atualizar_correlacoes.py) ──────────
# O snapshot embutido acima é de 14/08/2026; o script de refresh grava um
# JSON com a janela recalculada e este bloco o aplica POR CIMA no import.
# Falha em qualquer ponto (arquivo ausente, JSON inválido, shape errado) é
# silenciosa de propósito: o embutido continua valendo -- dado velho bem
# rotulado é melhor que processo quebrado. Pares do snapshot que o overlay
# não cobre permanecem com o valor (e a janela) originais.
CORRELACOES_JANELA_FIM = HOJE_SNAPSHOT.isoformat()
CORRELACOES_ATUALIZADO_EM: str | None = None
# Quantos tickers tiveram a vol de TEMA_IA substituída por vol MEDIDA por
# nós (ver adiante) -- 0 significa que tudo ainda vem da coleta manual.
VOL_MEDIDA_APLICADA = 0

_OVERLAY_PATH_DEFAULT = "/var/cache/premercado/radar_correlacoes.json"

# ── Overlay do calendário de earnings (atualizar_earnings.py) ──────────────
# O EARNINGS acima era digitado à mão e é o pedaço mais perecível do
# snapshot: correlação de 6 meses se move devagar, data de earnings vira
# passado em dias. O script busca o calendário na Alpha Vantage e grava um
# JSON que este bloco aplica por cima no import, mesma mecânica (e mesma
# tolerância a falha) do overlay de correlações.
EARNINGS_ATUALIZADO_EM: str | None = None
EARNINGS_APLICADOS = 0

_OVERLAY_EARNINGS_DEFAULT = "/var/cache/premercado/radar_earnings.json"


def _aplicar_vol_medida(blob: dict) -> None:
    """Substitui a vol semanal de TEMA_IA pela MEDIDA por nós, quando o
    overlay traz.

    A vol embutida veio de coleta manual externa e discordava da medição do
    próprio agente (visto em 15/08/2026: INTC 31.7% a.a. no radar contra
    79.1% medidos por get_scenario_params; NVDA saindo com 10.8% a.a.).
    Como ela é a base do stop sugerido e da contribuição de risco em
    parametros_volatilidade, o erro chegava até a decisão -- NVDA aparecia
    como a posição menos arriscada da carteira.

    Ticker que o overlay não cobre mantém o valor manual, e o marcador
    `est` (estimativa de setor) só cai pra False onde a medida entrou --
    assim o relatório continua distinguindo medido de estimado."""
    global VOL_MEDIDA_APLICADA
    vols = blob.get("vol_semanal")
    if not isinstance(vols, dict) or not vols:
        return
    for ticker, valor in vols.items():
        alvo = TEMA_IA.get(str(ticker).upper())
        if alvo is None:
            continue  # proxy de país (EWY etc.) não faz parte do tema
        try:
            v = round(float(valor), 2)
        except (TypeError, ValueError):
            continue
        if v <= 0:
            continue
        # Guarda o valor ORIGINAL da coleta manual antes de sobrescrever, e
        # só na primeira vez (senão a segunda aplicação guardaria a medição
        # da semana passada e o original se perderia).
        #
        # Sem isto o diagnóstico de divergência
        # (atualizar_correlacoes.divergencias_de_vol) cega depois do primeiro
        # refresh: ele compara a medição nova contra TEMA_IA, que já é a
        # medição anterior -- razão ~1, nenhuma divergência, e o erro da
        # coleta original some do relatório para sempre. Mesmo tipo de
        # armadilha do rótulo "vs snapshot": baseline que se auto-sobrescreve.
        if "vol_sem_snapshot" not in alvo:
            alvo["vol_sem_snapshot"] = alvo.get("vol_sem")
        alvo["vol_sem"] = v
        alvo["est"] = False  # deixou de ser estimativa: foi medida
        VOL_MEDIDA_APLICADA += 1
    if VOL_MEDIDA_APLICADA:
        print(f"[radar] vol medida aplicada a {VOL_MEDIDA_APLICADA} tickers "
              f"(substitui a coleta manual do snapshot)", file=sys.stderr)


def _aplicar_overlay_correlacoes() -> None:
    global CORRELACOES_JANELA_FIM, CORRELACOES_ATUALIZADO_EM
    import os
    path = os.environ.get("RADAR_CORR_OVERLAY") or _OVERLAY_PATH_DEFAULT
    try:
        with open(path, encoding="utf-8") as f:
            blob = json.load(f)
        _aplicar_vol_medida(blob)
        pares = blob.get("correlacoes")
        if not isinstance(pares, dict) or not pares:
            return
        aplicados = 0
        for chave, valor in pares.items():
            partes = str(chave).split("|")
            if len(partes) != 2:
                continue
            a, b = sorted(p.upper() for p in partes)
            try:
                c = round(float(valor), 2)
            except (TypeError, ValueError):
                continue
            # normaliza pra chave que o snapshot usa (qualquer ordem serve
            # pro lookup de correlacao(), mas evita par duplicado invertido)
            CORRELACOES.pop((b, a), None)
            CORRELACOES[(a, b)] = c
            aplicados += 1
        if aplicados:
            CORRELACOES_JANELA_FIM = str(blob.get("janela_fim") or CORRELACOES_JANELA_FIM)
            CORRELACOES_ATUALIZADO_EM = str(blob.get("atualizado_em")) if blob.get("atualizado_em") else None
            print(f"[radar] overlay de correlações aplicado: {aplicados} pares, "
                  f"janela até {CORRELACOES_JANELA_FIM} ({path})", file=sys.stderr)
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[radar] overlay de correlações ignorado ({path}): {e}", file=sys.stderr)


def _aplicar_overlay_earnings() -> None:
    global EARNINGS_ATUALIZADO_EM, EARNINGS_APLICADOS
    import os
    path = os.environ.get("RADAR_EARNINGS_OVERLAY") or _OVERLAY_EARNINGS_DEFAULT
    try:
        with open(path, encoding="utf-8") as f:
            blob = json.load(f)
        eventos = blob.get("earnings")
        if not isinstance(eventos, dict) or not eventos:
            return
        aplicados = 0
        for ticker, ev in eventos.items():
            alvo = EARNINGS.get(str(ticker).upper())
            # Ticker fora do universo do radar é ignorado de propósito: o
            # `setor` é classificação nossa, não vem da API, então criar
            # entrada aqui produziria linha sem setor na tela.
            if alvo is None or not isinstance(ev, dict):
                continue
            data = str(ev.get("data") or "").strip()
            if len(data) != 10 or data.count("-") != 2:
                continue
            alvo["data"] = data
            alvo["quando"] = ev.get("quando") if ev.get("quando") in ("BO", "AC") else None
            alvo["fonte"] = "alphavantage"
            # Toda `nota` do EARNINGS é especulação SOBRE A DATA ("fontes
            # divergem: pode ser 01/09", "não confirmado oficialmente"), e
            # especulação morre no instante em que uma fonte de calendário
            # responde. Deixá-la ao lado da data confirmada seria pior que
            # inútil: a tela mostraria "fontes divergem" embaixo do dado que
            # encerrou a divergência. Nota que não seja sobre a data não
            # pertence a este dicionário.
            alvo.pop("nota", None)
            aplicados += 1
        if aplicados:
            EARNINGS_APLICADOS = aplicados
            EARNINGS_ATUALIZADO_EM = str(blob.get("atualizado_em")) if blob.get("atualizado_em") else None
            print(f"[radar] overlay de earnings aplicado: {aplicados} tickers, "
                  f"coletado em {EARNINGS_ATUALIZADO_EM} ({path})", file=sys.stderr)
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[radar] overlay de earnings ignorado ({path}): {e}", file=sys.stderr)


_aplicar_overlay_correlacoes()
_aplicar_overlay_earnings()


# ============================================================================
# FUNÇÕES DE ANÁLISE
# ============================================================================

def correlacao(a: str, b: str) -> float | None:
    """Retorna a correlação medida entre dois tickers (ordem indiferente)."""
    a, b = a.upper(), b.upper()
    if a == b:
        return 1.0
    return CORRELACOES.get((a, b)) or CORRELACOES.get((b, a))


def vizinhos(ticker: str, minimo: float = 0.0) -> list[tuple[str, float]]:
    """Todos os tickers com correlação medida >= minimo, decrescente."""
    t = ticker.upper()
    out = []
    for (x, y), c in CORRELACOES.items():
        if t == x and c >= minimo:
            out.append((y, c))
        elif t == y and c >= minimo:
            out.append((x, c))
    return sorted(out, key=lambda p: -p[1])


def cluster_de(ticker: str) -> dict:
    """Grupo temático + vizinhos de alta correlação (>= CORR_ALTA)."""
    t = ticker.upper()
    grupo = TEMA_IA.get(t, {}).get("grupo", "desconhecido")
    fortes = [(v, c) for v, c in vizinhos(t) if c >= CORR_ALTA]
    return {"ticker": t, "grupo": grupo, "mesmo_trade": fortes,
            "aviso": ("sinais nesses pares são o MESMO sinal contado 2x"
                      if fortes else None)}


def risco_portfolio(tickers: list[str] | None = None) -> dict:
    """Correlação média par-a-par, pares críticos e leitura de stress."""
    tk = [t.upper() for t in (tickers or PORTFOLIO_DEFAULT)]
    pares, faltando = [], []
    for a, b in combinations(tk, 2):
        c = correlacao(a, b)
        if c is None:
            faltando.append((a, b))
        else:
            pares.append(((a, b), c))
    media = sum(c for _, c in pares) / len(pares) if pares else None
    criticos = [(p, c) for p, c in pares if c >= CORR_ALTA]
    return {
        "tickers": tk,
        "correlacao_media": round(media, 3) if media is not None else None,
        "pares_criticos_0.70+": criticos,
        "pares_sem_dado": faltando,
        "leitura": (
            "MESMO TRADE: correlação média >=0.70 — cesta se comporta como "
            "um único ativo, sem diversificação real"
            if media and media >= CORR_ALTA else
            "diversificação parcial em mercado calmo; em stress de setor "
            "correlações ~0.5 tendem a 0.7-0.8+ (caem juntos)"
            if media and media >= CORR_MODERADA else
            "correlação média baixa — diversificação razoável"),
    }


def earnings_proximos(dias: int = 14, ref: date | None = None) -> list[dict]:
    """Earnings nos próximos N dias a partir de ref (default: HOJE em BRT).

    Default seguro de propósito: o pacote original usava HOJE_SNAPSHOT
    (14/08/2026) como ref default, e o próprio guia de integração avisava que
    esquecer de trocar por date.today() faria os alertas "congelarem" no
    tempo. Aqui o default já é o dia corrente (em BRT, ver brt.py -- nunca
    date.today() cru, fuso do processo é UTC no container); pra reproduzir a
    visão do snapshot, passe ref=HOJE_SNAPSHOT explicitamente."""
    ref = ref or today_brt()
    fim = ref + timedelta(days=dias)
    out = []
    for t, info in EARNINGS.items():
        d = date.fromisoformat(info["data"])
        if ref <= d <= fim:
            out.append({"ticker": t, "data": info["data"],
                        "quando": info.get("quando"), "setor": info["setor"],
                        "evr": REACAO_EARNINGS.get(t, {}).get("evr"),
                        "move_impl_sem": REACAO_EARNINGS.get(t, {}).get("move_impl_sem"),
                        "nota": info.get("nota")})
    return sorted(out, key=lambda e: e["data"])


def alerta_contagio(ticker_evento: str, portfolio: list[str] | None = None,
                    limiar: float = CORR_MODERADA) -> dict:
    """
    Dado um evento (ex.: earnings) em `ticker_evento`, lista posições do
    portfólio expostas por correlação >= limiar. Uso: pré-mercado do dia
    de earnings de AMAT -> checar exposição de MU/SMCI etc.
    """
    tk = [t.upper() for t in (portfolio or PORTFOLIO_DEFAULT)]
    ev = ticker_evento.upper()
    expostos = []
    for p in tk:
        c = correlacao(ev, p)
        if c is not None and c >= limiar:
            expostos.append({"posicao": p, "correlacao": c,
                             "nivel": "ALTO" if c >= CORR_ALTA else "moderado"})
    data_ev = EARNINGS.get(ev, {}).get("data")
    return {"evento": ev, "data_earnings": data_ev,
            "posicoes_expostas": sorted(expostos, key=lambda e: -e["correlacao"]),
            "limiar": limiar}


def sinais_duplicados(sinais: list[str]) -> list[dict]:
    """
    Recebe lista de tickers com sinal do itci_engine/ConfluenceEngine e
    aponta pares que são o mesmo trade (corr >= CORR_ALTA) — dedup de sinal.
    """
    tk = [t.upper() for t in sinais]
    dups = []
    for a, b in combinations(tk, 2):
        c = correlacao(a, b)
        if c is not None and c >= CORR_ALTA:
            dups.append({"par": (a, b), "correlacao": c,
                         "acao": "considerar como 1 sinal / dividir sizing"})
    return dups


def ajustar_scores_por_cluster(scores: dict[str, float]) -> dict[str, float]:
    """Penaliza o menor score de cada par com corr >= CORR_ALTA (Passo 1 do
    guia de integração). Motivo: MU-SNDK (0.82) e MU-LRCX (0.80) são o mesmo
    trade — dois sinais no mesmo cluster é um sinal contado duas vezes.

    Muta e devolve o próprio dict (mesmo contrato do guia). A penalidade é
    proporcional à correlação: score *= (1 - corr/2), ex.: corr 0.82 corta
    41% do score do perdedor do par. Chamar depois do cálculo de score e
    antes do ranking final de qualquer consumidor multi-ticker."""
    dups = sinais_duplicados(list(scores))
    for d in dups:
        a, b = d["par"]
        menor = a if scores[a] <= scores[b] else b
        scores[menor] *= (1 - d["correlacao"] / 2)
    return scores


def sizing_por_vol(tickers: list[str], risco_alvo_sem_pct: float = 2.0) -> list[dict]:
    """
    Sizing inverso à volatilidade: peso_i ∝ 1/vol_i, normalizado.
    `risco_alvo_sem_pct` = quanto do capital você aceita oscilar por semana
    por posição (aprox. ingênua, sem covariância — ver limitação no docstring
    do módulo). Retorna pesos relativos e nota de vol usada.

    NÃO usar como otimizador de carteira: ignora covariância, e na cesta de
    memória (tudo corr 0.7+) isso subestima o risco conjunto — restrição
    explícita do guia de integração. No máximo, teto por posição isolada.
    """
    dados = []
    for t in [x.upper() for x in tickers]:
        vol = TEMA_IA.get(t, {}).get("vol_sem")
        if vol:
            dados.append((t, vol, TEMA_IA[t].get("est", True)))
    if not dados:
        return []
    inv = [(t, 1.0 / v, v, est) for t, v, est in dados]
    soma = sum(w for _, w, _, _ in inv)
    out = []
    for t, w, v, est in inv:
        peso = w / soma
        out.append({"ticker": t, "peso_sugerido": round(peso, 3),
                    "vol_semanal_pct": v, "vol_estimada": est,
                    "cap_por_risco_pct": round(risco_alvo_sem_pct / v, 3)})
    return sorted(out, key=lambda x: -x["peso_sugerido"])


def top_volateis(n: int = 5) -> list[dict]:
    """Top N mais voláteis do tema IA (vol semanal medida)."""
    med = [(t, d) for t, d in TEMA_IA.items() if d["vol_sem"] and not d["est"]]
    med.sort(key=lambda x: -x[1]["vol_sem"])
    return [{"ticker": t, "vol_sem": d["vol_sem"], "beta": d["beta"],
             "grupo": d["grupo"], "ytd": d["ytd"]} for t, d in med[:n]]


def ranking_ytd() -> list[tuple[str, float]]:
    return sorted(((t, d["ytd"]) for t, d in TEMA_IA.items()),
                  key=lambda x: -x[1])


# ============================================================================
# RELATÓRIO CLI
# ============================================================================

def _fmt_pct(v):
    return f"{v:+.1f}%" if v is not None else "n/d"


def _linha_procedencia(viva: dict) -> None:
    """Uma linha dizendo de onde veio cada coisa. O radar mistura dado vivo
    (preço/52s), manual (EVR/move implícito) e embutido (correlações); sem
    isso o leitor trata tudo como igualmente fresco."""
    idade = idade_overrides_dias()
    if OVERRIDES_COLETADO_EM:
        quando = "hoje" if idade == 0 else f"há {idade} dia(s)"
        print(f"  EVR/move implícito: coleta manual ({OVERRIDES_FONTE or 'n/d'}) "
              f"{quando}, em {OVERRIDES_COLETADO_EM}")
    else:
        print("  EVR/move implícito: SEM carimbo de coleta — idade desconhecida")

    if viva["atualizados"]:
        print(f"  preço e faixa de 52 semanas: vivos ({len(viva['atualizados'])} tickers)")
    if viva["fontesDegradadas"]:
        print("  ATENÇÃO — não atualizados, servindo o snapshot de "
              f"{HOJE_SNAPSHOT.isoformat()}:")
        for t, motivo in sorted(viva["fontesDegradadas"].items()):
            print(f"    {t}: {motivo}")
    for aviso in viva["avisos"]:
        print(f"  INCONSISTÊNCIA: {aviso}")


def relatorio_completo():
    viva = atualizar_min52_vivo()

    print("=" * 72)
    print("RADAR IA 2026")
    print("=" * 72)
    print("\n--- PROCEDÊNCIA DOS DADOS ---")
    _linha_procedencia(viva)

    print("\n--- EARNINGS PRÓXIMOS 14 DIAS ---")
    for e in earnings_proximos(14):
        evr = f"EVR {e['evr']}" if e["evr"] else "EVR n/d"
        mi = f"impl {e['move_impl_sem']}%" if e["move_impl_sem"] else ""
        print(f"  {e['data']}  {e['ticker']:<5} [{e['setor']}] "
              f"{e['quando'] or '':<2} {evr} {mi} {e.get('nota') or ''}")

    print("\n--- TOP 5 VOLATILIDADE (tema IA, medida) ---")
    for v in top_volateis(5):
        print(f"  {v['ticker']:<5} vol_sem {v['vol_sem']:>6.2f}%  "
              f"beta {v['beta']:.2f}  [{v['grupo']}]  YTD {_fmt_pct(v['ytd'])}")

    print("\n--- RISCO DO PORTFÓLIO (MU, SMCI, ARM, MRVL, AVGO) ---")
    r = risco_portfolio()
    print(f"  correlação média par-a-par: {r['correlacao_media']}")
    print(f"  pares >=0.70: {r['pares_criticos_0.70+'] or 'nenhum'}")
    print(f"  leitura: {r['leitura']}")

    print("\n--- CONTÁGIO: NVDA earnings 26/08 sobre o portfólio ---")
    a = alerta_contagio("NVDA")
    for p in a["posicoes_expostas"]:
        print(f"  {p['posicao']:<5} corr {p['correlacao']:.2f}  ({p['nivel']})")

    print("\n--- PROXY SAMSUNG (EWY) ---")
    for t in ["MU", "SNDK", "NVDA", "SMCI"]:
        print(f"  EWY-{t:<5} {correlacao('EWY', t):.2f}")
    print("  nota: EWY dilui Samsung (~25-30% do peso) — corr real é maior.")
    print("  Samsung = indicador (HBM/capacidade), não posição (sem Nomad).")

    print("\n--- TOP 10 YTD TEMA IA ---")
    for t, y in ranking_ytd()[:10]:
        print(f"  {t:<6} {_fmt_pct(y)}  [{TEMA_IA[t]['grupo']}]")
    print()


def exportar_json():
    viva = atualizar_min52_vivo()
    blob = {
        "snapshot": HOJE_SNAPSHOT.isoformat(),
        # Procedência explícita: o consumidor (tela Radar IA) precisa saber o
        # que é vivo, o que é manual e há quanto tempo.
        "overridesColetadoEm": OVERRIDES_COLETADO_EM,
        "overridesFonte": OVERRIDES_FONTE,
        "overridesIdadeDias": idade_overrides_dias(),
        "min52Atualizados": viva["atualizados"],
        "min52Avisos": viva["avisos"],
        # Janela real das correlações servidas: igual ao snapshot quando só
        # há o embutido; avança quando o overlay de atualizar_correlacoes.py
        # foi aplicado no import.
        "correlacoes_janela_fim": CORRELACOES_JANELA_FIM,
        "correlacoes_atualizado_em": CORRELACOES_ATUALIZADO_EM,
        # None enquanto só houver o calendário embutido; vira a data da
        # coleta quando o overlay de atualizar_earnings.py entrou. É o que
        # permite à tela dizer "calendário de hoje" em vez de deixar o
        # usuário supor que tudo ali é do snapshot.
        "earnings_atualizado_em": EARNINGS_ATUALIZADO_EM,
        "earnings": EARNINGS,
        "min52": MIN52,
        "reacao_earnings": REACAO_EARNINGS,
        "riscos": RISCOS,
        "tema_ia": TEMA_IA,
        "correlacoes": {f"{a}|{b}": c for (a, b), c in CORRELACOES.items()},
        "portfolio_default": PORTFOLIO_DEFAULT,
    }
    # Mesmo nome de campo que get_scenario_params/get_ticker_snapshot já usam
    # para degradação — um vocabulário só em todo o app.
    if viva["fontesDegradadas"]:
        blob["fontesDegradadas"] = viva["fontesDegradadas"]
    print(json.dumps(blob, ensure_ascii=False, indent=2))


def main(argv=None):
    p = argparse.ArgumentParser(description="Radar IA 2026 — análise consolidada")
    p.add_argument("--portfolio", action="store_true")
    p.add_argument("--earnings", type=int, metavar="DIAS")
    p.add_argument("--corr", nargs=2, metavar=("A", "B"))
    p.add_argument("--cluster", metavar="TICKER")
    p.add_argument("--contagio", metavar="TICKER")
    p.add_argument("--sizing", nargs="+", metavar="TICKER")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    if args.json:
        exportar_json()
    elif args.portfolio:
        print(json.dumps(risco_portfolio(), ensure_ascii=False, indent=2))
    elif args.earnings is not None:
        for e in earnings_proximos(args.earnings):
            print(e)
    elif args.corr:
        a, b = args.corr
        c = correlacao(a, b)
        print(f"corr({a.upper()},{b.upper()}) = {c if c is not None else 'não medida'}")
    elif args.cluster:
        print(json.dumps(cluster_de(args.cluster), ensure_ascii=False, indent=2))
    elif args.contagio:
        print(json_seguro.dumps(alerta_contagio(args.contagio), ensure_ascii=False, indent=2))
    elif args.sizing:
        for linha in sizing_por_vol(args.sizing):
            print(linha)
    else:
        relatorio_completo()


if __name__ == "__main__":
    main()

# ----------------------------------------------------------------------------
# FONTES: Alpha Vantage ANALYTICS_FIXED_WINDOW (correlações, 13/02-14/08/26);
# OptionSlam (EVR/moves implícitos); ChartRow (YTD tema IA, 13/08/26);
# TradingView/StockAnalysis (vol semanal, beta); Yahoo/Investing/CNN (preços,
# mínimas 52 sem.); EarningsCountdown/Kiplinger (calendário).
# LIMITAÇÕES: correlações são de janela única de 6 meses (não rolling);
# sizing_por_vol ignora covariância (usar matriz completa p/ risco real);
# vol marcada est=True é estimativa de setor, não medida.
# ----------------------------------------------------------------------------
