"""
Provider adapter — wraps OpenAI-compatible APIs (OpenAI, Gemini, OpenRouter, Kimi)
and Anthropic into a single interface that agent.py can use transparently.
"""

import json
import os
import re
import uuid
from dataclasses import dataclass, field
from typing import Any
from .security import mask_sensitive_data

# ── Normalized response types ─────────────────────────────────────────────────


@dataclass
class ToolUseBlock:
    type: str = "tool_use"
    id: str = ""
    name: str = ""
    input: dict = field(default_factory=dict)


@dataclass
class TextBlock:
    type: str = "text"
    text: str = ""


@dataclass
class NormalizedResponse:
    content: list
    stop_reason: str  # "tool_use" | "end_turn"


# ── Pseudo tool-call leak detection ───────────────────────────────────────────
# Alguns modelos Llama menores (ex.: visto em produção com llama-3.1-8b-instant,
# servido via OpenRouter/Kimi) às vezes não retornam tool_calls estruturado
# pela API e em vez disso "alucinam" a sintaxe de chamada de função como TEXTO
# da resposta, no formato:
#   <function=NOME>{"arg": "valor", ...}</function>
# Sem essa detecção, esse texto: (1) nunca executa a ferramenta de fato, e
# (2) vaza para o relatório final do usuário, como visto em produção.
# Mantida como proteção genérica mesmo após a remoção do Groq da cadeia de
# fallback, pois outros provedores (OpenRouter, Kimi) também servem modelos
# abertos sujeitos ao mesmo comportamento.
_FUNCTION_LEAK_RE = re.compile(r"<function=(\w+)>\s*(\{.*?\})\s*</function>", re.DOTALL)

# Modelos Gemini às vezes vazam a chamada como CÓDIGO PYTHON no texto, no estilo:
#   print(default_api.create_alert(symbol = "MU", condition = "above", ...))
# ou direto: default_api.create_alert(...). Capturamos o nome e os kwargs.
# [^()]* evita parênteses aninhados (as chamadas dessas ferramentas são planas).
_PYCALL_RE = re.compile(
    r"(?:print\s*\(\s*)?default_api\.(\w+)\s*\(([^()]*)\)\s*\)?",
    re.DOTALL,
)
_PYKW_RE = re.compile(
    r"(\w+)\s*=\s*"
    r"(\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'|[-+]?\d+\.?\d*|True|False|None)"
)


def _coerce_py_value(raw: str):
    """Converte um literal Python simples (string/num/bool/None) em valor Python."""
    if raw in ("True", "False"):
        return raw == "True"
    if raw == "None":
        return None
    if raw[:1] in ("\"", "'"):
        try:
            return raw[1:-1].encode().decode("unicode_escape")
        except Exception:
            return raw[1:-1]
    try:
        return int(raw)
    except ValueError:
        try:
            return float(raw)
        except ValueError:
            return raw


def _extract_python_style_calls(text: str) -> tuple[list[ToolUseBlock], str]:
    """Captura chamadas vazadas no estilo default_api.NOME(kwargs)."""
    blocks: list[ToolUseBlock] = []

    def _replace(match: "re.Match[str]") -> str:
        name, raw_args = match.group(1), match.group(2)
        args = {kw.group(1): _coerce_py_value(kw.group(2)) for kw in _PYKW_RE.finditer(raw_args)}
        blocks.append(
            ToolUseBlock(id=f"leaked_{uuid.uuid4().hex[:8]}", name=name, input=args)
        )
        return ""  # remove o trecho do texto visível

    cleaned = _PYCALL_RE.sub(_replace, text)
    # remove cercas de código que tenham ficado vazias após a remoção
    cleaned = re.sub(r"```(?:python)?\s*```", "", cleaned)
    return blocks, cleaned


