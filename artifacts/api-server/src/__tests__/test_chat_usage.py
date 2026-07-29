"""
Testes de agent.py::run_chat_stream -- cobre a linha USAGE:{json} adicionada
pra dar visibilidade de custo às chamadas do chat (mesmo padrão de
emit_usage() em run_agent.py, usado pelas runs diárias do agente).

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_chat_usage.py -v
(conftest.py no mesmo diretório já cuida do sys.path)
"""

import json as _json

from agent import agent as agent_module
from agent.provider import NormalizedResponse, TextBlock


class _FakeClient:
    provider_name = "anthropic"
    models = {"chat": "claude-sonnet-5"}

    def __init__(self, text: str):
        self._text = text

    def create(self, **kwargs):
        return NormalizedResponse(
            content=[TextBlock(text=self._text)],
            stop_reason="end_turn",
        )


def test_run_chat_stream_emits_usage_line_when_calls_recorded(monkeypatch, capsys):
    monkeypatch.setattr(agent_module, "_get_client", lambda: _FakeClient("Olá!"))
    monkeypatch.setattr(agent_module, "build_chat_prompt", lambda: "system prompt")

    fake_usage = {
        "calls": 2, "input_tokens": 500, "output_tokens": 80,
        "cache_read_tokens": 0, "cache_write_tokens": 0,
        "total_cost_usd": 0.0013,
        "providers": [{"provider": "anthropic", "model": "claude-sonnet-5"}],
    }
    monkeypatch.setattr("agent.provider.get_run_usage", lambda: fake_usage)

    agent_module.run_chat_stream("oi", history=[])

    out = capsys.readouterr().out
    usage_lines = [ln for ln in out.splitlines() if ln.startswith("USAGE:")]
    assert len(usage_lines) == 1
    parsed = _json.loads(usage_lines[0][len("USAGE:"):])
    assert parsed == fake_usage

    result_lines = [ln for ln in out.splitlines() if ln.startswith("RESULT:")]
    assert len(result_lines) == 1
    assert _json.loads(result_lines[0][len("RESULT:"):]) == "Olá!"


def test_run_chat_stream_skips_usage_line_when_no_calls_recorded(monkeypatch, capsys):
    monkeypatch.setattr(agent_module, "_get_client", lambda: _FakeClient("Olá!"))
    monkeypatch.setattr(agent_module, "build_chat_prompt", lambda: "system prompt")
    monkeypatch.setattr(
        "agent.provider.get_run_usage",
        lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0,
                  "cache_read_tokens": 0, "cache_write_tokens": 0,
                  "total_cost_usd": 0.0, "providers": []},
    )

    agent_module.run_chat_stream("oi", history=[])

    out = capsys.readouterr().out
    assert not any(ln.startswith("USAGE:") for ln in out.splitlines())
