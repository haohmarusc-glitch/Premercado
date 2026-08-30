"""Análise de tendência por confluência: técnico + estrutura + notícias.

Núcleo técnico (peso maior):
  - Cruzamento de médias (SMA20 vs SMA50, preço vs SMA200)
  - Estrutura de preço (Higher Highs/Higher Lows vs Lower Highs/Lower Lows)
  - MACD histogram e RSI (Wilder)
Camada de notícias (modificador):
  - Sentimento por palavras-chave nas headlines (yfinance), sem custo de LLM.
Filosofia: calculadora, não decisor — expõe os componentes, não dá ordem de trade.

Input (stdin JSON):  {"tickers": ["NVDA", "SMCI"]}
Output (stdout JSON): {"items": [{ticker, trend, score, components, news, confluence}, ...]}
"""
import sys, json, os, re, time, datetime
import yfinance as yf
import pandas as pd
# Serializacao que nao emite NaN/Infinity -- ver json_seguro.py.
from agent import json_seguro

from agent.security import sanitize_ticker, friendly_error
from agent.nomes_de_empresas import fala_do_papel
from agent.ciclo_volatilidade import _earnings_proximo, _SEM_EARNINGS
from agent.earnings_reaction_analysis import (
    _janela_da_reacao, _sessao_de_hoje_ainda_em_curso, _NY_TZ,
)
from agent import earnings_dates as _earnings_dates
from agent import market_data_provider

# ── Cache em disco, autocontido. O motivo original de não usar o
#    agent/cache.py acabou: este script rodava por caminho, fora do pacote,
#    e não alcançava um módulo de import relativo. Hoje roda como módulo e
#    alcançaria -- a cópia daqui virou escolha, não restrição, e vale
#    unificar numa próxima passada.
#    Mesmo padrão: JSON em /tmp, falha aberta. TTL 30min — tendência sobre
#    candle diário não muda a cada minuto, e o Yahoo rate-limita IP do Replit.
_CACHE_PATH = os.environ.get("TREND_CACHE_PATH", "/tmp/premercado_trend_cache.json")
_TTL_SECONDS = int(os.environ.get("TREND_CACHE_TTL", "1800"))

# Abertura do pregão americano em UTC. 9h30 ET = 13h30 UTC no horário de verão
# (EDT) e 14h30 UTC fora dele (EST). Usamos SEMPRE 13h30, o limite mais cedo:
# fora do horário de verão isso só invalida o cache uma hora antes do
# necessário -- um recálculo a mais por dia, contra o risco de servir dado
# pré-abertura depois do pregão começar. Constante em UTC de propósito, sem
# zoneinfo: este script roda por spawn num container slim, e depender do banco
# de fusos do sistema para uma regra de cache seria trocar um problema barato
# por uma falha de import.
_ABERTURA_UTC = datetime.time(13, 30)


def _cruzou_abertura(gravado_em: float, agora: float) -> bool:
    """A abertura do pregão ficou ENTRE a gravação do cache e agora?

    Visto em produção (NBIS, 17/08/2026 10:37 BRT): o painel Tendência trazia
    o fechamento de sexta ($277,68) enquanto os outros três painéis já
    mostravam o preço ao vivo ($269,87) -- a entrada tinha sido gravada antes
    da abertura e o TTL de 30min ainda não a tinha vencido. A análise com IA
    citou o preço velho como "o preço atual" e abriu o texto dizendo que o
    papel estava colado na máxima, num dia em que ele caía 2,66%.

    TTL sozinho não resolve: o problema não é a entrada ser ANTIGA, é ela ser
    de OUTRO regime de dado (pré-abertura contra pregão em curso).
    """
    gravado = datetime.datetime.utcfromtimestamp(gravado_em)
    atual = datetime.datetime.utcfromtimestamp(agora)
    abertura_de_hoje = datetime.datetime.combine(atual.date(), _ABERTURA_UTC)
    return gravado < abertura_de_hoje <= atual