def _extract_leaked_function_calls(text: str) -> tuple[list[ToolUseBlock], str]:
    """
    Procura por chamadas de função vazadas como texto, em dois formatos:
      1. <function=NOME>{...json...}</function>  (modelos Llama/abertos)
      2. default_api.NOME(kwargs)                (modelos Gemini)
    Retorna (lista de ToolUseBlock encontrados, texto restante sem essas chamadas).
    Se o JSON de algum match estiver malformado, ele é descartado silenciosamente
    (melhor perder uma tool call do que quebrar o turno inteiro).
    """
    blocks: list[ToolUseBlock] = []

    def _replace(match: "re.Match[str]") -> str:
        name, raw_args = match.group(1), match.group(2)
        try:
            args = json.loads(raw_args)
        except Exception:
            return match.group(0)  # JSON inválido: deixa o texto como estava
        blocks.append(
            ToolUseBlock(id=f"leaked_{uuid.uuid4().hex[:8]}", name=name, input=args)
        )
        return ""  # remove o trecho do texto visível

    cleaned = _FUNCTION_LEAK_RE.sub(_replace, text)
    py_blocks, cleaned = _extract_python_style_calls(cleaned)
    blocks.extend(py_blocks)
    return blocks, cleaned.strip()


# ── Provider config ───────────────────────────────────────────────────────────

PROVIDERS = {
    "anthropic": {
        "base_url": None,
        "api_key_env": "ANTHROPIC_API_KEY",
        "models": {
            "full": "claude-sonnet-4-6",
            "flash": "claude-haiku-4-5",
            "chat": "claude-haiku-4-5",
        },
        # Sem limite de TPM agressivo conhecido — não trunca por tamanho.
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
        "models": {
            "full": "gpt-4o",
            "flash": "gpt-4o-mini",
            "chat": "gpt-4o-mini",
        },
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "api_key_env": "GEMINI_API_KEY",
        "models": {
            # gemini-2.0-flash foi desativado pelo Google em 01/06/2026 (404
            # em produção). gemini-2.5-flash-lite é o substituto de preço
            # equivalente; note que o próprio 2.5-flash tem desligamento
            # anunciado para 16/10/2026 — vale checar de novo nessa época.
            "full": "gemini-2.5-flash-lite",
            "flash": "gemini-2.5-flash-lite",
            "chat": "gemini-2.5-flash-lite",
        },
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
        "models": {
            "full": "meta-llama/llama-3.3-70b-instruct:free",
            "flash": "meta-llama/llama-3.1-8b-instruct:free",
            "chat": "meta-llama/llama-3.3-70b-instruct:free",
        },
    },
    "kimi": {
        "base_url": "https://api.moonshot.cn/v1",
        "api_key_env": "KIMI_API_KEY",
        "models": {
            "full": "moonshot-v1-32k",
            "flash": "moonshot-v1-8k",
            "chat": "moonshot-v1-8k",
        },
    },
}

# ── Tool format converters ────────────────────────────────────────────────────


def _anthropic_tools_to_openai(tools: list) -> list:
    """Convert Anthropic tool schema to OpenAI function-calling format."""
    result = []
    for t in tools:
        entry = {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get(
                    "input_schema", {"type": "object", "properties": {}}
                ),
            },
        }
        result.append(entry)
    return result


def _anthropic_messages_to_openai(system: str | list, messages: list) -> list:
    """Convert Anthropic messages (with tool_use/tool_result) to OpenAI format."""
    # Flatten system
    if isinstance(system, list):
        sys_text = " ".join(b.get("text", "") for b in system if isinstance(b, dict))
    else:
        sys_text = system

    out = [{"role": "system", "content": sys_text}]

    for msg in messages:
        role = msg["role"]
        content = msg["content"]

        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue

        if role == "assistant":
            text_parts = []
            tool_calls = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text_parts.append(block["text"])
                    elif block.get("type") == "tool_use":
                        tool_calls.append(
                            {
                                "id": block["id"],
                                "type": "function",
                                "function": {
                                    "name": block["name"],
                                    "arguments": json.dumps(
                                        block["input"], ensure_ascii=False
                                    ),
                                },
                            }
                        )
            oai_msg: dict[str, Any] = {
                "role": "assistant",
                "content": " ".join(text_parts) or None,
            }
            if tool_calls:
                oai_msg["tool_calls"] = tool_calls
            out.append(oai_msg)

        elif role == "user":
            # Check if it's tool results
            if (
                isinstance(content, list)
                and content
                and isinstance(content[0], dict)
                and content[0].get("type") == "tool_result"
            ):
                for block in content:
                    out.append(
                        {
                            "role": "tool",
                            "tool_call_id": block["tool_use_id"],
                            "content": block["content"]
                            if isinstance(block["content"], str)
                            else json.dumps(block["content"]),
                        }
                    )
            else:
                text = " ".join(
                    b.get("text", "") if isinstance(b, dict) else str(b)
                    for b in content
                )
                out.append({"role": "user", "content": text})

    return out


