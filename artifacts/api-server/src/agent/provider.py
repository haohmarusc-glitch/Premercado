"""
Provider adapter — wraps OpenAI-compatible APIs (OpenAI, Groq, Gemini, Kimi)
and Anthropic into a single interface that agent.py can use transparently.
"""
import json
import os
from dataclasses import dataclass, field
from typing import Any


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


# ── Provider config ───────────────────────────────────────────────────────────

PROVIDERS = {
    "anthropic": {
        "base_url": None,
        "api_key_env": "ANTHROPIC_API_KEY",
        "models": {
            "full":  "claude-sonnet-4-6",
            "flash": "claude-haiku-4-5",
            "chat":  "claude-haiku-4-5",
        },
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
        "models": {
            "full":  "gpt-4o",
            "flash": "gpt-4o-mini",
            "chat":  "gpt-4o-mini",
        },
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "api_key_env": "GROQ_API_KEY",
        "models": {
            "full":  "llama-3.1-8b-instant",
            "flash": "llama-3.1-8b-instant",
            "chat":  "llama-3.1-8b-instant",
        },
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "api_key_env": "GEMINI_API_KEY",
        "models": {
            "full":  "gemini-2.0-flash",
            "flash": "gemini-2.0-flash",
            "chat":  "gemini-2.0-flash",
        },
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
        "models": {
            "full":  "meta-llama/llama-3.3-70b-instruct:free",
            "flash": "meta-llama/llama-3.1-8b-instruct:free",
            "chat":  "meta-llama/llama-3.3-70b-instruct:free",
        },
    },
    "kimi": {
        "base_url": "https://api.moonshot.cn/v1",
        "api_key_env": "KIMI_API_KEY",
        "models": {
            "full":  "moonshot-v1-32k",
            "flash": "moonshot-v1-8k",
            "chat":  "moonshot-v1-8k",
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
                "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
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
                        tool_calls.append({
                            "id": block["id"],
                            "type": "function",
                            "function": {
                                "name": block["name"],
                                "arguments": json.dumps(block["input"], ensure_ascii=False),
                            },
                        })
            oai_msg: dict[str, Any] = {"role": "assistant", "content": " ".join(text_parts) or None}
            if tool_calls:
                oai_msg["tool_calls"] = tool_calls
            out.append(oai_msg)

        elif role == "user":
            # Check if it's tool results
            if isinstance(content, list) and content and isinstance(content[0], dict) and content[0].get("type") == "tool_result":
                for block in content:
                    out.append({
                        "role": "tool",
                        "tool_call_id": block["tool_use_id"],
                        "content": block["content"] if isinstance(block["content"], str) else json.dumps(block["content"]),
                    })
            else:
                text = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
                out.append({"role": "user", "content": text})

    return out


def _openai_response_to_normalized(response) -> NormalizedResponse:
    """Convert OpenAI response to NormalizedResponse."""
    choice = response.choices[0]
    msg = choice.message
    finish = choice.finish_reason

    content = []
    if msg.content:
        content.append(TextBlock(text=msg.content))

    if getattr(msg, "tool_calls", None):
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments)
            except Exception:
                args = {}
            content.append(ToolUseBlock(id=tc.id, name=tc.function.name, input=args))

    stop_reason = "tool_use" if finish == "tool_calls" else "end_turn"
    return NormalizedResponse(content=content, stop_reason=stop_reason)


# ── Main client ───────────────────────────────────────────────────────────────

class ProviderClient:
    def __init__(self, provider_name: str | None = None):
        self.provider_name = (provider_name or os.environ.get("AGENT_PROVIDER", "anthropic")).lower()
        cfg = PROVIDERS.get(self.provider_name)
        if not cfg:
            raise ValueError(f"Unknown provider: {self.provider_name}. Choose from: {list(PROVIDERS)}")

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

    def create(self, *, model: str, max_tokens: int, system, tools: list, messages: list) -> NormalizedResponse:
        if self._anthropic:
            return self._call_anthropic(model=model, max_tokens=max_tokens, system=system, tools=tools, messages=messages)
        else:
            return self._call_openai(model=model, max_tokens=max_tokens, system=system, tools=tools, messages=messages)

    def _call_anthropic(self, *, model, max_tokens, system, tools, messages) -> NormalizedResponse:
        # Apply Anthropic prompt caching.
        # Se `system` já vier como lista de blocos, respeitamos os cache_control
        # definidos por quem chamou (bloco fixo cacheado, bloco volátil sem cache).
        # Só fazemos o wrap automático quando vier como string simples.
        if isinstance(system, str):
            system = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        cached_tools = list(tools)
        if cached_tools:
            cached_tools[-1] = {**cached_tools[-1], "cache_control": {"type": "ephemeral"}}
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
                content.append(ToolUseBlock(id=block.id, name=block.name, input=dict(block.input)))
        stop_reason = "tool_use" if resp.stop_reason == "tool_use" else "end_turn"
        return NormalizedResponse(content=content, stop_reason=stop_reason)

    def _call_openai(self, *, model, max_tokens, system, tools, messages) -> NormalizedResponse:
        oai_messages = _anthropic_messages_to_openai(system, messages)
        oai_tools = _anthropic_tools_to_openai(tools)
        resp = self._openai.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=oai_messages,
            tools=oai_tools if oai_tools else None,
        )
        return _openai_response_to_normalized(resp)

    def _normalized_to_anthropic_content(self, resp: NormalizedResponse) -> list:
        """Convert NormalizedResponse back to Anthropic-style content list for message history."""
        result = []
        for block in resp.content:
            if isinstance(block, TextBlock):
                result.append({"type": "text", "text": block.text})
            elif isinstance(block, ToolUseBlock):
                result.append({"type": "tool_use", "id": block.id, "name": block.name, "input": block.input})
        return result


# ── Fallback chain ────────────────────────────────────────────────────────────

# Order to try when a provider fails. Can be overridden via AGENT_PROVIDER_ORDER env var.
_DEFAULT_ORDER = ["anthropic", "gemini", "openrouter", "openai", "kimi", "groq"]

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
    return any(k in msg for k in [
        "credit balance", "quota", "rate limit", "429", "insufficient_quota",
        "billing", "too many requests", "tokens", "capacity",
    ])


class FallbackClient:
    """Tries providers in order, falling back on quota/auth errors."""

    def __init__(self):
        self._order = [p for p in _provider_order() if _has_key(p)]
        if not self._order:
            raise RuntimeError("No provider API keys found. Add at least one of: ANTHROPIC_API_KEY, GROQ_API_KEY, GEMINI_API_KEY, OPENAI_API_KEY, KIMI_API_KEY")
        self._clients: dict[str, ProviderClient] = {}
        self._current_idx = 0

    @property
    def provider_name(self) -> str:
        return self._order[self._current_idx] if self._current_idx < len(self._order) else self._order[-1]

    @property
    def models(self) -> dict:
        return self._get_client(self.provider_name).models

    def _get_client(self, name: str) -> ProviderClient:
        if name not in self._clients:
            self._clients[name] = ProviderClient(name)
        return self._clients[name]

    def create(self, *, model: str, max_tokens: int, system, tools: list, messages: list,
               system_fn=None, tools_fn=None) -> NormalizedResponse:
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

            # Se este provider é diferente do primário desta chamada, o histórico
            # de `messages` acumulado (tool_use/tool_result de turnos anteriores
            # no provider original) não serve para ele — só ocupa tokens e pode
            # estourar limites baixos (ex.: Groq free tier = 6000 TPM). Trocar de
            # provider no meio de uma run já é um "recomeço" para quem assume:
            # mantemos só a primeira mensagem do usuário.
            resolved_messages = messages if name == primary_name else messages[:1]
            if name != primary_name and len(messages) > 1:
                print(
                    f"[provider] histórico truncado para {name} "
                    f"({len(messages)} -> 1 mensagem, evita estourar TPM/contexto)",
                    flush=True,
                )

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
                print(f"[provider] {name} failed: {exc}", flush=True)
                if idx + 1 < len(self._order):
                    print(f"[provider] trying {self._order[idx + 1]}...", flush=True)
                else:
                    raise RuntimeError(f"All providers exhausted. Last error: {exc}") from exc
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
