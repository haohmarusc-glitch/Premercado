"""
Provider adapter — wraps OpenAI-compatible APIs (OpenAI, Gemini, OpenRouter, Kimi)
and Anthropic into a single interface that agent.py can use transparently.
"""

import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from .security import mask_sensitive_data

# ── Preços por modelo (US$ por 1M tokens; referência 2026-07) ─────────────────
# cache_read/cache_write só se aplicam a provedores com prompt caching faturado
# à parte (Anthropic: write 1.25x, read ~0.1x; OpenAI: cached input a 50%).
# Modelos ausentes daqui têm custo reportado como None (desconhecido), não 0.
MODEL_PRICING: dict[str, dict[str, float]] = {
    # Preço promocional de lançamento, vale até 31/08/2026 -- depois volta pro
    # padrão $3,00/$15,00 (mesmo nível do Sonnet 4.6). Atualizar essa linha
    # quando a promoção expirar.
    "claude-sonnet-5": {"input": 2.00, "output": 10.00, "cache_read": 0.20, "cache_write": 2.50},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write": 3.75},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00, "cache_read": 0.10, "cache_write": 1.25},
    "gpt-4o": {"input": 2.50, "output": 10.00, "cache_read": 1.25},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60, "cache_read": 0.075},
    # Cache implícito do Gemini 2.5: 90% de desconto nos tokens servidos do
    # cache (automático, sem custo de escrita/armazenamento) — confirmado
    # contra o faturamento real do Google Cloud em 03/07 (estimativa antiga,
    # sem esse desconto, veio ~40% acima do valor cobrado).
    "gemini-2.5-flash-lite": {"input": 0.10, "output": 0.40, "cache_read": 0.01},
    "gemini-2.5-flash": {"input": 0.30, "output": 2.50, "cache_read": 0.03},
    # Preço do tier <=200k de contexto (nosso prompt do fluxo DAILY fica bem
    # abaixo disso); acima de 200k a Google cobra $2,50/M de entrada. Ainda
    # não confirmado contra faturamento real (modelo só entrou em uso em
    # 17/07) -- ajustar se divergir, como já foi feito com o flash em 03/07.
    "gemini-2.5-pro": {"input": 1.25, "output": 10.00, "cache_read": 0.31},
    "meta-llama/llama-3.3-70b-instruct:free": {"input": 0.0, "output": 0.0},
    "meta-llama/llama-3.1-8b-instruct:free": {"input": 0.0, "output": 0.0},
    "moonshot-v1-32k": {"input": 1.00, "output": 3.00},
    "moonshot-v1-8k": {"input": 0.20, "output": 2.00},
}


def _call_cost_usd(model: str, input_tokens: int, output_tokens: int,
                   cache_read: int, cache_write: int) -> float | None:
    p = MODEL_PRICING.get(model)
    if p is None:
        return None
    cost = (
        input_tokens * p["input"]
        + output_tokens * p["output"]
        + cache_read * p.get("cache_read", p["input"])
        + cache_write * p.get("cache_write", p["input"])
    ) / 1_000_000
    return cost


class RunUsage:
    """Acumula tokens/custo de todas as chamadas de LLM desta run (todos os
    provedores, incluindo trocas de fallback no meio). Singleton do processo —
    cada run do agente é um processo Python novo, então zera naturalmente."""

    def __init__(self):
        self._by_model: dict[tuple[str, str], dict] = {}

    def record(self, provider: str, model: str, *, input_tokens: int = 0,
               output_tokens: int = 0, cache_read: int = 0, cache_write: int = 0) -> None:
        key = (provider, model)
        entry = self._by_model.setdefault(key, {
            "provider": provider, "model": model, "calls": 0,
            "input_tokens": 0, "output_tokens": 0,
            "cache_read_tokens": 0, "cache_write_tokens": 0,
        })
        entry["calls"] += 1
        entry["input_tokens"] += input_tokens
        entry["output_tokens"] += output_tokens
        entry["cache_read_tokens"] += cache_read
        entry["cache_write_tokens"] += cache_write

    def summary(self) -> dict:
        providers = []
        totals = {"calls": 0, "input_tokens": 0, "output_tokens": 0,
                  "cache_read_tokens": 0, "cache_write_tokens": 0}
        total_cost: float | None = 0.0
        for entry in self._by_model.values():
            cost = _call_cost_usd(
                entry["model"], entry["input_tokens"], entry["output_tokens"],
                entry["cache_read_tokens"], entry["cache_write_tokens"],
            )
            providers.append({**entry, "cost_usd": cost})
            for k in totals:
                totals[k] += entry[k]
            if cost is None:
                total_cost = None  # algum modelo sem preço conhecido → total indeterminado
            elif total_cost is not None:
                total_cost += cost
        return {
            **totals,
            "total_cost_usd": round(total_cost, 6) if total_cost is not None else None,
            "providers": providers,
        }


