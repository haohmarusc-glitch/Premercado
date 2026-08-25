"""
Fôlego de caixa -- quantos trimestres a empresa aguenta queimando o que queima.

O buraco que este módulo tapa: o agente não lia BALANÇO em lugar nenhum. Para
uma fabricante que constrói capacidade queimando caixa -- WOLF é o exemplo
puro --, "quantos trimestres de caixa restam" é o número que decide a
história, e é exatamente o que a estatística de preço não vê. Mesmo argumento
do capex dos hiperescaladores, do outro lado do balanço: lá é quanto o
comprador investe, aqui é quanto o fornecedor aguenta esperando.

O que este módulo NÃO faz: virar sinal, voto ou multiplicador de sizing. Entra
no snapshot do Veredito como CONTEXTO datado, sujeito ao validador como
qualquer outro número. A base estatística continua sem edge, e um modulador
sobre base sem edge é o RegimeStage arquivado em 20/08/2026.

Quatro armadilhas tratadas explicitamente, todas capazes de produzir número
certo com leitura errada:

1. DATA DE DISPONIBILIDADE. O balanço do trimestre encerrado em 30/06 só
   existe para quem olha de fora quando a empresa reporta, semanas depois.
   Cada linha carrega `disponivelEm` -- mesma disciplina do capex, mesmo vício
   que o backtest carregou até 20/08.

2. FÔLEGO SÓ EXISTE PARA QUEM QUEIMA. Dividir caixa por uma queima próxima de
   zero devolve "800 trimestres", que parece solidez e é só divisão por quase
   nada. Quem gera caixa recebe `geraCaixa: true` e fôlego None -- a ausência
   do número é a informação.

3. UM TRIMESTRE NÃO É TENDÊNCIA. A queima usa a MÉDIA dos últimos
   TRIMESTRES_DE_QUEIMA; um trimestre com pagamento concentrado viraria
   pânico, e um com recebimento atrasado viraria falsa calma.

4. REESTRUTURAÇÃO QUEBRA A SÉRIE. Este foi o caso que motivou o cuidado: as
   manchetes da WOLF em 25/08/2026 traziam "smaller net loss, and positive
   full year net income of $4.4 million" -- lucro anual positivo convivendo
   com prejuízo trimestral, o assinatura de ganho não-recorrente de
   reestruturação de dívida. Comparar a/a atravessando um evento desses mede
   contabilidade, não operação. Quando a dívida ou a quantidade de ações dá um
   salto grande num único trimestre, a linha é marcada com `quebraDeSerie` e o
   consumidor mostra o aviso em vez de comparar.

Fonte: yfinance (grátis, sem cota), com Alpha Vantage só quando o yfinance vem
vazio. Diferente do capex, aqui NÃO se paga cota por profundidade: fôlego
precisa de poucos trimestres (ver TRIMESTRES_DE_QUEIMA), e a AV cobraria DUAS
chamadas por ticker (balanço + fluxo) de um teto real de 25/dia já disputado
com earnings e notícias.

Rodar (na VPS, dentro do container):
    docker compose exec -T -w /app/artifacts/api-server/src app \
      /app/.venv/bin/python -m agent.folego_de_caixa < /dev/null
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta

try:
    import config
    import json_seguro
    from brt import today_brt
except ImportError:
    from agent import config, json_seguro
    from agent.brt import today_brt

OVERLAY_PATH_DEFAULT = "/var/cache/premercado/folego_de_caixa.json"

# Mesma defasagem conservadora do capex: entre o fim do trimestre fiscal e a
# divulgação passam de 3 a 6 semanas nas empresas que acompanhamos.
DIAS_ATE_DIVULGAR = 45

# Sobre quantos trimestres a queima é medida. Quatro cobre um ano inteiro --
# absorve a sazonalidade de capex e de capital de giro sem virar média tão
# longa que esconda uma piora recente.
TRIMESTRES_DE_QUEIMA = 4

# Profundidade mínima. Duas razões para ser BEM menor que a do capex (10): a
# conta de fôlego precisa de TRIMESTRES_DE_QUEIMA + 1, não de história longa;
# e complementar pela AV custaria duas chamadas por ticker (balanço + fluxo)
# de um teto real de 25/dia. Fôlego não vale esse preço; profundidade de capex
# valia, porque lá o experimento de regime dependia dela.
PROFUNDIDADE_MINIMA = TRIMESTRES_DE_QUEIMA + 1

TRIMESTRES_GUARDADOS = 12
TRIMESTRES_BRUTOS_GUARDADOS = 20

# Salto que marca quebra de série. 40% de variação da dívida ou da quantidade
# de ações num ÚNICO trimestre não é operação -- é reestruturação, emissão
# grande ou recompra grande. O número é generoso de propósito: marcar demais
# transformaria o aviso em ruído que ninguém lê.
SALTO_DE_QUEBRA_PCT = 40.0

# Piso de queima para o fôlego existir. Abaixo de um milhão de dólares por
# trimestre a divisão vira ruído: caixa/queima explode e o número deixa de
# significar qualquer coisa.
QUEIMA_MINIMA_USD = 1_000_000.0

PAUSA_ENTRE_CHAMADAS_AV_S = float(os.environ.get("FOLEGO_PAUSA_AV_S", "13"))


# ── conta pura ───────────────────────────────────────────────────────────────

def trimestre_calendario(data_iso: str) -> str | None:
    """"2026-06-30" -> "2026Q2"."""
    try:
        d = datetime.strptime(str(data_iso)[:10], "%Y-%m-%d").date()
    except Exception:
        return None
    return f"{d.year}Q{(d.month - 1) // 3 + 1}"


def disponivel_em(data_iso: str, dias: int = DIAS_ATE_DIVULGAR) -> str | None:
    """A data em que o número passou a existir para quem olha de fora."""
    try:
        d = datetime.strptime(str(data_iso)[:10], "%Y-%m-%d").date()
    except Exception:
        return None
    return (d + timedelta(days=dias)).isoformat()


def _num(v):
    """float ou None -- NaN entra como None, não como zero.

    A diferença importa: zero de dívida é uma afirmação sobre a empresa, e
    NaN é ausência de dado. Tratar um como o outro inventaria balanço."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # NaN


