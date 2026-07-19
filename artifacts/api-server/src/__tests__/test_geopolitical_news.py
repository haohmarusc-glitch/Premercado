"""
Testes de tools.py::get_geopolitical_news — nova ferramenta que cobre falas/
decisões de chefes de estado (tarifas, comércio), guerra, petróleo, Big
Techs e controle de exportação de semicondutores, usando proxies de mercado
amplo (^GSPC, ^NDX, CL=F, SOXX) via o mesmo mecanismo (Ticker.news) já usado
por get_news -- sem precisar de API paga de rede social (X/Twitter exige
plano pago desde 2023 pra busca).

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_geopolitical_news.py -v
(conftest.py no mesmo diretório já cuida do sys.path)
"""

import pytest

from agent import cache as cache_module
from agent import tools


@pytest.fixture(autouse=True)
def _disable_cache(monkeypatch):
    # _get_news_for_macro_proxy é cacheada (@cached) -- sem desligar isso,
    # o valor mockado de um teste vazaria pro próximo via o dict _mem
    # compartilhado no processo (mesma chave proxy_ticker:max_items).
    monkeypatch.setattr(cache_module.config, "CACHE_ENABLED", False)


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


def test_get_geopolitical_news_covers_all_proxies(monkeypatch):
    fake_news = [_raw_item("Título", "Resumo da notícia.", "2026-07-18T12:00:00Z", "Reuters")]
    monkeypatch.setattr(tools.yf, "Ticker", lambda symbol: _FakeTicker(fake_news))

    result = tools.get_geopolitical_news()

    assert set(result.keys()) == {"mercado_amplo_eua", "big_techs", "petroleo_wti", "semicondutores"}
    for items in result.values():
        assert items[0]["title"] == "Título"
        assert items[0]["source"] == "Reuters"


def test_get_geopolitical_news_respects_max_items(monkeypatch):
    fake_news = [_raw_item(f"Título {i}", "x", "2026-07-18T12:00:00Z", "Reuters") for i in range(10)]
    monkeypatch.setattr(tools.yf, "Ticker", lambda symbol: _FakeTicker(fake_news))

    result = tools.get_geopolitical_news(max_items=2)

    for items in result.values():
        assert len(items) == 2


def test_get_geopolitical_news_handles_fetch_error(monkeypatch):
    def _raise(symbol):
        raise RuntimeError("network down")
    monkeypatch.setattr(tools.yf, "Ticker", _raise)

    result = tools.get_geopolitical_news()

    for items in result.values():
        assert items == [{"error": "network down"}]


def test_parse_news_items_truncates_long_summary():
    long_summary = "a" * 500
    raw = [_raw_item("T", long_summary, "2026-07-18T12:00:00Z", "AP")]

    parsed = tools._parse_news_items(raw, max_items=6)

    assert len(parsed) == 1
    assert parsed[0]["summary"].endswith("...")
    assert len(parsed[0]["summary"]) == 283  # 280 chars + "..."


def test_parse_news_items_falls_back_to_legacy_shape():
    """Formato antigo do yfinance (sem 'content' aninhado) também deve funcionar."""
    raw = [{"title": "T", "summary": "S", "providerPublishTime": 123}]

    parsed = tools._parse_news_items(raw, max_items=6)

    assert parsed[0]["title"] == "T"
    assert parsed[0]["summary"] == "S"
    assert parsed[0]["published"] == 123
    assert parsed[0]["source"] == ""
