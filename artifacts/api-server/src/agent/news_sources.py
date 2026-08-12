"""
news_sources.py — camada de COLETA de notícias por trás de get_news/
get_geopolitical_news (tools.py).

Por que existe: até aqui as manchetes vinham só de `yfinance.Ticker.news`.
Isso tem dois furos conhecidos em produção -- (1) ticker de menor cobertura
(ALAB, CRDO, SKHY, HCC, AMR) frequentemente volta lista VAZIA, e o ativo
entra no relatório sem nenhum catalisador de notícia; (2) quando o Yahoo cai
ou é rate-limitado, a ferramenta inteira zera pra TODOS os tickers de uma vez.

Desenho (deliberado): isto é uma camada de coleta, NÃO ferramentas novas pro
LLM. O agente continua enxergando `get_news` e `get_geopolitical_news` e nada
mais -- uma tool por provedor multiplicaria turnos (o custo real do loop) sem
adicionar informação, e o modelo não tem como escolher fonte melhor que o
Python já filtrado.

Princípios:
- Fail-open por fonte: fonte fora do ar/sem chave devolve lista vazia e a
  ferramenta segue com o que as outras trouxeram. Só devolve erro quando
  NENHUMA fonte produziu manchete E pelo menos uma falhou de verdade.
- Cache por (fonte, alvo): uma fonte lenta/quebrada não invalida o cache das
  outras, e resultado de erro nunca é cacheado (ver cache.py).
- Orçamento de tempo: as fontes de um alvo são buscadas em paralelo com teto
  total (config.NEWS_FETCH_BUDGET_S), sempre abaixo do TOOL_TIMEOUT_SECONDS
  do lado do agent loop.
- Formato canônico único {title, published, summary, source, origin} pra
  qualquer fonte, com dedupe entre elas antes de chegar no prompt.
"""

import datetime
import html
import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError, as_completed
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

import yfinance as yf

from . import config
from .cache import cached
from .http_retry import SESSION
from .security import mask_sensitive_data, sanitize_for_llm, sanitize_url

# Teto de bytes aceito de um feed RSS antes de tentar parsear. O
# xml.etree.ElementTree da stdlib não expande entidade EXTERNA (então não dá
# SSRF/leitura de arquivo por aí), mas é sensível a blowup quadrático de
# entidade interna -- limitar o tamanho da resposta fecha o vetor sem
# precisar de dependência nova (defusedxml) só pra isso. A alternativa
# feedparser também não foi adicionada: ElementTree resolve um feed RSS
# simples como o do Google News sem dependência extra no requirements.
_MAX_FEED_BYTES = 2 * 1024 * 1024

_GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
_FMP_STABLE_NEWS = "https://financialmodelingprep.com/stable/news/stock"
_FMP_LEGACY_NEWS = "https://financialmodelingprep.com/api/v3/stock_news"
_FINNHUB_COMPANY_NEWS = "https://finnhub.io/api/v1/company-news"
_ALPHA_VANTAGE_NEWS = "https://www.alphavantage.co/query"


# Recusas da FMP que são da CONTA, não da requisição: nenhuma espera resolve,
# e nenhum outro ticker vai se sair melhor. Cada uma pede uma ação diferente
# de quem opera, então a mensagem precisa distinguir -- "renove a chave" e
# "seu plano não cobre isso" levam a lugares opostos.
_FMP_RECUSAS = {
    401: "chave inválida ou expirada -- renove FMP_API_KEY",
    402: "plano não cobre este endpoint, ou o limite da conta estourou "
         "-- a chave é válida, o acesso é que não vem junto",
    403: "sem acesso a este endpoint neste plano",
}


class _FmpSemAcesso(RuntimeError):
    """Recusa de conta da FMP (401/402/403), não falha da requisição.

    Categoria à parte porque é PERMANENTE dentro da run. Herda de RuntimeError
    pra continuar sendo pega pelos `except Exception` que já existem no caminho
    da API legada, onde 402/403 é resposta esperada e não deve desligar nada.

    O 402 entrou depois dos outros dois, e por um motivo que vale registrar: a
    primeira versão só tratava 401/403, então quando a chave foi renovada em
    03/08 e a conta passou a responder 402, a fonte voltou a queimar uma
    chamada condenada por ticker -- o mesmo desperdício, por outra porta.
    """