def divida_liquida(caixa, divida):
    """Dívida menos caixa. None quando falta qualquer um dos dois -- somar
    com zero implícito diria "sem dívida" para quem só não reportou."""
    if caixa is None or divida is None:
        return None
    return round(divida - caixa, 2)


def fluxo_livre(operacional, capex):
    """FCF = caixa operacional - capex.

    O capex chega positivo ou negativo conforme a fonte (o yfinance devolve
    saída de caixa negativa); aqui sempre se SUBTRAI a magnitude, senão um
    trimestre de capex pesado viraria FCF inflado."""
    if operacional is None:
        return None
    return round(operacional - abs(capex or 0.0), 2)


def queima_media(linhas: list, trimestres: int = TRIMESTRES_DE_QUEIMA) -> float | None:
    """Média do FCF NEGATIVO dos últimos trimestres, em módulo.

    Devolve None quando não há FCF suficiente, e 0.0 quando o período não
    queimou -- que é diferente de "não sei", e por isso não vira None."""
    fcfs = [l["fcfUsd"] for l in linhas if l.get("fcfUsd") is not None]
    if not fcfs:
        return None
    janela = fcfs[-trimestres:]
    queimas = [-f for f in janela if f < 0]
    if not queimas:
        return 0.0
    # Divide pela JANELA inteira, não só pelos trimestres que queimaram: um
    # ano com três trimestres neutros e um de queima forte tem queima média
    # menor que um ano queimando todo trimestre, e a conta tem que dizer isso.
    return round(sum(queimas) / len(janela), 2)


def folego_trimestres(caixa, queima) -> float | None:
    """Quantos trimestres o caixa cobre na queima média. None para quem não
    queima -- ver armadilha 2 no topo."""
    if caixa is None or queima is None or queima < QUEIMA_MINIMA_USD:
        return None
    return round(caixa / queima, 1)


