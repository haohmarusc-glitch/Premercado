"""
O resumo por camada da tradução das manchetes.

Contexto (26/08/2026): a tela de Notícias apareceu inteira em inglês e a
pergunta que veio foi "tradução não é mais possível?". Era: `get_news_feed.py`
nunca deixou de chamar `traduzir`. O que faltava era a tela DIZER quando as
camadas falham -- `portfolio.tsx` já mostrava o selo "original em inglês"
desde 25/08, `news.tsx` não.

E o selo por item não basta para diagnosticar: dez selos amarelos não
distinguem "o Google bloqueou e o modelo cobriu" de "as três camadas caíram".
Por isso o resumo por camada -- ele É o diagnóstico, porque as três degradam
em ordem conhecida.
"""
import pytest

from agent.get_news_feed import aplicar_traducao


def _items(*titulos):
    return [{"ticker": "NVDA",
             "news": [{"title": t, "summary": f"resumo de {t}"} for t in titulos]}]


def _traduzir(origens):
    """Dublê de `traduzir`: devolve o texto marcado e as origens pedidas."""
    def _fn(textos):
        assert len(textos) == len(origens), "o dublê tem que casar com a chamada"
        return ([f"pt:{t}" if o != "original" else t
                 for t, o in zip(textos, origens)], list(origens))
    return _fn


def test_dia_normal_nao_marca_nada():
    items = _items("A")
    resumo = aplicar_traducao(items, _traduzir(["cache", "google"]))
    n = items[0]["news"][0]
    assert n["title"] == "pt:A" and n["traduzido"] is True
    assert resumo == {"total": 2, "cache": 1, "google": 1}


def test_texto_em_ingles_marca_o_item_e_entra_na_contagem():
    items = _items("A")
    resumo = aplicar_traducao(items, _traduzir(["original", "original"]))
    assert items[0]["news"][0]["traduzido"] is False
    assert resumo["original"] == 2


def test_meia_noticia_traduzida_nao_e_noticia_traduzida():
    """Título em pt e resumo em inglês ainda é uma notícia que o leitor vai
    ler pela metade. O selo vale para o item inteiro."""
    items = _items("A")
    aplicar_traducao(items, _traduzir(["google", "original"]))
    assert items[0]["news"][0]["traduzido"] is False


def test_a_ordem_dos_campos_nao_muda_o_veredito():
    """O inverso do caso acima: resumo traduzido, título não."""
    items = _items("A")
    aplicar_traducao(items, _traduzir(["original", "google"]))
    assert items[0]["news"][0]["traduzido"] is False


def test_contagem_distingue_gratuito_de_pago():
    """A distinção que motivou o resumo: os dois cenários abaixo produzem
    ZERO selos na tela, mas um custa dinheiro por manchete e o outro não."""
    a = aplicar_traducao(_items("A", "B"), _traduzir(["google"] * 4))
    b = aplicar_traducao(_items("A", "B"), _traduzir(["llm"] * 4))
    assert a.get("original", 0) == b.get("original", 0) == 0
    assert a["google"] == 4 and "llm" not in a
    assert b["llm"] == 4 and "google" not in b


def test_sem_texto_nenhum_nao_ha_resumo():
    """Resumo com total=0 apareceria na tela como se algo tivesse sido
    tentado. Nada tentado é ausência, não zero."""
    def _nunca_chamado(_):
        pytest.fail("não deveria chamar traduzir sem texto")
    assert aplicar_traducao([{"ticker": "NVDA", "error": "sem rede"}],
                            _nunca_chamado) == {}
    assert aplicar_traducao([], _nunca_chamado) == {}


def test_manchete_sem_resumo_nao_vira_texto_vazio():
    items = [{"ticker": "NVDA", "news": [{"title": "A"}, {"title": "B", "summary": ""}]}]
    resumo = aplicar_traducao(items, _traduzir(["google", "google"]))
    assert resumo["total"] == 2, "só os campos preenchidos entram"
