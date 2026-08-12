"""
Testes de news_sources.py — a camada de coleta de notícias por trás de
get_news/get_geopolitical_news (Yahoo + Google News RSS + FMP + Finnhub,
com dedupe entre fontes e fail-open por fonte).

O que estes testes travam, em ordem de importância:
1. fail-open — fonte quebrada não pode zerar a ferramenta (o motivo de a
   camada existir: quando o Yahoo caía, TODOS os tickers ficavam sem notícia);
2. dedupe — a mesma notícia chega por 2-3 fontes com títulos quase iguais, e
   sem isso o payload de input triplicaria dizendo a mesma coisa;
3. normalização de data — cada fonte usa um formato diferente (ISO, epoch,
   RFC 2822, "YYYY-MM-DD HH:MM:SS") e sem epoch comum não dá pra ordenar.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_news_sources.py -v
"""

import pytest

from agent import cache as cache_module
from agent import config as config_module
from agent import news_sources as ns


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch):
    monkeypatch.setattr(cache_module.config, "CACHE_ENABLED", False)


class _FakeResponse:
    def __init__(self, *, content=b"", payload=None, status=200):
        self.content = content
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _rss(items_xml: str) -> bytes:
    return f"<rss><channel>{items_xml}</channel></rss>".encode("utf-8")


_RSS_ITEM = """
<item>
  <title>Nvidia beats Q3 estimates - Reuters</title>
  <pubDate>Fri, 01 Aug 2025 12:00:00 GMT</pubDate>
  <source url="https://reuters.com">Reuters</source>
</item>
"""


# ── Normalização de data ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2025-08-01T12:00:00Z", "2025-08-01T12:00:00Z"),  # yfinance (novo)
        (1754049600, "2025-08-01T12:00:00Z"),  # yfinance (legado, epoch)
        ("Fri, 01 Aug 2025 12:00:00 GMT", "2025-08-01T12:00:00Z"),  # RSS
        ("2025-08-01 12:00:00", "2025-08-01T12:00:00Z"),  # FMP
        ("1754049600", "2025-08-01T12:00:00Z"),  # epoch como string (Finnhub)
    ],
)
def test_published_normalizado_para_iso_utc(raw, expected):
    assert ns._item("T", raw, "", "S", "yahoo")["published"] == expected


def test_published_ilegivel_nao_quebra_o_item():
    """Data que não dá pra interpretar mantém o texto original e vai pro fim
    da ordenação — perder a manchete inteira por causa da data seria pior."""
    item = ns._item("T", "ontem à tarde", "", "S", "yahoo")
    assert item["published"] == "ontem à tarde"
    assert item["_ts"] is None


# ── Google News RSS ───────────────────────────────────────────────────────────


def test_google_rss_parseia_item_e_remove_sufixo_do_veiculo(monkeypatch):
    monkeypatch.setattr(ns.SESSION, "get", lambda *a, **k: _FakeResponse(content=_rss(_RSS_ITEM)))

    items = ns._fetch_google_rss("nvidia", max_items=5)

    assert len(items) == 1
    # O Google repete o veículo no fim do título; com `source` preenchido o
    # sufixo é só ruído duplicado no prompt.
    assert items[0]["title"] == "Nvidia beats Q3 estimates"
    assert items[0]["source"] == "Reuters"
    assert items[0]["origin"] == "google_rss"
    assert items[0]["published"] == "2025-08-01T12:00:00Z"
    # Resumo do feed do Google é HTML repetindo o título — não é aproveitado.
    assert items[0]["summary"] == ""


def test_google_rss_respeita_max_items(monkeypatch):
    monkeypatch.setattr(
        ns.SESSION, "get", lambda *a, **k: _FakeResponse(content=_rss(_RSS_ITEM * 10))
    )
    assert len(ns._fetch_google_rss("nvidia", max_items=3)) == 3


def test_google_rss_recusa_feed_gigante(monkeypatch):
    """Guarda contra blowup de entidade no ElementTree: acima do teto de
    bytes o feed é recusado antes de ser parseado."""
    huge = b"<rss><channel>" + b"x" * (ns._MAX_FEED_BYTES + 1) + b"</channel></rss>"
    monkeypatch.setattr(ns.SESSION, "get", lambda *a, **k: _FakeResponse(content=huge))

    with pytest.raises(ValueError, match="grande demais"):
        ns._fetch_google_rss("nvidia", max_items=3)