def liquidez_corrente(ativo, passivo) -> float | None:
    if ativo is None or not passivo:
        return None
    return round(ativo / passivo, 2)


def _saltou(atual, anterior, limite_pct: float = SALTO_DE_QUEBRA_PCT) -> bool:
    if atual is None or anterior in (None, 0):
        return False
    return abs(atual - anterior) / abs(anterior) * 100 >= limite_pct


def montar_serie(balanco: list, fluxo: list, hoje: str | None = None) -> list:
    """Une balanço e fluxo por trimestre e calcula as derivadas.

    Devolve do mais antigo para o mais novo, já filtrado pelas linhas
    DIVULGADAS -- linha de trimestre fechado mas não reportado é look-ahead."""
    por_trimestre: dict = {}
    for linha in list(balanco or []) + list(fluxo or []):
        t = linha.get("trimestre")
        if not t:
            continue
        por_trimestre.setdefault(t, {"trimestre": t})
        for k, v in linha.items():
            if k != "trimestre" and v is not None:
                por_trimestre[t][k] = v

    saida = []
    for t in sorted(por_trimestre):
        linha = dict(por_trimestre[t])
        linha["fcfUsd"] = fluxo_livre(linha.get("caixaOperacionalUsd"),
                                      linha.get("capexUsd"))
        linha["dividaLiquidaUsd"] = divida_liquida(linha.get("caixaUsd"),
                                                   linha.get("dividaUsd"))
        linha["liquidezCorrente"] = liquidez_corrente(
            linha.get("ativoCirculanteUsd"), linha.get("passivoCirculanteUsd"))
        saida.append(linha)

    # Quebra de série: comparada com o trimestre IMEDIATAMENTE anterior da
    # série, não com um valor de referência fixo.
    for i, linha in enumerate(saida):
        ant = saida[i - 1] if i else None
        linha["quebraDeSerie"] = bool(ant and (
            _saltou(linha.get("dividaUsd"), ant.get("dividaUsd"))
            or _saltou(linha.get("acoesEmCirculacao"), ant.get("acoesEmCirculacao"))))

    hoje = hoje or today_brt().isoformat()
    divulgadas = [l for l in saida
                  if not l.get("disponivelEm") or l["disponivelEm"] <= hoje]
    return divulgadas[-TRIMESTRES_GUARDADOS:]


def avaliar(serie: list) -> dict:
    """O que o Veredito cita: a foto do último trimestre divulgado."""
    if not serie:
        return {"disponivel": False, "nota": "sem balanço divulgado"}
    ultimo = serie[-1]
    queima = queima_media(serie)
    caixa = ultimo.get("caixaUsd")
    folego = folego_trimestres(caixa, queima)
    gera_caixa = queima is not None and queima < QUEIMA_MINIMA_USD
    return {
        "disponivel": True,
        "trimestre": ultimo["trimestre"],
        "disponivelEm": ultimo.get("disponivelEm"),
        "caixaUsd": caixa,
        "dividaLiquidaUsd": ultimo.get("dividaLiquidaUsd"),
        "liquidezCorrente": ultimo.get("liquidezCorrente"),
        "fcfTrimestralUsd": ultimo.get("fcfUsd"),
        "queimaMediaUsd": queima,
        "trimestresDeQueima": TRIMESTRES_DE_QUEIMA,
        "folegoTrimestres": folego,
        "geraCaixa": gera_caixa,
        # Vale para a SÉRIE, não só para o último: uma reestruturação dois
        # trimestres atrás continua invalidando a comparação a/a de hoje.
        "quebraDeSerie": any(l.get("quebraDeSerie") for l in serie),
        "trimestresNaSerie": len(serie),
    }


# ── coleta (rede) ────────────────────────────────────────────────────────────