def _openai_response_to_normalized(response) -> NormalizedResponse:
    """Convert OpenAI response to NormalizedResponse."""
    choice = response.choices[0]
    msg = choice.message
    finish = choice.finish_reason

    content = []
    leaked_calls: list[ToolUseBlock] = []

    if msg.content:
        leaked_calls, cleaned_text = _extract_leaked_function_calls(msg.content)
        if leaked_calls:
            print(
                f"[provider] {len(leaked_calls)} chamada(s) de função vazada(s) "
                f"como texto foram recuperadas: {[b.name for b in leaked_calls]}",
                flush=True,
            )
        if cleaned_text:
            content.append(TextBlock(text=cleaned_text))

    if getattr(msg, "tool_calls", None):
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments)
            except Exception:
                args = {}
            content.append(ToolUseBlock(id=tc.id, name=tc.function.name, input=args))

    # Tool calls recuperadas do texto contam como tool_use de verdade — sem
    # isso, save_observation (e qualquer outra ferramenta) nunca executava,
    # mesmo o modelo "pedindo" para chamá-la.
    content.extend(leaked_calls)

    stop_reason = "tool_use" if (finish == "tool_calls" or leaked_calls) else "end_turn"
    return NormalizedResponse(content=content, stop_reason=stop_reason)


# ── Main client ───────────────────────────────────────────────────────────────


def _try_recover_tool_use_failed(exc: Exception) -> "NormalizedResponse | None":
    """
    Alguns provedores (visto em produção: Groq com llama-3.1-8b-instant)
    retornam erro HTTP 400 'tool_use_failed' quando o modelo monta a chamada
    de função com sintaxe errada — colando os argumentos no nome da tool, ex.:
        get_stock_data={"ticker": "NVDA"}
    O corpo do erro inclui 'failed_generation' com o texto bruto que o modelo
    tentou emitir, no formato <function=NOME>{...}</function> OU
    <function=NOME={...}></function> (variação sem o JSON bem formado).

    Tenta recuperar uma ToolUseBlock utilizável a partir disso, para o agente
    seguir em vez de abortar a run inteira por causa de um erro de formatação
    do modelo. Retorna None se não conseguir recuperar nada (deixa a exceção
    seguir seu curso normal nesse caso).
    """
    msg = str(exc)
    if "tool_use_failed" not in msg and "tool call validation failed" not in msg:
        return None

    # Tenta extrair o campo failed_generation do corpo do erro (texto cru).
    match = re.search(r"failed_generation['\"]?\s*:\s*'((?:[^'\\]|\\.)*)'", msg)
    if not match:
        match = re.search(r'failed_generation["\']?\s*:\s*"((?:[^"\\]|\\.)*)"', msg)
    if not match:
        return None

    raw = match.group(1).encode("utf-8").decode("unicode_escape", errors="ignore")

    # Caso 1: formato normal <function=NOME>{...}</function>
    blocks, _ = _extract_leaked_function_calls(raw)
    if blocks:
        print(
            f"[provider] recuperado de tool_use_failed (formato padrão): {[b.name for b in blocks]}",
            flush=True,
        )
        return NormalizedResponse(content=blocks, stop_reason="tool_use")

    # Caso 2: formato visto em produção, sem JSON separado:
    # <function=get_stock_data={"ticker": "NVDA"}></function>
    alt_match = re.match(r"<function=(\w+)=(\{.*\})>\s*</function>", raw.strip())
    if alt_match:
        name, raw_args = alt_match.group(1), alt_match.group(2)
        try:
            args = json.loads(raw_args)
        except Exception:
            return None
        block = ToolUseBlock(
            id=f"recovered_{uuid.uuid4().hex[:8]}", name=name, input=args
        )
        print(
            f"[provider] recuperado de tool_use_failed (formato alternativo): {name}",
            flush=True,
        )
        return NormalizedResponse(content=[block], stop_reason="tool_use")

    return None


