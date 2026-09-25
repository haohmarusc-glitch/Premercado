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

# A função REAL, capturada antes de a fixture `sem_cotacao` trocá-la. Os dois
# testes que exercitam a própria busca de cotação usam esta referência; o resto
# do arquivo usa o stub.
_PRECO_ATUAL_REAL = rr._preco_atual


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def sem_cotacao(monkeypatch):
    """Sem preço atual, por padrão, em TODO teste deste arquivo.

    `resultado_realizado` busca uma cotação por ticker do ranking, e isso
    passa por get_stock_data -> rede. Quem testa o preço atual declara o que
    quer encontrar; o resto do arquivo não precisa saber que ele existe.
    """
    monkeypatch.setattr(rr, "_preco_atual", lambda _t: None)


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
        assert r["tickersComVenda"] == 8
        # DOIS totais, cada um com o escopo no nome. O relatório de
        # 25/09/2026 publicou o da carteira inteira como "total (top 7)":
        # os sete somavam 385,88 e o campo trazia 401,52, dos onze.
        assert r["somaDoRankingUsd"] == 4200.0        # 900+800+700+600+500+400+300
        assert r["totalLucroRealizadoTodosOsTickersUsd"] == 4400.0  # + HHH (200)
        assert "totalLucroRealizadoUsd" not in r      # nome ambíguo, aposentado
        assert "somaDoRankingUsd" in r["criterio"]
        assert "Não troque um pelo outro" in r["criterio"]

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
        assert r["totalLucroRealizadoTodosOsTickersUsd"] == 500.0
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


class TestChatTemTudo:
    """O chat recebe TODAS as ferramentas, e o prompt as LISTA todas.

    O defeito que isto fecha não é uma ferramenta específica: é a lista
    mantida à mão. `_CHAT_TOOL_NAMES` precisava ser editada para cada
    ferramenta nova, e doze ficaram de fora ao longo do tempo -- entre elas
    get_earnings_reaction_history e get_earnings_calendar, justo as que
    respondem as perguntas mais frequentes. O inventário escrito no prompt
    tinha a mesma doença, por cima: ferramenta no schema e ausente da lista é
    ferramenta que o modelo não sabe que tem.
    """

    def test_nenhuma_ferramenta_fica_fora_do_chat(self):
        from agent import tools
        from agent.llm_runtime import CHAT_TOOLS
        no_chat = {t["name"] for t in CHAT_TOOLS}
        faltando = sorted({t["name"] for t in tools.TOOLS} - no_chat)
        assert faltando == [], f"ferramenta fora do chat: {faltando}"

    def test_as_doze_que_estavam_de_fora(self):
        # Nomeadas uma a uma: se alguém reintroduzir um filtro, o teste diz
        # QUAL sumiu, não só que a contagem mudou.
        from agent.llm_runtime import CHAT_TOOLS
        no_chat = {t["name"] for t in CHAT_TOOLS}
        for nome in [
            "search_edgar_filings", "read_filing", "save_observation",
            "update_exit_plan_item", "create_exit_plan_item",
            "get_earnings_calendar", "get_earnings_reaction_history",
            "get_global_market_snapshot", "get_europe_regime_signal",
            "detect_sector_contagion", "check_market_alerts",
            "get_backtest_summary",
        ]:
            assert nome in no_chat, nome

    def test_as_so_do_chat_continuam_so_no_chat(self):
        # get_gamma_exposure/get_earnings_transcript têm cota de tier grátis e
        # não podem entrar em varredura automática.
        from agent import tools
        from agent.llm_runtime import CHAT_TOOLS
        gerais = {t["name"] for t in tools.TOOLS}
        no_chat = {t["name"] for t in CHAT_TOOLS}
        for nome in ("get_gamma_exposure", "get_earnings_transcript"):
            assert nome in no_chat
            assert nome not in gerais, f"{nome} vazou para as rodadas automáticas"

    def test_as_rodadas_automaticas_continuam_estreitas(self):
        # O chat é uma pessoa pedindo uma coisa por vez; as rodadas varrem a
        # carteira sozinhas, e lá o custo se multiplica por ticker.
        from agent import tools
        from agent.llm_runtime import (ALERTS_TOOLS, CHAT_TOOLS,
                                       EXIT_PLAN_TOOLS, PREMARKET_TOOLS)
        for subconjunto in (PREMARKET_TOOLS, ALERTS_TOOLS, EXIT_PLAN_TOOLS):
            assert len(subconjunto) < len(tools.TOOLS)
        assert len(CHAT_TOOLS) >= len(tools.TOOLS)

    def test_o_prompt_lista_toda_ferramenta_que_o_chat_tem(self):
        from unittest import mock
        import agent.llm_runtime as L
        with mock.patch.object(L.memory, "rich_context_block", return_value="(x)"), \
                mock.patch.object(L.memory, "recent_context", return_value="(y)"):
            prompt = L.build_chat_prompt()
        ausentes = [t["name"] for t in L.CHAT_TOOLS if t["name"] not in prompt]
        assert ausentes == [], f"no schema mas fora do inventário: {ausentes}"

    def test_o_prompt_nao_proibe_mais_o_que_agora_esta_liberado(self):
        from unittest import mock
        import agent.llm_runtime as L
        with mock.patch.object(L.memory, "rich_context_block", return_value="(x)"), \
                mock.patch.object(L.memory, "recent_context", return_value="(y)"):
            prompt = L.build_chat_prompt()
        # A lista "NÃO use: save_observation, search_edgar_filings, ..." era o
        # segundo bloqueio, independente do schema.
        assert "NÃO use:" not in prompt

    def test_escrita_continua_pedindo_pedido_do_usuario(self):
        # Liberar as ferramentas de escrita não é liberar escrever por
        # iniciativa própria: agora o chat pode mexer no plano de saída e na
        # memória do agente.
        from unittest import mock
        import agent.llm_runtime as L
        with mock.patch.object(L.memory, "rich_context_block", return_value="(x)"), \
                mock.patch.object(L.memory, "recent_context", return_value="(y)"):
            prompt = L.build_chat_prompt()
        assert "ESCRITA só quando o usuário PEDIR" in prompt
        for escrita in ("create_exit_plan_item", "update_exit_plan_item",
                        "save_observation", "create_alert", "delete_alert"):
            assert escrita in prompt


