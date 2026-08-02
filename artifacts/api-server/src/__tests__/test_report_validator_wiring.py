"""
Testes de integração da rubrica de rótulo no caminho real do relatório diário:
coleta dentro de _agent_loop e retry de correção em run().

Os testes unitários (test_report_validator.py) cobrem os gates isoladamente.
Aqui o alvo é a ligação: o snapshot precisa se preencher a partir dos
tool_results que passam pelo loop, e um relatório com rótulo violando a rubrica
precisa disparar exatamente UM retry -- sem derrubar a run se o retry falhar.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_report_validator_wiring.py -v
(conftest.py no mesmo diretório já cuida do sys.path)
"""

import json as _json

import pytest

from agent import agent as agent_module
from agent import config
from agent.provider import NormalizedResponse, TextBlock, ToolUseBlock
from agent.report_validator import new_snapshot


class _FakeClient:
    provider_name = "anthropic"
    models = {"full": "fake-model", "flash": "fake-flash"}

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(list(kwargs["messages"]))
        return self._responses.pop(0)


def test_loop_preenche_snapshot_a_partir_dos_tool_results(monkeypatch):
    """O snapshot sai do que o modelo REALMENTE recebeu -- sem refazer rede."""
    quote = {"ticker": "ARM", "change_pct": -0.8, "as_of": "2026-08-01"}
    monkeypatch.setattr(agent_module, "run_tool", lambda name, args: _json.dumps(quote))

    responses = [
        NormalizedResponse(
            content=[ToolUseBlock(id="t1", name="get_stock_data", input={"ticker": "ARM"})],
            stop_reason="tool_use",
        ),
        NormalizedResponse(
            content=[TextBlock(text="Relatório final completo " * 10)],
            stop_reason="end_turn",
        ),
    ]
    snap = new_snapshot()
    agent_module._agent_loop(
        client=_FakeClient(responses),
        model="fake-model",
        system="s",
        tools=[],
        messages=[{"role": "user", "content": "vai"}],
        max_turns=5,
        max_tokens=100,
        report_snapshot=snap,
    )

    assert snap["quotes"]["ARM"]["change_pct"] == -0.8
    assert snap["quotes"]["ARM"]["as_of"] == "2026-08-01"


def test_loop_sem_snapshot_nao_quebra(monkeypatch):
    """report_snapshot=None é o caminho dos outros modos (veredito, flash,
    carteira) -- eles não coletam nada e seguem iguais."""
    monkeypatch.setattr(agent_module, "run_tool", lambda name, args: '{"ticker": "ARM"}')
    responses = [
        NormalizedResponse(
            content=[ToolUseBlock(id="t1", name="get_stock_data", input={})],
            stop_reason="tool_use",
        ),
        NormalizedResponse(content=[TextBlock(text="ok " * 20)], stop_reason="end_turn"),
    ]
    texto = agent_module._agent_loop(
        client=_FakeClient(responses),
        model="fake-model",
        system="s",
        tools=[],
        messages=[{"role": "user", "content": "vai"}],
        max_turns=5,
        max_tokens=100,
    )
    assert "ok" in texto