def test_query_do_ticker_usa_nome_da_empresa(monkeypatch):
    """ALAB sozinho traz lixo (é sigla de várias coisas) — a query precisa
    levar o nome da empresa junto."""
    capturado = {}

    def _fake_get(url, params=None, timeout=None):
        capturado.update(params or {})
        return _FakeResponse(content=_rss(""))

    monkeypatch.setattr(ns.SESSION, "get", _fake_get)
    monkeypatch.setattr(config_module, "NEWS_SOURCES", ["google_rss"])

    ns.headlines_for_ticker("ALAB", 3)

    assert "Astera Labs" in capturado["q"]
    assert f"when:{config_module.NEWS_RSS_WINDOW}" in capturado["q"]


# ── FMP / Finnhub ─────────────────────────────────────────────────────────────


def test_fmp_sem_chave_nao_participa(monkeypatch):
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    assert ns._fetch_fmp("NVDA", 3) == []


def test_finnhub_sem_chave_nao_participa(monkeypatch):
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    assert ns._fetch_finnhub("NVDA", 3) == []


def test_fmp_cai_para_api_legada_quando_a_stable_vem_vazia(monkeypatch):
    """A /api/v3 foi descontinuada pra contas novas em 31/08/2025, mas ainda
    responde pra assinantes antigos — a stable é tentada primeiro e a legada
    é o fallback, nunca o contrário."""
    monkeypatch.setenv("FMP_API_KEY", "test-key")
    chamadas = []

    def _fake_get(url, params=None, timeout=None):
        chamadas.append(url)
        if url == ns._FMP_STABLE_NEWS:
            return _FakeResponse(payload=[])
        return _FakeResponse(
            payload=[
                {
                    "title": "Micron raises guidance",
                    "publishedDate": "2025-08-01 12:00:00",
                    "text": "Memory maker lifts outlook.",
                    "site": "benzinga.com",
                }
            ]
        )

    monkeypatch.setattr(ns.SESSION, "get", _fake_get)

    items = ns._fetch_fmp("MU", 3)

    assert chamadas == [ns._FMP_STABLE_NEWS, ns._FMP_LEGACY_NEWS]
    assert items[0]["title"] == "Micron raises guidance"
    assert items[0]["source"] == "benzinga.com"
    assert items[0]["origin"] == "fmp"


def test_finnhub_normaliza_campos_proprios(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "test-token")
    monkeypatch.setattr(
        ns.SESSION,
        "get",
        lambda *a, **k: _FakeResponse(
            payload=[
                {
                    "headline": "Arm signs licensing deal",
                    "summary": "Deal covers new cores.",
                    "source": "Bloomberg",
                    "datetime": 1754049600,
                }
            ]
        ),
    )

    items = ns._fetch_finnhub("ARM", 3)

    assert items[0]["title"] == "Arm signs licensing deal"
    assert items[0]["published"] == "2025-08-01T12:00:00Z"
    assert items[0]["origin"] == "finnhub"


# ── Alpha Vantage (get_geopolitical_news) ───────────────────────────────────


def test_alphavantage_sem_chave_nao_participa(monkeypatch):
    monkeypatch.delenv("ALPHAVANTAGE_API_KEY", raising=False)
    assert ns._fetch_alpha_vantage_raw("economy_monetary") == []


def test_alphavantage_time_published_sem_separador():
    """A Alpha Vantage manda time_published como \"YYYYMMDDTHHMMSS\", sem
    hífen/dois-pontos -- formato que _parse_published (usado por todo mundo
    via _item) não reconhece. Sem a conversão o item entra sem data e afunda
    pro fim do merge."""
    assert ns._av_time_to_iso("20260812T142919") == "2026-08-12T14:29:19Z"
    # fail-open: formato inesperado passa direto, não quebra
    assert ns._av_time_to_iso("lixo") == "lixo"


