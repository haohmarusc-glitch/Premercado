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
    MODEL_PRICING,
    NormalizedResponse,
    TextBlock,
    ToolUseBlock,
    _anthropic_messages_to_openai,
    _anthropic_tools_to_openai,
    _extract_leaked_function_calls,
    _has_key,
    _is_falha_permanente,
    _is_model_not_found,
    _is_quota_error,
    _is_transient_error,
    _openai_response_to_normalized,
    _DEFAULT_ORDER,
    FallbackClient,
    _provider_order,
    _resolve_tier,
    _condense_history_for_fallback,
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

    # ── Estilo Python (Gemini): default_api.NOME(kwargs) ──────────────────────
    def test_python_style_call_recovered(self):
        text = 'print(default_api.create_alert(symbol = "MU", condition = "above", threshold_pct = 5.0, reason = "momentum"))'
        blocks, cleaned = _extract_leaked_function_calls(text)
        assert len(blocks) == 1
        assert blocks[0].name == "create_alert"
        assert blocks[0].input == {
            "symbol": "MU",
            "condition": "above",
            "threshold_pct": 5.0,
            "reason": "momentum",
        }
        assert blocks[0].id.startswith("leaked_")
        assert "default_api" not in cleaned

    def test_python_style_without_print_wrapper(self):
        text = 'default_api.create_alert(symbol="SMCI", threshold_pct=10)'
        blocks, _ = _extract_leaked_function_calls(text)
        assert len(blocks) == 1
        assert blocks[0].input == {"symbol": "SMCI", "threshold_pct": 10}

    def test_python_style_multiple_calls_in_code_fence(self):
        text = (
            "```python\n"
            'print(default_api.create_alert(symbol = "MU", condition = "above", threshold_pct = 5.0))\n'
            'print(default_api.create_alert(symbol = "CRDO", condition = "above", threshold_pct = 5.0))\n'
            "```"
        )
        blocks, cleaned = _extract_leaked_function_calls(text)
        assert len(blocks) == 2
        assert {b.input["symbol"] for b in blocks} == {"MU", "CRDO"}
        assert "default_api" not in cleaned

    def test_python_style_preserves_surrounding_report_text(self):
        text = 'Resumo do dia.\nprint(default_api.create_alert(symbol="MU", threshold_pct=5))\nFim.'
        blocks, cleaned = _extract_leaked_function_calls(text)
        assert len(blocks) == 1
        assert "Resumo do dia." in cleaned and "Fim." in cleaned


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


