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
            "full":  "llama-3.3-70b-versatile",
            "flash": "llama-3.1-8b-instant",
            "chat":  "llama-3.3-70b-versatile",
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
        resp = self._anthropic.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            tools=tools,
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


# ── Singleton factory ─────────────────────────────────────────────────────────

_client: ProviderClient | None = None

def get_client() -> ProviderClient:
    global _client
    if _client is None:
        _client = ProviderClient()
    return _client