def test_alphavantage_uma_chamada_cobre_todos_os_topicos_pedidos(monkeypatch):
    """O ponto inteiro de existir _fetch_alpha_vantage_raw/_alpha_vantage_for_labels
    em vez de uma busca por (label, origin) como as outras fontes: a cota
    diária da Alpha Vantage no plano básico é baixa (histórico: 25/dia,
    compartilhada com qualquer outro uso da mesma chave) -- uma chamada por
    tema (6 temas) gastaria quase um quarto da cota numa execução só."""
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "test-key")
    chamadas = []

    def _fake_get(url, params=None, timeout=None):
        chamadas.append(params.get("topics"))
        return _FakeResponse(
            payload={
                "feed": [
                    {
                        "title": "Fed sinaliza corte de juros",
                        "time_published": "20260812T093000",
                        "summary": "FOMC indica postura mais dovish.",
                        "source": "Reuters",
                        "topics": [{"topic": "economy_monetary", "relevance_score": "0.9"}],
                    },
                    {
                        "title": "Controle de exportação de chips pressiona setor",
                        "time_published": "20260812T101500",
                        "summary": "Novas regras miram Taiwan/China.",
                        "source": "Bloomberg",
                        "topics": [
                            {"topic": "technology", "relevance_score": "0.8"},
                            {"topic": "economy_macro", "relevance_score": "0.5"},
                        ],
                    },
                ]
            }
        )

    monkeypatch.setattr(ns.SESSION, "get", _fake_get)

    topics = {
        "juros_fed": {"proxy": None, "query": "", "av_topic": "economy_monetary"},
        "big_techs": {"proxy": None, "query": "", "av_topic": "technology"},
        "semicondutores": {"proxy": None, "query": "", "av_topic": "technology"},
        "carvao_metalurgico": {"proxy": None, "query": "", "av_topic": "manufacturing"},
    }
    needed = {"economy_monetary", "technology", "manufacturing"}
    result = ns._alpha_vantage_for_labels(topics, needed, max_items=6)

    # UMA chamada só, mesmo com 3 tópicos distintos pedidos por 4 temas.
    assert len(chamadas) == 1
    assert set(chamadas[0].split(",")) == needed

    assert [i["title"] for i in result["juros_fed"]] == ["Fed sinaliza corte de juros"]
    # big_techs e semicondutores compartilham av_topic="technology" -- os
    # dois recebem o MESMO item, cada um enxergando seu próprio tema.
    assert result["big_techs"] == result["semicondutores"]
    assert len(result["big_techs"]) == 1
    # nenhum item tem o tópico "manufacturing" -- vazio, não erro.
    assert result["carvao_metalurgico"] == []


def test_alphavantage_erro_de_conta_nao_quebra_e_nao_levanta(monkeypatch):
    """A Alpha Vantage devolve 200 OK mesmo em erro de chave/cota -- o corpo
    vira {"Information": "..."} em vez de {"feed": [...]}, sem status HTTP
    de erro pra pegar. Precisa virar lista vazia (fail-open), não exceção
    propagada pro @cached (que não tem try/except em volta de fn())."""
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "test-key")
    monkeypatch.setattr(
        ns.SESSION,
        "get",
        lambda *a, **k: _FakeResponse(payload={"Information": "cota diaria esgotada"}),
    )

    assert ns._fetch_alpha_vantage_raw("economy_monetary") == []


# ── Merge / dedupe ────────────────────────────────────────────────────────────


def _mk(title, ts, origin, summary=""):
    return ns._item(title, ts, summary, origin, origin)


def test_merge_remove_quase_duplicata_entre_fontes():
    """Título quase igual (a mesma notícia com um pedaço a mais) conta como
    duplicata — comparação exata sozinha deixaria passar quase todas."""
    yahoo = [_mk("Nvidia beats Q3 estimates", 1754049600, "yahoo", "Resumo do Yahoo.")]
    rss = [_mk("Nvidia beats Q3 estimates, shares jump", 1754053200, "google_rss")]

    merged = ns.merge_news([("yahoo", yahoo), ("google_rss", rss)], max_items=6)

    assert len(merged) == 1
    # Fica a versão da fonte de MAIOR prioridade (a primeira da lista), mesmo
    # sendo a mais antiga: é a única que traz resumo.
    assert merged[0]["origin"] == "yahoo"
    assert merged[0]["summary"] == "Resumo do Yahoo."


def test_merge_mantem_noticias_diferentes_e_ordena_pela_mais_recente():
    yahoo = [_mk("Micron raises guidance", 1754049600, "yahoo")]
    rss = [_mk("Intel cuts foundry outlook", 1754136000, "google_rss")]

    merged = ns.merge_news([("yahoo", yahoo), ("google_rss", rss)], max_items=6)

    assert [m["title"] for m in merged] == [
        "Intel cuts foundry outlook",
        "Micron raises guidance",
    ]
    assert "_ts" not in merged[0]  # campo interno não vaza pro prompt


