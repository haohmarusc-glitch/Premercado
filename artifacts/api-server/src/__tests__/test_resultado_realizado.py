"""
Testes de resultado_realizado -- o lucro já realizado por ticker e a faixa de
preço pago.

Ver o cabeçalho do módulo para o incidente: o chat não via ticker vendido
NENHUM, porque a única ferramenta de carteira que ele tinha corta posição com
quantidade zero, e posição liquidada fica exatamente com zero.

Rodar: pytest artifacts/api-server/src/__tests__/test_resultado_realizado.py -v
"""
from unittest import mock

import pytest

from agent import resultado_realizado as rr


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _api(posicoes, lotes_por_id):
    """Fake do GET: /api/portfolio e /api/portfolio/<id>/purchases."""
    def get(url, **_kw):
        if url.endswith("/api/portfolio"):
            return _Resp(posicoes)
        for pid, lotes in lotes_por_id.items():
            if url.endswith(f"/api/portfolio/{pid}/purchases"):
                return _Resp(lotes)
        raise AssertionError(f"URL inesperada: {url}")
    return mock.patch.object(rr.SESSION, "get", side_effect=get)


def _pos(pid, ticker, simulado=False):
    return {"id": pid, "ticker": ticker, "isSimulated": simulado}


def _lote(amount, preco, venda=None, preco_venda=None, compra="2026-01-05"):
    return {
        "purchaseDate": compra, "amount": amount, "purchasePrice": preco,
        "saleDate": venda, "salePrice": preco_venda,
    }


class TestLucroPorLote:
    def test_lucro_sai_lote_por_lote_nao_por_preco_medio(self):
        # Dois lotes do MESMO papel com preços bem diferentes: 1000 a US$ 100
        # (10 ações) e 1000 a US$ 200 (5 ações), os dois vendidos a US$ 220.
        #   lote 1: 10 x 220 = 2200 -> +1200
        #   lote 2:  5 x 220 = 1100 -> +100
        # Total +1300. Por preço médio (150, 13,33 ações) daria +933 -- e a
        # faixa "menor/maior pago", que é o que o pedido quer, desapareceria.
        with _api([_pos(1, "NVDA")], {1: [
            _lote(1000, 100, "2026-03-01", 220),
            _lote(1000, 200, "2026-03-01", 220),
        ]}):
            r = rr.resultado_realizado()
        t = r["tickers"][0]
        assert t["ticker"] == "NVDA"
        assert t["lucroRealizadoUsd"] == 1300.0
        assert t["custoDoQueFoiVendidoUsd"] == 2000.0
        assert t["lucroRealizadoPct"] == 65.0
        assert t["menorPrecoPagoUsd"] == 100.0
        assert t["maiorPrecoPagoUsd"] == 200.0
        assert t["lotesVendidos"] == 2

    def test_prejuizo_entra_negativo(self):
        with _api([_pos(1, "INTC")], {1: [_lote(1000, 50, "2026-02-01", 40)]}):
            r = rr.resultado_realizado()
        t = r["tickers"][0]
        assert t["lucroRealizadoUsd"] == -200.0
        assert t["lucroRealizadoPct"] == -20.0

    def test_a_faixa_de_preco_cobre_lote_aberto_tambem(self):
        # O menor preço pago pode estar num lote que AINDA não foi vendido --
        # a pergunta é "quanto paguei neste papel", não "quanto paguei no que
        # vendi". O critério devolvido diz isso em texto.
        with _api([_pos(1, "ARM")], {1: [
            _lote(1000, 80, "2026-03-01", 120),
            _lote(500, 45),                      # aberto, e o mais barato
        ]}):
            r = rr.resultado_realizado()
        t = r["tickers"][0]
        assert t["menorPrecoPagoUsd"] == 45.0
        assert t["maiorPrecoPagoUsd"] == 80.0
        assert t["lotesAbertos"] == 1
        assert t["lotesVendidos"] == 1
        assert "vendidos e abertos" in r["criterio"]


class TestOQueNaoDaParaCalcular:
    @pytest.mark.parametrize("lote,motivo", [
        (_lote(1000, 100, "2026-03-01", None), "sem preço de venda"),
        (_lote(1000, None, "2026-03-01", 120), "sem preço de compra"),
        (_lote(0, 100, "2026-03-01", 120), "sem valor investido"),
    ])
    def test_lote_incalculavel_e_relatado_nao_ignorado(self, lote, motivo):
        # Soma que descarta em silêncio o que não sabe calcular é pior que
        # nenhuma: parece completa.
        with _api([_pos(1, "MU")], {1: [lote]}):
            r = rr.resultado_realizado()
        assert r["tickers"] == []   # sem lote computável, não entra no ranking
        assert r["tickersComVenda"] == 0

    def test_incomputavel_convive_com_computavel_na_mesma_linha(self):
        with _api([_pos(1, "MU")], {1: [
            _lote(1000, 100, "2026-03-01", 150),
            _lote(1000, 100, "2026-03-02", None),
        ]}):
            r = rr.resultado_realizado()
        t = r["tickers"][0]
        assert t["lucroRealizadoUsd"] == 500.0
        # O aviso fica NA LINHA do ticker: quem lê os 500 tem que ver ali que
        # falta um lote na conta.
        assert len(t["lotesIncomputaveis"]) == 1
        assert "sem preço de venda" in t["lotesIncomputaveis"][0]["motivo"]

    def test_carteira_sem_venda_diz_que_nao_ha_LANCAMENTO_de_venda(self):
        # A diferença importa: "não vendi nada" e "não lancei a venda no app"
        # levam a ações opostas.
        with _api([_pos(1, "NVDA")], {1: [_lote(1000, 100)]}):
            r = rr.resultado_realizado()
        assert r["tickers"] == []
        assert "nenhuma venda foi lançada" in r["nota"]