# Desliga a FMP pelo resto do processo depois de uma recusa de conta na API
# stable. Cada run do agente é um processo novo, então isso zera sozinho -- uma
# chave renovada (ou um plano atualizado) volta a valer na execução seguinte
# sem precisar de restart.
#
# O lock existe porque as fontes de cada ticker são buscadas EM PARALELO
# (ThreadPoolExecutor mais abaixo): `if not flag: ... flag = True` é
# check-then-act, e várias threads passam pelo teste antes de a primeira
# gravar. Visto em produção 03/08 -- o aviso de desativação saiu duas vezes
# na mesma run, ou seja, duas chamadas condenadas em vez de uma. A garantia
# que interessa não é o log limpo: é "no máximo uma chamada condenada por
# execução", que era o ponto de existir a flag.
_FMP_SEM_ACESSO = False
_FMP_LOCK = threading.Lock()


def _desligar_fmp(motivo: str) -> None:
    """Marca a FMP como indisponível e avisa UMA vez, mesmo sob concorrência."""
    global _FMP_SEM_ACESSO
    with _FMP_LOCK:
        if _FMP_SEM_ACESSO:
            return
        _FMP_SEM_ACESSO = True
    print(
        f"[news_sources] FMP desativada nesta execução: {motivo} na API stable.",
        file=sys.stderr, flush=True,
    )


_HTTP_TIMEOUT = 10

# Nome da empresa pra montar a query do Google News: buscar só "ALAB" traz
# lixo (é sigla de várias coisas), enquanto "Astera Labs" traz a notícia de
# verdade. Só os tickers sob cobertura precisam estar aqui -- qualquer outro
# cai no fallback '"TICKER" stock', que funciona bem pra ticker conhecido.
# Não vale resolver isso via yf.Ticker().info: é uma chamada de rede lenta a
# mais por ticker justamente no caminho que existe pra ser rápido.
_COMPANY_NAMES = {
    "NVDA": "Nvidia",
    "SMCI": "Super Micro Computer",
    "MU": "Micron Technology",
    "INTC": "Intel",
    "GOOGL": "Alphabet Google",
    "ARM": "Arm Holdings",
    "TSLA": "Tesla",
    "SNDK": "SanDisk",
    "WDC": "Western Digital",
    "ALAB": "Astera Labs",
    "CRDO": "Credo Technology",
    "ANET": "Arista Networks",
    "VRT": "Vertiv",
    "TSM": "TSMC Taiwan Semiconductor",
    "ASML": "ASML",
    "HCC": "Warrior Met Coal",
    "AMR": "Alpha Metallurgical Resources",
    "AVGO": "Broadcom",
    "MRVL": "Marvell Technology",
    "SKHY": "SK Hynix",
}


# ── Normalização ──────────────────────────────────────────────────────────────