def test_merge_nao_funde_titulos_curtos_com_palavras_em_comum():
    """A regra de containment exige 4+ palavras úteis: sem isso, dois títulos
    curtos e genéricos sobre fatos diferentes virariam um só."""
    a = [_mk("Nvidia stock rises", 1754049600, "yahoo")]
    b = [_mk("Nvidia stock rises after Q3 beat and buyback plan", 1754053200, "google_rss")]

    merged = ns.merge_news([("yahoo", a), ("google_rss", b)], max_items=6)

    assert len(merged) == 2


def test_merge_descarta_itens_de_erro_e_respeita_o_cap():
    yahoo = [{"error": "network down", "origin": "yahoo"}]
    rss = [_mk(f"Manchete {i}", 1754049600 + i, "google_rss") for i in range(8)]

    merged = ns.merge_news([("yahoo", yahoo), ("google_rss", rss)], max_items=3)

    assert len(merged) == 3
    assert all("error" not in m for m in merged)


def test_merge_poe_item_sem_data_no_fim():
    com_data = [_mk("Com data", 1754049600, "yahoo")]
    sem_data = [_mk("Sem data", "", "google_rss")]

    merged = ns.merge_news([("yahoo", com_data), ("google_rss", sem_data)], max_items=6)

    assert [m["title"] for m in merged] == ["Com data", "Sem data"]


# ── Fail-open ─────────────────────────────────────────────────────────────────


def test_uma_fonte_quebrada_nao_zera_a_ferramenta(monkeypatch):
    """O motivo de a camada existir: com o Yahoo fora do ar, o ticker
    continua tendo manchete."""
    monkeypatch.setattr(config_module, "NEWS_SOURCES", ["yahoo", "google_rss"])

    def _yahoo_quebrado(symbol, max_items):
        raise RuntimeError("Yahoo rate limited")

    monkeypatch.setattr(ns, "_fetch_yahoo", _yahoo_quebrado)
    monkeypatch.setattr(ns.SESSION, "get", lambda *a, **k: _FakeResponse(content=_rss(_RSS_ITEM)))

    items = ns.headlines_for_ticker("NVDA", 5)

    assert [i["origin"] for i in items] == ["google_rss"]
    assert all("error" not in i for i in items)


def test_erro_so_aparece_quando_nenhuma_fonte_entregou_nada(monkeypatch):
    monkeypatch.setattr(config_module, "NEWS_SOURCES", ["yahoo"])

    def _quebrado(symbol, max_items):
        raise RuntimeError("network down")

    monkeypatch.setattr(ns, "_fetch_yahoo", _quebrado)

    assert ns.headlines_for_ticker("NVDA", 5) == [{"error": "network down"}]


def test_fonte_sem_cobertura_devolve_lista_vazia_sem_erro(monkeypatch):
    """Ticker que nenhuma fonte cobre é lista vazia, não erro — erro faria o
    agente relatar falha de ferramenta onde só não existe notícia."""
    monkeypatch.setattr(config_module, "NEWS_SOURCES", ["yahoo"])
    monkeypatch.setattr(ns, "_fetch_yahoo", lambda symbol, max_items: [])

    assert ns.headlines_for_ticker("SKHY", 5) == []


def test_nome_de_fonte_desconhecido_no_env_e_ignorado(monkeypatch):
    monkeypatch.setattr(config_module, "NEWS_SOURCES", ["yahoo", "twitter", "google_rss"])
    assert ns.enabled_sources() == ["yahoo", "google_rss"]


# ── Orçamento de tempo ────────────────────────────────────────────────────────


def test_chamada_inteira_divide_um_orcamento_so(monkeypatch):
    """Um orçamento POR TICKER garantiria o estouro em vez de evitá-lo: 8
    tickers × 10s = 80s numa ferramenta que o agent loop mata em 15s. Todos
    os pares (ticker × fonte) têm que ir num gather só."""
    monkeypatch.setattr(config_module, "NEWS_SOURCES", ["yahoo", "google_rss"])
    monkeypatch.setattr(ns, "_fetch_yahoo", lambda symbol, max_items: [])
    monkeypatch.setattr(ns.SESSION, "get", lambda *a, **k: _FakeResponse(content=_rss("")))

    gathers = []
    original = ns._gather

    def _spy(tasks, budget_s):
        gathers.append(sorted(tasks))
        return original(tasks, budget_s)

    monkeypatch.setattr(ns, "_gather", _spy)

    ns.headlines_for_tickers(["NVDA", "MU", "ALAB"], 3)

    assert len(gathers) == 1
    assert gathers[0] == [
        (ticker, origin)
        for ticker in ["ALAB", "MU", "NVDA"]
        for origin in ["google_rss", "yahoo"]
    ]