# O yfinance troca o rótulo das linhas entre versões e entre empresas; cada
# tupla é uma lista de sinônimos, na ordem de preferência.
_LINHAS_BALANCO = {
    "caixaUsd": ("Cash Cash Equivalents And Short Term Investments",
                 "Cash And Cash Equivalents", "CashAndCashEquivalents"),
    "dividaUsd": ("Total Debt", "TotalDebt"),
    "ativoCirculanteUsd": ("Current Assets", "Total Current Assets"),
    "passivoCirculanteUsd": ("Current Liabilities", "Total Current Liabilities"),
    "patrimonioUsd": ("Stockholders Equity", "Total Stockholder Equity"),
    "acoesEmCirculacao": ("Ordinary Shares Number", "Share Issued"),
}
_LINHAS_FLUXO = {
    "caixaOperacionalUsd": ("Operating Cash Flow", "Total Cash From Operating Activities"),
    "capexUsd": ("Capital Expenditure", "CapitalExpenditures", "Capital Expenditures"),
}


def _colher(df, mapa: dict) -> list:
    """DataFrame do yfinance (linhas = contas, colunas = datas) -> [linha]."""
    if df is None or getattr(df, "empty", True):
        return []
    saida = []
    for coluna in df.columns:
        data = str(coluna)[:10]
        t = trimestre_calendario(data)
        if not t:
            continue
        linha = {"trimestre": t, "fimFiscal": data,
                 "disponivelEm": disponivel_em(data), "fonte": "yfinance"}
        achou = False
        for campo, nomes in mapa.items():
            for nome in nomes:
                if nome in df.index:
                    v = _num(df.loc[nome, coluna])
                    if v is not None:
                        linha[campo] = v
                        achou = True
                    break
        if achou:
            saida.append(linha)
    return saida


def _do_yfinance(ticker: str) -> dict:
    import yfinance as yf
    t = yf.Ticker(ticker)
    return {"balanco": _colher(t.quarterly_balance_sheet, _LINHAS_BALANCO),
            "fluxo": _colher(t.quarterly_cashflow, _LINHAS_FLUXO)}


def _av_json(funcao: str, ticker: str) -> dict | None:
    """Uma chamada à Alpha Vantage, com débito de cota e aviso tratado.

    O aviso de cota vem como 200 OK com JSON de aviso -- e esse aviso ECOA A
    CHAVE, por isso passa por censurar_chave antes de virar log (ver o
    incidente de 25/08/2026 em alpha_vantage_provider.py)."""
    try:
        from alpha_vantage_provider import (  # type: ignore
            _api_key, aviso_e_limite_diario, censurar_chave,
            limite_diario_batido, marcar_limite_diario)
        from http_retry import SESSION
        from provider_health import consumir_orcamento_diario
    except ImportError:
        from agent.alpha_vantage_provider import (  # type: ignore
            _api_key, aviso_e_limite_diario, censurar_chave,
            limite_diario_batido, marcar_limite_diario)
        from agent.http_retry import SESSION
        from agent.provider_health import consumir_orcamento_diario
    chave = _api_key()
    if not chave:
        return None
    # Cada ticker custa DUAS chamadas aqui, então desistir cedo vale ainda
    # mais que no capex.
    if limite_diario_batido():
        raise RuntimeError("Alpha Vantage já recusou por limite diário nesta "
                           "rodada -- pulando o resto")
    orcamento = int(os.environ.get("AGENT_ALPHAVANTAGE_MAX_DIA", "15"))
    if not consumir_orcamento_diario("alphavantage", orcamento):
        print(f"[folego] cota diária da Alpha Vantage ({orcamento}) esgotada — "
              f"{ticker} fica sem {funcao}", file=sys.stderr)
        return None
    r = SESSION.get("https://www.alphavantage.co/query",
                    params={"function": funcao, "symbol": ticker, "apikey": chave},
                    timeout=20)
    r.raise_for_status()
    dados = r.json()
    aviso = (dados.get("Note") or dados.get("Information")
             or dados.get("Error Message"))
    if aviso:
        if aviso_e_limite_diario(aviso):
            marcar_limite_diario()
        raise RuntimeError("Alpha Vantage respondeu aviso em vez de dados: "
                           f"{censurar_chave(aviso)[:180]}")
    if "quarterlyReports" not in dados:
        raise RuntimeError(f"Alpha Vantage sem quarterlyReports para {ticker} "
                           f"({funcao}): {str(dados)[:180]}")
    return dados