class TestPrecoAtual:
    """O preço atual ao lado do preço pago -- pedido de 25/09/2026.

    O ponto do campo é a comparação: "paguei entre X e Y, hoje está Z". Por
    isso ele vem com a FONTE (mercado / fechamento / pré-mercado) e com a
    distância até os dois extremos pagos.
    """

    def _uma_venda(self):
        return _api([_pos(1, "MU")], {1: [
            _lote(400, 793, "2026-06-18", 1000),
            _lote(400, 1042.36),
        ]})

    def test_preco_fonte_e_distancias(self, monkeypatch):
        monkeypatch.setattr(rr, "_preco_atual", lambda _t: (1080.53, "mercado"))
        with self._uma_venda():
            r = rr.resultado_realizado()
        t = r["tickers"][0]
        assert t["precoAtualUsd"] == 1080.53
        assert t["precoAtualFonte"] == "mercado"
        # 1080,53 / 1042,36 - 1 = +3,66% sobre o pior preço pago
        assert t["precoAtualVsMaiorPagoPct"] == 3.66
        # 1080,53 / 793 - 1 = +36,26% sobre o melhor
        assert t["precoAtualVsMenorPagoPct"] == 36.26

    def test_cotacao_ausente_e_DITA_nao_omitida(self, monkeypatch):
        # Campo que simplesmente falta é indistinguível de "papel sem preço",
        # e é aí que o modelo preenche de memória.
        monkeypatch.setattr(rr, "_preco_atual", lambda _t: None)
        with self._uma_venda():
            r = rr.resultado_realizado()
        t = r["tickers"][0]
        assert t["precoAtualUsd"] is None
        assert "não disponível" in t["precoAtualNota"]

    def test_pode_ser_desligado(self, monkeypatch):
        monkeypatch.setattr(rr, "_preco_atual",
                            lambda _t: pytest.fail("não devia buscar cotação"))
        with self._uma_venda():
            r = rr.resultado_realizado(incluir_preco_atual=False)
        assert "precoAtualUsd" not in r["tickers"][0]

    def test_cota_so_o_que_entra_no_ranking(self, monkeypatch):
        # Com 8 vendedores e top=3, são 3 cotações, não 8: buscar antes do
        # corte pagaria 5 que ninguém vê.
        pedidos = []
        monkeypatch.setattr(rr, "_preco_atual",
                            lambda t: (pedidos.append(t), (10.0, "mercado"))[1])
        posicoes, lotes = TestRanking()._carteira()
        with _api(posicoes, lotes):
            r = rr.resultado_realizado(top=3)
        assert len(r["tickers"]) == 3
        assert pedidos == ["AAA", "BBB", "CCC"]

    def test_a_fonte_do_preco_vem_da_precedencia_do_quote(self, monkeypatch):
        # Mesma precedência de portfolio_snapshot: negociação, depois último
        # fechamento, depois pré-mercado.
        from agent import tools
        casos = [
            ({"regular_market_price": 100, "last_close": 90}, (100.0, "mercado")),
            ({"last_close": 90, "pre_market_price": 95}, (90.0, "último fechamento")),
            ({"pre_market_price": 95}, (95.0, "pré-mercado")),
            ({}, None),
            ({"regular_market_price": 0}, None),
        ]
        for quote, esperado in casos:
            with mock.patch.object(tools, "get_stock_data", return_value=quote):
                assert _PRECO_ATUAL_REAL("MU") == esperado, quote

    def test_quote_que_explode_nao_derruba_o_relatorio(self):
        from agent import tools
        with mock.patch.object(tools, "get_stock_data", side_effect=OSError("timeout")):
            assert _PRECO_ATUAL_REAL("MU") is None

    def test_a_ferramenta_declara_o_parametro(self):
        from agent import tools
        f = next(t for t in tools.TOOLS if t["name"] == "resultado_realizado")
        assert "incluir_preco_atual" in f["input_schema"]["properties"]
        assert "fonte" in f["description"]