def test_temas_macro_tambem_dividem_um_orcamento_so(monkeypatch):
    monkeypatch.setattr(config_module, "NEWS_SOURCES", ["yahoo", "google_rss"])
    monkeypatch.setattr(ns, "_fetch_yahoo", lambda symbol, max_items: [])
    monkeypatch.setattr(ns.SESSION, "get", lambda *a, **k: _FakeResponse(content=_rss("")))

    gathers = []
    original = ns._gather

    def _spy(tasks, budget_s):
        gathers.append(set(tasks))
        return original(tasks, budget_s)

    monkeypatch.setattr(ns, "_gather", _spy)

    topics = {
        "com_proxy": {"proxy": "^GSPC", "query": "tariffs"},
        "so_query": {"proxy": None, "query": "coking coal"},
    }
    result = ns.headlines_for_macro_topics(topics, 3)

    assert set(result) == {"com_proxy", "so_query"}
    assert len(gathers) == 1
    # Tema sem proxy não gera tarefa no Yahoo (não existe índice pra ele).
    assert gathers[0] == {
        ("com_proxy", "yahoo"),
        ("com_proxy", "google_rss"),
        ("so_query", "google_rss"),
    }


# ── Contrato de get_news (tools.py) ───────────────────────────────────────────


def test_get_news_preserva_as_chaves_como_vieram(monkeypatch):
    """O agente usa o retorno direto como headlines_by_ticker de
    check_market_alerts, que cruza pelas MESMAS strings que ele passou."""
    from agent import tools

    monkeypatch.setattr(
        ns, "headlines_for_tickers", lambda symbols, max_items: {s: [] for s in symbols}
    )

    result = tools.get_news(["nvda", "MU"], 3)

    assert set(result) == {"nvda", "MU"}


def test_get_news_reporta_ticker_invalido_sem_derrubar_os_demais(monkeypatch):
    from agent import tools

    monkeypatch.setattr(
        ns,
        "headlines_for_tickers",
        lambda symbols, max_items: {s: [_mk("Manchete", 1754049600, "yahoo")] for s in symbols},
    )

    result = tools.get_news(["NVDA", "!!!"], 3)

    assert result["NVDA"][0]["title"] == "Manchete"
    assert "error" in result["!!!"][0]


# ── Diagnóstico da FMP ────────────────────────────────────────────────────────
#
# O except da chamada stable fazia `rows = []` em silêncio, e só a legada podia
# levantar exceção. Resultado: o único erro que chegava ao log vinha da fonte
# que já se ESPERA falhar.
#
# Em produção 03/08 isso apareceu como "403 Forbidden" em /api/v3/stock_news
# nos 9 tickers -- que lido de fora parece chave morta. Mas 403 na legada é o
# comportamento normal de conta posterior a 31/08/2025. O que aconteceu com a
# stable (401? 429 de cota? plano sem notícias?) não foi registrado em lugar
# nenhum, e era a única informação que importava.


def test_erro_da_stable_aparece_junto_com_o_da_legada(monkeypatch):
    """As duas falham, e o log precisa citar as DUAS.

    Stable em 500 (transitório) de propósito: 401/403 ali tem caminho próprio
    -- desliga a fonte e nem tenta a legada. O que sobra pra esta mensagem
    combinada é justamente a falha passageira da principal.
    """
    monkeypatch.setenv("FMP_API_KEY", "test-key")

    def _fake_get(url, params=None, timeout=None):
        if url == ns._FMP_STABLE_NEWS:
            return _FakeResponse(status=500)
        return _FakeResponse(status=403)

    monkeypatch.setattr(ns.SESSION, "get", _fake_get)

    with pytest.raises(Exception) as exc:
        ns._fetch_fmp("NVDA", 3)

    msg = str(exc.value)
    assert "stable" in msg and "500" in msg, "erro da chamada principal sumiu"
    assert "legada" in msg and "403" in msg