class TestCondenseHistoryForFallback:
    """Trocar de provider não pode custar o trabalho já pago.

    Produção 04/08: a anthropic deu timeout no turno 11, o histórico foi de 21
    mensagens para 1, e o gemini reexecutou a FASE 1 inteira -- os mesmos 7
    tools do turno 1. US$ 0,74 de coleta no lixo, e os turnos restantes não
    deram pra terminar: a run acabou em 188 caracteres, bloqueada pelo
    preflight.

    O problema nunca foi o formato do histórico -- é que jogar o formato fora
    levava o CONTEÚDO junto. Aqui ele vira texto e sobrevive.
    """

    def _sessao(self):
        return [
            {"role": "user", "content": "primeira pergunta"},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "1", "name": "get_stock_data",
                     "input": {"ticker": "NVDA"}},
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "1",
                     "content": '{"price": 206.64, "changePct": 2.93}'},
                ],
            },
        ]

    def test_preserva_a_pergunta_original(self):
        result = _condense_history_for_fallback(self._sessao())
        assert len(result) == 1
        assert "primeira pergunta" in result[0]["content"]

    def test_preserva_o_que_as_ferramentas_devolveram(self):
        """O ponto todo: o novo provider recebe o DADO, não só o aviso de que
        alguém já buscou. Sem isso ele buscaria de novo."""
        result = _condense_history_for_fallback(self._sessao())
        texto = result[0]["content"]
        assert "get_stock_data" in texto
        assert "NVDA" in texto
        assert "206.64" in texto

    def test_manda_nao_repetir(self):
        texto = _condense_history_for_fallback(self._sessao())[0]["content"]
        assert "JÁ FORAM EXECUTADAS" in texto
        assert "NÃO chame" in texto

    def test_resultado_gigante_e_cortado_por_chamada(self):
        """Cadeia de opções e série de preços passam de 100k chars sozinhas."""
        messages = self._sessao()
        messages[2]["content"][0]["content"] = "x" * 500_000
        texto = _condense_history_for_fallback(messages)[0]["content"]
        assert "...(cortado)" in texto
        assert len(texto) < 100_000

    def test_sessao_longa_respeita_o_teto_total(self):
        """O provider de fallback costuma ser o mais fraco da cadeia."""
        messages = [{"role": "user", "content": "pergunta"}]
        for i in range(500):
            messages.append({
                "role": "assistant",
                "content": [{"type": "tool_use", "id": str(i), "name": "get_news",
                             "input": {"ticker": f"T{i}"}}],
            })
            messages.append({
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": str(i),
                             "content": "y" * 2000}],
            })
        texto = _condense_history_for_fallback(messages)[0]["content"]
        assert "limite de tamanho" in texto
        assert len(texto) < 200_000

    def test_sem_ferramentas_executadas_volta_ao_comportamento_antigo(self):
        """Nada foi coletado ainda -- não há trabalho a preservar."""
        messages = [
            {"role": "user", "content": "oi"},
            {"role": "assistant", "content": [{"type": "text", "text": "olá"}]},
        ]
        result = _condense_history_for_fallback(messages)
        assert result == [{"role": "user", "content": "oi"}]

    def test_chamada_sem_resultado_aparece_como_tal(self):
        """Turno cortado no meio: o tool_use existe e o tool_result não. Dizer
        isso é melhor que omitir a chamada e o novo provider achar que nunca
        aconteceu."""
        messages = [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "9", "name": "get_news", "input": {}}]},
        ]
        texto = _condense_history_for_fallback(messages)[0]["content"]
        assert "get_news" in texto
        assert "sem resultado registrado" in texto

    def test_conteudo_em_blocos_nao_vira_string(self):
        """A primeira mensagem pode ser lista de blocos (prompt com cache)."""
        messages = self._sessao()
        messages[0] = {"role": "user", "content": [{"type": "text", "text": "pergunta"}]}
        result = _condense_history_for_fallback(messages)
        assert isinstance(result[0]["content"], list)
        assert result[0]["content"][0]["text"] == "pergunta"
        assert "get_stock_data" in result[0]["content"][-1]["text"]

    def test_empty_list_stays_empty(self):
        assert _condense_history_for_fallback([]) == []

    def test_single_message_unchanged(self):
        messages = [{"role": "user", "content": "oi"}]
        assert _condense_history_for_fallback(messages) == messages


class TestResolveTier:
    def test_known_model_resolves_to_tier(self):
        assert _resolve_tier("claude-sonnet-5") == "full"
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


class TestIsModelNotFound:
    """Modelo inexistente é erro de CONFIGURAÇÃO, e precisa ser separado de
    quota/capacidade -- foi essa confusão que escondeu o `gemini-2.5-pro`
    respondendo 404 por meses, com o log só dizendo 'trying anthropic...'."""

    @pytest.mark.parametrize(
        "msg",
        [
            "Error code: 404 - models/gemini-2.5-pro is not found for API version v1beta",
            "404 NOT_FOUND: Publisher Model not available to new users",
            "This model is no longer available to new users (404)",
        ],
    )
    def test_reconhece_modelo_inexistente(self, msg):
        assert _is_model_not_found(Exception(msg)) is True

    @pytest.mark.parametrize(
        "msg",
        [
            "429 Too Many Requests",
            "503 Service Unavailable: high demand",
            "connection reset by peer",
            # 404 de rota/endpoint, sem falar de modelo: não é este caso.
            "404 page not found",
        ],
    )
    def test_nao_confunde_com_quota_capacidade_ou_rede(self, msg):
        assert _is_model_not_found(Exception(msg)) is False

    def test_nao_colide_com_erro_transitorio(self):
        """As duas checagens precisam ser mutuamente exclusivas: um 503 de
        capacidade merece retry, um 404 de modelo nunca vai passar."""
        capacidade = Exception("503 Service Unavailable: high demand")
        assert _is_transient_error(capacidade) is True
        assert _is_model_not_found(capacidade) is False