def test_coleta_com_defeito_nao_derruba_a_run(monkeypatch):
    """Coleta é acessória: se ela levantar, o relatório continua saindo."""
    monkeypatch.setattr(agent_module, "run_tool", lambda name, args: '{"ticker": "ARM"}')

    def _explode(*a, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(agent_module, "collect_tool_result", _explode)

    responses = [
        NormalizedResponse(
            content=[ToolUseBlock(id="t1", name="get_stock_data", input={})],
            stop_reason="tool_use",
        ),
        NormalizedResponse(content=[TextBlock(text="ok " * 20)], stop_reason="end_turn"),
    ]
    texto = agent_module._agent_loop(
        client=_FakeClient(responses),
        model="fake-model",
        system="s",
        tools=[],
        messages=[{"role": "user", "content": "vai"}],
        max_turns=5,
        max_tokens=100,
        report_snapshot=new_snapshot(),
    )
    assert "ok" in texto


# ------------------------------------------------------------- run() ---


@pytest.fixture
def _run_isolado(monkeypatch):
    """run() sem rede: carteira de um ticker só e sem deadline suave."""
    monkeypatch.setattr(config, "PORTFOLIO_TICKERS", ["ARM"])
    monkeypatch.setattr(config, "SOFT_DEADLINE_TS", None)
    monkeypatch.setattr(config, "MAX_TOKENS", 100)
    monkeypatch.setattr(agent_module, "build_system_prompt_blocks", lambda: "sys")


def _run_tool_falso(quote: dict):
    """run() exige save_observation (require_observations=True) -- sem um
    'saved: true' o loop gasta turnos cobrando e o fake fica sem respostas."""
    def _run(name, args):
        if name == "save_observation":
            return _json.dumps({"saved": True})
        return _json.dumps(quote)
    return _run


def _respostas_com_rotulo(rotulo: str):
    # Acima de _min_report_chars(1) = 150 chars, senão o loop trata como
    # "resposta curta demais para ser relatório" e cobra continuação.
    relatorio = f"# Relatório\n\n## ARM\n\n{rotulo} — leitura do dia.\n\n" + ("blá " * 60)
    return [
        NormalizedResponse(
            content=[ToolUseBlock(id="t1", name="get_stock_data", input={"ticker": "ARM"})],
            stop_reason="tool_use",
        ),
        NormalizedResponse(
            content=[ToolUseBlock(id="t2", name="save_observation", input={"ticker": "ARM"})],
            stop_reason="tool_use",
        ),
        NormalizedResponse(content=[TextBlock(text=relatorio)], stop_reason="end_turn"),
    ]


def test_run_dispara_retry_quando_rotulo_viola_a_rubrica(monkeypatch, _run_isolado):
    quote = {"ticker": "ARM", "change_pct": -0.8, "as_of": "2026-08-01"}
    monkeypatch.setattr(agent_module, "run_tool", _run_tool_falso(quote))

    corrigido = "# Relatório\n\n## ARM\n\n🟡 — queda no dia.\n\n" + ("blá " * 60)
    responses = _respostas_com_rotulo("🟢") + [
        NormalizedResponse(content=[TextBlock(text=corrigido)], stop_reason="end_turn"),
    ]
    client = _FakeClient(responses)
    monkeypatch.setattr(agent_module, "_get_client", lambda: client)
    monkeypatch.setattr(agent_module, "run_alerts_management", lambda *a, **k: None, raising=False)

    texto = agent_module.run()

    assert "🟡" in texto, "o retorno deve ser o texto corrigido"
    # 3 chamadas do loop + 1 do retry de correção
    assert len(client.calls) == 4
    ultima = client.calls[-1][-1]["content"]
    assert "ARM" in ultima and "rubrica" in ultima


def test_run_nao_dispara_retry_quando_rotulo_esta_correto(monkeypatch, _run_isolado):
    quote = {"ticker": "ARM", "change_pct": 2.0, "as_of": "2026-08-01"}
    monkeypatch.setattr(agent_module, "run_tool", _run_tool_falso(quote))

    client = _FakeClient(_respostas_com_rotulo("🟢"))
    monkeypatch.setattr(agent_module, "_get_client", lambda: client)

    texto = agent_module.run()

    assert "🟢" in texto
    assert len(client.calls) == 3, "sem violação, nenhum retry"


def test_run_sobrevive_a_falha_no_retry(monkeypatch, _run_isolado):
    """Falha no retry mantém o texto original (com as violações logadas) em vez
    de propagar a exceção -- mesma postura de run_veredito."""
    quote = {"ticker": "ARM", "change_pct": -0.8, "as_of": "2026-08-01"}
    monkeypatch.setattr(agent_module, "run_tool", _run_tool_falso(quote))

    class _ClientQueFalhaNoRetry(_FakeClient):
        def create(self, **kwargs):
            if not self._responses:
                raise RuntimeError("provedor caiu no retry")
            return super().create(**kwargs)

    client = _ClientQueFalhaNoRetry(_respostas_com_rotulo("🟢"))
    monkeypatch.setattr(agent_module, "_get_client", lambda: client)

    texto = agent_module.run()

    assert "🟢" in texto, "fica o original quando o retry falha"
