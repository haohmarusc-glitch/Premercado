"""
Testes da integração do get_quotes.py com a cadeia de fallback.

O ponto central não é "o fallback funciona" (isso é test_provider_fallback.py)
e sim QUANDO ele entra: por LOTE, nunca por símbolo. A cota da Alpha Vantage é
compartilhada com o feed de notícias, e um ticker deslistado não pode consumi-la.

Import de PACOTE (`from agent import ...`) — ver o cabeçalho de
test_provider_fallback.py para o porquê.
"""
import pytest

from agent import get_quotes
from agent import provider_health


@pytest.fixture(autouse=True)
def _isolated_health_file(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_health, "_PATH", str(tmp_path / "health.json"))
    yield


def _quote(symbol, price):
    q = get_quotes._empty_quote(symbol, None)  # noqa: SLF001
    if price is not None:
        q.update({"price": price, "source": "yfinance"})
    return q


def _fallback_falso(symbol):
    q = get_quotes._empty_quote(symbol, None)  # noqa: SLF001
    q.update({
        "price": 99.0, "previousClose": 98.0, "change": 1.0, "changePct": 1.02,
        "isDelayed": True, "source": "alphavantage_eod",
        "sourceWarnings": ["Cotação ao vivo indisponível — mostrando fechamento de 2026-08-14"],
    })
    return q


# ── quando o fallback NÃO deve entrar ───────────────────────────────────────

def test_nao_aciona_fallback_com_lote_saudavel(monkeypatch):
    monkeypatch.setattr(
        get_quotes, "_quote_do_fallback",
        lambda s: pytest.fail("lote saudável não deveria gastar cota"),
    )
    out = get_quotes.aplicar_fallback([_quote("NVDA", 10.0), _quote("MU", 20.0)])
    assert all(q["source"] == "yfinance" for q in out)


def test_nao_aciona_fallback_por_simbolo_isolado(monkeypatch):
    """Um ticker sem preço no meio de um lote que funcionou é problema do
    ticker (deslistado, digitado errado) — falharia em qualquer fonte, e
    gastar cota nele é jogar fora a cota que as notícias dividem conosco."""
    monkeypatch.setattr(
        get_quotes, "_quote_do_fallback",
        lambda s: pytest.fail("símbolo isolado não deveria acionar a fonte externa"),
    )
    out = get_quotes.aplicar_fallback([_quote("NVDA", 10.0), _quote("XXXX", None)])
    ruim = next(q for q in out if q["symbol"] == "XXXX")
    assert ruim["price"] is None
    assert ruim["isDelayed"] is False


def test_lista_vazia_nao_quebra():
    assert get_quotes.aplicar_fallback([]) == []


# ── quando o fallback DEVE entrar ───────────────────────────────────────────

def test_aciona_fallback_quando_o_lote_inteiro_falha(monkeypatch):
    monkeypatch.setattr(get_quotes, "_quote_do_fallback", _fallback_falso)
    out = get_quotes.aplicar_fallback([_quote("NVDA", None), _quote("MU", None)])
    assert len(out) == 2
    for q in out:
        assert q["price"] == 99.0
        assert q["isDelayed"] is True
        assert q["source"] == "alphavantage_eod"


def test_fallback_preserva_o_erro_original(monkeypatch):
    """A cotação veio, mas continua útil saber por que a fonte primária não
    respondeu — o erro não pode sumir junto com o problema."""
    monkeypatch.setattr(get_quotes, "_quote_do_fallback", _fallback_falso)
    ruim = _quote("NVDA", None)
    ruim["error"] = "Tempo esgotado buscando cotação"
    out = get_quotes.aplicar_fallback([ruim])
    assert out[0]["error"] == "Tempo esgotado buscando cotação"
    assert out[0]["price"] == 99.0


def test_fallback_que_tambem_falha_mantem_a_cotacao_vazia(monkeypatch):
    monkeypatch.setattr(get_quotes, "_quote_do_fallback", lambda s: None)
    out = get_quotes.aplicar_fallback([_quote("NVDA", None)])
    assert out[0]["price"] is None
    assert out[0]["source"] == "none"


def test_nao_duplica_simbolos(monkeypatch):
    monkeypatch.setattr(get_quotes, "_quote_do_fallback", _fallback_falso)
    out = get_quotes.aplicar_fallback([_quote("NVDA", None), _quote("MU", 20.0)])
    assert sorted(q["symbol"] for q in out) == ["MU", "NVDA"]


# ── disjuntor: uma vez por lote ─────────────────────────────────────────────

def test_registra_sucesso_uma_vez_por_lote():
    get_quotes.aplicar_fallback([_quote("NVDA", 10.0), _quote("MU", 20.0), _quote("STX", 5.0)])
    st = provider_health.status()["yfinance"]
    # Três símbolos, UM registro — um ticker morto não pode penalizar o
    # provedor inteiro (ver provider_health, "O disjuntor é por PROVEDOR").
    assert st["total_successes"] == 1


def test_registra_falha_uma_vez_por_lote(monkeypatch):
    monkeypatch.setattr(get_quotes, "_quote_do_fallback", lambda s: None)
    get_quotes.aplicar_fallback([_quote("NVDA", None), _quote("MU", None), _quote("STX", None)])
    st = provider_health.status()["yfinance"]
    assert st["total_failures"] == 1
    assert st["consecutive_failures"] == 1


def test_lote_parcial_conta_como_sucesso(monkeypatch):
    """Um símbolo ruim no meio não é sinal de que o Yahoo caiu."""
    monkeypatch.setattr(get_quotes, "_quote_do_fallback", lambda s: None)
    get_quotes.aplicar_fallback([_quote("NVDA", 10.0), _quote("XXXX", None)])
    st = provider_health.status()["yfinance"]
    assert st["total_successes"] == 1
    assert st["total_failures"] == 0


# ── contrato de saída ───────────────────────────────────────────────────────

def test_cotacao_vazia_tem_os_campos_novos():
    q = get_quotes._empty_quote("NVDA", "erro")  # noqa: SLF001
    assert q["isDelayed"] is False
    assert q["source"] == "none"
    assert q["sourceWarnings"] == []


def test_campos_novos_sao_serializaveis():
    """O resultado vai pra stdout como JSON e é lido pelo Node — um tipo que
    o json não serializa aqui derruba o consumidor inteiro."""
    import json
    json.dumps(get_quotes.aplicar_fallback([_quote("NVDA", 10.0)]))
