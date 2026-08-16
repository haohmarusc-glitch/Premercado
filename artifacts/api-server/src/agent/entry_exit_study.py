"""Estudo de Entrada e Saída — probabilidade de UM ticker bater um preço-alvo
até uma data-alvo, com referência de suporte (mínima/média de baixa dos
últimos 12 meses e 6 meses) pra entrada.

Diferente do Painel de Cenários (carteira inteira, premissa de movimento de
setor via slider), este estudo é sempre por ticker isolado, com o preço-alvo
informado pelo usuário -- e o passeio aleatório usado pra probabilidade tem
DRIFT ZERO (sem viés de alta nem de baixa embutido, só a volatilidade real do
papel) -- ver decisão explícita do usuário, não usa beta*setor como
scenario-math faz pro Painel de Cenários.

Reaproveita 3 módulos já existentes em vez de duplicar lógica:
  - get_scenario_params.compute(): vol_annual/beta_sector (mesma fórmula do
    checker diário de scenario_params).
  - get_earnings.get_earnings(): data do próximo balanço.
  - earnings_reaction_analysis.analyze_ticker(): desvio-padrão histórico da
    reação de preço em earnings passados (jumpStdPct) -- se o balanço cai
    dentro da janela [hoje, data-alvo], a variância desse salto é somada à
    volatilidade de difusão, mesma fórmula de volComSalto em
    @workspace/scenario-math (lib/scenario-math/src/index.ts), só que
    reimplementada aqui em Python porque este script não roda em Node.
  - get_news_feed.for_ticker(): manchetes recentes, incluídas só como
    contexto informativo (não entram no cálculo -- não há como quantificar
    sentimento de notícia de forma confiável, mesma limitação documentada em
    scenario-math.ts sobre vol ser estimativa histórica).

Input (stdin JSON):
  {"studies": [{"ticker": "SMCI", "targetPrice": 45.0, "targetDate": "2026-09-15"}, ...]}
Output (stdout JSON):
  {"results": [{ticker, targetPrice, targetDate, currentPrice, avgLow1y,
                minLow1y, avgLow6m, minLow6m, entryPullbackPrice, volAnnual,
                betaSector, earningsDate, daysUntilTarget, probReachTarget,
                news, error?}, ...]}

Busca em PARALELO (bounded_parallel_map, mesmo padrão de get_bounce_alerts.py)
-- cada estudo é uma sequência de várias chamadas de rede (yfinance +
earnings + reação histórica + notícias), então vale rodar os estudos em
threads separadas em vez de sequencial.
"""
import sys
import json
import math
from statistics import NormalDist

from startup_probe import boot as _probe_boot, imports_prontos as _probe_imports

_probe_boot()

import pandas as pd
import yfinance as yf

from bounded_parallel import bounded_parallel_map, budget_from_deadline, exit_now
from security import sanitize_ticker
try:  # import duplo: spawn por caminho e também como membro do pacote
    from agent import market_data_provider
except ImportError:
    import market_data_provider
from brt import today_brt
from get_scenario_params import compute as compute_scenario_params
from get_earnings import get_earnings
from earnings_reaction_analysis import analyze_ticker as analyze_earnings_reaction
from get_news_feed import for_ticker as news_for_ticker, _company_names, translate_all

_probe_imports()

# Mesmo benchmark setorial usado por scenario-params-checker.ts, pra
# vol_annual/beta_sector saírem consistentes com o Painel de Cenários.
BENCHMARK = "SMH"
# Mesmo fallback de scenarios.ts quando o ticker ainda não tem linha em
# scenario_params (posição/ticker novo fora da cesta setorial original).
DEFAULT_VOL = 0.5
# Timeout do lado Node (routes/entry-exit-study.ts) é 60s -- ver BUDGET_S em
# get_bounce_alerts.py pro mesmo raciocínio (orçamento sempre menor que o
# timeout externo, cobrindo o custo de import de pandas/numpy/yfinance).
BUDGET_S = 45
# ~6 meses em pregões (252 pregões/ano / 2) -- mesma lógica de janela em
# pregões (não em dias corridos) já usada por MOMENTUM_LOOKBACK_DAYS em
# get_scenario_params.py.
SIX_MONTHS_TRADING_DAYS = 126