class TestRanking:
    def _carteira(self):
        posicoes, lotes = [], {}
        for i, (tk, lucro) in enumerate([
            ("AAA", 900), ("BBB", 800), ("CCC", 700), ("DDD", 600),
            ("EEE", 500), ("FFF", 400), ("GGG", 300), ("HHH", 200),
        ], start=1):
            posicoes.append(_pos(i, tk))
            # 1000 investidos, vendidos por 1000 + lucro
            lotes[i] = [_lote(1000, 10, "2026-03-01", 10 * (1000 + lucro) / 1000)]
        return posicoes, lotes

    def test_top_7_por_lucro_em_usd(self):
        posicoes, lotes = self._carteira()
        with _api(posicoes, lotes):
            r = rr.resultado_realizado(top=7)
        assert [t["ticker"] for t in r["tickers"]] == \
            ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG"]
        # O total NÃO é o do top: soma quem vendeu, os oito.
        assert r["tickersComVenda"] == 8
        assert r["totalLucroRealizadoUsd"] == 4400.0

    def test_top_zero_traz_todos(self):
        posicoes, lotes = self._carteira()
        with _api(posicoes, lotes):
            assert len(rr.resultado_realizado(top=0)["tickers"]) == 8

    def test_papel_sem_venda_fica_fora_do_ranking(self):
        # Lucro realizado 0 num ranking de "quem deu mais lucro" se lê como
        # "vendi e não ganhei nada", que é outra afirmação.
        with _api([_pos(1, "NVDA"), _pos(2, "ARM")], {
            1: [_lote(1000, 100, "2026-03-01", 150)],
            2: [_lote(1000, 100)],
        }):
            r = rr.resultado_realizado()
        assert [t["ticker"] for t in r["tickers"]] == ["NVDA"]


class TestSimulado:
    def test_simulado_fora_por_padrao_e_contado(self):
        with _api([_pos(1, "NVDA"), _pos(2, "FAKE", simulado=True)], {
            1: [_lote(1000, 100, "2026-03-01", 150)],
            2: [_lote(1000, 10, "2026-03-01", 100)],   # +9000 de mentira
        }):
            r = rr.resultado_realizado()
        assert [t["ticker"] for t in r["tickers"]] == ["NVDA"]
        assert r["totalLucroRealizadoUsd"] == 500.0
        # Excluir calado seria tão ruim quanto somar: quem lê precisa saber.
        assert r["posicoesSimuladasExcluidas"] == 1

    def test_incluir_simulado_quando_pedido(self):
        with _api([_pos(1, "NVDA"), _pos(2, "FAKE", simulado=True)], {
            1: [_lote(1000, 100, "2026-03-01", 150)],
            2: [_lote(1000, 10, "2026-03-01", 100)],
        }):
            r = rr.resultado_realizado(incluir_simulado=True)
        assert [t["ticker"] for t in r["tickers"]] == ["FAKE", "NVDA"]
        assert "posicoesSimuladasExcluidas" not in r


class TestFalhas:
    def test_carteira_ilegivel_nao_vira_carteira_vazia(self):
        with mock.patch.object(rr.SESSION, "get", side_effect=OSError("timeout")):
            r = rr.resultado_realizado()
        assert r["tickers"] == []
        assert "não foi possível ler a carteira" in r["error"]
        # Sem `nota` de "nenhuma venda lançada": afirmar isso a partir de uma
        # leitura que falhou é o erro que _falha_de_leitura existe pra evitar.
        assert "nota" not in r

    def test_lote_ilegivel_de_uma_posicao_nao_derruba_as_outras(self):
        def get(url, **_kw):
            if url.endswith("/api/portfolio"):
                return _Resp([_pos(1, "NVDA"), _pos(2, "ARM")])
            if url.endswith("/api/portfolio/1/purchases"):
                return _Resp([_lote(1000, 100, "2026-03-01", 150)])
            raise OSError("timeout")
        with mock.patch.object(rr.SESSION, "get", side_effect=get):
            r = rr.resultado_realizado()
        assert [t["ticker"] for t in r["tickers"]] == ["NVDA"]
        assert [x["ticker"] for x in r["posicoesSemLotes"]] == ["ARM"]

    def test_numero_como_string_do_driver_pg(self):
        # `money` volta como string no pg; float("1000.00") tem que funcionar.
        with _api([_pos(1, "NVDA")], {1: [
            {"purchaseDate": "2026-01-05", "amount": "1000.00",
             "purchasePrice": "100.0000", "saleDate": "2026-03-01",
             "salePrice": "150.0000"},
        ]}):
            r = rr.resultado_realizado()
        assert r["tickers"][0]["lucroRealizadoUsd"] == 500.0


class TestFerramentaRegistrada:
    def test_o_chat_tem_a_ferramenta(self):
        # Sem isto o conserto não chega ao usuário: o módulo existiria e o chat
        # continuaria sem ver venda nenhuma.
        from agent.llm_runtime import CHAT_TOOLS
        assert "resultado_realizado" in {t["name"] for t in CHAT_TOOLS}

    def test_o_snapshot_avisa_que_nao_traz_vendido(self):
        # O modelo escolhe a ferramenta pela descrição. Sem este aviso ele
        # continua perguntando o vendido ao snapshot, que responde sem ele.
        from agent import tools
        desc = next(t["description"] for t in tools.TOOLS
                    if t["name"] == "get_portfolio_snapshot")
        assert "NÃO devolve papel já vendido" in desc
        assert "resultado_realizado" in desc

    def test_esta_no_mapa_de_execucao(self):
        from agent import tools
        assert tools.DISPATCH["resultado_realizado"] is rr.resultado_realizado