def _clean_text(value) -> str:
    """Texto de feed vem com entidade HTML, tag solta e quebra de linha —
    normaliza tudo pra uma linha só antes de qualquer truncagem."""
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_published(value) -> float | None:
    """Converte o campo de data de QUALQUER fonte em epoch (segundos).

    Cada fonte usa um formato: yfinance manda ISO 8601 (`pubDate`) no formato
    novo e epoch (`providerPublishTime`) no antigo, RSS manda RFC 2822
    ("Fri, 01 Aug 2026 12:00:00 GMT"), Finnhub manda epoch e a FMP manda
    "YYYY-MM-DD HH:MM:SS". Sem isso não dá pra ordenar/desempatar entre
    fontes diferentes. Devolve None quando não dá pra interpretar (o item
    continua válido, só vai pro fim da ordenação).
    """
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value).strip()
    if re.fullmatch(r"\d{9,13}", text):  # epoch em segundos ou milissegundos
        num = float(text)
        return num / 1000.0 if len(text) > 10 else num
    iso = text.replace("Z", "+00:00")
    for candidate in (iso, iso.replace(" ", "T", 1)):
        try:
            dt = datetime.datetime.fromisoformat(candidate)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            return dt.timestamp()
        except ValueError:
            pass
    try:
        dt = parsedate_to_datetime(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.timestamp()
    except (TypeError, ValueError):
        return None


def _iso_utc(epoch: float | None, fallback) -> str:
    if epoch is None:
        return _clean_text(fallback)
    return (
        datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def _item(title, published, summary, source, origin) -> dict:
    """Monta o formato canônico. `title`/`summary` passam por sanitize_for_llm
    porque manchete é texto de terceiro entrando direto no prompt (o vetor de
    prompt injection mais óbvio que a ferramenta tem)."""
    epoch = _parse_published(published)
    text = _clean_text(summary)
    limit = config.NEWS_SUMMARY_CHARS
    if len(text) > limit:
        text = text[:limit] + "..."
    return {
        "title": sanitize_for_llm(_clean_text(title)),
        "published": _iso_utc(epoch, published),
        "summary": sanitize_for_llm(text),
        "source": _clean_text(source),
        "origin": origin,
        "_ts": epoch,  # interno: ordenação/desempate; removido antes de sair
    }


_DUP_THRESHOLD = 0.7
_CONTAINMENT_THRESHOLD = 0.9

_STOPWORDS = frozenset(
    "a an and as at by for from in of on or the to with is are be s us its it"
    .split()
)


def _title_tokens(title: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", (title or "").lower())
    return {w for w in words if w not in _STOPWORDS}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _same_story(a: set[str], b: set[str]) -> bool:
    """Decide se dois títulos são a MESMA notícia vinda de fontes diferentes.

    Jaccard sozinho não basta: o caso mais comum na prática é um título ser o
    outro mais um pedaço ("Nvidia beats Q3 estimates" vs "Nvidia beats Q3
    estimates, shares jump"), que dá 4/6 = 0.67 e passaria batido pelo corte
    de 0.7. Por isso a segunda regra: um título CONTIDO no outro (containment
    >= 0.9) também conta como duplicata -- exigindo pelo menos 4 palavras
    úteis, senão título curto genérico ("Nvidia stock rises") engoliria
    notícia diferente com as mesmas poucas palavras.
    """
    if not a or not b:
        return False
    if _jaccard(a, b) >= _DUP_THRESHOLD:
        return True
    smaller = min(len(a), len(b))
    return smaller >= 4 and len(a & b) / smaller >= _CONTAINMENT_THRESHOLD


# ── Fontes ────────────────────────────────────────────────────────────────────


def parse_yahoo_items(news: list, max_items: int) -> list[dict]:
    """Normaliza a lista bruta de `Ticker.news` do yfinance. Aceita as DUAS
    formas conhecidas: a nova (campos dentro de `content`) e a legada (campos
    na raiz do item, com `providerPublishTime` em epoch)."""
    result = []
    for raw in news[:max_items]:
        content = raw.get("content") if isinstance(raw.get("content"), dict) else {}
        provider = content.get("provider")
        result.append(
            _item(
                title=content.get("title", raw.get("title", "")),
                published=content.get("pubDate", raw.get("providerPublishTime", "")),
                summary=content.get("summary", raw.get("summary", "")) or "",
                source=provider.get("displayName", "") if isinstance(provider, dict) else "",
                origin="yahoo",
            )
        )
        # url fica de fora do payload de propósito — não é usada na análise e
        # só consome token de input.
    return result


def _fetch_yahoo(symbol: str, max_items: int) -> list[dict]:
    return parse_yahoo_items(yf.Ticker(symbol).news or [], max_items)


def _fetch_google_rss(query: str, max_items: int) -> list[dict]:
    """Google News RSS — sem chave, cobre ticker/tema que o Yahoo não cobre.

    O resumo não é aproveitado: no feed do Google ele é só um bloco de HTML
    repetindo o título e o nome do veículo, então guardá-lo gastaria token de
    input sem acrescentar nada ao que o título já diz.
    """
    url = sanitize_url(_GOOGLE_NEWS_RSS)
    resp = SESSION.get(
        url,
        params={"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"},
        timeout=_HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    body = resp.content or b""
    if len(body) > _MAX_FEED_BYTES:
        raise ValueError(f"feed RSS grande demais ({len(body)} bytes)")

    root = ElementTree.fromstring(body)
    items = []
    for node in root.iterfind("./channel/item"):
        if len(items) >= max_items:
            break
        title = _clean_text(node.findtext("title"))
        source = _clean_text(node.findtext("source"))
        # O Google sufixa o veículo no título ("Nvidia beats — Reuters");
        # com o veículo já no campo `source`, o sufixo é ruído duplicado.
        if source and title.endswith(f" - {source}"):
            title = title[: -len(f" - {source}")].strip()
        if not title:
            continue
        items.append(
            _item(
                title=title,
                published=node.findtext("pubDate"),
                summary="",
                source=source,
                origin="google_rss",
            )
        )
    return items


def _fetch_fmp(symbol: str, max_items: int) -> list[dict]:
    """FMP stock news. Sem FMP_API_KEY a fonte simplesmente não participa.

    Tenta a API "stable" primeiro e só então a legada (/api/v3): a legada foi
    descontinuada pra contas novas em 31/08/2025 (mesma armadilha já
    documentada em tools.py::get_valuation_metrics), mas continua respondendo
    pra quem já era assinante antes disso — então a ordem inversa faria a
    maioria das contas gastar um round-trip inútil antes de acertar.
    """
    api_key = os.environ.get("FMP_API_KEY", "").strip()
    if not api_key or _FMP_SEM_ACESSO:
        return []

    def _rows(url, params):
        resp = SESSION.get(url, params=params, timeout=_HTTP_TIMEOUT)
        # Recusa de conta é permanente: nenhuma espera conserta e nenhum
        # outro ticker vai se sair melhor. Separar aqui é o que permite
        # desistir da fonte em vez de repetir a mesma falha por ticker.
        codigo = getattr(resp, "status_code", 0)
        if codigo in _FMP_RECUSAS:
            raise _FmpSemAcesso(f"HTTP {codigo} ({_FMP_RECUSAS[codigo]})")
        resp.raise_for_status()
        body = resp.json()
        return body if isinstance(body, list) else []

    # A falha da chamada PRINCIPAL não pode ser engolida.
    #
    # Antes, o except da stable fazia `rows = []` em silêncio e só a legada
    # podia levantar -- então o único erro que chegava ao log era o da fonte
    # que já se espera falhar. Em produção 03/08 isso apareceu como
    # "403 Forbidden" em /api/v3/stock_news para os 9 tickers, que lido de
    # fora parece chave morta; mas 403 na legada é o comportamento NORMAL de
    # conta posterior a 31/08/2025 (ver docstring). O que realmente aconteceu
    # com a stable -- 401? 429 de cota? plano sem notícias? zero itens? --
    # não foi registrado em lugar nenhum, e era a única informação útil.
    rows = []
    falha_stable = None
    try:
        rows = _rows(
            _FMP_STABLE_NEWS,
            {"symbols": symbol, "limit": max_items, "apikey": api_key},
        )
    except _FmpSemAcesso as e:
        # A chave não serve. Desliga a fonte pelo resto do processo em vez de
        # repetir a mesma falha em cada ticker: em 03/08 foram 9 chamadas
        # condenadas por run, dentro de um orçamento de notícias de 10s que
        # estourou e derrubou 6 pares (ticker, fonte) que teriam funcionado --
        # ou seja, a chave morta não tirava só a FMP, tirava notícia boa junto.
        _desligar_fmp(str(e))
        return []
    except Exception as e:
        falha_stable = f"{type(e).__name__}: {e}"
        rows = []

    if not rows:
        motivo_stable = falha_stable or "respondeu sem itens"
        try:
            rows = _rows(
                _FMP_LEGACY_NEWS,
                {"tickers": symbol, "limit": max_items, "apikey": api_key},
            )
        except Exception as e:
            # As DUAS pontas na mesma linha: sem isso o diagnóstico aponta
            # para a fonte errada. O mascaramento da chave é aplicado por
            # quem loga (ver o print de [news_sources] mais abaixo).
            raise RuntimeError(
                f"stable {motivo_stable} | legada {type(e).__name__}: {e}"
            ) from e
        if falha_stable:
            # Legada salvou a jogada, mas a principal está quebrada e isso
            # precisa aparecer -- senão só se descobre quando a legada morrer.
            print(
                f"[news_sources] fmp/{symbol}: stable falhou "
                f"({mask_sensitive_data(motivo_stable)}), servido pela legada",
                file=sys.stderr, flush=True,
            )

    return [
        _item(
            title=row.get("title", ""),
            published=row.get("publishedDate", ""),
            summary=row.get("text", ""),
            source=row.get("publisher") or row.get("site") or "FMP",
            origin="fmp",
        )
        for row in rows[:max_items]
        if isinstance(row, dict)
    ]


def _fetch_finnhub(symbol: str, max_items: int) -> list[dict]:
    """Finnhub company-news. Sem FINNHUB_API_KEY a fonte não participa."""
    token = os.environ.get("FINNHUB_API_KEY", "").strip()
    if not token:
        return []
    today = datetime.datetime.now(datetime.timezone.utc).date()
    resp = SESSION.get(
        _FINNHUB_COMPANY_NEWS,
        params={
            "symbol": symbol,
            "from": (today - datetime.timedelta(days=3)).isoformat(),
            "to": today.isoformat(),
            "token": token,
        },
        timeout=_HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    rows = resp.json()
    if not isinstance(rows, list):
        return []
    return [
        _item(
            title=row.get("headline", ""),
            published=row.get("datetime", ""),
            summary=row.get("summary", ""),
            source=row.get("source", "Finnhub"),
            origin="finnhub",
        )
        for row in rows[:max_items]
        if isinstance(row, dict)
    ]


_AV_TIME_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})$")


def _av_time_to_iso(raw: str) -> str:
    """Alpha Vantage manda `time_published` sem separador (\"20260812T142919\"),
    formato que _parse_published (usado por _item) não reconhece -- sem isto o
    item entra na mescla sem data e afunda pro fim da ordenação por data."""
    m = _AV_TIME_RE.match(str(raw or ""))
    if not m:
        return raw
    y, mo, d, h, mi, s = m.groups()
    return f"{y}-{mo}-{d}T{h}:{mi}:{s}Z"


# Tópico-âncora único pra chamada consolidada -- "financial_markets" é o mais
# amplo do catálogo da Alpha Vantage, cobrindo o feed geral de mercado.
# Testado em produção (12/08/2026): `topics=A,B,C` na Alpha Vantage filtra
# por INTERSEÇÃO (artigo precisa carregar TODOS os tópicos ao mesmo tempo),
# não união como a docstring original desta função assumia -- com os 5
# tópicos dos nossos temas juntos isso devolvia só 3 artigos genéricos o
# bastante pra carregar os 5 rótulos simultaneamente, a maioria de anos
# atrás (um de 2022), nenhuma notícia de verdade. Um tópico só devolve o
# feed geral, e cada artigo carrega SEUS PRÓPRIOS tópicos (geralmente
# vários) -- a distribuição pros nossos temas usa esses tópicos individuais
# depois (ver _alpha_vantage_for_labels), não uma busca mais restrita.
_ALPHA_VANTAGE_ANCHOR_TOPIC = "financial_markets"


@cached("news_av_raw", ttl=1800)
def _fetch_alpha_vantage_raw() -> list[dict]:
    """UMA chamada só (tópico-âncora amplo, ver _ALPHA_VANTAGE_ANCHOR_TOPIC)
    -- cacheada 30min, mesmo TTL de _macro_source. Existe separada do fluxo
    por (label, origin) de propósito: a Alpha Vantage tem cota diária baixa
    no plano gratuito/básico (histórico: 25 chamadas/dia, compartilhada com
    QUALQUER outro uso da mesma chave), e um round-trip POR TEMA (6 temas)
    gastaria quase um quarto da cota numa execução só.

    Cada item volta com a lista de tópicos ORIGINAL da Alpha Vantage anexada
    (chave "topics"), porque é ela quem decide a quais dos NOSSOS temas o
    item pertence -- um único artigo pode ter vários tópicos ao mesmo tempo
    (ex.: petróleo + macro), então "pertence a um tema só" seria descartar
    informação real da fonte. A distribuição por tema acontece depois, em
    Python, sem chamada extra -- ver _alpha_vantage_for_labels.
    """
    api_key = os.environ.get("ALPHAVANTAGE_API_KEY", "").strip()
    if not api_key:
        return []
    try:
        resp = SESSION.get(
            _ALPHA_VANTAGE_NEWS,
            params={
                "function": "NEWS_SENTIMENT",
                "topics": _ALPHA_VANTAGE_ANCHOR_TOPIC,
                "limit": 200,
                "apikey": api_key,
            },
            timeout=_HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        body = resp.json()
        feed = body.get("feed") if isinstance(body, dict) else None
        if not isinstance(feed, list):
            # A Alpha Vantage devolve 200 OK mesmo em erro de chave/cota --
            # o corpo vira {"Information": "..."} / {"Note": "..."} em vez de
            # {"feed": [...]}, sem status HTTP pra pegar no except abaixo.
            msg = (
                body.get("Information") or body.get("Note") or body.get("Error Message")
                if isinstance(body, dict) else None
            )
            raise RuntimeError(msg or "resposta sem 'feed'")
    except Exception as e:
        print(f"[news_sources] alphavantage: {mask_sensitive_data(str(e))}", file=sys.stderr)
        return []

    return [
        {
            "item": _item(
                title=row.get("title", ""),
                published=_av_time_to_iso(row.get("time_published", "")),
                summary=row.get("summary", ""),
                source=row.get("source", "Alpha Vantage"),
                origin="alphavantage",
            ),
            "topics": [t.get("topic") for t in row.get("topics", []) if isinstance(t, dict)],
        }
        for row in feed
        if isinstance(row, dict)
    ]


def _alpha_vantage_for_labels(topics: dict, max_items: int) -> dict[str, list[dict]]:
    """Distribui o feed único da Alpha Vantage pelos NOSSOS temas (label),
    filtrando por qual tópico AV cada item carrega. Sem chamada de rede
    própria -- só reparte o que _fetch_alpha_vantage_raw já trouxe (e que já
    está cacheado por 30min, então isto é essencialmente grátis)."""
    raw = _fetch_alpha_vantage_raw()
    if not raw:
        return {}
    result: dict[str, list[dict]] = {}
    for label, topic in topics.items():
        av_topic = topic.get("av_topic")
        if not av_topic:
            continue
        result[label] = [entry["item"] for entry in raw if av_topic in entry["topics"]][:max_items]
    return result


# ── Merge / dedupe ────────────────────────────────────────────────────────────


def merge_news(by_origin: list[tuple[str, list[dict]]], max_items: int) -> list[dict]:
    """Junta as listas de várias fontes numa só: remove duplicata, ordena da
    mais recente pra mais antiga e corta em max_items.

    O critério de duplicata está em _same_story (a mesma notícia quase nunca
    chega com título idêntico em duas fontes, então comparação exata sozinha
    deixaria passar quase todas).
    Entre duplicatas fica a versão da fonte de maior prioridade (a ordem em
    que as fontes chegam aqui, que é a de config.NEWS_SOURCES); empate de
    prioridade decide pela mais recente. Isso mantém o resumo do Yahoo quando
    ele existe, em vez de trocá-lo por um título solto de RSS.
    """
    kept: list[tuple[int, dict, set[str]]] = []
    for priority, (_origin, items) in enumerate(by_origin):
        for item in items:
            if not isinstance(item, dict) or item.get("error") or not item.get("title"):
                continue
            tokens = _title_tokens(item["title"])
            duplicate_of = None
            for index, (_p, existing, existing_tokens) in enumerate(kept):
                if _same_story(tokens, existing_tokens):
                    duplicate_of = index
                    break
            if duplicate_of is None:
                kept.append((priority, item, tokens))
                continue
            kept_priority, kept_item, _ = kept[duplicate_of]
            if priority < kept_priority or (
                priority == kept_priority
                and (item.get("_ts") or 0) > (kept_item.get("_ts") or 0)
            ):
                kept[duplicate_of] = (priority, item, tokens)

    # A primeira chave da ordenação é "tem data?": item com data ilegível vai
    # pro fim da lista em vez de ser tratado como epoch 0 e afundar junto com
    # notícia de verdade antiga.
    ordered = sorted(
        kept,
        key=lambda k: (k[1].get("_ts") is not None, k[1].get("_ts") or 0),
        reverse=True,
    )
    result = []
    for _priority, item, _tokens in ordered[:max_items]:
        item = dict(item)
        item.pop("_ts", None)
        result.append(item)
    return result


# ── Orquestração ──────────────────────────────────────────────────────────────


def _gather(tasks: dict, budget_s: float) -> dict:
    """Roda as buscas em paralelo e devolve o que respondeu dentro do
    orçamento. Quem não terminou é abandonado (fail-open).

    Não usa bounded_parallel.bounded_parallel_map de propósito: aquele helper
    é pra script CLI de vida curta que termina com os._exit() logo depois (ele
    nunca dá shutdown no pool). Aqui quem chama é o agent loop, um processo
    longo com dezenas de chamadas de ferramenta -- pool sem shutdown vazaria
    thread a cada chamada e, pior, o atexit hook de concurrent.futures
    seguraria o processo no fim esperando thread presa (exatamente o problema
    que derrubou os 4 checkers de fundo). Por isso o shutdown(wait=False,
    cancel_futures=True) no finally: cancela o que ainda está na fila e libera
    as threads ociosas; a que estiver no meio de uma requisição morre sozinha
    no timeout do próprio requests (_HTTP_TIMEOUT), não no fim do processo.
    """
    if not tasks:
        return {}
    pool = ThreadPoolExecutor(max_workers=min(16, len(tasks)))
    try:
        futures = {pool.submit(fn): name for name, fn in tasks.items()}
        pending = set(futures)
        results: dict = {}
        try:
            for future in as_completed(futures, timeout=budget_s):
                pending.discard(future)
                results[futures[future]] = future.result()
        except FutureTimeoutError:
            print(
                f"[news_sources] orçamento de {budget_s}s esgotado, seguindo sem: "
                f"{[futures[f] for f in pending]}",
                file=sys.stderr,
            )
        return results
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


@cached("news_src:{0}:{1}:{2}", ttl=600)
def _source_for_symbol(origin: str, symbol: str, max_items: int) -> list[dict]:
    """Uma fonte, um ticker — cacheado separado por fonte pra que uma fonte
    fora do ar não derrube o cache das outras (erro nunca é cacheado)."""
    try:
        if origin == "yahoo":
            return _fetch_yahoo(symbol, max_items)
        if origin == "google_rss":
            name = _COMPANY_NAMES.get(symbol.upper())
            base = f'"{name}" OR "{symbol} stock"' if name else f'"{symbol}" stock'
            return _fetch_google_rss(f"{base} when:{config.NEWS_RSS_WINDOW}", max_items)
        if origin == "fmp":
            return _fetch_fmp(symbol, max_items)
        if origin == "finnhub":
            return _fetch_finnhub(symbol, max_items)
        return []
    except Exception as e:
        print(f"[news_sources] {origin}/{symbol}: {mask_sensitive_data(str(e))}", file=sys.stderr)
        return [{"error": str(e), "origin": origin}]


def _finalize(by_origin: list[tuple[str, list[dict]]], max_items: int) -> list[dict]:
    """Mescla o que as fontes de UM alvo trouxeram e decide o retorno de erro.

    Só devolve erro quando NENHUMA fonte trouxe manchete E pelo menos uma
    falhou -- fonte que respondeu vazia (ticker sem cobertura) não é erro, e
    lista vazia é resposta legítima. O formato [{"error": ...}] é o mesmo de
    antes, inclusive pro cache de tools.py, que não cacheia erro.
    """
    merged = merge_news(by_origin, max_items)
    if merged:
        return merged
    errors = [
        item["error"]
        for _origin, items in by_origin
        for item in items
        if isinstance(item, dict) and item.get("error")
    ]
    return [{"error": errors[0]}] if errors else []


_SOURCE_NAMES = ("yahoo", "google_rss", "fmp", "finnhub", "alphavantage")


def enabled_sources() -> list[str]:
    """Fontes ativas, NA ORDEM de config.NEWS_SOURCES (que também é a
    prioridade de desempate no dedupe). Nome desconhecido no env é ignorado
    em silêncio em vez de quebrar a ferramenta."""
    return [s for s in config.NEWS_SOURCES if s in _SOURCE_NAMES]


def headlines_for_tickers(symbols: list[str], max_items: int) -> dict[str, list[dict]]:
    """Manchetes de VÁRIOS tickers, já mescladas entre as fontes ativas.

    Busca TODOS os pares (ticker × fonte) de uma vez, sob um orçamento de
    tempo ÚNICO pra chamada inteira. Fazer um orçamento por ticker seria a
    armadilha óbvia aqui: com 8 tickers do Grupo A, 8 × NEWS_FETCH_BUDGET_S
    daria ~80s numa ferramenta que o agent loop mata em TOOL_TIMEOUT_SECONDS
    (15s) -- ou seja, o orçamento "de proteção" garantiria o estouro em vez
    de evitá-lo. Medido: com a rede bloqueada, o orçamento por alvo levava a
    chamada a 59s; com o orçamento único ela fecha dentro do teto.
    """
    origins = enabled_sources()
    tasks = {
        (symbol, origin): (lambda s=symbol, o=origin: _source_for_symbol(o, s, max_items))
        for symbol in symbols
        for origin in origins
    }
    fetched = _gather(tasks, config.NEWS_FETCH_BUDGET_S)
    return {
        symbol: _finalize(
            [(origin, fetched.get((symbol, origin), [])) for origin in origins], max_items
        )
        for symbol in symbols
    }


def headlines_for_ticker(symbol: str, max_items: int) -> list[dict]:
    """Manchetes de UM ticker (atalho sobre headlines_for_tickers)."""
    return headlines_for_tickers([symbol], max_items)[symbol]


@cached("news_macro:{0}:{1}:{2}", ttl=1800)
def _macro_source(origin: str, key: str, max_items: int) -> list[dict]:
    """Uma fonte, um tema macro. Cache mais longo (30min) que notícia de ação
    (10min) — manchete macro muda menos ao longo do dia."""
    try:
        if origin == "yahoo":
            return _fetch_yahoo(key, max_items)
        if origin == "google_rss":
            return _fetch_google_rss(f"{key} when:{config.NEWS_RSS_WINDOW}", max_items)
        return []
    except Exception as e:
        print(f"[news_sources] macro {origin}/{key}: {mask_sensitive_data(str(e))}", file=sys.stderr)
        return [{"error": str(e), "origin": origin}]


def headlines_for_macro_topics(topics: dict, max_items: int) -> dict[str, list[dict]]:
    """Manchetes por tema macro: proxy de mercado amplo no Yahoo (quando o
    tema tem um) + busca temática no Google News + Alpha Vantage (quando o
    tema tem `av_topic` e a fonte está habilitada), mescladas. Mesmo
    orçamento único de headlines_for_tickers, pelo mesmo motivo.

    `topics` = {label: {"proxy": ticker|None, "query": str, "av_topic": str|None}}.

    FMP/Finnhub não entram aqui: as duas são APIs por SÍMBOLO (company news),
    não têm busca por tema — usá-las exigiria escolher um ticker representativo,
    que é exatamente o que o proxy do Yahoo já faz.

    Alpha Vantage é buscada FORA do _gather paralelo de propósito: uma
    chamada por tema estouraria a cota diária baixa do plano gratuito/básico
    em uma execução só (ver _fetch_alpha_vantage_raw). Em vez disso é UMA
    chamada com tópico-âncora amplo, cacheada, e distribuída pelos temas em
    Python -- ver _alpha_vantage_for_labels.
    """
    origins = enabled_sources()
    keys: dict[tuple[str, str], str] = {}  # (label, origin) -> alvo da busca
    for label, topic in topics.items():
        if "yahoo" in origins and topic.get("proxy"):
            keys[(label, "yahoo")] = topic["proxy"]
        if "google_rss" in origins and topic.get("query"):
            keys[(label, "google_rss")] = topic["query"]

    tasks = {
        key: (lambda o=key[1], k=target: _macro_source(o, k, max_items))
        for key, target in keys.items()
    }
    fetched = _gather(tasks, config.NEWS_FETCH_BUDGET_S)

    av_by_label: dict[str, list[dict]] = {}
    if "alphavantage" in origins and any(t.get("av_topic") for t in topics.values()):
        av_by_label = _alpha_vantage_for_labels(topics, max_items)

    return {
        label: _finalize(
            [
                (origin, fetched.get((label, origin), []))
                for origin in origins
                if (label, origin) in keys
            ]
            + ([("alphavantage", av_by_label[label])] if label in av_by_label else []),
            max_items,
        )
        for label in topics
    }