class TestDefaultOrderContrato:
    """A ordem de fallback é o que o teto de custo precisa poder recortar --
    o runner.ts monta AGENT_PROVIDER_ORDER a partir de uma cópia desta lista
    (ver lib/agent-budget.ts, que tem o teste espelho)."""

    def test_ordem_padrao_comeca_no_anthropic(self):
        assert _DEFAULT_ORDER[0] == "anthropic"

    def test_env_sobrescreve_a_ordem_inteira(self, monkeypatch):
        """É por aqui que o rebaixamento por orçamento tira o provedor
        estourado da cadeia -- se AGENT_PROVIDER_ORDER deixasse de mandar, o
        teto voltaria a ser decorativo."""
        monkeypatch.setenv("AGENT_PROVIDER_ORDER", "gemini,openrouter,openai,kimi")
        monkeypatch.setenv("AGENT_PROVIDER", "anthropic")
        assert _provider_order() == ["gemini", "openrouter", "openai", "kimi"]
        assert "anthropic" not in _provider_order()


class TestFalhaPermanente:
    """Separa "acabou o saldo/modelo não existe" de "429 passageiro".

    As duas famílias vieram juntas na cascata de 03/08 -- gemini e openrouter
    com modelo 404, openai sem cota e kimi com a conta suspensa -- mas só uma
    delas condena o provedor. Rate limit passa em segundos; conta suspensa não
    passa hoje. Marcar um provedor como morto por rate limit passageiro seria
    pior que o problema.
    """

    @pytest.mark.parametrize("msg", [
        "Error code: 404 - models/gemini-2.5-pro is no longer available to new users",
        "404 - This model is unavailable for free. The paid version is available now",
        "Error code: 429 - insufficient_quota: You exceeded your current quota",
        "429 - Your account is suspended due to insufficient balance",
        "exceeded_current_quota_error",
        "Your credit balance is too low to access the API",
    ])
    def test_reconhece_o_que_condena(self, msg):
        assert _is_falha_permanente(Exception(msg)) is True

    @pytest.mark.parametrize("msg", [
        "429 Too Many Requests, please retry",
        "rate limit exceeded, Retry-After: 30",
        "503 Service Unavailable: high demand",
        "connection reset by peer",
    ])
    def test_nao_condena_por_erro_passageiro(self, msg):
        assert _is_falha_permanente(Exception(msg)) is False

    def test_rate_limit_e_quota_mas_nao_e_permanente(self):
        """_is_quota_error junta os dois; _is_falha_permanente precisa separar."""
        passageiro = Exception("429 Too Many Requests, please retry")
        assert _is_quota_error(passageiro) is True
        assert _is_falha_permanente(passageiro) is False


class _ClienteQueFalha:
    """ProviderClient falso: levanta o erro configurado e conta as tentativas."""

    def __init__(self, erro, models=None):
        self.erro = erro
        self.models = models or {"full": "m", "flash": "m", "chat": "m"}
        self.tentativas = 0

    def create(self, **kwargs):
        self.tentativas += 1
        raise self.erro


