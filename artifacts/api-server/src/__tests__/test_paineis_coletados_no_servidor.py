"""Os quatro painéis da Análise Rápida são coletados pelo SERVIDOR.

Incidente (29/08/2026, MRVL): a tela mandava para a IA os painéis que
tivesse no React Query naquele clique. Cada painel tem seu próprio ciclo de
refresh, então nada garantia que os quatro fossem do mesmo momento -- e a
Técnica chegou com preço e variação de uma sessão anterior à do resto. A
prosa descreveu duas sessões diferentes como se fossem uma.

Dava para provar pela tela, sem dado de mercado: o preço e a variação de
-1,49% da Técnica batiam com a linha 2026-08-27 da tabela de earnings, não
com a sessão que os outros painéis mostravam.

`_defasagem_entre_paineis` DETECTA isso e avisa. Estes testes cobrem a
PREVENÇÃO: quatro painéis lidos em sequência dentro de um processo não
conseguem estar em sessões diferentes.
"""
import os
import sys

_SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from agent import analise_rapida_ia as mod  # noqa: E402


def _dubles(monkeypatch, marca="X"):
    """Troca os quatro coletores por dublês, sem rede."""
    from agent import earnings_reaction_analysis as reacao
    from agent import get_technicals as tecnica
    from agent import get_ticker_snapshot as snap
    from agent import get_trend as tendencia

    monkeypatch.setattr(tendencia, "com_cache",
                        lambda tickers: [{"ticker": tickers[0], "de": f"trend-{marca}"}])
    monkeypatch.setattr(tecnica, "technicals",
                        lambda t, *a, **k: {"ticker": t, "de": f"tech-{marca}"})
    monkeypatch.setattr(snap, "snapshot",
                        lambda t, b: {"ticker": t, "benchmark": b, "de": f"snap-{marca}"})
    monkeypatch.setattr(reacao, "analyze_ticker",
                        lambda t, n, b: {"ticker": t, "de": f"reac-{marca}"})


def test_coleta_os_quatro_quando_o_chamador_nao_manda(monkeypatch):
    """A rota passou a mandar só ticker e benchmark. Se a coleta não
    acontecesse, a análise rodaria sem painel nenhum."""
    _dubles(monkeypatch)
    saida = mod._com_paineis({"ticker": "MRVL", "benchmark": "SMH"})
    assert saida["trend"]["de"] == "trend-X"
    assert saida["technicals"]["de"] == "tech-X"
    assert saida["snapshot"]["de"] == "snap-X"
    assert saida["reaction"]["de"] == "reac-X"


def test_o_benchmark_chega_a_quem_precisa_dele(monkeypatch):
    """Snapshot e reação usam benchmark; mandar o errado (ou o default) faria
    o excesso sobre setor sair contra a referência errada, calado."""
    _dubles(monkeypatch)
    saida = mod._com_paineis({"ticker": "MRVL", "benchmark": "KWEB"})
    assert saida["snapshot"]["benchmark"] == "KWEB"


def test_painel_mandado_pelo_chamador_tem_precedencia(monkeypatch):
    """O seam que mantém a suíte funcionando: teste que monta painel à mão
    continua exercitando AQUELE painel, sem coleta por baixo."""
    _dubles(monkeypatch)
    meu = {"ticker": "MRVL", "de": "montado-a-mao"}
    saida = mod._com_paineis({"ticker": "MRVL", "trend": meu})
    assert saida["trend"] is meu
    assert saida["technicals"]["de"] == "tech-X"   # os outros vieram da coleta


def test_um_coletor_que_estoura_nao_derruba_os_outros(monkeypatch):
    """Falha aberta por painel -- mesmo contrato de quando a tela mandava um
    painel vazio. Derrubar a análise inteira por causa de uma fonte fora do
    ar seria trocar informação parcial por nenhuma."""
    _dubles(monkeypatch)
    from agent import get_technicals as tecnica

    def _explode(t, *a, **k):
        raise RuntimeError("yfinance fora do ar")
    monkeypatch.setattr(tecnica, "technicals", _explode)

    saida = mod._com_paineis({"ticker": "MRVL"})
    assert "error" in saida["technicals"]
    assert saida["trend"]["de"] == "trend-X"
    assert saida["snapshot"]["de"] == "snap-X"


def test_sem_ticker_nao_coleta_nada(monkeypatch):
    """Sem ticker não há o que coletar, e sanitize_ticker reprovaria depois.
    Coletar aqui seria quatro chamadas de rede para nada."""
    def _nunca(*a, **k):
        raise AssertionError("não devia ter coletado")
    from agent import get_trend as tendencia
    monkeypatch.setattr(tendencia, "com_cache", _nunca)
    assert mod._com_paineis({"ticker": "  "}) == {"ticker": "  "}


def test_a_lista_de_paineis_cobre_o_que_a_defasagem_vigia():
    """`_defasagem_entre_paineis` compara quatro painéis. Se um deles saísse
    de `_PAINEIS`, ele deixaria de ser coletado e voltaria a chegar da tela --
    e a checagem de defasagem continuaria verde, olhando um painel que
    ninguém mais alimenta."""
    assert set(mod._PAINEIS) == {"trend", "technicals", "snapshot", "reaction"}


def test_coletor_pendurado_nao_segura_a_analise(monkeypatch):
    """A prova de comportamento do teto, não só a da constante.

    Um socket que não responde é o caso normal quando um provedor cai: a
    chamada não estoura, ela fica. Sem prazo no `result()`, a coleta esperaria
    até o Node matar o processo aos 245s -- e o usuário receberia 500 genérico
    em vez de uma análise com um painel a menos.

    Aqui a Técnica dorme muito além do teto; o esperado é a coleta devolver na
    hora, com aquele painel marcado e os outros três inteiros."""
    import time

    _dubles(monkeypatch)
    from agent import get_technicals as tecnica

    monkeypatch.setattr(mod, "_TETO_COLETA_S", 0.3)
    monkeypatch.setattr(tecnica, "technicals",
                        lambda t, *a, **k: time.sleep(30) or {"nunca": True})

    comeco = time.monotonic()
    saida = mod._coletar_paineis("MRVL", "SMH")
    gasto = time.monotonic() - comeco

    assert gasto < 5, f"a coleta ficou pendurada {gasto:.1f}s apesar do teto"
    assert "error" in saida["technicals"]
    assert saida["trend"]["de"] == "trend-X"
    assert saida["snapshot"]["de"] == "snap-X"
    assert saida["reaction"]["de"] == "reac-X"