def _cache_load() -> dict:
    try:
        if os.path.exists(_CACHE_PATH):
            with open(_CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def _cache_save(cache: dict) -> None:
    try:
        with open(_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
    except Exception:
        pass  # disco cheio/sem permissão: segue sem cache

# ── Sentimento por palavras-chave (headlines vêm em inglês do yfinance) ──────
POSITIVE = [
    "beat", "beats", "surge", "surges", "soar", "soars", "rally", "rallies",
    "record", "upgrade", "upgraded", "outperform", "buy rating", "raises",
    "raised", "strong", "growth", "jumps", "gains", "tops", "exceeds",
    "bullish", "breakout", "wins", "award", "expands", "partnership",
]
NEGATIVE = [
    "miss", "misses", "plunge", "plunges", "sink", "sinks", "fall", "falls",
    "drop", "drops", "downgrade", "downgraded", "underperform", "sell rating",
    "cuts", "cut", "weak", "lawsuit", "probe", "investigation", "recall",
    "bearish", "warning", "warns", "layoffs", "slump", "tumbles", "fraud",
    # "ARM's 93X Earnings Multiple OVERSHADOWS Its Growth Potential" (26/08/2026)
    # saiu como POSITIVA: "growth" pontuava e o verbo que inverte a frase nao
    # estava em lista nenhuma. E' o unico termo que esta auditoria acrescenta
    # -- lista de palavra-chave cresce sem fim, e cada entrada nova e' uma
    # chance de virar o rotulo do lado errado.
    "overshadow", "overshadows",
    # MRVL, 27/08/2026. "Marvell Shares SLIDE After Hours" saiu como MISTO por
    # empate 0-0: nenhuma das duas listas conhecia o verbo. Nao e' categoria
    # nova -- e' buraco numa que ja' existe: fall/drop/sink/slump/tumble/plunge
    # ja' estao aqui, e slide/slip sao o mesmo movimento de preco. Fechar a
    # lacuna de uma familia representada e' diferente de inventar uma familia,
    # que e' o crescimento contra o qual o comentario acima adverte.
    #
    # O efeito nao foi so' de rotulo dessa manchete: com ela em MISTO, o
    # denominador do score ficou em 3 (2 positivas x 1 negativa) e a tela
    # publicou "alta forte CONFIRMADA por fluxo de noticias positivo" com
    # tres das quatro manchetes exibidas falando de queda. Reconhecida, o
    # placar vira 2x2 e o rotulo cai para "misto".
    "slide", "slides", "slip", "slips", "slipped",
    # AOSL, 28/08/2026 (apontado em auditoria externa). "Alpha and Omega
    # Semiconductor (AOSL) Reports Q4 Loss, Beats Revenue Estimates" saiu
    # 1x0 POSITIVA: "beats" pontuava, e "loss" -- o substantivo do MESMO
    # resultado que "miss/misses" já cobre (não bater a expectativa) --
    # não pontuava nada. Mesmo padrão do slide/slip acima: família já
    # representada na lista, só faltava o substantivo. Reconhecida, o
    # placar dessa manchete vira 1x1 (mista), não 1x0.
    "loss", "losses",
]

# Casamento por PALAVRA INTEIRA, não por substring.
#
# `"gains" in title` era substring: "against" contém "gains" (a-G-A-I-N-S-t), o
# que dava ponto POSITIVO para toda manchete com "bet against", "case against",
# "lawsuit against". "stops" contém "tops"; "commission"/"mission"/"permission"
# contêm "miss"; "praised" contém "raised". Numa cobertura de infraestrutura de
# IA, "mission-critical" aparece o tempo todo.
#
# O erro nem sempre virava rótulo errado -- muitas vezes empatava p e n, e o
# empate faz a manchete ser DESCARTADA (nem positiva nem negativa). Ou seja,
# ele apagava notícias em silêncio, que é pior que classificá-las mal: some do
# `analisadas` × `positivas`/`negativas` sem deixar rastro.
#
# Efeito colateral bem-vindo: singular e plural na lista ("beat"/"beats",
# "surge"/"surges") contavam DOIS pontos para a mesma palavra no texto. Com
# \b cada ocorrência casa só a entrada exata, e o `set` mantém a contagem por
# termo distinto, como era a intenção original.
# ── Manchete com RESSALVA não é endosso ─────────────────────────────────────
#
# "Arm Holdings (ARM) Delivers Strong Growth, BUT Has the Valuation Run Too
# Far?" (26/08/2026) saiu como POSITIVA: "strong" e "growth" pontuaram, e a
# metade que importa -- a que vem depois do "but", e o proprio ponto de
# interrogacao -- nao pesava nada.
#
# A saida NAO e' contar como negativa: a manchete nao afirma queda, ela
# PERGUNTA. Vira uma terceira categoria, "misto", que fica de fora do score e
# aparece na contagem. Deixar de fora sem aparecer seria repetir o defeito que
# o comentario do casamento por palavra inteira ja' descreve: apagar noticia
# em silencio e' pior que classifica-la mal.
_RESSALVA = re.compile(
    r"\b(?:but|however|yet|though|although|despite|amid|concerns?|"
    r"questions?|doubts?)\b|\?\s*$", re.IGNORECASE)

_POSITIVE_RE = re.compile(r"\b(?:" + "|".join(re.escape(w) for w in POSITIVE) + r")\b")
_NEGATIVE_RE = re.compile(r"\b(?:" + "|".join(re.escape(w) for w in NEGATIVE) + r")\b")

# ── Tradução das manchetes (agent/traducao.py) ───────────────────────────────
# Só as headlines destacadas (ao usuário) são traduzidas; a classificação de
# sentimento usa o título original em inglês.
#
# Este arquivo tinha a SUA PRÓPRIA cópia da chamada ao endpoint gratuito do
# Google, dentro de `except: pass`. Quando esse endpoint passou a devolver 429
# (bloqueio de tráfego automatizado), o conserto de 25/08/2026 criou
# agent/traducao.py -- cache em disco -> Google -> LLM da cadeia -- e migrou
# get_news_feed.py. As cópias daqui e de get_market_alerts_snapshot.py ficaram
# para trás e seguiram devolvendo inglês em silêncio, porque copiar o padrão é
# fácil e migrar todas as cópias exige procurar por elas.
def _translate_join(texts: list[str]) -> list[str]:
    if not texts:
        return texts
    try:
        from traducao import traduzir
    except ImportError:
        from agent.traducao import traduzir
    traduzidos, _origens = traduzir(list(texts))
    return traduzidos

# Quantas manchetes precisam ter sido CLASSIFICADAS para o rotulo poder
# afirmar direcao.
#
# Incidente real (NVDA, 26/08/2026). O painel dizia "Noticias (positivo)" e a
# tela concluia "alta forte CONFIRMADA por fluxo de noticias positivo" -- em
# cima de UMA manchete. O texto era honesto sobre isso ("tom positivo em 1 de
# 8 manchetes analisadas") e mesmo assim o rotulo saiu com confianca maxima,
# porque o score divide por (positivas + negativas), nao pelas ANALISADAS:
#
#     score = (1 - 0) / (1 + 0) = 1,00  ->  "positivo"
#
# Uma manchete em oito pesava igual a oito em oito. E a correcao de
# relevancia (fala_do_papel) AUMENTA a chance disso: descartada e ambigua
# saem do denominador, entao amostras de uma ou duas ficam mais comuns. Uma
# defesa que cria o proximo buraco precisa fechar os dois.
#
# Tres e' o menor numero em que "maioria" quer dizer alguma coisa: com duas
# manchetes concordando o score ja' da' 1,00, e uma delas virar do outro lado
# zera tudo. E' julgamento, nao teorema -- por isso esta' nomeado aqui e nao
# enterrado numa comparacao.
MINIMO_PARA_ROTULAR = 3


def _sem_contrarias(news: dict, campo: str, singular: str, plural: str) -> str:
    """"sem notícias X" só quando REALMENTE não há nenhuma.

    A frase base era fixa, e a ressalva vinha depois entre parênteses -- o que
    produzia uma sentença que se contradiz dentro de si mesma:

        "sem notícias contrárias (amostra pequena: 1 notícia(s) na direção
         contrária, ainda sem confirmar)"

    Um auditor externo leu isso no MRVL (29/08) como contradição do painel, e
    tinha razão sobre a frase, ainda que a ressalva já trouxesse o número: a
    primeira metade nega o que a segunda admite. Quem lê só o começo sai com a
    informação errada.

    Agora a frase base muda de forma conforme o placar: zero contrárias diz
    "sem"; alguma contrária abaixo do mínimo diz "nenhuma CONFIRMADA", que é o
    que o sistema de fato sabe.
    """
    # As duas formas vêm EXPLÍCITAS. Derivar o plural com "%ss" produziu
    # "favorávels": em português, palavra terminada em -l faz -eis, e uma
    # regra ingênua acerta "contrárias" e erra "favoráveis" -- exatamente o
    # tipo de esperteza que passa no caso que a gente testou e falha no outro.
    return ("sem notícias %s" % plural if not (news.get(campo) or 0)
            else "nenhuma notícia %s confirmada" % singular)


def _amostra_insuficiente_nota(news: dict, campo: str, direcao: str) -> str:
    """Ressalva pra quando "sem notícias contrárias/favoráveis" lê como
    contradição do próprio placar ao lado.

    `news_dir` vira 0 (o gatilho de "sem notícias X") sempre que a amostra
    é menor que MINIMO_PARA_ROTULAR, mesmo com `positivas`/`negativas` > 0
    -- é o mesmo motivo do `label` ficar "neutro" com 1 manchete favorável
    sozinha (ver news_sentiment). Sem esta nota, "sinal: venda -- técnico
    de baixa forte sem notícias favoráveis" ao lado de um placar mostrando
    "1+/0-/2~" lê como o sistema negando um dado que ele mesmo mostrou.

    Visto em produção (AOSL, 28/08/2026, apontado em auditoria externa):
    exatamente esse caso -- 1 notícia positiva, amostra pequena demais pra
    confirmar rótulo, frase soando como se não houvesse nenhuma."""
    contagem = news.get(campo) or 0
    if contagem > 0 and (news.get("classificadas") or 0) < MINIMO_PARA_ROTULAR:
        return (f" (amostra pequena: {contagem} notícia(s) na direção "
                f"{direcao}, ainda sem confirmar)")
    return ""

# ── Veto de balanço: sinal direcional na véspera ────────────────────────────
#
# A tela de Previsao de Vol ja' avisa, no mesmo papel e no mesmo dia:
#
#     "Balanco em 0 dia(s) (2026-08-26) -- na vespera de earnings a previsao
#      certa vem do threshold da Reacao a Earnings, nao da banda de vol."
#
# A Analise Rapida de NVDA, com o balanco saindo depois do fechamento DAQUELE
# dia, recomendou COMPRA -- "tecnico de alta forte sem noticias contrarias".
# O aviso existia numa tela e a vizinha nasceu sem ele. E' o quinto caso do
# mesmo padrao em 26/08/2026.
#
# O tecnico nao esta errado; ele so' nao sabe do evento. Um papel com score 60
# na vespera de um resultado que ainda vai sair nao e' "compra", e' "compra
# se o numero vier bom" -- e isso o modelo nao tem como saber. O sinal vira
# `aguardar` e diz por que, em vez de sumir com a leitura tecnica.
#
# DOIS pregoes, mesma janela do veto de catalisador do veredito
# (`EARNINGS_PROXIMO_DIAS` em veredito_validator.py). Duplicado aqui porque
# este arquivo roda por spawn e nao importa o pacote de validacao; ha' teste
# fixando que os dois numeros nao divergem, mesmo padrao de _rvol_signal.
EARNINGS_VETO_DIAS = 2


def _reacao_do_ultimo_balanco_pendente(ticker: str, hist) -> dict | None:
    """O balanço mais recente já divulgado ainda não teve sua sessão de
    reação fechada?

    `_earnings_proximo` só enxerga o calendário FUTURO do yfinance -- que
    vira de trimestre assim que a empresa reporta. No exato momento em que a
    proteção mais importa (o resultado saiu, mas o mercado ainda não
    precificou a reação), o calendário já esqueceu do balanço.

    Visto em produção (MRVL, 28/08/2026): balanço AMC em 27/08, a reação é o
    pregão de 28/08 e ainda não tinha fechado -- e o sinal saiu "compra" sem
    menção ao balanço, porque `_earnings_proximo` já apontava para o
    trimestre seguinte (meses à frente, fora de EARNINGS_VETO_DIAS).

    A sessão da reação vem da MESMA regra de `_janela_da_reacao`
    (earnings_reaction_analysis.py): BMO reage no PRÓPRIO pregão do anúncio,
    AMC reage no pregão SEGUINTE. `hist` -- já carregado por `for_ticker`
    para os indicadores técnicos -- faz as vezes de calendário de pregões,
    sem precisar de biblioteca de feriados: o primeiro pregão da série
    depois do dia do anúncio já É o próximo pregão real.
    """
    if hist is None or hist.empty:
        return None
    t = ticker.upper()
    if t.startswith("^") or t in _SEM_EARNINGS:
        return None
    try:
        earnings, _fonte, _erro = _earnings_dates.buscar(
            t, lambda: yf.Ticker(t).get_earnings_dates(limit=6), limit=6)
        if earnings is None or earnings.empty:
            return None
        agora_ny = datetime.datetime.now(_NY_TZ)
        agora = pd.Timestamp.now(tz=earnings.index.tz) if earnings.index.tz \
            else pd.Timestamp.now()
        passados = earnings[earnings.index < agora]
        if passados.empty:
            return None
        ts = passados.index.max()
        janela, _inferida = _janela_da_reacao(ts)
        data_evento = ts.date()

        if janela == "anuncio":
            sessao_pendente = data_evento
        else:
            posteriores = [d for d in hist.index if d.date() > data_evento]
            if not posteriores:
                # balanço tão recente que o próprio pregão seguinte ainda não
                # apareceu na série -- claramente pendente.
                return {"data": str(data_evento), "tipo": "reacao_pendente"}
            sessao_pendente = min(posteriores).date()

        if _sessao_de_hoje_ainda_em_curso(sessao_pendente, agora_ny):
            return {"data": str(data_evento), "tipo": "reacao_pendente"}
        return None
    except Exception:  # noqa: BLE001 — calendário indisponível não pode derrubar o sinal
        return None


def balanco_que_veta(sinal: str, ticker: str, hist=None) -> dict | None:
    """O balanço que veta este sinal direcional, ou None.

    A consulta ao calendário só acontece quando HÁ sinal para vetar: sem essa
    guarda seria uma chamada de rede por ticker para mudar nada, no caminho
    que existe para ser rápido.
    """
    if sinal not in ("compra", "venda"):
        return None
    proximo = _earnings_proximo(ticker)
    if proximo and proximo.get("dias") is not None \
            and proximo["dias"] <= EARNINGS_VETO_DIAS:
        return proximo
    return _reacao_do_ultimo_balanco_pendente(ticker, hist)


def news_sentiment(ticker: str, max_items: int = 8) -> dict:
    try:
        news = yf.Ticker(ticker).news or []
    except Exception:
        news = []
    pos = neg = ambiguas = descartadas = 0
    scored = []
    for item in news[:max_items]:
        content = item.get("content", {}) if isinstance(item.get("content"), dict) else {}
        raw_title = str(content.get("title", item.get("title", "")) or "")
        title = raw_title.lower()
        if not title:
            continue
        # Manchete de OUTRA empresa nao e' sentimento desta. Ver
        # `fala_do_papel` em news_sources.py: o feed do Yahoo para ARM trouxe
        # duas materias da AMD, e elas mudaram o sinal da tela para AGUARDAR.
        if not fala_do_papel(raw_title, ticker):
            descartadas += 1
            continue
        # Timestamp de publicação (ms) — usado pelos marcadores no gráfico de velas
        ts = None
        pub = content.get("pubDate") or item.get("pubDate") or item.get("providerPublishTime")
        try:
            if isinstance(pub, (int, float)):  # epoch em segundos
                ts = int(pub) * 1000
            elif isinstance(pub, str) and pub:  # ISO 8601, ex: 2026-07-02T14:30:00Z
                ts = int(pd.Timestamp(pub).timestamp() * 1000)
        except Exception:
            ts = None
        p = len(set(_POSITIVE_RE.findall(title)))
        n = len(set(_NEGATIVE_RE.findall(title)))
        if _RESSALVA.search(raw_title):
            ambiguas += 1
            scored.append({"title": raw_title[:120], "tone": "misto", "ts": ts})
        elif p > n:
            pos += 1
            scored.append({"title": raw_title[:120], "tone": "positivo", "ts": ts})
        elif n > p:
            neg += 1
            scored.append({"title": raw_title[:120], "tone": "negativo", "ts": ts})
        else:
            # Empate entre positivas e negativas. Ate' aqui esta manchete
            # sumia sem deixar rastro -- o comentario do casamento por palavra
            # inteira, logo acima, ja' dizia que apagar noticia em silencio e'
            # pior que classifica-la mal, e o `continue` implicito fazia
            # exatamente isso. Agora ela conta e aparece.
            ambiguas += 1
            scored.append({"title": raw_title[:120], "tone": "misto", "ts": ts})
    total = pos + neg
    score = round((pos - neg) / total, 2) if total else 0.0
    if total < MINIMO_PARA_ROTULAR:
        # Amostra pequena demais para afirmar direcao. "neutro" faz
        # `news_dir` virar 0 la' embaixo, e o texto de confluencia passa a
        # dizer "sem confirmacao nem divergencia" -- que e' a verdade.
        label = "neutro"
    else:
        label = "positivo" if score > 0.25 else "negativo" if score < -0.25 else "misto"
    # Traduz só as headlines exibidas ao usuário (destaques), pt-BR
    destaques = scored[:4]
    if destaques:
        translated = _translate_join([d["title"] for d in destaques])
        for d, tr in zip(destaques, translated):
            d["title"] = tr
    return {"label": label, "score": score, "positivas": pos, "negativas": neg,
            # `ambiguas` e `descartadas` sao contagem VISIVEL de proposito: um
            # filtro que roda calado devolve "0 negativas" sem dizer que jogou
            # metade do feed fora.
            "ambiguas": ambiguas, "descartadas": descartadas,
            # `classificadas` e' o denominador do score. Sem ele na tela, "1 de
            # 8 analisadas" e "8 de 8" ficam indistinguiveis no rotulo.
            "classificadas": total, "minimoParaRotular": MINIMO_PARA_ROTULAR,
            "analisadas": len(news[:max_items]), "destaques": destaques}

# ── Estrutura de preço: topos/fundos via pivôs simples ───────────────────────
def price_structure(close: pd.Series, lookback: int = 60, window: int = 3) -> str:
    """Detecta HH/HL (alta), LH/LL (baixa) ou indefinida nos últimos `lookback` pregões."""
    s = close.iloc[-lookback:].reset_index(drop=True)
    highs, lows = [], []
    for i in range(window, len(s) - window):
        seg = s.iloc[i - window:i + window + 1]
        if s.iloc[i] == seg.max():
            highs.append(float(s.iloc[i]))
        if s.iloc[i] == seg.min():
            lows.append(float(s.iloc[i]))
    if len(highs) >= 2 and len(lows) >= 2:
        hh = highs[-1] > highs[-2]
        hl = lows[-1] > lows[-2]
        lh = highs[-1] < highs[-2]
        ll = lows[-1] < lows[-2]
        if hh and hl:
            return "alta"       # topos e fundos ascendentes
        if lh and ll:
            return "baixa"      # topos e fundos descendentes
    # Fallback: pivôs insuficientes/ambíguos → inclinação normalizada do período
    first_third = float(s.iloc[: len(s) // 3].mean())
    last_third = float(s.iloc[-(len(s) // 3):].mean())
    if first_third > 0:
        chg = (last_third - first_third) / first_third
        if chg > 0.04:
            return "alta"
        if chg < -0.04:
            return "baixa"
    return "indefinida"

# ── Cruzamento de médias: NÍVEL e DIREÇÃO juntos ────────────────────────────
#
# "SMA20 acima ou abaixo da SMA50" é uma leitura ATRASADA quando tomada
# sozinha. Depois de uma queda forte seguida de recuperação em V, a SMA20 fica
# abaixo da SMA50 por semanas enquanto o preço já subiu muito -- o
# "cruzamento de baixa" descreve o tombo passado, não a tendência atual.
#
# Visto em produção (NBIS, ago/2026): o papel caiu 48% em julho e recuperou 87%
# em 12 pregões. Com o preço 21,7% ACIMA da SMA50 e as duas médias subindo, o
# componente ainda marcava "baixa" e tirava 25 dos 100 pontos do score --
# levando 65 para 40 e rebaixando "alta forte" para "alta". A análise com IA
# repetia isso como "divergência interna que vale monitorar", quando era
# defasagem mecânica de um evento já superado.
#
# A correção não é ignorar o cruzamento: é exigir que nível e direção
# concordem. Quando discordam (nível diz baixa, inclinação diz alta), o
# honesto é pontuar ZERO -- não há informação de tendência ali, nem pra um
# lado nem pro outro. O mesmo vale do outro lado: um cruzamento de alta se
# desfazendo também deixa de valer +25. Tratar só o caso de baixa embutiria
# viés altista permanente no score.
#
# Duplicado em backtest.py::_classificar_cruzamento, que roda por spawn e não
# importa do pacote -- test_backtest_confluencia.py amarra as duas cópias.
CRUZAMENTO_JANELA = 5  # pregões para medir inclinação e fechamento do gap


def classificar_cruzamento(sma20, sma50, sma20_antes, sma50_antes):
    """(estado, nota, pontos) do componente SMA20 × SMA50.

    `*_antes` são os valores de CRUZAMENTO_JANELA pregões atrás. Sem eles
    (None/NaN, histórico curto), cai no comportamento antigo de dois estados.
    """
    acima = sma20 > sma50
    tem_antes = (
        sma20_antes is not None and sma50_antes is not None
        and sma20_antes == sma20_antes and sma50_antes == sma50_antes  # descarta NaN
        and sma50_antes != 0
    )
    if not tem_antes:
        return ("alta" if acima else "baixa", None, 25 if acima else -25)

    gap = (sma20 - sma50) / sma50
    gap_antes = (sma20_antes - sma50_antes) / sma50_antes
    sobe20 = sma20 > sma20_antes

    if acima:
        # Gap positivo encolhendo com a MM20 caindo: a alta está se desfazendo.
        if not sobe20 and gap < gap_antes:
            return ("alta",
                    "cruzamento de alta ENFRAQUECENDO — MM20 caindo e encostando "
                    "na MM50; nível e direção discordam",
                    0)
        return ("alta", None, 25)

    # Gap negativo encolhendo (indo em direção a zero) com a MM20 subindo:
    # o cruzamento é resíduo de uma queda anterior, não sinal atual.
    if sobe20 and gap > gap_antes:
        return ("baixa",
                "cruzamento de baixa EM REVERSÃO — MM20 abaixo da MM50 mas subindo "
                "e fechando a distância; defasagem da queda anterior, não "
                "confirmação de baixa",
                0)
    return ("baixa", None, -25)


# ── RSI de Wilder (igual metodologia já usada no projeto) ────────────────────
def rsi_wilder(close: pd.Series, period: int = 14) -> float | None:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean().iloc[-1]
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean().iloc[-1]
    if pd.isna(avg_gain) or pd.isna(avg_loss):
        return None
    if avg_loss == 0:  # sem perdas no período → RSI máximo (evita divisão por zero)
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return round(float(100 - 100 / (1 + rs)), 2)

def for_ticker(ticker: str) -> dict:
    try:
        ticker = sanitize_ticker(ticker)
    except ValueError as e:
        return {"ticker": str(ticker), "error": str(e)}
    try:
        # permitir_externa=False: a série é AJUSTADA e a fonte externa é "as
        # traded" -- um split dentro do ano viraria degrau de preço, e
        # estrutura/médias/MACD sairiam com um salto que nunca existiu. O
        # cache vencido continua valendo: foi gravado do yfinance, ajustado.
        resultado = market_data_provider.get_daily_history(
            ticker, "1y", auto_adjust=True, permitir_externa=False
        )
        if not resultado.ok:
            return {"ticker": ticker, "error": "Dados insuficientes"}
        hist = resultado.df
        if hist.empty or len(hist) < 60:
            return {"ticker": ticker, "error": "Dados insuficientes"}
        if hasattr(hist.columns, "levels"):
            hist.columns = hist.columns.get_level_values(0)
        close = hist["Close"].dropna()
        price = float(close.iloc[-1])

        sma20_serie = close.rolling(20).mean()
        sma50_serie = close.rolling(50).mean()
        sma20 = float(sma20_serie.iloc[-1])
        sma50 = float(sma50_serie.iloc[-1])
        sma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None
        # Valores de CRUZAMENTO_JANELA pregões atrás, para medir a direção das
        # médias (ver classificar_cruzamento). None quando o histórico não alcança.
        if len(sma20_serie) > CRUZAMENTO_JANELA:
            sma20_antes = float(sma20_serie.iloc[-1 - CRUZAMENTO_JANELA])
            sma50_antes = float(sma50_serie.iloc[-1 - CRUZAMENTO_JANELA])
        else:
            sma20_antes = sma50_antes = None

        ema12 = close.ewm(span=12).mean()
        ema26 = close.ewm(span=26).mean()
        macd_hist = float((ema12 - ema26 - (ema12 - ema26).ewm(span=9).mean()).iloc[-1])

        rsi = rsi_wilder(close)
        structure = price_structure(close)

        # ── Pontuação técnica: -100 (baixa forte) a +100 (alta forte) ────────
        score = 0
        comp = {}

        cruz_estado, cruz_nota, cruz_pontos = classificar_cruzamento(
            sma20, sma50, sma20_antes, sma50_antes
        )
        comp["maCruzamento"] = cruz_estado
        if cruz_nota:
            comp["maCruzamentoNota"] = cruz_nota
        score += cruz_pontos

        if sma200 is not None:
            comp["precoVsSma200"] = "acima" if price > sma200 else "abaixo"
            score += 20 if price > sma200 else -20
        else:
            comp["precoVsSma200"] = None

        comp["estrutura"] = structure
        score += 30 if structure == "alta" else -30 if structure == "baixa" else 0

        comp["macd"] = "bullish" if macd_hist > 0 else "bearish"
        score += 15 if macd_hist > 0 else -15

        comp["rsi"] = rsi
        if rsi is not None:
            if rsi > 70:
                comp["rsiNota"] = "sobrecomprado — tendência de alta pode estar esticada"
                score -= 5
            elif rsi < 30:
                comp["rsiNota"] = "sobrevendido — tendência de baixa pode estar esticada"
                score += 5
            else:
                comp["rsiNota"] = "neutro"

        trend = ("alta forte" if score >= 60 else "alta" if score >= 25
                 else "baixa forte" if score <= -60 else "baixa" if score <= -25
                 else "lateral")

        # ── Notícias como modificador de confluência ─────────────────────────
        news = news_sentiment(ticker)
        tech_dir = 1 if score >= 25 else -1 if score <= -25 else 0
        news_dir = 1 if news["label"] == "positivo" else -1 if news["label"] == "negativo" else 0

        if tech_dir == 0:
            confluence = "sem tendência técnica definida"
        elif news_dir == 0:
            confluence = f"{trend} — notícias neutras/mistas (sem confirmação nem divergência)"
        elif tech_dir == news_dir:
            confluence = f"{trend} CONFIRMADA por fluxo de notícias {news['label']}"
        else:
            confluence = f"{trend} com DIVERGÊNCIA — notícias {news['label']} contradizem o técnico (cautela)"

        # ── Sinal objetivo (regras transparentes; ferramenta, não recomendação) ──
        # compra:   técnico de alta forte (>=60) sem notícias contra, ou alta (>=25) confirmada por notícias
        # venda:    espelho para baixa
        # aguardar: lateral, sinais fracos ou divergência técnico × notícias
        if score >= 60 and news_dir >= 0:
            sinal, sinal_motivo = (
                "compra",
                "técnico de alta forte "
                + _sem_contrarias(news, "negativas", "contrária", "contrárias")
                + _amostra_insuficiente_nota(news, "negativas", "contrária"))
        elif score >= 25 and news_dir > 0:
            sinal, sinal_motivo = "compra", "técnico de alta confirmado por notícias positivas"
        elif score <= -60 and news_dir <= 0:
            sinal, sinal_motivo = (
                "venda",
                "técnico de baixa forte "
                + _sem_contrarias(news, "positivas", "favorável", "favoráveis")
                + _amostra_insuficiente_nota(news, "positivas", "favorável"))
        elif score <= -25 and news_dir < 0:
            sinal, sinal_motivo = "venda", "técnico de baixa confirmado por notícias negativas"
        else:
            sinal, sinal_motivo = "aguardar", ("divergência técnico × notícias" if tech_dir != 0 and news_dir != 0 and tech_dir != news_dir else "sinais insuficientes")

        # ── Veto de balanço ──────────────────────────────────────────────────
        balanco = balanco_que_veta(sinal, ticker, hist)
        if balanco:
            tecnico_dizia = sinal
            sinal = "aguardar"
            if balanco.get("tipo") == "reacao_pendente":
                sinal_motivo = (
                    f"balanço de {balanco['data']} ainda não teve a reação "
                    f"precificada — o técnico sozinho dizia {tecnico_dizia}")
            else:
                sinal_motivo = (
                    f"balanço em {balanco['dias']} pregão(ões) ({balanco['data']}) "
                    f"— a reação ainda não ocorreu; o técnico sozinho dizia "
                    f"{tecnico_dizia}")

        saida = {
            "ticker": ticker,
            "price": round(price, 2),
            # Até que sessão este painel alcança. Ver `_DEFASAGEM_*` em
            # analise_rapida_ia.py para o incidente que pediu o campo.
            "dadosAte": str(close.index[-1].date()),
            "trend": trend,
            "score": score,
            "components": comp,
            "news": news,
            "confluence": confluence,
            "sinal": sinal,
            "sinalMotivo": sinal_motivo,
            # Presente só quando o veto ATUOU. A tela usa para explicar que o
            # "aguardar" veio do calendário, não de sinal fraco.
            "vetoDeBalanco": balanco,
        }
        # Reaproveita o campo `stale` que o módulo já emite no stale-if-error
        # do __main__, em vez de inventar um segundo vocabulário de
        # degradação: quem consome já sabe tratá-lo.
        #
        # Sem isto a cadeia seria uma PIORA de honestidade: hoje um resultado
        # velho vem marcado; calculado sobre série vencida ele viria fresco e
        # sem marca -- e um sinal de compra em cima do fechamento de ontem,
        # sem aviso, é exatamente o que não pode acontecer.
        if resultado.is_stale or resultado.source not in ("yfinance", "yfinance_cache"):
            saida["stale"] = True
            saida["fonteHistorico"] = resultado.source
        return saida
    except Exception as e:
        print(f"[get_trend] {ticker}: {e}", file=sys.stderr)
        return {"ticker": ticker, "error": friendly_error(e)}

def com_cache(tickers: list) -> list:
    """`for_ticker` para vários tickers, passando pelo cache em disco.

    Extraído do `__main__` para que o analise_rapida_ia possa coletar o painel
    de Tendência NO MESMO PROCESSO sem furar o cache. Chamar `for_ticker`
    direto funcionaria, mas gastaria uma ida ao Yahoo por análise -- e o
    rate limit do Yahoo sobre o IP do servidor é o motivo de este cache
    existir.
    """
    cache = _cache_load()
    now = time.time()
    items = []
    dirty = False
    for t in tickers:
        key = f"trend:{str(t).upper()}"
        entry = cache.get(key)
        # 1) Cache fresco → usa direto, sem tocar no Yahoo.
        #    "Fresco" = dentro do TTL E do mesmo lado da abertura do pregão:
        #    uma entrada gravada no pré-mercado não vale depois que o pregão
        #    começou, por mais nova que seja (ver _cruzou_abertura).
        if entry and (now - entry[0]) < _TTL_SECONDS and not _cruzou_abertura(entry[0], now):
            items.append(entry[1])
            continue
        # 2) Busca ao vivo
        result = for_ticker(t)
        if "error" not in result:
            cache[key] = [now, result]
            dirty = True
            items.append(result)
        elif entry:
            # 3) Stale-if-error: Yahoo falhou (ex: rate limit) mas há resultado
            #    antigo → serve o antigo marcado como stale, melhor que erro.
            stale = dict(entry[1])
            stale["stale"] = True
            stale["staleAgeSeconds"] = int(now - entry[0])
            items.append(stale)
        else:
            items.append(result)
    if dirty:
        _cache_save(cache)
    return items


if __name__ == "__main__":
    args = json.loads(sys.stdin.read())
    print(json_seguro.dumps({"items": com_cache(args.get("tickers", []))},
                            ensure_ascii=False))