_run_usage = RunUsage()


def get_run_usage() -> dict:
    return _run_usage.summary()

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
            "full": "claude-sonnet-5",
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
            # em produção). O 2.5-flash (não-lite) foi tentado no tier "full"
            # depois que o flash-lite abandonava o fluxo multi-turno no meio
            # (visto em produção em 03/07 — parou no turno 7 sem
            # save_observation) -- mas o 2.5-flash tem o MESMO problema em
            # escala maior: visto em produção em 17/07 completando as 12
            # rodadas do fluxo DAILY completo (17 ferramentas) sem nunca
            # chamar save_observation, mesmo após duas cobranças, gerando
            # relatório vazio ("análise incompleta", 0 observações) que ainda
            # assim volta como run "success" pro runner.ts. O tier "full" só
            # é usado quando o orçamento diário do Anthropic estoura e o
            # sistema cai pro cheapProvider (ver runner.ts,
            # getEffectiveAgentProvider) -- nesse fallback, ainda vale gastar
            # mais por chamada pra ter uma chance real de completar o fluxo
            # em vez de queimar a run inteira. Nota: o 2.5-flash tem
            # desligamento anunciado para 16/10/2026 — checar nessa época se
            # ainda for usado em algum tier.
            "full": "gemini-2.5-pro",
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

        # AGENT_MAX_RETRIES é o retry INTERNO do SDK (backoff curto, ~0.5-8s,
        # para blips rápidos de rede/servidor) — deliberadamente baixo porque
        # FallbackClient.create já tem seu PRÓPRIO retry para erros
        # transitórios sustentados (AGENT_TRANSIENT_RETRIES, backoff de
        # 5-30s). As duas camadas empilham (o outer loop chama c.create(),
        # que já esgotou o retry do SDK antes de levantar a exceção) — um
        # default alto aqui multiplicava tentativas e atraso sem coordenação
        # (visto em produção: até 3x4=12 tentativas, >100s, arriscando o
        # timeout de 10 min da run). Aplicado nos dois clientes (Anthropic e
        # OpenAI-compatível) para não ter dois orçamentos de retry divergentes.
        sdk_max_retries = int(os.environ.get("AGENT_MAX_RETRIES", "1"))

        if self.provider_name == "anthropic":
            import anthropic

            self._anthropic = anthropic.Anthropic(
                api_key=api_key,
                timeout=float(os.environ.get("API_TIMEOUT_SECONDS", "60")),
                max_retries=sdk_max_retries,
            )
            self._openai = None
        else:
            from openai import OpenAI

            self._openai = OpenAI(api_key=api_key, base_url=cfg["base_url"], max_retries=sdk_max_retries)
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

    @staticmethod
    def _with_history_cache(messages: list) -> list:
        """
        Cache incremental do HISTÓRICO (Anthropic): marca o último bloco das duas
        últimas mensagens com cache_control, sem mutar a lista original — o
        agent.py reutiliza `messages` entre turnos, e mutá-la acumularia
        breakpoints além do máximo de 4 por request.

        Por que DUAS mensagens: o lookback do cache é de no máx. 20 blocos, e um
        turno agrupado (ex.: 17 tool_use + 17 tool_result) passa disso; com um
        breakpoint também na penúltima mensagem o espaçamento fica dentro do
        limite. Total de breakpoints: 1 (system) + 1 (tools) + 2 (histórico) = 4.
        """
        out = list(messages)
        marked = 0
        for i in range(len(out) - 1, -1, -1):
            if marked == 2:
                break
            msg = out[i]
            content = msg.get("content")
            if isinstance(content, str):
                if not content.strip():
                    continue
                content = [{"type": "text", "text": content}]
            elif isinstance(content, list) and content:
                content = list(content)
            else:
                continue
            last_block = content[-1]
            if not isinstance(last_block, dict):
                continue
            content[-1] = {**last_block, "cache_control": {"type": "ephemeral"}}
            out[i] = {**msg, "content": content}
            marked += 1
        return out

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
            messages=self._with_history_cache(messages),
        )
        usage = getattr(resp, "usage", None)
        if usage is not None:
            _run_usage.record(
                "anthropic", model,
                input_tokens=getattr(usage, "input_tokens", 0) or 0,
                output_tokens=getattr(usage, "output_tokens", 0) or 0,
                cache_read=getattr(usage, "cache_read_input_tokens", 0) or 0,
                cache_write=getattr(usage, "cache_creation_input_tokens", 0) or 0,
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
        usage = getattr(resp, "usage", None)
        if usage is not None:
            prompt = getattr(usage, "prompt_tokens", 0) or 0
            details = getattr(usage, "prompt_tokens_details", None)
            cached = (getattr(details, "cached_tokens", 0) or 0) if details else 0
            _run_usage.record(
                self.provider_name, model,
                # prompt_tokens INCLUI os cacheados — separa para precificar cada parte
                input_tokens=max(prompt - cached, 0),
                output_tokens=getattr(usage, "completion_tokens", 0) or 0,
                cache_read=cached,
            )
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


def _is_transient_error(exc: Exception) -> bool:
    """Erro que tende a passar em segundos/minutos (vale re-tentar no MESMO
    provedor antes de cair para o próximo): 503 de capacidade (visto em produção
    com gemini-2.5-flash-lite pago, 'high demand') e rate-limit temporário
    upstream do OpenRouter ('temporarily rate-limited', Retry-After ~30s).
    Falta de crédito/quota mensal NÃO é transitório — cai direto no fallback."""
    msg = str(exc).lower()
    return any(
        k in msg
        for k in [
            "503",
            "unavailable",
            "overloaded",
            "high demand",
            "temporarily rate-limited",
            "try again later",
        ]
    )


_RETRY_AFTER_RE = re.compile(r"[Rr]etry[-_][Aa]fter[^0-9]{0,20}(\d{1,3})")


def _retry_after_seconds(exc: Exception) -> int | None:
    """Extrai Retry-After/retry_after_seconds do corpo do erro, se presente."""
    m = _RETRY_AFTER_RE.search(str(exc))
    return int(m.group(1)) if m else None


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

            # Erros transitórios (503 de capacidade, rate-limit com Retry-After)
            # merecem novas tentativas no MESMO provedor antes do fallback —
            # sem isso, um pico de demanda do Gemini pago derruba a run inteira
            # para os provedores gratuitos. Backoff limitado para não estourar
            # o timeout de 10 min da run. Default baixo (1) porque isso já
            # empilha com o retry interno do SDK (AGENT_MAX_RETRIES, também
            # default 1) — pior caso agora é 2x2=4 tentativas totais em vez
            # de até 3x4=12.
            transient_retries = int(os.environ.get("AGENT_TRANSIENT_RETRIES", "1"))
            last_exc: Exception | None = None
            for attempt in range(transient_retries + 1):
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
                    last_exc = exc
                    if attempt < transient_retries and _is_transient_error(exc):
                        delay = min(_retry_after_seconds(exc) or 5 * (attempt + 1), 30)
                        print(
                            f"[provider] {name} erro transitório "
                            f"(tentativa {attempt + 1}/{transient_retries + 1}) — "
                            f"aguardando {delay}s antes de re-tentar...",
                            flush=True,
                        )
                        time.sleep(delay)
                        continue
                    break

            safe_exc = mask_sensitive_data(str(last_exc))
            print(f"[provider] {name} failed: {safe_exc}", flush=True)
            if idx + 1 < len(self._order):
                print(f"[provider] trying {self._order[idx + 1]}...", flush=True)
            else:
                raise RuntimeError(
                    f"All providers exhausted. Last error: {safe_exc}"
                ) from last_exc
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
