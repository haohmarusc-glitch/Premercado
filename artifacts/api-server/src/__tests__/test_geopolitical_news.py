"""
Testes de tools.py::get_geopolitical_news — ferramenta que cobre falas/
decisões de chefes de estado (tarifas, comércio), guerra, petróleo, Big
Techs, controle de exportação de semicondutores, juros do Fed e carvão
metalúrgico. Cada tema combina um proxy de mercado amplo no Yahoo
(^GSPC, ^NDX, CL=F, SOXX) com uma busca temática no Google News -- sem
precisar de API paga de rede social (X/Twitter exige plano pago desde 2023
pra busca).

Aqui as fontes ficam restritas a "yahoo" (ver fixture) pra o teste não
depender de rede; RSS/merge/dedupe são testados em test_news_sources.py.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_geopolitical_news.py -v
(conftest.py no mesmo diretório já cuida do sys.path)
"""

import pytest

from agent import cache as cache_module
from agent import config as config_module
from agent import tools


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    # _macro_source é cacheada (@cached) -- sem desligar isso, o valor
    # mockado de um teste vazaria pro próximo via o dict _mem compartilhado
    # no processo (mesma chave origem:tema:max_items).
    monkeypatch.setattr(cache_module.config, "CACHE_ENABLED", False)
    # Só Yahoo: as outras fontes fariam requisição HTTP de verdade.
    monkeypatch.setattr(config_module, "NEWS_SOURCES", ["yahoo"])


class _FakeTicker:
    def __init__(self, news_items):
        self.news = news_items


def _raw_item(title, summary, published, provider):
    return {
        "content": {
            "title": title,
            "summary": summary,
            "pubDate": published,
            "provider": {"displayName": provider},
        }
    }


# Temas que têm proxy no Yahoo (os únicos com resultado quando só o Yahoo
# está ativo) e temas que só existem via busca temática no Google News.
_PROXY_LABELS = {"mercado_amplo_eua", "big_techs", "petroleo_wti", "semicondutores"}
_QUERY_ONLY_LABELS = {"juros_fed", "carvao_metalurgico"}


def test_get_geopolitical_news_covers_all_topics(monkeypatch):
    fake_news = [_raw_item("Título", "Resumo da notícia.", "2026-07-18T12:00:00Z", "Reuters")]
    monkeypatch.setattr(tools.yf, "Ticker", lambda symbol: _FakeTicker(fake_news))

    result = tools.get_geopolitical_news()

    assert set(result.keys()) == _PROXY_LABELS | _QUERY_ONLY_LABELS
    for label in _PROXY_LABELS:
        assert result[label][0]["title"] == "Título"
        assert result[label][0]["source"] == "Reuters"
        assert result[label][0]["origin"] == "yahoo"
    for label in _QUERY_ONLY_LABELS:
        # Sem proxy no Yahoo e com o RSS desligado: vazio não é erro.
        assert result[label] == []


def test_get_geopolitical_news_respects_max_items(monkeypatch):
    fake_news = [
        _raw_item(f"Título {i}", "x", f"2026-07-18T12:0{i}:00Z", "Reuters") for i in range(9)
    ]
    monkeypatch.setattr(tools.yf, "Ticker", lambda symbol: _FakeTicker(fake_news))

    result = tools.get_geopolitical_news(max_items=2)

    for label in _PROXY_LABELS:
        assert len(result[label]) == 2


def test_get_geopolitical_news_handles_fetch_error(monkeypatch):
    def _raise(symbol):
        raise RuntimeError("network down")
    monkeypatch.setattr(tools.yf, "Ticker", _raise)

    result = tools.get_geopolitical_news()

    for label in _PROXY_LABELS:
        assert result[label] == [{"error": "network down"}]


def test_parse_news_items_truncates_long_summary():
    long_summary = "a" * 500
    raw = [_raw_item("T", long_summary, "2026-07-18T12:00:00Z", "AP")]

    parsed = tools._parse_news_items(raw, max_items=6)

    assert len(parsed) == 1
    assert parsed[0]["summary"].endswith("...")
    assert len(parsed[0]["summary"]) == config_module.NEWS_SUMMARY_CHARS + 3


def test_parse_news_items_falls_back_to_legacy_shape():
    """Formato antigo do yfinance (sem 'content' aninhado) também deve funcionar."""
    raw = [{"title": "T", "summary": "S", "providerPublishTime": 1753012800}]

    parsed = tools._parse_news_items(raw, max_items=6)

    assert parsed[0]["title"] == "T"
    assert parsed[0]["summary"] == "S"
    # published é normalizado pra ISO/UTC em TODAS as fontes -- sem isso não
    # dá pra ordenar manchete do yfinance junto com a de RSS/FMP/Finnhub.
    assert parsed[0]["published"] == "2025-07-20T12:00:00Z"
    assert parsed[0]["source"] == ""
