"""
Testes de agent.py::_agent_loop — cobre o bug de produção em que blocos
tool_use presentes na resposta ficavam órfãos no histórico sempre que o
stop_reason normalizado não era literalmente "tool_use" (ex.: Anthropic
retornando "max_tokens"/"pause_turn" com tool_use já completo antes do
corte). Isso deixava a mensagem seguinte sem tool_result correspondente,
e a chamada seguinte à API quebrava com 400 invalid_request_error
("tool_use ids were found without tool_result blocks"). Bug visto em
produção com claude-sonnet-5 em 17/07.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_agent_loop.py -v
(conftest.py no mesmo diretório já cuida do sys.path)
"""

import json as _json
import time

from agent import agent as agent_module
from agent.provider import NormalizedResponse, TextBlock, ToolUseBlock


class _FakeClient:
    """Devolve uma sequência fixa de respostas, uma por chamada a .create()."""

    provider_name = "anthropic"

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        # messages é mutado in-place pelo loop (append) -- precisa copiar a
        # lista aqui, senão todas as entradas de self.calls acabam apontando
        # pro mesmo objeto (o estado final), mascarando o snapshot de cada
        # chamada.
        self.calls.append(list(kwargs["messages"]))
        return self._responses.pop(0)


def test_tool_use_block_resolved_even_when_stop_reason_not_tool_use(monkeypatch):
    """Reproduz o bug: resp.content tem um ToolUseBlock mas o stop_reason já
    veio normalizado como "end_turn" (caso real: a Anthropic mandou
    "max_tokens" com um tool_use completo antes do corte). O loop precisa
    gerar o tool_result mesmo assim, senão a próxima mensagem enviada à API
    fica com um tool_use órfão."""
    monkeypatch.setattr(agent_module, "run_tool", lambda name, args: '{"ok": true}')

    responses = [
        NormalizedResponse(
            content=[ToolUseBlock(id="toolu_1", name="get_stock_data", input={})],
            stop_reason="end_turn",  # bug real: stop_reason != "tool_use" mas há tool_use
        ),
        NormalizedResponse(
            content=[TextBlock(text="Relatório final completo " * 10)],
            stop_reason="end_turn",
        ),
    ]
    client = _FakeClient(responses)

    result = agent_module._agent_loop(
        client=client,
        model="claude-sonnet-5",
        system="system prompt",
        tools=[],
        messages=[{"role": "user", "content": "start"}],
        max_turns=5,
        max_tokens=1024,
    )

    assert "Relatório final completo" in result
    # A 2a chamada à API precisa ter recebido um tool_result pro tool_use da
    # 1a resposta, senão a Anthropic rejeita a mensagem com 400.
    second_call_messages = client.calls[1]
    assistant_msg = second_call_messages[-2]
    tool_result_msg = second_call_messages[-1]
    assert assistant_msg["role"] == "assistant"
    tool_use_ids = {b["id"] for b in assistant_msg["content"] if b["type"] == "tool_use"}
    assert tool_use_ids == {"toolu_1"}
    assert tool_result_msg["role"] == "user"
    result_ids = {b["tool_use_id"] for b in tool_result_msg["content"] if b["type"] == "tool_result"}
    assert result_ids == tool_use_ids


def test_normal_tool_use_turn_still_works(monkeypatch):
    """Garante que o caminho comum (stop_reason == "tool_use") não regrediu."""
    monkeypatch.setattr(agent_module, "run_tool", lambda name, args: '{"ok": true}')

    responses = [
        NormalizedResponse(
            content=[ToolUseBlock(id="toolu_a", name="get_news", input={})],
            stop_reason="tool_use",
        ),
        NormalizedResponse(
            content=[TextBlock(text="Relatório final completo " * 10)],
            stop_reason="end_turn",
        ),
    ]
    client = _FakeClient(responses)

    result = agent_module._agent_loop(
        client=client,
        model="claude-sonnet-5",
        system="system prompt",
        tools=[],
        messages=[{"role": "user", "content": "start"}],
        max_turns=5,
        max_tokens=1024,
    )

    assert "Relatório final completo" in result
    assert len(client.calls) == 2


def test_multi_tool_call_turn_runs_in_parallel_and_preserves_result_mapping(monkeypatch):
    """As ferramentas de um turno agora rodam em paralelo (ThreadPoolExecutor)
    pra evitar o timeout de processo em runs com muitos ativos (cada tool call
    de rede levava vários segundos, e eram executadas em série). Este teste
    garante que, mesmo com tempos de resposta diferentes por ferramenta (a
    mais lenta termina por último), cada tool_result acaba pareado com o
    tool_use_id correto -- e que roda de fato em paralelo (tempo total ~=
    max(delays), não soma dos delays)."""
    delays = {"toolu_slow": 0.15, "toolu_fast": 0.01, "toolu_mid": 0.05}

    def fake_run_tool(name, args):
        time.sleep(delays[args["id"]])
        return _json.dumps({"id": args["id"]})

    monkeypatch.setattr(agent_module, "run_tool", fake_run_tool)

    blocks = [
        ToolUseBlock(id="toolu_slow", name="get_options_data", input={"id": "toolu_slow"}),
        ToolUseBlock(id="toolu_fast", name="get_options_data", input={"id": "toolu_fast"}),
        ToolUseBlock(id="toolu_mid", name="get_options_data", input={"id": "toolu_mid"}),
    ]
    responses = [
        NormalizedResponse(content=blocks, stop_reason="tool_use"),
        NormalizedResponse(content=[TextBlock(text="Relatório final completo " * 10)], stop_reason="end_turn"),
    ]
    client = _FakeClient(responses)

    start = time.monotonic()
    result = agent_module._agent_loop(
        client=client,
        model="claude-sonnet-5",
        system="system prompt",
        tools=[],
        messages=[{"role": "user", "content": "start"}],
        max_turns=5,
        max_tokens=1024,
    )
    elapsed = time.monotonic() - start

    assert "Relatório final completo" in result
    # Em série seria >= 0.15+0.01+0.05 = 0.21s; em paralelo fica perto do
    # maior delay (0.15s). Margem generosa pra não flakar em CI lento.
    assert elapsed < 0.19

    second_call_messages = client.calls[1]
    tool_result_msg = second_call_messages[-1]
    by_id = {b["tool_use_id"]: _json.loads(b["content"])["id"] for b in tool_result_msg["content"]}
    assert by_id == {"toolu_slow": "toolu_slow", "toolu_fast": "toolu_fast", "toolu_mid": "toolu_mid"}
    # Ordem no histórico segue a ordem dos tool_use blocks, não a ordem de
    # conclusão (fast terminou primeiro, mas slow ainda deve vir primeiro).
    assert [b["tool_use_id"] for b in tool_result_msg["content"]] == ["toolu_slow", "toolu_fast", "toolu_mid"]