def test_stable_vazia_e_legada_falhando_diz_que_a_stable_veio_vazia(monkeypatch):
    """Distinguir "a stable ERROU" de "a stable respondeu sem itens" é o que
    separa chave quebrada de simplesmente não haver notícia."""
    monkeypatch.setenv("FMP_API_KEY", "test-key")

    def _fake_get(url, params=None, timeout=None):
        if url == ns._FMP_STABLE_NEWS:
            return _FakeResponse(payload=[])
        return _FakeResponse(status=403)

    monkeypatch.setattr(ns.SESSION, "get", _fake_get)

    with pytest.raises(Exception) as exc:
        ns._fetch_fmp("NVDA", 3)

    assert "sem itens" in str(exc.value)
    assert "401" not in str(exc.value)


def test_avisa_quando_a_legada_salva_uma_stable_quebrada(capsys, monkeypatch):
    """Sem esse aviso, a stable quebrada fica invisível até a legada morrer
    também -- e aí as notícias somem de uma vez, sem histórico do porquê."""
    monkeypatch.setenv("FMP_API_KEY", "test-key")

    def _fake_get(url, params=None, timeout=None):
        if url == ns._FMP_STABLE_NEWS:
            return _FakeResponse(status=429)
        return _FakeResponse(payload=[{
            "title": "Nvidia sobe",
            "publishedDate": "2026-08-03 12:00:00",
            "text": "resumo",
            "site": "reuters.com",
        }])

    monkeypatch.setattr(ns.SESSION, "get", _fake_get)

    items = ns._fetch_fmp("NVDA", 3)

    assert items[0]["title"] == "Nvidia sobe"  # o usuário ainda recebe a notícia
    err = capsys.readouterr().err
    assert "stable falhou" in err
    assert "429" in err


def test_caminho_feliz_nao_gera_ruido(capsys, monkeypatch):
    """Aviso que aparece sempre não é aviso."""
    monkeypatch.setenv("FMP_API_KEY", "test-key")
    monkeypatch.setattr(ns.SESSION, "get", lambda url, params=None, timeout=None: _FakeResponse(
        payload=[{"title": "ok", "publishedDate": "2026-08-03 12:00:00",
                  "text": "t", "site": "s"}]
    ))

    assert ns._fetch_fmp("NVDA", 3)[0]["title"] == "ok"
    assert capsys.readouterr().err == ""


# ── Chave morta: desistir da fonte em vez de repetir a falha ──────────────────
#
# Confirmado em 03/08: a API stable devolve 401 (chave inválida). Sem desligar
# a fonte, cada run gastava 9 chamadas condenadas -- dentro de um orçamento de
# notícias de 10s que estourou e derrubou 6 pares (ticker, fonte) que teriam
# funcionado. Ou seja, a chave morta não tirava só a FMP: tirava notícia boa
# junto, e isso é invisível no relatório final.


@pytest.fixture(autouse=True)
def _fmp_religada():
    """A flag é global do processo -- religa entre casos pra não vazar."""
    ns._FMP_SEM_ACESSO = False
    yield
    ns._FMP_SEM_ACESSO = False


def test_401_na_stable_desliga_a_fmp_para_os_tickers_seguintes(capsys, monkeypatch):
    monkeypatch.setenv("FMP_API_KEY", "chave-morta")
    chamadas = []

    def _fake_get(url, params=None, timeout=None):
        chamadas.append(url)
        return _FakeResponse(status=401)

    monkeypatch.setattr(ns.SESSION, "get", _fake_get)

    assert ns._fetch_fmp("NVDA", 3) == []
    # A legada nem chega a ser tentada: 401 na principal já resolve a questão.
    assert chamadas == [ns._FMP_STABLE_NEWS]

    for ticker in ("SMCI", "ARM", "MU"):
        assert ns._fetch_fmp(ticker, 3) == []
    assert len(chamadas) == 1, "ticker seguinte repetiu a chamada condenada"

    err = capsys.readouterr().err
    assert "FMP desativada nesta execução" in err
    assert err.count("FMP desativada") == 1, "aviso repetido por ticker"
    assert "renove FMP_API_KEY" in err, "aviso precisa dizer a ação a tomar"


def test_403_na_legada_nao_desliga_a_fonte(monkeypatch):
    """403 na legada é resposta ESPERADA de conta pós-31/08/2025 -- desligar
    a FMP por causa dela tiraria a fonte de quem está com tudo certo."""
    monkeypatch.setenv("FMP_API_KEY", "chave-boa")

    def _fake_get(url, params=None, timeout=None):
        if url == ns._FMP_STABLE_NEWS:
            return _FakeResponse(payload=[])
        return _FakeResponse(status=403)

    monkeypatch.setattr(ns.SESSION, "get", _fake_get)

    with pytest.raises(Exception):
        ns._fetch_fmp("NVDA", 3)
    assert ns._FMP_SEM_ACESSO is False