class TestDisjuntorDeProvedor:
    """Provedor condenado não é re-tentado no resto da run.

    Medido em produção 03/08: a cascata gemini(404) -> openrouter(404) ->
    openai(sem cota) -> kimi(suspensa) levou ~15s pra chegar em "All providers
    exhausted". Sem disjuntor, esses 15s se repetem em CADA turno que precise
    do fallback, dentro de uma run que tem prazo.
    """

    def _cliente(self, monkeypatch, erros: dict):
        monkeypatch.setenv("AGENT_PROVIDER_ORDER", ",".join(erros))
        for nome in erros:
            monkeypatch.setenv(PROVIDERS[nome]["api_key_env"], "k")
        fc = FallbackClient()
        falsos = {nome: _ClienteQueFalha(err) for nome, err in erros.items()}
        fc._clients = falsos
        monkeypatch.setattr(fc, "_get_client", lambda n: falsos[n])
        return fc, falsos

    def test_nao_re_tenta_provedor_condenado(self, monkeypatch):
        fc, falsos = self._cliente(monkeypatch, {
            "gemini": Exception("Error code: 404 - model no longer available"),
            "openai": Exception("Error code: 429 - insufficient_quota"),
        })

        # 1ª chamada: tenta os dois, ambos falham, ambos são condenados.
        with pytest.raises(RuntimeError, match="All providers exhausted"):
            fc.create(model="m", max_tokens=10, system="s", tools=[], messages=[])

        # Da 2ª em diante nem chega a tentar -- e a mensagem MUDA de propósito:
        # "esgotei tentando" e "não sobrou ninguém pra tentar" são situações
        # diferentes, e a segunda precisa nomear quem já caiu.
        for _ in range(2):
            with pytest.raises(RuntimeError, match="No providers available") as exc:
                fc.create(model="m", max_tokens=10, system="s", tools=[], messages=[])
            assert "gemini" in str(exc.value) and "openai" in str(exc.value)

        # Uma tentativa cada, não três.
        assert falsos["gemini"].tentativas == 1
        assert falsos["openai"].tentativas == 1

    def test_erro_passageiro_continua_sendo_re_tentado(self, monkeypatch):
        """O disjuntor não pode transformar instabilidade em desistência."""
        monkeypatch.setenv("AGENT_TRANSIENT_RETRIES", "0")
        fc, falsos = self._cliente(monkeypatch, {
            "gemini": Exception("429 Too Many Requests, please retry"),
        })

        for _ in range(3):
            with pytest.raises(RuntimeError):
                fc.create(model="m", max_tokens=10, system="s", tools=[], messages=[])

        assert falsos["gemini"].tentativas == 3

    def test_erro_final_lista_todos_os_condenados(self, monkeypatch):
        """Sem isso o operador só vê o motivo do ÚLTIMO da fila e não descobre
        que a cadeia inteira está fora, nem por quê -- que foi exatamente o que
        aconteceu em 03/08."""
        fc, _ = self._cliente(monkeypatch, {
            "gemini": Exception("Error code: 404 - model no longer available"),
            "kimi": Exception("account is suspended due to insufficient balance"),
        })

        with pytest.raises(RuntimeError) as exc:
            fc.create(model="m", max_tokens=10, system="s", tools=[], messages=[])

        msg = str(exc.value)
        assert "condenados nesta run" in msg
        assert "gemini" in msg and "kimi" in msg


class TestModelosConfiguradosTemPreco:
    """Todo modelo apontado por um tier precisa estar no MODEL_PRICING.

    Modelo sem preço reporta custo None, e custo None soma ZERO no teto diário
    (ver lib/agent-budget.ts) -- ou seja, um provedor mal cadastrado gasta sem
    aparecer no orçamento. É furo silencioso, do tipo que só se descobre pela
    fatura.
    """

    @pytest.mark.parametrize("provedor", sorted(PROVIDERS))
    def test_todos_os_tiers_tem_preco_conhecido(self, provedor):
        sem_preco = [
            m for m in PROVIDERS[provedor]["models"].values()
            if m not in MODEL_PRICING
        ]
        assert not sem_preco, (
            f"{provedor} aponta pra modelo sem preço: {sem_preco}. "
            "Adicione em MODEL_PRICING, senão o custo vira None e soma zero no teto."
        )

    def test_gemini_nao_aponta_mais_pro_modelo_que_da_404(self):
        """gemini-2.5-pro APARECE na listagem da API e responde 404 no uso
        ('no longer available to new users'). Foi a razão de o probe medir
        usando, em vez de ler a lista -- e este teste impede a volta."""
        assert "gemini-2.5-pro" not in PROVIDERS["gemini"]["models"].values()