# Horizonte e probabilidade usados pro nível de entrada projetado
# (_entry_pullback_price) -- motivado por papéis que subiram muito no
# último ano (ex.: INTC/SMCI ago/2026): a mínima de 12 meses fica tão longe
# do preço atual que o alerta baseado nela nunca dispara. Em vez de olhar
# pra um piso histórico que pode nunca mais se repetir, projeta um nível
# plausível de curto prazo a partir da VOL ATUAL do próprio papel -- mesma
# matemática de probReachTarget (lognormal, drift zero), só invertida.
# 30 dias / 30% é deliberadamente moderado: alto o bastante pra ter chance
# real de disparar num pull-back normal, baixo o bastante pra não ser só o
# preço atual (que seria p=50%, z=0, inútil como alerta).
ENTRY_PULLBACK_DAYS = 30
ENTRY_PULLBACK_PROB = 0.30


def _phi(z: float) -> float:
    """CDF normal padrão -- mesma fórmula lognormal de scenario-math.ts::Phi,
    só que via math.erf (exato) em vez da aproximação de Abramowitz-Stegun
    que o JS usa por não ter erf() na stdlib."""
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def _low_stats(series: "pd.Series") -> tuple[float | None, float | None]:
    """(média das mínimas diárias, menor mínima) da janela -- os dois números
    de referência de entrada/suporte pedidos, não só o ponto mais baixo
    isolado."""
    clean = series.dropna()
    if clean.empty:
        return None, None
    return round(float(clean.mean()), 4), round(float(clean.min()), 4)


def _entry_pullback_price(current_price: float, vol_annual: float | None) -> float | None:
    """Preço em que há ENTRY_PULLBACK_PROB de chance do papel estar em
    ENTRY_PULLBACK_DAYS, assumindo drift zero -- Phi^-1(p) em vez de Phi(z)
    de probReachTarget. None quando não há vol pra projetar (mesmo caso que
    já faz DEFAULT_VOL entrar em cena antes desta função ser chamada)."""
    if not current_price or current_price <= 0 or not vol_annual or vol_annual <= 0:
        return None
    t_curto = ENTRY_PULLBACK_DAYS / 365
    z = NormalDist().inv_cdf(ENTRY_PULLBACK_PROB)  # negativo, p < 0.5
    return round(current_price * math.exp(z * vol_annual * math.sqrt(t_curto)), 4)


