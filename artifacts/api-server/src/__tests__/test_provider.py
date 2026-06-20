"""
Testes de provider.py — focados nas funções puras (sem I/O de rede), que são
o ponto de maior complexidade e risco do agente: conversão entre o formato
Anthropic e o formato OpenAI-compatible, recuperação de tool-calls "vazadas"
como texto por modelos menores, e a lógica de seleção/fallback de provider.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_provider.py -v
(conftest.py no mesmo diretório já cuida do sys.path)
"""

import json
from types import SimpleNamespace

import pytest

from agent.provider import (
    NormalizedResponse,
    TextBlock,
    ToolUseBlock,
    _anthropic_messages_to_openai,
    _anthropic_tools_to_openai,
    _extract_leaked_function_calls,
    _has_key,
    _is_quota_error,
    _openai_response_to_normalized,
    _provider_order,
    _resolve_tier,
    _truncate_history_for_fallback,
    _try_recover_tool_use_failed,
    PROVIDERS,
)


class TestExtractLeakedFunctionCalls:
    def test_no_leak_returns_empty_and_original_text(self):
        blocks, cleaned = _extract_leaked_function_calls(
            "Texto normal sem function call."
        )
        assert blocks == []
        assert cleaned == "Texto normal sem function call."

    def test_single_leaked_call_recovered(self):
        text = '<function=get_stock_data>{"ticker": "NVDA"}</function>'
        blocks, cleaned = _extract_leaked_function_calls(text)
        assert len(blocks) == 1
        assert blocks[0].name == "get_stock_data"
        assert blocks[0].input == {"ticker": "NVDA"}
        assert blocks[0].id.startswith("leaked_")
        assert cleaned == ""

    def test_leaked_call_with_surrounding_text_is_stripped(self):
        text = 'Vou checar o preço. <function=get_stock_data>{"ticker": "MU"}</function> Aguarde.'
        blocks, cleaned = _extract_leaked_function_calls(text)
        assert len(blocks) == 1
        assert blocks[0].name == "get_stock_data"
        assert "function=" not in cleaned
        assert "Vou checar" in cleaned and "Aguarde" in cleaned

    def test_multiple_leaked_calls_in_same_text(self):
        text = (
            '<function=get_stock_data>{"ticker": "NVDA"}</function>'
            '<function=get_news>{"ticker": "NVDA", "max_items": 3}</function>'
        )
        blocks, _ = _extract_leaked_function_calls(text)
        assert len(blocks) == 2
        assert {b.name for b in blocks} == {"get_stock_data", "get_news"}

    def test_malformed_json_is_discarded_silently(self):
        text = "<function=get_stock_data>{ticker: NVDA (sem aspas)}</function>"
        blocks, cleaned = _extract_leaked_function_calls(text)
        assert blocks == []
        assert "<function=get_stock_data>" in cleaned

    def test_each_call_gets_unique_id(self):
        text = (
            '<function=get_stock_data>{"ticker": "NVDA"}</function>'
            '<function=get_stock_data>{"ticker": "MU"}</function>'
        )
        blocks, _ = _extract_leaked_function_calls(text)
        assert len(blocks) == 2
        assert blocks[0].id != blocks[1].id


class TestAnthropicToolsToOpenai:
    def test_basic_conversion(self):
        tools = [
            {
                "name": "get_stock_data",
                "description": "Retorna cotação",
                "input_schema": {
                    "type": "object",
                    "properties": {"ticker": {"type": "string"}},
                },
            }
        ]
        result = _anthropic_tools_to_openai(tools)
        assert result == [
            {
                "type": "function",
                "function": {
                    "name": "get_stock_data",
                    "description": "Retorna cotação",
                    "parameters": {
                        "type": "object",
                        "properties": {"ticker": {"type": "string"}},
                    },
                },
            }
        ]

    def test_empty_list(self):
        assert _anthropic_tools_to_openai([]) == []

    def test_missing_description_defaults_to_empty_string(self):
        tools = [{"name": "x", "input_schema": {}}]
        result = _anthropic_tools_to_openai(tools)
        assert result[0]["function"]["description"] == ""

    def test_cache_control_field_is_ignored_not_propagated(self):
        tools = [
            {
                "name": "x",
                "description": "d",
                "input_schema": {},
                "cache_control": {"type": "ephemeral"},
            }
        ]
        result = _anthropic_tools_to_openai(tools)
        assert "cache_control" not in result[0]
        assert "cache_control" not in result[0]["function"]