def test_erro_transitorio_na_stable_nao_desliga_a_fonte(monkeypatch):
    """500/timeout passa; só 401/403 é permanente."""
    monkeypatch.setenv("FMP_API_KEY", "chave-boa")

    def _fake_get(url, params=None, timeout=None):
        if url == ns._FMP_STABLE_NEWS:
            return _FakeResponse(status=500)
        return _FakeResponse(payload=[{"title": "ok", "publishedDate": "2026-08-03 12:00:00",
                                       "text": "t", "site": "s"}])

    monkeypatch.setattr(ns.SESSION, "get", _fake_get)

    assert ns._fetch_fmp("NVDA", 3)[0]["title"] == "ok"
    assert ns._FMP_SEM_ACESSO is False


def test_402_tambem_desliga_a_fonte(capsys, monkeypatch):
    """402 = chave VÁLIDA, plano sem acesso (ou limite estourado).

    Confirmado em produção 03/08: depois de renovar a chave morta, a conta
    passou a responder 402. A primeira versão desta proteção só olhava
    401/403, então a FMP voltou a queimar uma chamada condenada por ticker --
    o mesmo desperdício de antes, entrando por outra porta.
    """
    monkeypatch.setenv("FMP_API_KEY", "chave-valida-plano-limitado")
    chamadas = []

    def _fake_get(url, params=None, timeout=None):
        chamadas.append(url)
        return _FakeResponse(status=402)

    monkeypatch.setattr(ns.SESSION, "get", _fake_get)

    assert ns._fetch_fmp("NVDA", 3) == []
    for ticker in ("SMCI", "ARM", "MU", "AVGO"):
        assert ns._fetch_fmp(ticker, 3) == []
    assert len(chamadas) == 1, "ticker seguinte repetiu a chamada condenada"

    err = capsys.readouterr().err
    assert "402" in err
    # A ação de 402 é oposta à de 401: não adianta renovar a chave.
    assert "plano" in err
    assert "renove FMP_API_KEY" not in err


def test_a_mensagem_distingue_chave_de_plano(monkeypatch):
    """401 e 402 levam a lugares opostos -- trocar a chave não resolve 402, e
    mexer no plano não resolve 401. Uma mensagem só pros dois seria pior que
    nenhuma."""
    assert "renove FMP_API_KEY" in ns._FMP_RECUSAS[401]
    assert "plano" in ns._FMP_RECUSAS[402]
    assert "renove" not in ns._FMP_RECUSAS[402]


def test_desligamento_da_fmp_e_seguro_sob_concorrencia(capsys, monkeypatch):
    """As fontes de cada ticker são buscadas EM PARALELO, e o desligamento era
    check-then-act: várias threads passavam pelo teste antes de a primeira
    gravar a flag.

    Visto em produção 03/08 -- o aviso saiu DUAS vezes na mesma run, ou seja,
    duas chamadas condenadas em vez de uma. A garantia que interessa não é o
    log limpo: é "no máximo uma chamada condenada por execução", que era o
    ponto de existir a flag.
    """
    import threading
    from concurrent.futures import ThreadPoolExecutor

    monkeypatch.setenv("FMP_API_KEY", "chave-sem-plano")
    chamadas = []
    trava = threading.Lock()
    largada = threading.Event()

    def _fake_get(url, params=None, timeout=None):
        # Segura todas as threads no mesmo ponto pra maximizar a janela de
        # corrida -- sem isso o teste passaria por acaso de escalonamento.
        largada.wait(timeout=2)
        with trava:
            chamadas.append(url)
        return _FakeResponse(status=402)

    monkeypatch.setattr(ns.SESSION, "get", _fake_get)

    with ThreadPoolExecutor(max_workers=8) as pool:
        futuros = [pool.submit(ns._fetch_fmp, t, 3)
                   for t in ("NVDA", "SMCI", "ARM", "MU", "AVGO", "MRVL", "SKHY", "HCC")]
        largada.set()
        assert all(f.result() == [] for f in futuros)

    assert capsys.readouterr().err.count("FMP desativada") == 1, "aviso duplicado"