def _study_for(spec: dict) -> dict:
    raw_ticker = spec.get("ticker", "")
    try:
        ticker = sanitize_ticker(raw_ticker)
    except ValueError as e:
        return {"ticker": raw_ticker, "error": str(e)}

    target_price = spec.get("targetPrice")
    target_date = spec.get("targetDate")
    if not target_price or target_price <= 0:
        return {"ticker": ticker, "error": "targetPrice inválido"}
    if not target_date:
        return {"ticker": ticker, "error": "targetDate obrigatória"}

    today = today_brt()
    try:
        target_dt = pd.Timestamp(target_date).date()
    except Exception:
        return {"ticker": ticker, "error": f"targetDate inválida: {target_date}"}
    days_until = (target_dt - today).days
    if days_until < 1:
        return {"ticker": ticker, "error": "targetDate precisa ser no futuro"}

    # Duas buscas com riscos bem diferentes, por isso separadas.
    #
    # O HISTÓRICO alimenta as mínimas (níveis de entrada). É série não
    # ajustada, então aceita a cadeia inteira, fonte externa inclusive: uma
    # média de mínimas calculada sobre dado de ontem continua útil.
    #
    # O PREÇO ATUAL é outra história: ele entra em log(alvo/preço), ou seja,
    # define a distância até o alvo e portanto a probabilidade inteira. Servir
    # o fechamento de ontem como "preço atual" muda a resposta sem mudar a
    # pergunta -- num papel que andou 5% hoje, a probabilidade sai
    # materialmente errada. Continua vindo do yfinance, e se ele não
    # responder o estudo FALHA em vez de calcular sobre um preço velho
    # disfarçado de atual.
    try:
        resultado_hist = market_data_provider.get_daily_history(
            ticker, "1y", auto_adjust=False
        )
        if not resultado_hist.ok:
            return {"ticker": ticker, "error": "sem histórico de preço"}
        hist = resultado_hist.df
        current_price = float(yf.Ticker(ticker).fast_info.last_price)
    except Exception as e:
        return {"ticker": ticker, "error": f"falha ao buscar preço/histórico: {type(e).__name__}: {e}"}

    avg_low_1y, min_low_1y = _low_stats(hist["Low"])
    avg_low_6m, min_low_6m = _low_stats(hist["Low"].tail(SIX_MONTHS_TRADING_DAYS))

    scenario_out = compute_scenario_params([ticker], BENCHMARK)
    params = scenario_out.get("params", {}).get(ticker, {})
    vol_annual = params.get("volAnnual") or DEFAULT_VOL
    beta_sector = params.get("betaSector")
    entry_pullback_price = _entry_pullback_price(current_price, vol_annual)
    # Momentum do benchmark setorial, do MESMO download que já trouxe
    # vol/beta (sem chamada de rede extra) -- alimenta a probabilidade
    # alternativa "com momentum" abaixo.
    sector_momentum = scenario_out.get("sectorMomentum") or {}
    momentum_annual_pct = sector_momentum.get("momentumAnnualPct")

    earnings_date = None
    try:
        earnings_info = get_earnings([ticker])
        if earnings_info:
            earnings_date = earnings_info[0].get("earningsDate")
    except Exception as e:
        print(f"[entry_exit_study] earnings de {ticker}: {e}", file=sys.stderr)

    jump_std_pct = None
    try:
        reaction = analyze_earnings_reaction(ticker)
        jump_std_pct = (reaction.get("summary") or {}).get("close_pct_std")
    except Exception as e:
        print(f"[entry_exit_study] reação de earnings de {ticker}: {e}", file=sys.stderr)

    # Passeio aleatório com DRIFT ZERO (decisão explícita: sem viés
    # direcional embutido) -- só a volatilidade de difusão, somada ao salto
    # de earnings quando o balanço cai dentro de [hoje, data-alvo]. Mesma
    # fórmula de volComSalto/probEmpateIndividual em scenario-math.ts, sem o
    # termo de drift beta*setor (que só faz sentido pro Painel de Cenários,
    # com a premissa explícita de movimento de setor do slider).
    T = days_until / 365
    vol_eff = vol_annual
    if jump_std_pct and earnings_date:
        try:
            evento = pd.Timestamp(earnings_date).date()
            if today <= evento <= target_dt:
                jump_var = (jump_std_pct / 100) ** 2
                vol_eff = math.sqrt(vol_annual ** 2 + jump_var / T)
        except Exception:
            pass

    sd = vol_eff * math.sqrt(T)
    prob_reach_target = None
    if current_price > 0 and sd > 0:
        prob_reach_target = round(1 - _phi(math.log(target_price / current_price) / sd), 4)

    # Probabilidade ALTERNATIVA com drift de momentum -- premissa explícita
    # "se a tendência recente do setor continuar", nunca o número principal.
    # Mesma matemática do cenário central do Painel de Cenários
    # (probEmpateIndividual em scenario-math.ts): o papel move beta × o
    # movimento do setor extrapolado pro horizonte, com piso em zero; o
    # drift lognormal é ln desse multiplicador. Momentum vem de dado REAL
    # (retorno dos últimos 90 pregões do benchmark, ver _sector_momentum em
    # get_scenario_params.py) -- diferente de um "sentimento" chutado, dá
    # pra dizer exatamente qual premissa gerou o número. None quando o
    # benchmark não tem histórico suficiente ou o beta do ticker falhou.
    prob_reach_target_momentum = None
    if (
        prob_reach_target is not None
        and momentum_annual_pct is not None
        and beta_sector is not None
    ):
        setor_pct = momentum_annual_pct * T  # movimento do setor até a data-alvo
        central = max(0.0, 1 + (beta_sector * setor_pct) / 100)
        drift = math.log(max(central, 1e-6))
        prob_reach_target_momentum = round(
            1 - _phi((math.log(target_price / current_price) - drift) / sd), 4
        )

    news_items = []
    try:
        names = _company_names([ticker])
        news_items = news_for_ticker(ticker, 5, [ticker], names).get("news", [])
    except Exception as e:
        print(f"[entry_exit_study] news de {ticker}: {e}", file=sys.stderr)

    return {
        "ticker": ticker,
        "targetPrice": target_price,
        "targetDate": target_date,
        "currentPrice": round(current_price, 4),
        "avgLow1y": avg_low_1y,
        "minLow1y": min_low_1y,
        "avgLow6m": avg_low_6m,
        "minLow6m": min_low_6m,
        "entryPullbackPrice": entry_pullback_price,
        "volAnnual": vol_annual,
        "betaSector": beta_sector,
        "earningsDate": earnings_date,
        "daysUntilTarget": days_until,
        "probReachTarget": prob_reach_target,
        "probReachTargetMomentum": prob_reach_target_momentum,
        "momentumAnnualPct": momentum_annual_pct,
        "news": news_items,
        # Marca de onde veio a SÉRIE (não o preço atual, que é sempre ao
        # vivo). Só aparece quando degradada: campo ausente é o caso normal,
        # e assim nenhum consumidor precisa mudar para continuar funcionando.
        **({"fonteHistorico": resultado_hist.source}
           if resultado_hist.source not in ("yfinance", "yfinance_cache") else {}),
    }