_AV_BALANCO = {
    "caixaUsd": ("cashAndCashEquivalentsAtCarryingValue", "cashAndShortTermInvestments"),
    "dividaUsd": ("shortLongTermDebtTotal",),
    "ativoCirculanteUsd": ("totalCurrentAssets",),
    "passivoCirculanteUsd": ("totalCurrentLiabilities",),
    "patrimonioUsd": ("totalShareholderEquity",),
    "acoesEmCirculacao": ("commonStockSharesOutstanding",),
}
_AV_FLUXO = {
    "caixaOperacionalUsd": ("operatingCashflow",),
    "capexUsd": ("capitalExpenditures",),
}


def _do_alpha_vantage(ticker: str) -> dict:
    """Só quando o yfinance vem VAZIO -- ver PROFUNDIDADE_MINIMA para por que
    aqui não se paga cota por profundidade."""
    saida = {"balanco": [], "fluxo": []}
    for chave_saida, funcao, mapa in (("balanco", "BALANCE_SHEET", _AV_BALANCO),
                                      ("fluxo", "CASH_FLOW", _AV_FLUXO)):
        dados = _av_json(funcao, ticker)
        if not dados:
            continue
        for rel in (dados.get("quarterlyReports") or []):
            data = str(rel.get("fiscalDateEnding", ""))[:10]
            t = trimestre_calendario(data)
            if not t:
                continue
            linha = {"trimestre": t, "fimFiscal": data,
                     "disponivelEm": disponivel_em(data), "fonte": "alpha_vantage"}
            achou = False
            for campo, nomes in mapa.items():
                for nome in nomes:
                    bruto = rel.get(nome)
                    if bruto not in (None, "None", ""):
                        v = _num(bruto)
                        if v is not None:
                            linha[campo] = v
                            achou = True
                        break
            if achou:
                saida[chave_saida].append(linha)
    return saida


def combinar(principal: dict, complemento: dict) -> dict:
    """Une as duas fontes por trimestre, com a PRINCIPAL vencendo o empate --
    trocar de fonte no meio da série criaria degrau artificial justamente na
    variação, que é o que se lê aqui."""
    saida = {}
    for secao in ("balanco", "fluxo"):
        por_t = {l["trimestre"]: l for l in (complemento.get(secao) or [])
                 if l.get("trimestre")}
        por_t.update({l["trimestre"]: l for l in (principal.get(secao) or [])
                      if l.get("trimestre")})
        saida[secao] = sorted(por_t.values(), key=lambda l: l["trimestre"])
    return saida


def _profundidade(bruto: dict) -> int:
    """Trimestres com balanço E fluxo -- é a interseção que permite a conta."""
    b = {l["trimestre"] for l in (bruto.get("balanco") or []) if l.get("trimestre")}
    f = {l["trimestre"] for l in (bruto.get("fluxo") or []) if l.get("trimestre")}
    return len(b & f)


def _limite_batido() -> bool:
    """Disjuntor da AV, por import tardio -- ver capex_hyperscalers._limite_batido."""
    try:
        try:
            from alpha_vantage_provider import limite_diario_batido
        except ImportError:
            from agent.alpha_vantage_provider import limite_diario_batido
        return limite_diario_batido()
    except Exception:
        return False