class TestAnthropicMessagesToOpenai:
    def test_simple_string_system_and_user_message(self):
        result = _anthropic_messages_to_openai(
            "Você é um analista.",
            [
                {"role": "user", "content": "Qual o preço da NVDA?"},
            ],
        )
        assert result[0] == {"role": "system", "content": "Você é um analista."}
        assert result[1] == {"role": "user", "content": "Qual o preço da NVDA?"}

    def test_system_as_block_list_is_flattened(self):
        system_blocks = [
            {
                "type": "text",
                "text": "Parte estável.",
                "cache_control": {"type": "ephemeral"},
            },
            {"type": "text", "text": "Parte volátil."},
        ]
        result = _anthropic_messages_to_openai(system_blocks, [])
        assert result[0]["content"] == "Parte estável. Parte volátil."

    def test_assistant_message_with_tool_use_converted_to_tool_calls(self):
        messages = [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Vou checar."},
                    {
                        "type": "tool_use",
                        "id": "call_1",
                        "name": "get_stock_data",
                        "input": {"ticker": "NVDA"},
                    },
                ],
            }
        ]
        result = _anthropic_messages_to_openai("sys", messages)
        assistant_msg = result[1]
        assert assistant_msg["role"] == "assistant"
        assert assistant_msg["content"] == "Vou checar."
        assert len(assistant_msg["tool_calls"]) == 1
        assert assistant_msg["tool_calls"][0]["id"] == "call_1"
        assert assistant_msg["tool_calls"][0]["function"]["name"] == "get_stock_data"
        assert json.loads(assistant_msg["tool_calls"][0]["function"]["arguments"]) == {
            "ticker": "NVDA"
        }

    def test_assistant_message_without_text_has_none_content(self):
        messages = [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call_1",
                        "name": "get_news",
                        "input": {},
                    },
                ],
            }
        ]
        result = _anthropic_messages_to_openai("sys", messages)
        assert result[1]["content"] is None

    def test_user_message_with_tool_result_converted_to_tool_role(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call_1",
                        "content": '{"price": 100}',
                    },
                ],
            }
        ]
        result = _anthropic_messages_to_openai("sys", messages)
        assert result[1] == {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": '{"price": 100}',
        }

    def test_user_message_with_non_string_tool_result_content_is_json_encoded(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call_1",
                        "content": {"price": 100},
                    },
                ],
            }
        ]
        result = _anthropic_messages_to_openai("sys", messages)
        assert json.loads(result[1]["content"]) == {"price": 100}

    def test_plain_user_text_message_as_block_list(self):
        messages = [{"role": "user", "content": [{"type": "text", "text": "Olá"}]}]
        result = _anthropic_messages_to_openai("sys", messages)
        assert result[1] == {"role": "user", "content": "Olá"}


def _fake_openai_response(content=None, tool_calls=None, finish_reason="stop"):
    """Monta um objeto que imita response.choices[0].message/finish_reason do SDK OpenAI."""
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice])


def _fake_tool_call(call_id, name, arguments_json):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments_json),
    )


class TestOpenaiResponseToNormalized:
    def test_plain_text_response(self):
        resp = _fake_openai_response(content="Olá, tudo bem?", finish_reason="stop")
        result = _openai_response_to_normalized(resp)
        assert result.stop_reason == "end_turn"
        assert len(result.content) == 1
        assert isinstance(result.content[0], TextBlock)
        assert result.content[0].text == "Olá, tudo bem?"

    def test_structured_tool_call_response(self):
        tc = _fake_tool_call("call_1", "get_stock_data", '{"ticker": "NVDA"}')
        resp = _fake_openai_response(
            content=None, tool_calls=[tc], finish_reason="tool_calls"
        )
        result = _openai_response_to_normalized(resp)
        assert result.stop_reason == "tool_use"
        assert len(result.content) == 1
        assert isinstance(result.content[0], ToolUseBlock)
        assert result.content[0].name == "get_stock_data"
        assert result.content[0].input == {"ticker": "NVDA"}

    def test_malformed_tool_call_arguments_default_to_empty_dict(self):
        tc = _fake_tool_call("call_1", "get_news", "{not valid json")
        resp = _fake_openai_response(
            content=None, tool_calls=[tc], finish_reason="tool_calls"
        )
        result = _openai_response_to_normalized(resp)
        assert result.content[0].input == {}

    def test_leaked_function_call_in_text_recovered_as_tool_use(self):
        resp = _fake_openai_response(
            content='<function=get_stock_data>{"ticker": "MU"}</function>',
            tool_calls=None,
            finish_reason="stop",
        )
        result = _openai_response_to_normalized(resp)
        assert result.stop_reason == "tool_use"
        tool_blocks = [b for b in result.content if isinstance(b, ToolUseBlock)]
        assert len(tool_blocks) == 1
        assert tool_blocks[0].name == "get_stock_data"

    def test_leaked_call_plus_real_text_both_preserved(self):
        resp = _fake_openai_response(
            content='Aqui está: <function=get_news>{"ticker": "NVDA"}</function>',
            tool_calls=None,
            finish_reason="stop",
        )
        result = _openai_response_to_normalized(resp)
        text_blocks = [b for b in result.content if isinstance(b, TextBlock)]
        tool_blocks = [b for b in result.content if isinstance(b, ToolUseBlock)]
        assert len(text_blocks) == 1
        assert "Aqui está" in text_blocks[0].text
        assert len(tool_blocks) == 1

    def test_empty_content_and_no_tool_calls_is_end_turn_with_no_blocks(self):
        resp = _fake_openai_response(
            content=None, tool_calls=None, finish_reason="stop"
        )
        result = _openai_response_to_normalized(resp)
        assert result.stop_reason == "end_turn"
        assert result.content == []


