"""
Testes de get_news_feed.py -- relevância e correlação de notícias por ticker.

get_news_feed.py roda como script standalone (spawnado direto por
routes/analysis.ts, não faz parte do pacote `agent` importável) -- carrega
via importlib pra não precisar de um __init__ novo só pra isso, mesmo padrão
de test_get_chart_session.py. src/agent/ entra em sys.path porque o próprio
get_news_feed.py faz imports "flat" (`from security import ...`), replicando
o cwd real de quando o script roda sozinho.

Por que estes testes existem: visto em produção (11/08) o feed da NVDA
trazia resumo de teleconferência de resultados da Middleby Corporation e da
Janus International -- itens que a Yahoo devolve como "preenchimento" do
feed, sem relação nenhuma com o ticker pedido. A defesa (_relevant_tickers)
só mantém um item sob um ticker se ele genuinamente cita esse ticker
(símbolo OU nome da empresa) no título/resumo, e marca em relatedTickers
quando a mesma matéria também cita outro ticker da carteira.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_get_news_feed.py -v
"""
import os
import sys
import importlib.util

import pytest

_AGENT_DIR = os.path.join(os.path.dirname(__file__), "..", "agent")
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)

_MODULE_PATH = os.path.join(_AGENT_DIR, "get_news_feed.py")
_spec = importlib.util.spec_from_file_location("get_news_feed", _MODULE_PATH)
gnf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gnf)


PORTFOLIO = ["NVDA", "SMCI", "ARM", "MRVL", "SKHY"]

NAMES = {
    "NVDA": "NVIDIA Corporation",
    "SMCI": "Super Micro Computer, Inc.",
    "ARM": "Arm Holdings plc",
    "MRVL": "Marvell Technology, Inc.",
    "SKHY": "SK hynix Inc.",
}


def _relevant(ticker, title, summary="", names=NAMES, candidates=PORTFOLIO, tagged=frozenset()):
    text = f"{title} {summary}"
    relevant = gnf._relevant_tickers(text, set(tagged), candidates, names)
    return ticker in relevant, sorted(t for t in relevant if t != ticker)


class TestFiltraFillerNaoRelacionado:
    """Item que não cita nem o símbolo nem o nome da empresa é descartado."""

    def test_earnings_call_de_outra_empresa_e_filtrado(self):
        keep, _ = _relevant(
            "NVDA",
            "Resumo da teleconferência de resultados do segundo trimestre de 2026 da Middleby Corporation",
        )
        assert keep is False

    def test_segunda_empresa_nao_relacionada_tambem_filtrada(self):
        keep, _ = _relevant(
            "NVDA",
            "Janus International Group, Inc. Resumo da teleconferência de resultados do segundo trimestre de 2026",
        )
        assert keep is False


class TestMantemNoticiaGenuina:
    def test_simbolo_entre_parenteses(self):
        keep, _ = _relevant("ARM", "Por que a Arm Holdings (ARM) aumentou 18,2% após o acordo")
        assert keep is True

    def test_so_o_nome_sem_simbolo(self):
        keep, _ = _relevant("ARM", "Astera Labs vs. Arm: Qual ação de tecnologia é a melhor compra em 2026?")
        assert keep is True

    def test_nome_curto_nvidia(self):
        keep, _ = _relevant("NVDA", "Nvidia sobe após anúncio de parceria bilionária em IA")
        assert keep is True


class TestNomeComposto:
    """Supermicro/Super Micro: manchete abrevia o nome de formas diferentes,
    e nunca inclui a palavra 'Computer' que sobra do nome oficial."""

    def test_supermicro_sem_espaco(self):
        keep, _ = _relevant("SMCI", "Ações de tecnologia hoje: CoreWeave e Supermicro relatarão resultados")
        assert keep is True

    def test_super_micro_com_espaco(self):
        keep, _ = _relevant("SMCI", "Super Micro anuncia novo servidor otimizado para IA")
        assert keep is True

    def test_sk_hynix_com_espaco(self):
        keep, _ = _relevant("SKHY", "SK Hynix sofreu pós-IPO como bandeiras de memória")
        assert keep is True


class TestPalavraGenericaNaoFalseiaPositivo:
    """Fragmento truncado de um nome de várias palavras (ex.: só 'Super', de
    'Super Micro Computer') não pode casar com texto não relacionado -- é
    palavra comum demais pra valer sozinha."""

    def test_super_bowl_nao_vira_smci(self):
        keep, _ = _relevant("SMCI", "Super Bowl commercials cost a record amount this year")
        assert keep is False

    def test_sk_telecom_nao_vira_skhy(self):
        keep, _ = _relevant("SKHY", "SK Telecom announces new roaming plan for travelers")
        assert keep is False


class TestCorrelacaoEntreTickers:
    """Notícia que menciona mais de um ticker da carteira marca os outros em
    relatedTickers, em vez de aparecer sem contexto só sob o ticker buscado."""

    def test_alianca_entre_duas_empresas_da_carteira(self):
        keep, related = _relevant(
            "MRVL",
            "A Aliança NVDA da MRVL pode ajudá-la a desafiar a ALAB e a AVGO?",
            "A aliança expandida da NVIDIA com a Marvell Technology e o investimento de US$ 2 bilhões.",
        )
        assert keep is True
        assert related == ["NVDA"]

    def test_sem_correlacao_related_fica_vazio(self):
        keep, related = _relevant("NVDA", "Nvidia anuncia novo chip para data centers")
        assert keep is True
        assert related == []


class TestTagPelaPropriaYahoo:
    """Quando o payload da Yahoo já marca o ticker (finance.stockTickers),
    isso basta -- não depende do texto citar símbolo nem nome."""

    def test_tag_da_yahoo_e_suficiente_mesmo_sem_texto(self):
        keep, _ = _relevant("MRVL", "Manchete genérica sem nenhuma menção direta", tagged=["MRVL"])
        assert keep is True


class TestNameTokens:
    def test_nome_composto_gera_forma_completa_e_abreviada(self):
        tokens = gnf._name_tokens("Super Micro Computer, Inc.")
        assert "supermicrocomputer" in tokens
        assert "supermicro" in tokens
        assert "super" not in tokens  # fragmento genérico demais, descartado

    def test_nome_de_uma_palavra_so_fica_sozinho(self):
        assert gnf._name_tokens("Marvell Technology, Inc.") == ["marvell"]

    def test_nome_vazio_nao_gera_token(self):
        assert gnf._name_tokens("") == []


class TestBaseSymbol:
    @pytest.mark.parametrize("ticker,expected", [("SKHY", "SKHY"), ("SKHY.SA", "SKHY"), ("petr4.sa", "PETR4")])
    def test_remove_sufixo_de_bolsa(self, ticker, expected):
        assert gnf._base_symbol(ticker) == expected