def coletar(tickers=None, *, yf_fn=_do_yfinance, av_fn=_do_alpha_vantage,
            profundidade_minima: int = PROFUNDIDADE_MINIMA,
            pausa_s: float = PAUSA_ENTRE_CHAMADAS_AV_S,
            guardado: dict | None = None) -> dict:
    """{ticker: {balanco, fluxo}} + relatório. Funções injetáveis para a suíte
    exercitar a cascata sem rede.

    `guardado` entra só na DECISÃO de gastar cota, igual ao capex: histórico
    que já está no disco não precisa ser comprado de novo."""
    alvo = list(tickers) if tickers else list(config.PORTFOLIO_TICKERS)
    guardado = guardado or {}
    por_ticker, falhas, rasos = {}, [], []
    usou_av = False
    for tk in alvo:
        bruto = {"balanco": [], "fluxo": []}
        try:
            bruto = yf_fn(tk) or bruto
        except Exception as e:
            print(f"[folego] yfinance falhou em {tk}: {type(e).__name__}: {e}",
                  file=sys.stderr)
        no_disco = guardado.get(tk) or {"balanco": [], "fluxo": []}
        if _profundidade(combinar(bruto, no_disco)) < profundidade_minima:
            if _limite_batido():
                # A pausa mora aqui, antes da chamada: sem esta saída o
                # disjuntor pouparia a chamada mas não os 13s de espera.
                print(f"[folego] {tk}: Alpha Vantage já recusou por limite "
                      f"diário nesta rodada — nem tento", file=sys.stderr)
                if bruto.get("balanco") or bruto.get("fluxo"):
                    por_ticker[tk] = bruto
                else:
                    falhas.append(tk)
                continue
            print(f"[folego] {tk}: balanço raso no yfinance "
                  f"({_profundidade(bruto)} trimestres completos, mínimo "
                  f"{profundidade_minima}), tentando Alpha Vantage", file=sys.stderr)
            if usou_av and pausa_s > 0:
                # 5 chamadas/minuto no plano grátis, e aqui cada ticker custa
                # DUAS (balanço + fluxo) -- espaçar não é opcional.
                time.sleep(pausa_s)
            usou_av = True
            try:
                bruto = combinar(bruto, av_fn(tk))
            except Exception as e:
                print(f"[folego] alpha vantage falhou em {tk}: {type(e).__name__}: {e}",
                      file=sys.stderr)
        if bruto.get("balanco") or bruto.get("fluxo"):
            por_ticker[tk] = bruto
        else:
            falhas.append(tk)
            print(f"[folego] {tk}: SEM BALANÇO nas duas fontes", file=sys.stderr)
    return {"porTicker": por_ticker, "falhas": falhas, "rasos": rasos}


def mesclar_bruto(anterior: dict, novo: dict) -> dict:
    """Une o bruto guardado com o coletado, por ticker. Novo vence o empate:
    reapresentação de balanço corrige número antigo, e o guardado fornece
    alcance, não versão. Mesma regra do capex, pelo mesmo motivo -- sem isso a
    série encolhe quando uma coleta vem curta."""
    saida = {}
    for tk in set(anterior) | set(novo):
        unido = combinar(novo.get(tk) or {}, anterior.get(tk) or {})
        for secao in ("balanco", "fluxo"):
            unido[secao] = unido[secao][-TRIMESTRES_BRUTOS_GUARDADOS:]
        if unido["balanco"] or unido["fluxo"]:
            saida[tk] = unido
    return saida


def montar(tickers=None, *, bruto_anterior=None, hoje: str | None = None, **kw) -> dict:
    alvo = list(tickers) if tickers else list(config.PORTFOLIO_TICKERS)
    col = coletar(alvo, guardado=bruto_anterior or {}, **kw)
    por_ticker = mesclar_bruto(bruto_anterior or {}, col["porTicker"])

    series, resumos = {}, {}
    for tk, bruto in por_ticker.items():
        serie = montar_serie(bruto.get("balanco"), bruto.get("fluxo"), hoje=hoje)
        series[tk] = serie
        resumos[tk] = avaliar(serie)

    profundidade = kw.get("profundidade_minima", PROFUNDIDADE_MINIMA)
    rasos = sorted(tk for tk in alvo
                   if len(series.get(tk) or []) < profundidade)
    falhas = sorted(tk for tk in alvo if not por_ticker.get(tk))
    usou_guardado = sorted(tk for tk in col["falhas"] if por_ticker.get(tk))
    if usou_guardado:
        print(f"[folego] sem coleta nova para {', '.join(usou_guardado)} — "
              f"seguindo com o balanço guardado no overlay", file=sys.stderr)

    fontes = sorted({l.get("fonte") for bruto in por_ticker.values()
                     for secao in ("balanco", "fluxo")
                     for l in (bruto.get(secao) or []) if l.get("fonte")})
    return {
        "coletadoEm": today_brt().isoformat(),
        "tickersPedidos": len(alvo),
        "tickersComDado": len(por_ticker),
        "falhas": falhas,
        "serieRasa": rasos,
        "usandoGuardado": usou_guardado,
        "fontes": fontes,
        "porTicker": por_ticker,
        "series": series,
        "resumo": resumos,
    }