class TestPrecoDeVenda:
    """"Vendi cedo?" -- a pergunta que a coluna anterior não respondia.

    O relatório de 25/09/2026 concluiu "META e INTC continuam subindo após
    vendidas" citando precoAtualVsMaiorPagoPct, que compara o preço de hoje
    com o preço PAGO. A conclusão podia até estar certa; o número não a
    sustentava, e não havia campo que sustentasse.
    """

    def test_preco_medio_de_venda_e_ponderado_pela_quantidade(self, monkeypatch):
        monkeypatch.setattr(rr, "_preco_atual", lambda _t: None)
        # Lote A: 1000 a US$ 100 = 10 ações, vendidas a 120 -> 1200
        # Lote B: 1000 a US$ 500 =  2 ações, vendidas a 600 -> 1200
        # Recebido 2400 em 12 ações -> média ponderada 200, NÃO (120+600)/2=360.
        with _api([_pos(1, "X")], {1: [
            _lote(1000, 100, "2026-03-01", 120),
            _lote(1000, 500, "2026-03-01", 600),
        ]}):
            r = rr.resultado_realizado()
        assert r["tickers"][0]["precoVendaMedioUsd"] == 200.0

    def test_vs_venda_diz_se_subiu_depois_da_saida(self, monkeypatch):
        # Vendido a 100, hoje 126 -> +26% DEPOIS da venda (dinheiro deixado na
        # mesa), mesmo tendo sido pago 599: os dois números respondem coisas
        # diferentes, e é essa a distinção que faltava.
        monkeypatch.setattr(rr, "_preco_atual", lambda _t: (126.0, "mercado"))
        with _api([_pos(1, "META")], {1: [_lote(599, 599, "2026-03-01", 100)]}):
            r = rr.resultado_realizado()
        t = r["tickers"][0]
        assert t["precoVendaMedioUsd"] == 100.0
        assert t["precoAtualVsVendaPct"] == 26.0
        # E continua diferente do vs-pago, que é o que foi confundido.
        assert t["precoAtualVsMaiorPagoPct"] == round((126 / 599 - 1) * 100, 2)

    def test_venda_acima_do_preco_de_hoje_da_negativo(self, monkeypatch):
        # Vendeu bem: saiu a 120, hoje está 90.
        monkeypatch.setattr(rr, "_preco_atual", lambda _t: (90.0, "mercado"))
        with _api([_pos(1, "ARM")], {1: [_lote(1000, 100, "2026-03-01", 120)]}):
            r = rr.resultado_realizado()
        assert r["tickers"][0]["precoAtualVsVendaPct"] == -25.0

    def test_o_criterio_manda_usar_o_campo_certo(self, monkeypatch):
        monkeypatch.setattr(rr, "_preco_atual", lambda _t: None)
        with _api([_pos(1, "X")], {1: [_lote(1000, 100, "2026-03-01", 120)]}):
            r = rr.resultado_realizado()
        assert "precoAtualVsVendaPct" in r["criterio"]
        assert "não responde essa pergunta" in r["criterio"]

    def test_lote_incomputavel_nao_entra_na_media_de_venda(self, monkeypatch):
        monkeypatch.setattr(rr, "_preco_atual", lambda _t: None)
        with _api([_pos(1, "X")], {1: [
            _lote(1000, 100, "2026-03-01", 120),
            _lote(1000, 100, "2026-03-02", None),   # sem preço de venda
        ]}):
            r = rr.resultado_realizado()
        # Só o primeiro lote conta: 10 ações a 120.
        assert r["tickers"][0]["precoVendaMedioUsd"] == 120.0

    def test_a_ferramenta_avisa_qual_campo_usar(self):
        from agent import tools
        f = next(t for t in tools.TOOLS if t["name"] == "resultado_realizado")
        assert "precoAtualVsVendaPct" in f["description"]
        assert "somaDoRankingUsd" in f["description"]


