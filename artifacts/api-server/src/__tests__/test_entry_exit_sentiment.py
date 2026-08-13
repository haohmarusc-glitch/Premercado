"""
Testes de entry_exit_sentiment.py -- rótulo de sentimento das manchetes do
Estudo de Entrada e Saída via LLM barato.

Roda como módulo do pacote (`-m agent.entry_exit_sentiment`, import normal
aqui) porque provider.py usa import relativo. O cliente de LLM é mockado --
estes testes validam a montagem do prompt, a extração/validação do JSON da
resposta (modelos embrulham em markdown, inventam tickers, devolvem rótulo
fora do vocabulário) e o contrato de "falha nunca propaga".

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_entry_exit_sentiment.py -v
"""
import os
import sys

import pytest

_SRC_DIR = os.path.join(os.path.dirname(__file__), "..")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from agent import entry_exit_sentiment as ees  # noqa: E402


class _FakeResponse:
    def __init__(self, text):
        self.content = [{"type": "text", "text": text}]


class _FakeClient:
    def __init__(self, resposta):
        self._resposta = resposta
        self.models = {"flash": "modelo-flash-fake"}
        self.chamadas = []

    def create(self, **kwargs):
        self.chamadas.append(kwargs)
        return _FakeResponse(self._resposta)


STUDIES = [
    {"ticker": "NVDA", "news": [{"title": "Nvidia sobe forte", "summary": "Resultado acima do esperado"}]},
    {"ticker": "SMCI", "news": [{"title": "SMCI cai após guidance fraco"}]},
]


def test_analisar_rotula_por_ticker(monkeypatch):
    fake = _FakeClient('{"NVDA": {"sentimento": "positivo", "justificativa": "resultado forte"}, '
                       '"SMCI": {"sentimento": "negativo", "justificativa": "guidance fraco"}}')
    monkeypatch.setattr(ees, "get_client", lambda: fake)
    out = ees.analisar(STUDIES)
    assert out["NVDA"]["sentimento"] == "positivo"
    assert out["SMCI"]["sentimento"] == "negativo"
    # usou o tier flash, não o full
    assert fake.chamadas[0]["model"] == "modelo-flash-fake"
    # UMA chamada cobrindo os dois tickers, não uma por ticker
    assert len(fake.chamadas) == 1


def test_analisar_aceita_json_embrulhado_em_markdown(monkeypatch):
    fake = _FakeClient('Claro! Aqui está:\n```json\n{"NVDA": {"sentimento": "neutro", "justificativa": "agenda"}}\n```')
    monkeypatch.setattr(ees, "get_client", lambda: fake)
    out = ees.analisar([STUDIES[0]])
    assert out["NVDA"]["sentimento"] == "neutro"


def test_analisar_descarta_rotulo_fora_do_vocabulario_e_ticker_inventado(monkeypatch):
    fake = _FakeClient('{"NVDA": {"sentimento": "muito otimista", "justificativa": "x"}, '
                       '"TSLA": {"sentimento": "positivo", "justificativa": "nao pedido"}}')
    monkeypatch.setattr(ees, "get_client", lambda: fake)
    out = ees.analisar([STUDIES[0]])
    # rótulo inválido não passa; ticker que não foi pedido não entra
    assert out == {}


def test_analisar_sem_manchete_nao_chama_llm(monkeypatch):
    fake = _FakeClient("{}")
    monkeypatch.setattr(ees, "get_client", lambda: fake)
    out = ees.analisar([{"ticker": "NVDA", "news": []}, {"ticker": "SMCI"}])
    assert out == {}
    assert fake.chamadas == []


def test_extrair_json_falha_claramente_sem_objeto():
    with pytest.raises(ValueError):
        ees._extrair_json("nenhum json aqui")