def gravar_overlay(dados: dict, caminho: str = OVERLAY_PATH_DEFAULT) -> bool:
    try:
        os.makedirs(os.path.dirname(caminho) or ".", exist_ok=True)
        tmp = caminho + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json_seguro.dumps(dados))
        os.replace(tmp, caminho)
        return True
    except Exception as e:
        print(f"[folego] não consegui gravar o overlay ({caminho}): {e}", file=sys.stderr)
        return False


def ler_overlay(caminho: str = OVERLAY_PATH_DEFAULT) -> dict | None:
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except Exception as e:
        print(f"[folego] overlay ilegível ({caminho}): {e}", file=sys.stderr)
        return None


def _bi(v) -> str:
    return "—" if v is None else f"{v / 1e9:,.2f}"


if __name__ == "__main__":
    modo_json = "--json" in sys.argv
    caminho = os.environ.get("FOLEGO_OVERLAY_PATH", OVERLAY_PATH_DEFAULT)
    guardado = (ler_overlay(caminho) or {}).get("porTicker") or {}
    try:
        dados = montar(bruto_anterior=guardado)
    except Exception as e:
        if modo_json:
            print(json_seguro.dumps({"ok": False, "erro": f"{type(e).__name__}: {e}"}))
            sys.exit(0)
        raise

    if modo_json:
        gravou = gravar_overlay(dados, caminho)
        queimando = sorted(tk for tk, r in dados["resumo"].items()
                           if r.get("folegoTrimestres") is not None)
        print(json_seguro.dumps({
            "ok": bool(gravou and dados["tickersComDado"]),
            "tickersComDado": dados["tickersComDado"],
            "falhas": dados["falhas"],
            "serieRasa": dados["serieRasa"],
            "usandoGuardado": dados["usandoGuardado"],
            "queimandoCaixa": queimando,
            "fontes": dados["fontes"],
            "overlay": caminho if gravou else None,
        }))
        sys.exit(0)

    print(f"{'TICKER':<8}{'TRI':<8}{'CAIXA(bi)':>11}{'DÍV.LÍQ(bi)':>13}"
          f"{'FCF(bi)':>10}{'FÔLEGO':>10}  AVISO")
    for tk in sorted(dados["resumo"]):
        r = dados["resumo"][tk]
        if not r.get("disponivel"):
            print(f"{tk:<8}{'—':<8}{r.get('nota', '')}")
            continue
        folego = ("gera caixa" if r["geraCaixa"]
                  else f"{r['folegoTrimestres']} tri" if r["folegoTrimestres"] is not None
                  else "—")
        aviso = "série quebrada (reestruturação?)" if r["quebraDeSerie"] else ""
        print(f"{tk:<8}{r['trimestre']:<8}{_bi(r['caixaUsd']):>11}"
              f"{_bi(r['dividaLiquidaUsd']):>13}{_bi(r['fcfTrimestralUsd']):>10}"
              f"{folego:>10}  {aviso}")
    if dados["falhas"]:
        print(f"\nsem balanço: {', '.join(dados['falhas'])}")
    if dados["serieRasa"]:
        print(f"série curta (< {PROFUNDIDADE_MINIMA} trimestres): "
              f"{', '.join(dados['serieRasa'])}")
    if gravar_overlay(dados, caminho):
        print(f"overlay gravado em {caminho}")