class ProviderClient:
    def __init__(self, provider_name: str | None = None):
        self.provider_name = (
            provider_name or os.environ.get("AGENT_PROVIDER", "anthropic")
        ).lower()
        cfg = PROVIDERS.get(self.provider_name)
        if not cfg:
            raise ValueError(
                f"Unknown provider: {self.provider_name}. Choose from: {list(PROVIDERS)}"
            )

        self.models = cfg["models"]
        api_key = os.environ.get(cfg["api_key_env"], "")

        if self.provider_name == "anthropic":
            import anthropic

            self._anthropic = anthropic.Anthropic(
                api_key=api_key,
                timeout=float(os.environ.get("API_TIMEOUT_SECONDS", "60")),
                max_retries=int(os.environ.get("AGENT_MAX_RETRIES", "3")),
            )
            self._openai = None
        else:
            from openai import OpenAI

            self._openai = OpenAI(api_key=api_key, base_url=cfg["base_url"])
            self._anthropic = None

    def create(
        self, *, model: str, max_tokens: int, system, tools: list, messages: list
    ) -> NormalizedResponse:
        if self._anthropic:
            return self._call_anthropic(
                model=model,
                max_tokens=max_tokens,
                system=system,
                tools=tools,
                messages=messages,
            )
        else:
            return self._call_openai(
                model=model,
                max_tokens=max_tokens,
                system=system,
                tools=tools,
                messages=messages,
            )

    def _call_anthropic(
        self, *, model, max_tokens, system, tools, messages
    ) -> NormalizedResponse:
        # Apply Anthropic prompt caching.
        # Se `system` já vier como lista de blocos, respeitamos os cache_control
        # definidos por quem chamou (bloco fixo cacheado, bloco volátil sem cache).
        # Só fazemos o wrap automático quando vier como string simples.
        if isinstance(system, str):
            system = [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ]
        cached_tools = list(tools)
        if cached_tools:
            cached_tools[-1] = {
                **cached_tools[-1],
                "cache_control": {"type": "ephemeral"},
            }
        resp = self._anthropic.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            tools=cached_tools,
            messages=messages,
        )
        content = []
        for block in resp.content:
            if block.type == "text":
                content.append(TextBlock(text=block.text))
            elif block.type == "tool_use":
                content.append(
                    ToolUseBlock(id=block.id, name=block.name, input=dict(block.input))
                )
        stop_reason = "tool_use" if resp.stop_reason == "tool_use" else "end_turn"
        return NormalizedResponse(content=content, stop_reason=stop_reason)

    def _call_openai(
        self, *, model, max_tokens, system, tools, messages
    ) -> NormalizedResponse:
        oai_messages = _anthropic_messages_to_openai(system, messages)
        oai_tools = _anthropic_tools_to_openai(tools)
        try:
            resp = self._openai.chat.completions.create(
                model=model,
                max_tokens=max_tokens,
                messages=oai_messages,
                tools=oai_tools if oai_tools else None,
            )
        except Exception as exc:
            recovered = _try_recover_tool_use_failed(exc)
            if recovered is not None:
                return recovered
            raise
        return _openai_response_to_normalized(resp)

    def _normalized_to_anthropic_content(self, resp: NormalizedResponse) -> list:
        """Convert NormalizedResponse back to Anthropic-style content list for message history."""
        result = []
        for block in resp.content:
            if isinstance(block, TextBlock):
                result.append({"type": "text", "text": block.text})
            elif isinstance(block, ToolUseBlock):
                result.append(
                    {
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    }
                )
        return result


# ── Fallback chain ────────────────────────────────────────────────────────────

# Order to try when a provider fails. Can be overridden via AGENT_PROVIDER_ORDER env var.
_DEFAULT_ORDER = ["anthropic", "gemini", "openrouter", "openai", "kimi"]


def _provider_order() -> list[str]:
    env = os.environ.get("AGENT_PROVIDER_ORDER", "")
    if env:
        return [p.strip() for p in env.split(",") if p.strip()]
    # Put AGENT_PROVIDER first, then the rest of the defaults
    primary = os.environ.get("AGENT_PROVIDER", "anthropic").lower()
    order = [primary] + [p for p in _DEFAULT_ORDER if p != primary]
    return order


def _has_key(provider_name: str) -> bool:
    cfg = PROVIDERS.get(provider_name)
    if not cfg:
        return False
    return bool(os.environ.get(cfg["api_key_env"], "").strip())