class TestNaoAfirmaTopoNemFundo:
    """Dois pontos não são a série.

    Relatório de 25/09/2026: "INTC saiu praticamente no topo (vendido em
    $123,59, hoje $124,80), confirmando timing preciso". Os dois números estão
    certos e a conclusão não se sustenta -- entre a venda e hoje o papel pode
    ter ido a $150 e voltado, e o relatório não vê esse caminho. É a mesma
    família do erro anterior: afirmação que passa do que o número mede.
    """

    def _prompt(self):
        """O prompt com o espaço em branco normalizado.

        O texto da regra é escrito quebrado em várias linhas para caber na
        coluna, então procurar a frase inteira falharia pela quebra e não pelo
        conteúdo -- um teste que reprova formatação em vez de significado.
        """
        from unittest import mock
        import agent.llm_runtime as L
        with mock.patch.object(L.memory, "rich_context_block", return_value="(x)"), \
                mock.patch.object(L.memory, "recent_context", return_value="(y)"):
            return " ".join(L.build_chat_prompt().split())

    def test_a_regra_esta_no_prompt(self):
        p = self._prompt()
        assert "Você tem PONTOS, não a série" in p

    def test_nomeia_as_frases_que_nao_pode_usar(self):
        # Nomeadas porque foram as que apareceram: regra abstrata ("seja
        # cuidadoso") não muda a frase que o modelo escreve.
        p = self._prompt()
        for frase in ("saiu no topo", "pegou o fundo", "timing preciso"):
            assert frase in p, frase

    def test_diz_o_que_FAZER_no_lugar(self):
        # Proibição sem alternativa vira omissão: o modelo deixa de dizer o que
        # sabe. A distância entre os dois pontos continua sendo afirmável.
        p = self._prompt()
        assert "Diga a distância entre os dois" in p
        assert "máxima/mínima que venham no JSON" in p