def _traduzir_noticias(results: list) -> None:
    """Traduz título+resumo das manchetes pra pt-BR num LOTE único cobrindo
    todos os estudos, mutando `results` no lugar -- mesmo padrão do __main__
    de get_news_feed.py (que já fazia isso pra tela de Notícias; este script
    buscava as mesmas manchetes mas entregava em inglês). Fora do _study_for
    de propósito: dentro seria uma requisição de tradução por thread; aqui é
    uma pro conjunto. translate_all devolve os originais se a tradução
    falhar, então o pior caso é manchete em inglês, nunca manchete perdida."""
    refs = []  # (item_dict, campo)
    texts = []
    for r in results:
        for n in r.get("news") or []:
            for campo in ("title", "summary"):
                if n.get(campo):
                    refs.append((n, campo))
                    texts.append(n[campo])
    if not texts:
        return
    try:
        translated = translate_all(texts)
        if len(translated) == len(texts):
            for (n, campo), tr in zip(refs, translated):
                n[campo] = tr
    except Exception as e:
        print(f"[entry_exit_study] tradução falhou, mantendo originais: {e}", file=sys.stderr)


if __name__ == "__main__":
    try:
        args = json.loads(sys.stdin.read() or "{}")
    except Exception:
        args = {}
    studies = args.get("studies") or []

    results = bounded_parallel_map(
        _study_for,
        studies,
        budget_s=budget_from_deadline(BUDGET_S, label="entry_exit_study"),
        label="entry_exit_study",
    )
    _traduzir_noticias(results)
    exit_now(json.dumps({"results": results}, ensure_ascii=False) + "\n")