def _is_quota_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(
        k in msg
        for k in [
            "credit balance",
            "quota",
            "rate limit",
            "429",
            "insufficient_quota",
            "billing",
            "too many requests",
            "tokens",
            "capacity",
        ]
    )


def _truncate_history_for_fallback(messages: list) -> list:
    """
    Ao trocar para um provider diferente do que iniciou esta chamada, o
    histórico de tool_use/tool_result acumulado no provider original não faz
    sentido para o novo assumir de onde parou — mantemos só a primeira
    mensagem (a instrução original do usuário) e o novo provider recomeça o
    fluxo de ferramentas do zero.
    """
    return messages[:1] if messages else messages


class FallbackClient:
    """Tries providers in order, falling back on quota/auth errors."""

    def __init__(self):
        self._order = [p for p in _provider_order() if _has_key(p)]
        if not self._order:
            raise RuntimeError(
                "No provider API keys found. Add at least one of: ANTHROPIC_API_KEY, GEMINI_API_KEY, OPENROUTER_API_KEY, OPENAI_API_KEY, KIMI_API_KEY"
            )
        self._clients: dict[str, ProviderClient] = {}
        self._current_idx = 0

    @property
    def provider_name(self) -> str:
        return (
            self._order[self._current_idx]
            if self._current_idx < len(self._order)
            else self._order[-1]
        )

    @property
    def models(self) -> dict:
        return self._get_client(self.provider_name).models

    def _get_client(self, name: str) -> ProviderClient:
        if name not in self._clients:
            self._clients[name] = ProviderClient(name)
        return self._clients[name]

    def create(
        self,
        *,
        model: str,
        max_tokens: int,
        system,
        tools: list,
        messages: list,
        system_fn=None,
        tools_fn=None,
    ) -> NormalizedResponse:
        """
        system_fn: optional callable(provider_name) -> str for per-provider system prompt.
        tools_fn:  optional callable(provider_name) -> list for per-provider tools subset.
        """
        primary_name = self._order[self._current_idx]
        for idx in range(self._current_idx, len(self._order)):
            name = self._order[idx]
            c = self._get_client(name)
            tier = _resolve_tier(model)
            resolved_model = c.models.get(tier, model) if tier else model
            resolved_system = system_fn(name) if system_fn else system
            resolved_tools = tools_fn(name) if tools_fn else tools

            if name != primary_name:
                # Trocando de provider no meio desta chamada: o histórico de
                # tool_use/tool_result acumulado no provider original não faz
                # sentido para o novo assumir de onde parou — ele recomeça o
                # fluxo de ferramentas do zero.
                resolved_messages = _truncate_history_for_fallback(messages)
                if len(messages) > 1:
                    print(
                        f"[provider] histórico truncado para {name} "
                        f"({len(messages)} -> {len(resolved_messages)} mensagem(ns))",
                        flush=True,
                    )
            else:
                resolved_messages = messages

            try:
                result = c.create(
                    model=resolved_model,
                    max_tokens=max_tokens,
                    system=resolved_system,
                    tools=resolved_tools,
                    messages=resolved_messages,
                )
                if idx != self._current_idx:
                    print(f"[provider] switched to {name}", flush=True)
                    self._current_idx = idx
                return result
            except Exception as exc:
                safe_exc = mask_sensitive_data(str(exc))
                print(f"[provider] {name} failed: {safe_exc}", flush=True)
                if idx + 1 < len(self._order):
                    print(f"[provider] trying {self._order[idx + 1]}...", flush=True)
                else:
                    raise RuntimeError(
                        f"All providers exhausted. Last error: {safe_exc}"
                    ) from exc
        raise RuntimeError("No providers available")


# Tier detection: map a model name back to its tier key
_TIER_MAP: dict[str, str] = {}
for _pname, _pcfg in PROVIDERS.items():
    for _tier, _mname in _pcfg["models"].items():
        _TIER_MAP[_mname] = _tier


def _resolve_tier(model: str) -> str | None:
    return _TIER_MAP.get(model)


# ── Singleton factory ─────────────────────────────────────────────────────────

_client: FallbackClient | None = None


def get_client() -> FallbackClient:
    global _client
    if _client is None:
        _client = FallbackClient()
    return _client