class TestTryRecoverToolUseFailed:
    def test_unrelated_exception_returns_none(self):
        assert _try_recover_tool_use_failed(Exception("rate limit exceeded")) is None

    def test_standard_format_recovered(self):
        exc = Exception(
            "Error code: 400 - {'error': {'message': \"tool_use_failed: "
            'failed_generation: \'<function=get_stock_data>{\\"ticker\\": \\"NVDA\\"}</function>\'"}}'
        )
        result = _try_recover_tool_use_failed(exc)
        assert result is not None
        assert result.stop_reason == "tool_use"
        assert result.content[0].name == "get_stock_data"

    def test_no_failed_generation_field_returns_none(self):
        exc = Exception("tool_use_failed but no failed_generation field present here")
        assert _try_recover_tool_use_failed(exc) is None

    def test_alternate_format_without_separate_json_recovered(self):
        raw = '<function=get_stock_data={"ticker": "NVDA"}></function>'
        exc_msg = (
            "tool call validation failed: failed_generation: '"
            + raw.replace("'", "\\'")
            + "'"
        )
        exc = Exception(exc_msg)
        result = _try_recover_tool_use_failed(exc)
        assert result is not None
        assert result.content[0].name == "get_stock_data"
        assert result.content[0].input == {"ticker": "NVDA"}


class TestProviderOrder:
    def test_explicit_env_override(self, monkeypatch):
        monkeypatch.setenv("AGENT_PROVIDER_ORDER", "openai, gemini , anthropic")
        assert _provider_order() == ["openai", "gemini", "anthropic"]

    def test_default_order_with_primary_first(self, monkeypatch):
        monkeypatch.delenv("AGENT_PROVIDER_ORDER", raising=False)
        monkeypatch.setenv("AGENT_PROVIDER", "gemini")
        order = _provider_order()
        assert order[0] == "gemini"
        assert order.count("gemini") == 1
        assert set(order) == {"anthropic", "gemini", "openrouter", "openai", "kimi"}

    def test_default_primary_is_anthropic(self, monkeypatch):
        monkeypatch.delenv("AGENT_PROVIDER_ORDER", raising=False)
        monkeypatch.delenv("AGENT_PROVIDER", raising=False)
        assert _provider_order()[0] == "anthropic"


class TestHasKey:
    def test_returns_true_when_env_set(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test123")
        assert _has_key("anthropic") is True

    def test_returns_false_when_env_missing(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert _has_key("anthropic") is False

    def test_returns_false_when_env_blank(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "   ")
        assert _has_key("gemini") is False

    def test_unknown_provider_returns_false(self):
        assert _has_key("nao_existe") is False


class TestIsQuotaError:
    @pytest.mark.parametrize(
        "msg",
        [
            "Error: insufficient_quota",
            "429 Too Many Requests",
            "rate limit exceeded, please retry",
            "Your credit balance is too low",
            "billing issue on this account",
        ],
    )
    def test_recognizes_quota_indicators(self, msg):
        assert _is_quota_error(Exception(msg)) is True

    def test_unrelated_error_is_not_quota(self):
        assert _is_quota_error(Exception("connection reset by peer")) is False


class TestTruncateHistoryForFallback:
    def test_keeps_only_first_message(self):
        messages = [
            {"role": "user", "content": "primeira pergunta"},
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "1", "name": "x", "input": {}}],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "1", "content": "ok"}
                ],
            },
        ]
        result = _truncate_history_for_fallback(messages)
        assert len(result) == 1
        assert result[0]["content"] == "primeira pergunta"

    def test_empty_list_stays_empty(self):
        assert _truncate_history_for_fallback([]) == []

    def test_single_message_unchanged(self):
        messages = [{"role": "user", "content": "oi"}]
        assert _truncate_history_for_fallback(messages) == messages


class TestResolveTier:
    def test_known_model_resolves_to_tier(self):
        assert _resolve_tier("claude-sonnet-4-6") == "full"
        assert _resolve_tier("claude-haiku-4-5") in ("flash", "chat")

    def test_unknown_model_returns_none(self):
        assert _resolve_tier("modelo-que-nao-existe-em-nenhum-provider") is None


class TestProvidersConfig:
    def test_all_providers_have_required_keys(self):
        for name, cfg in PROVIDERS.items():
            assert "api_key_env" in cfg, f"{name} sem api_key_env"
            assert "models" in cfg, f"{name} sem models"
            for tier in ("full", "flash", "chat"):
                assert tier in cfg["models"], f"{name} sem tier '{tier}'"

    def test_anthropic_has_no_base_url(self):
        assert PROVIDERS["anthropic"]["base_url"] is None

    def test_openai_compatible_providers_have_base_url(self):
        for name in ("openai", "gemini", "openrouter", "kimi"):
            assert PROVIDERS[name]["base_url"], f"{name} sem base_url"
