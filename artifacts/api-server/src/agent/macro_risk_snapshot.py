"""
Coleta as seis fontes do risco macro e devolve o retrato do dia.

Roda 1x por pregão no pré-mercado. O stdout é EXCLUSIVO do JSON final (o Node
faz JSON.parse); todo diagnóstico vai para stderr.

## A regra que organiza este arquivo

Fonte que falha vira `None`, nunca zero. O macro_risk.py distingue "medi e está
calmo" de "não consegui medir" -- e essa distinção só sobrevive se a coleta
respeitá-la. Um `except` que devolvesse 0.0 aqui reintroduziria, na camada de
baixo, exatamente o bug que o módulo corrige: cegueira lida como segurança.

Por isso cada bloco é isolado: uma fonte fora não pode derrubar as outras
cinco, e cada falha é nomeada em `coleta.erros`.

## Roda como MÓDULO, nunca por caminho

    python3 -m agent.macro_risk_snapshot        # certo
    python3 agent/macro_risk_snapshot.py        # QUEBRA três fontes

Este script usa market_alerts (Kospi), tools (notícias) e o pacote agent para
earnings, e esses módulos fazem `from .cache import cached` -- import relativo
que só resolve em contexto de pacote.

Rodar por caminho põe agent/ no sys.path e o pacote deixa de resolver pelo
nome. Na época havia um agravante -- um `agent.py` DENTRO de `agent/`, que
sombreava o pacote e fazia `from agent import market_alerts` procurar um
atributo dentro do módulo errado. Esse arquivo virou `llm_runtime.py` e a
sombra acabou; rodar por caminho continua quebrando, pelo import relativo.
Medido em produção 19/08/2026:

    "^KS11":    "attempted relative import with no known parent package"
    "earnings":  idem
    "noticias":  idem

Três das seis fontes caíram em silêncio -- a coleta isola falha por bloco, então
o retrato saiu com cobertura 90% em vez de erro. Mesma regra de
analise_rapida_ia.py e get_market_alerts_snapshot.py.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta

from agent import json_seguro
from agent import brt
from agent.macro_risk import MacroRiskModule
from agent.http_retry import SESSION

def _hoje() -> date:
    """"Hoje" em Brasília, não em UTC.

    O processo roda em UTC, então entre 21h e 23h59 BRT o dia do container já
    virou -- é exatamente o que o brt.py existe para evitar, e o que aconteceu
    aqui em 19/08/2026: às 22:07 BRT do dia 18, o retrato foi gravado com
    `snapshot_date = 2026-08-19`.

    O estrago é maior que um rótulo errado, porque a data é a CHAVE da série: o
    dia 18 nunca existiria, e a linha do 19 seria sobrescrita pelo cron da
    manhã carregando, até lá, dado da noite anterior. Buraco no histórico que
    só aparece meses depois, ao comparar um dia ruim com o padrão.
    """
    return brt.today_brt()


def _log(msg: str) -> None:
    print(f"[macro_risk] {msg}", file=sys.stderr, flush=True)


# ── FRED ────────────────────────────────────────────────────────────────────
#
# A chave já existe (FRED_API_KEY, ver get_macro_indicators em tools.py). O que
# não existia era a leitura de DUAS observações: get_macro_indicators pede
# limit=1 porque só quer o nível corrente, e aqui o sinal é a VARIAÇÃO.
#
# DGS30/DGS10 saem com ~1 dia de atraso e o WTI com 2-3. "Hoje" aqui é sempre a
# observação mais recente publicada, não o pregão corrente -- por isso as datas
# viajam no payload: um delta calculado entre observações distantes não é
# variação de um dia, e quem lê a tela precisa poder ver isso.

SERIES_FRED = {
    "yield_30y": "DGS30",
    "yield_10y": "DGS10",
    "wti": "DCOILWTICO",
}

# Idade máxima da observação mais recente para ela ainda valer como "hoje".
#
# Produção 18/08/2026, primeira coleta completa:
#
#     "datasFred": {"yield_30y": ["2026-08-17", "2026-08-14"],
#                   "wti":       ["2026-08-11", "2026-08-10"]}
#
# Os yields vieram de ontem (normal). O WTI veio de SETE DIAS antes -- ou seja,
# o sinal de choque geopolítico estava comparando 11 contra 10 de agosto e
# apresentando isso como o movimento do dia. Um salto do petróleo hoje só
# apareceria daqui a uma semana: o sinal desenhado para pegar choque agudo
# chegava tarde por construção.
#
# 4 dias cobre fim de semana emendado com feriado sem aceitar dado de semana
# passada. Acima disso a série vira sem_dado com a idade nomeada -- é o mesmo
# princípio do `suspect` no snapshot global: número velho rotulado é melhor que
# número velho usado como se fosse fresco.
IDADE_MAX_OBS_DIAS = 4

# Futuro do petróleo: fecha todo pregão e é o preço que o mercado está olhando
# AGORA. O FRED continua como reserva -- ele é a fonte oficial, mas publica com
# atraso variável, e para este sinal a frescura importa mais que a procedência.
WTI_TICKER = "CL=F"


def _fred_duas_ultimas(series_id: str) -> tuple[float | None, float | None, list[str]]:
    """(mais recente, anterior, datas). Pede 10 e filtra: a série vem com '.'
    em feriado, e pedir limit=2 devolveria dois pontos vazios numa emenda."""
    chave = os.environ.get("FRED_API_KEY", "").strip()
    if not chave:
        raise RuntimeError("FRED_API_KEY não configurada")

    r = SESSION.get(
        "https://api.stlouisfed.org/fred/series/observations",
        params={"series_id": series_id, "api_key": chave, "file_type": "json",
                "sort_order": "desc", "limit": 10},
        timeout=15,
    )
    r.raise_for_status()
    validas = [
        o for o in ((r.json() or {}).get("observations") or [])
        if o.get("value") not in (None, ".", "")
    ]
    if len(validas) < 2:
        raise RuntimeError(f"{series_id}: menos de 2 observações válidas")
    return (
        float(validas[0]["value"]),
        float(validas[1]["value"]),
        [validas[0].get("date", ""), validas[1].get("date", "")],
    )


# ── Ásia ────────────────────────────────────────────────────────────────────

SK_HYNIX = "000660.KS"
SAMSUNG = "005930.KS"


# Fechamento de cada praça: sufixo do ticker -> (offset UTC, hora local do
# fechamento). Só praças cujo pregão precisa estar ENCERRADO para o número valer.
#
# Coreia não observa horário de verão, então UTC+9 é exato -- mesma premissa que
# o brt.py usa para o UTC-3 do Brasil. Sufixo desconhecido mantém o
# comportamento antigo: quem não está aqui não é tratado, e isso é dito no
# código em vez de virar suposição silenciosa.
MERCADOS_POR_SUFIXO = {
    ".KS": (9, 15.5),    # KRX, fecha 15:30 KST
    ".KQ": (9, 15.5),    # KOSDAQ
}
# Índices, que não têm sufixo de praça.
MERCADOS_POR_TICKER = {
    "^KS11": (9, 15.5),  # KOSPI Composite
    "^KQ11": (9, 15.5),  # KOSDAQ Composite
}
# Margem depois do fechamento para o dado assentar na fonte.
MARGEM_APOS_FECHAMENTO_H = 0.5


def _sessao_ainda_aberta(ticker: str, data_da_barra, agora_utc: datetime | None = None) -> bool:
    """A última barra é de um pregão que ainda pode estar em curso?

    Produção 19/08/2026, 00:25 UTC (09:25 em Seul, 25 min após a abertura da
    KRX). Duas coletas do mesmo dado com minutos de diferença:

        linha de comando   sk_hynix +1,03   samsung -2,19   kospi +2,42
        rota (botão)       sk_hynix -9,33   samsung -7,45   kospi descartado

    A primeira leu o pregão de ONTEM; a segunda, a barra em ANDAMENTO de hoje.
    `sem_barra_incompleta` não pega isso -- ela descarta barra com Close vazio, e
    barra intradiária tem Close, só que provisório.

    O estrago é maior que a inconsistência: ler sessão em curso como dia fechado
    faz o número mudar a manhã inteira, e o sinal dispara ou não conforme a hora
    em que alguém abre a tela. Também contradiz a premissa do próprio sinal --
    ele vale como leading indicator PORQUE a Coreia já fechou.

    O cron das 07:50 BRT (19:50 em Seul) sempre pegou sessão encerrada; quem
    esbarra nisso é a coleta sob demanda.
    """
    alvo = ticker.upper()
    praca = MERCADOS_POR_TICKER.get(alvo)
    if praca is None:
        sufixo = next((s for s in MERCADOS_POR_SUFIXO if alvo.endswith(s)), None)
        praca = MERCADOS_POR_SUFIXO.get(sufixo) if sufixo else None
    if praca is None:
        return False

    offset_h, fecha_h = praca
    agora_local = (agora_utc or datetime.utcnow()) + timedelta(hours=offset_h)
    if data_da_barra != agora_local.date():
        return False
    hora_local = agora_local.hour + agora_local.minute / 60
    return hora_local < fecha_h + MARGEM_APOS_FECHAMENTO_H


def _variacao_do_dia(ticker: str) -> float | None:
    """Variação do último pregão ENCERRADO."""
    try:
        from agent import market_data_provider as mdp
    except ImportError:
        import market_data_provider as mdp  # type: ignore

    res = mdp.get_daily_history(ticker, "1mo")
    if not res.ok or res.df is None or len(res.df) < 2:
        return None

    fech = res.df["Close"]
    fim = -1
    if _sessao_ainda_aberta(ticker, res.df.index[-1].date()):
        # Recua um pregão em vez de devolver None: o que o sinal quer é a última
        # sessão FECHADA, e ela está logo atrás. Devolver nada aqui apagaria o
        # sinal a manhã inteira na Ásia.
        fim = -2
    if len(fech) < abs(fim) + 1:
        return None

    anterior = float(fech.iloc[fim - 1])
    if not anterior:
        return None
    return round((float(fech.iloc[fim]) / anterior - 1) * 100, 2)


# Casas decimais do preço vindo do provider.
#
# O yfinance devolve float32, e a conversão para float64 expõe o ruído da
# representação: em 19/08/2026 o WTI chegou como 84.43000030517578. Na tela sai
# $84,43 (o formatador arredonda), mas o valor CRU é gravado no `raw` do
# snapshot -- que existe justamente para revisar thresholds meses depois, e
# nesse momento a sujeira atrapalha a leitura.
#
# 4 e não 2: preço de índice ou de câmbio pode precisar de mais que centavo, e
# 4 já mata o ruído de float32 sem inventar precisão.
CASAS_DO_PRECO = 4


def _dois_ultimos_fechamentos(ticker: str) -> tuple[float, float, str]:
    """(último, anterior, data do último). Levanta quando não dá para formar o
    par -- quem chama transforma isso em sem_dado com motivo."""
    try:
        from agent import market_data_provider as mdp
    except ImportError:
        import market_data_provider as mdp  # type: ignore

    res = mdp.get_daily_history(ticker, "1mo")
    if not res.ok or res.df is None or len(res.df) < 2:
        raise RuntimeError(f"{ticker}: histórico insuficiente")
    fech = res.df["Close"]
    return (
        round(float(fech.iloc[-1]), CASAS_DO_PRECO),
        round(float(fech.iloc[-2]), CASAS_DO_PRECO),
        str(res.df.index[-1].date()),
    )


def _kospi_do_snapshot_global() -> tuple[float | None, str]:
    """Reusa get_global_market_snapshot (playbook §2b: não recalcular o que já
    existe). Devolve (pct, motivo_da_recusa).

    Índice marcado `suspect` NÃO é usado: o rótulo existe justamente porque o
    número pode ser comparação atravessando sessões. Perder o índice não é
    grave -- check_asia_contagion dispara por ação OU índice, e SK Hynix e
    Samsung vêm de outra fonte.

    Nota para quem revisar: o limite de implausibilidade do snapshot é 8,0% e o
    Kospi fechou a -8,0% em 28/07/2026, com circuit breaker acionado. Ou seja,
    uma queda REAL um pouco maior chegaria aqui rotulada como suspeita. Esse
    limite serve outros consumidores e não foi mexido, mas é por isso que este
    sinal não depende só do índice."""
    try:
        from agent import market_alerts as ma
    except ImportError:
        import market_alerts as ma  # type: ignore

    itens = (ma.get_global_market_snapshot() or {}).get("items") or []
    kospi = next((i for i in itens if i.get("ticker") == "^KS11"), None)
    if not kospi:
        return None, "^KS11 ausente do snapshot global"
    if kospi.get("suspect"):
        return None, f"marcado suspeito: {kospi.get('suspectReason', 'sem motivo')}"

    # Sessão em curso é descartada, pelo mesmo motivo das AÇÕES coreanas (ver
    # _sessao_ainda_aberta) -- mas por um caminho diferente, e foi por isso que
    # escapou do primeiro conserto.
    #
    # Produção 19/08/2026, 00:58 UTC (09:58 em Seul):
    #
    #     sk_hynix  +1,03   samsung  -2,19    <- pregão fechado
    #     kospi     -7,27                     <- sessão em curso
    #
    # O sinal disparou com DUAS bases de tempo no mesmo cartão, o que é pior
    # que a inconsistência que o antecedeu: parece coerente.
    #
    # O que se perde é só o intradiário no botão. O cron das 07:50 BRT roda às
    # 19:50 em Seul, com tudo fechado -- para o uso real, o retrato antes da
    # abertura americana, a queda coreana aparece inteira e no tempo certo.
    quando = kospi.get("asOf")
    try:
        data_barra = datetime.strptime(str(quando), "%Y-%m-%d").date() if quando else None
    except ValueError:
        data_barra = None
    if data_barra and _sessao_ainda_aberta("^KS11", data_barra):
        return None, f"pregão de {quando} ainda em curso em Seul"

    return kospi.get("changePct"), ""


# ── SOX ─────────────────────────────────────────────────────────────────────

# ^SOX é o índice; SOXX é o ETF que o replica. A reserva existe porque o
# yfinance serve índices de forma menos confiável que ações/ETFs, e para a
# medida que importa aqui -- retorno de 9 semanas -- os dois são
# intercambiáveis na prática.
SOX_TICKERS = ["^SOX", "SOXX"]


def _serie_sox(pregoes_minimos: int = 46) -> tuple[list[float] | None, str]:
    try:
        from agent import market_data_provider as mdp
    except ImportError:
        import market_data_provider as mdp  # type: ignore

    ultimo_erro = "nenhum ticker tentado"
    for t in SOX_TICKERS:
        try:
            res = mdp.get_daily_history(t, "1y")
            if res.ok and res.df is not None and len(res.df) >= pregoes_minimos:
                return [float(v) for v in res.df["Close"]], ""
            ultimo_erro = f"{t}: série com {0 if res.df is None else len(res.df)} pregões"
        except Exception as e:  # noqa: BLE001
            ultimo_erro = f"{t}: {e}"
    return None, ultimo_erro


# ── FOMC ────────────────────────────────────────────────────────────────────

JANELA_FOMC_DIAS = 1


def _idade_em_dias(iso: str, hoje: date | None = None) -> int | None:
    try:
        return ((hoje or _hoje()) - datetime.strptime(iso, "%Y-%m-%d").date()).days
    except (ValueError, TypeError):
        return None


def _perto_do_fomc(hoje: date | None = None) -> bool:
    """As datas oficiais já existem em market_alerts.MACRO_EVENTS -- manter uma
    segunda lista aqui seria criar fonte divergente (playbook §10)."""
    try:
        from agent import market_alerts as ma
    except ImportError:
        import market_alerts as ma  # type: ignore

    hoje = hoje or _hoje()
    for iso in ma.MACRO_EVENTS.get("FOMC", []):
        try:
            d = datetime.strptime(iso, "%Y-%m-%d").date()
        except ValueError:
            continue
        if abs((d - hoje).days) <= JANELA_FOMC_DIAS:
            return True
    return False


# ── Balanço recente ─────────────────────────────────────────────────────────

# Janela em dias corridos: um balanço de sexta reage na sexta e ainda pesa na
# segunda. Mais que isso e o "reagiu ao balanço" vira "andou na semana".
JANELA_EARNINGS_DIAS = 3

# Nomes de coluna já vistos no earnings_dates do yfinance. A lista existe
# porque a biblioteca renomeia colunas entre versões, e um KeyError aqui
# derrubaria a busca inteira -- o que este módulo trata como sem_dado, mas com
# um motivo inútil ("KeyError") em vez do nome do que faltou.
COLUNAS_SURPRESA = ("Surprise(%)", "Surprise (%)", "surprise(%)", "Surprise Pct")


def _surpresa_recente(ticker: str, hoje: date | None = None) -> tuple[float | None, str]:
    """Surpresa de EPS do último balanço, se ele foi na janela.

    Só EPS: a receita viria da FMP, que devolve 402 nesta conta. Ver o
    comentário de check_priced_for_perfection sobre por que isso não invalida
    o sinal."""
    import yfinance as yf

    hoje = hoje or _hoje()
    df = yf.Ticker(ticker).earnings_dates
    if df is None or len(df) == 0:
        return None, f"{ticker}: sem earnings_dates"

    coluna = next((c for c in COLUNAS_SURPRESA if c in df.columns), None)
    if coluna is None:
        return None, f"{ticker}: sem coluna de surpresa em {list(df.columns)[:4]}"

    # O índice vem com fuso; comparar date com date evita o off-by-one que o
    # processo em UTC introduziria (mesma lição do get_earnings_calendar).
    for quando, linha in df.iterrows():
        try:
            dia = quando.date()
        except AttributeError:
            continue
        if not (0 <= (hoje - dia).days <= JANELA_EARNINGS_DIAS):
            continue
        valor = linha.get(coluna)
        if valor is None or valor != valor:      # NaN: balanço agendado, ainda não reportado
            return None, f"{ticker}: balanço em {dia} sem número reportado"
        return round(float(valor), 2), ""
    return None, ""      # sem balanço na janela -- não é erro


def _earnings_da_carteira() -> tuple[float | None, float | None, str]:
    """(surpresa de EPS, reação do dia, motivo).

    Varre os tickers cobertos e usa o PRIMEIRO com balanço na janela. Um por
    vez de propósito: o sinal descreve um evento -- "fulano bateu e caiu" --, e
    misturar dois balanços numa média produziria um número que não aconteceu
    com ninguém.
    """
    try:
        from agent import config
    except ImportError:
        import config  # type: ignore

    motivos: list[str] = []
    tickers = (getattr(config, "TICKERS", None) or [])[:12]
    for t in tickers:
        try:
            surpresa, motivo = _surpresa_recente(t)
        except Exception as e:  # noqa: BLE001
            motivos.append(f"{t}: {e}")
            continue
        if motivo:
            motivos.append(motivo)
        if surpresa is None:
            continue
        reacao = _variacao_do_dia(t)
        _log(f"balanço recente: {t} surpresa {surpresa:+.1f}% reação {reacao}")
        return surpresa, reacao, ""
    return None, None, _resumir(motivos)


# Quantos motivos distintos cabem no erro antes de virar contagem.
#
# Quando a fonte cai, ela cai para TODOS os tickers com a mesma mensagem: em
# 19/08/2026 isso produziu doze cópias de um erro de curl de 130 caracteres
# dentro de `coleta.erros` -- que é persistido no `raw` e fica lá para sempre.
# Doze repetições não informam mais que uma; informam menos, porque escondem
# um motivo diferente que estivesse no meio.
MAX_MOTIVOS = 3


def _resumir(motivos: list[str]) -> str:
    """Junta os motivos sem repetir o mesmo texto doze vezes."""
    if not motivos:
        return ""
    vistos: list[str] = []
    for m in motivos:
        # Sem o prefixo "TICKER: ", que é o que difere quando a causa é a mesma.
        corpo = m.split(": ", 1)[-1]
        if corpo not in [v.split(": ", 1)[-1] for v in vistos]:
            vistos.append(m)
    resumo = "; ".join(vistos[:MAX_MOTIVOS])
    restantes = len(motivos) - len(vistos[:MAX_MOTIVOS])
    return f"{resumo} (+{restantes} ticker(s) com o mesmo motivo)" if restantes > 0 else resumo


# ── Manchetes China/semis ───────────────────────────────────────────────────

def _manchetes_china() -> tuple[list[dict] | None, str]:
    """get_geopolitical_news já cobre controle de exportação de semicondutores
    (China/Taiwan) numa chamada só, sem ticker.

    O tom sai do léxico de get_trend.py, o mesmo usado na análise de tendência
    -- duplicar uma lista de palavras aqui faria duas medidas de "notícia ruim"
    divergirem com o tempo."""
    try:
        from agent import tools
        from agent.get_trend import _NEGATIVE_RE, _POSITIVE_RE
    except ImportError:
        import tools  # type: ignore
        from get_trend import _NEGATIVE_RE, _POSITIVE_RE  # type: ignore

    por_tema = tools.get_geopolitical_news() or {}
    manchetes: list[dict] = []
    for lista in por_tema.values():
        for n in lista or []:
            titulo = str(n.get("title") or "")
            if not titulo:
                continue
            baixo = titulo.lower()
            neg = len(set(_NEGATIVE_RE.findall(baixo)))
            pos = len(set(_POSITIVE_RE.findall(baixo)))
            # -0.3 / 0 / +0.3: o check só compara contra um limiar negativo, e
            # o léxico dá tom, não intensidade. Fingir uma escala contínua aqui
            # sugeriria precisão que a medida não tem.
            escore = -0.3 if neg > pos else (0.3 if pos > neg else 0.0)
            manchetes.append({"title": titulo, "overall_sentiment_score": escore})
    return manchetes, ""


# ── Coleta ──────────────────────────────────────────────────────────────────

def coletar() -> tuple[dict, dict]:
    """(kwargs do evaluate, diagnóstico da coleta).

    Cada fonte no seu próprio try: uma fora não pode levar as outras cinco.
    """
    dados: dict = {}
    erros: dict[str, str] = {}
    datas: dict[str, list[str]] = {}

    # WTI pelo futuro primeiro: o FRED publica com atraso variável (7 dias em
    # 18/08/2026) e este sinal existe para pegar choque AGUDO.
    try:
        hoje_p, ant_p, quando = _dois_ultimos_fechamentos(WTI_TICKER)
        dados["wti_hoje"], dados["wti_ant"] = hoje_p, ant_p
        datas["wti"] = [quando, ""]
    except Exception as e:  # noqa: BLE001
        _log(f"{WTI_TICKER} indisponível ({e}) -- caindo para o FRED")

    for campo, series_id in SERIES_FRED.items():
        if f"{campo}_hoje" in dados:
            continue                      # já resolvido por fonte mais fresca
        try:
            hoje_v, ant_v, ds = _fred_duas_ultimas(series_id)
        except Exception as e:  # noqa: BLE001
            erros[series_id] = str(e)
            _log(f"{series_id} indisponível: {e}")
            continue
        # Observação velha demais não vale como "hoje". Usá-la assim produz um
        # delta de semana passada com cara de variação do dia -- pior que não
        # ter o dado, porque parece medição.
        idade = _idade_em_dias(ds[0])
        if idade is not None and idade > IDADE_MAX_OBS_DIAS:
            erros[series_id] = f"observação de {ds[0]} tem {idade} dias (máx {IDADE_MAX_OBS_DIAS})"
            _log(f"{series_id} descartado -- {erros[series_id]}")
            continue
        dados[f"{campo}_hoje"], dados[f"{campo}_ant"] = hoje_v, ant_v
        datas[campo] = ds

    for nome, ticker in (("sk_hynix", SK_HYNIX), ("samsung", SAMSUNG)):
        try:
            dados[nome] = _variacao_do_dia(ticker)
            if dados[nome] is None:
                erros[ticker] = "histórico insuficiente"
        except Exception as e:  # noqa: BLE001
            dados[nome] = None
            erros[ticker] = str(e)
            _log(f"{ticker} indisponível: {e}")

    try:
        dados["kospi"], motivo = _kospi_do_snapshot_global()
        if motivo:
            erros["^KS11"] = motivo
            _log(f"Kospi descartado -- {motivo}")
    except Exception as e:  # noqa: BLE001
        dados["kospi"] = None
        erros["^KS11"] = str(e)

    try:
        dados["sox"], motivo = _serie_sox()
        if motivo:
            erros["SOX"] = motivo
    except Exception as e:  # noqa: BLE001
        dados["sox"] = None
        erros["SOX"] = str(e)

    try:
        dados["manchetes"], _ = _manchetes_china()
    except Exception as e:  # noqa: BLE001
        dados["manchetes"] = None
        erros["noticias"] = str(e)
        _log(f"manchetes indisponíveis: {e}")

    try:
        dados["eps_surpresa"], dados["reacao"], motivo = _earnings_da_carteira()
        if motivo:
            erros["earnings"] = motivo
    except Exception as e:  # noqa: BLE001
        dados["eps_surpresa"] = dados["reacao"] = None
        erros["earnings"] = str(e)
        _log(f"earnings indisponível: {e}")

    try:
        dados["fomc"] = _perto_do_fomc()
    except Exception:  # noqa: BLE001
        dados["fomc"] = False

    return dados, {"erros": erros, "datasFred": datas}


def montar() -> dict:
    dados, diag = coletar()
    saida = MacroRiskModule().evaluate(
        yield_30y_today=dados.get("yield_30y_hoje"),
        yield_30y_prev=dados.get("yield_30y_ant"),
        near_fomc_window=bool(dados.get("fomc")),
        sk_hynix_pct=dados.get("sk_hynix"),
        samsung_pct=dados.get("samsung"),
        kospi_pct=dados.get("kospi"),
        # Receita fica de fora: a fonte (earnings_dates) publica só EPS, e a de
        # receita viria da FMP, que devolve 402 nesta conta. O check trata a
        # receita como opcional -- ver o comentário lá.
        eps_surprise_pct=dados.get("eps_surpresa"),
        premarket_reaction_pct=dados.get("reacao"),
        manchetes=dados.get("manchetes"),
        sox_precos=dados.get("sox"),
        wti_hoje=dados.get("wti_hoje"),
        wti_anterior=dados.get("wti_ant"),
        yield_10y_hoje=dados.get("yield_10y_hoje"),
        yield_10y_anterior=dados.get("yield_10y_ant"),
    )
    saida["coleta"] = diag
    saida["snapshotDate"] = _hoje().isoformat()
    _log(
        f"cobertura {saida['cobertura_pct']}% | score {saida['aggregate_score']} | "
        f"{len(diag['erros'])} fonte(s) com erro"
    )
    return saida


if __name__ == "__main__":
    try:
        print(json_seguro.dumps(montar()))
    except Exception as e:  # noqa: BLE001
        print(json_seguro.dumps({"error": str(e) or e.__class__.__name__}))
