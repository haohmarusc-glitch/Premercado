"""
Provider adapter — wraps OpenAI-compatible APIs (OpenAI, Gemini, OpenRouter, Kimi, DeepSeek)
and Anthropic into a single interface that agent.py can use transparently.
"""

import json
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from .security import mask_sensitive_data

# ── Preços por modelo (US$ por 1M tokens; referência 2026-07/08) ───────────────
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
    # DeepSeek V4 (oficial ago/2026). Cache hit de input é ~$0.0028 (flash) /
    # ~$0.0036 (pro); usamos cache_read ≈ 2% do input miss para aproximar.
    "deepseek-v4-flash": {"input": 0.14, "output": 0.28, "cache_read": 0.0028},
    "deepseek-v4-pro": {"input": 0.435, "output": 0.87, "cache_read": 0.0036},
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


def texto_da_resposta(resp) -> str:
    """Concatena os blocos de texto de uma NormalizedResponse.

    Mora aqui porque o formato do bloco é detalhe DESTE módulo: os dois
    caminhos (_call_anthropic e _openai_response_to_normalized) montam
    dataclasses TextBlock, com acesso por ATRIBUTO. Consumidor que checava
    `isinstance(b, dict)` extraía string vazia sempre — e extração vazia não
    parece bug, parece "o modelo não respondeu" (foi o que aconteceu com o
    sentimento do Estudo, que engole a falha, e com a Análise com IA, que ao
    menos avisava "resposta curta demais").

    Aceita os três formatos plausíveis (dataclass, dict e string crua) para
    que uma futura mudança de normalização aqui não quebre os consumidores
    de novo.
    """
    partes = []
    for b in getattr(resp, "content", None) or []:
        if isinstance(b, str):
            partes.append(b)
        elif isinstance(b, dict):
            if b.get("type") == "text":
                partes.append(str(b.get("text") or ""))
        elif getattr(b, "type", None) == "text":
            partes.append(str(getattr(b, "text", "") or ""))
    return " ".join(p for p in partes if p).strip()


@dataclass
class NormalizedResponse:
    content: list
    stop_reason: str  # "tool_use" | "end_turn"
    # Motivo de parada CRU do provedor, antes de achatar pros dois valores
    # acima. Existe porque a normalização apagava a diferença entre "o modelo
    # terminou" e "eu cortei no meio por max_tokens" -- e o corte no meio de um
    # tool_use deixa o JSON de input incompleto, que chega às ferramentas como
    # {} e vira TypeError de argumento faltando. Visto em produção 03/08 com
    # get_technical_indicators e get_short_interest, sem NENHUM sinal do corte
    # em lugar nenhum: só o sintoma, a 3 camadas de distância da causa.
    # Anthropic usa "max_tokens"; a camada OpenAI-compat usa "length".
    raw_stop_reason: str = ""
    # DeepSeek (modo "thinking", ex.: deepseek-v4-pro) exige que o
    # reasoning_content de um turno anterior seja ecoado de volta na próxima
    # chamada -- sem isso a API rejeita com 400 "reasoning_content in the
    # thinking mode must be passed back". Anthropic/demais provedores nunca
    # preenchem este campo (fica None).
    reasoning_content: str | None = None


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
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "api_key_env": "DEEPSEEK_API_KEY",
        "models": {
            # V4 (oficial 2026). Pro = melhor qualidade/raciocínio; Flash =
            # forte em agent/tool-calling e bem mais barato (atualizado 31/07).
            "full": "deepseek-v4-pro",
            "flash": "deepseek-v4-flash",
            "chat": "deepseek-v4-flash",
        },
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
            # MEDIDO em 03/08 com probe_providers.py, contra a chave real:
            # dos 41 candidatos servidos pela camada OpenAI-compat, UM só
            # sustentou os dois turnos de tool calling -- o gemini-2.5-flash.
            #
            # Por que a família 3.x inteira reprovou, embora esteja listada:
            #   "Function call is missing a thought_signature in functionCall
            #    parts. This is required for tools to work correctly"
            # O campo thought_signature é exigido pelos modelos novos e a
            # camada OpenAI-compat não o transporta. Não é modelo ruim: é
            # incompatibilidade do shim. Enquanto isso não mudar, 3.x não
            # serve por este caminho, por melhor que seja.
            #
            # E o gemini-2.5-pro, que estava aqui no "full", RESPONDE na
            # listagem e dá 404 no uso ("no longer available to new users").
            # É a razão de o probe medir usando, não lendo a lista.
            #
            # Histórico que continua valendo como aviso: o 2.5-flash-lite
            # abandonou o fluxo multi-turno em 03/07 (parou no turno 7 sem
            # save_observation), e o próprio 2.5-flash fez o mesmo em 17/07 --
            # completou as 12 rodadas do fluxo DAILY sem nunca chamar
            # save_observation. Ou seja: o que está aqui é o MENOS RUIM
            # medido, não um modelo confiável.
            #
            # Ainda assim é melhor que o 404: com 404 a run morre na hora; com
            # o 2.5-flash existe chance de completar, e se ele repetir o vazio
            # de julho o preflight BLOQUEIA o e-mail (report-preflight.ts,
            # RELATORIO_VAZIO) em vez de entregar relatório oco -- proteção
            # que não existia em julho.
            #
            # Os três tiers apontam pro mesmo modelo porque foi o único
            # aprovado; o flash-lite reprovou no mesmo teste.
            #
            # Nota: o 2.5-flash tem desligamento anunciado para 16/10/2026 --
            # rodar o probe de novo antes disso.
            "full": "gemini-2.5-flash",
            "flash": "gemini-2.5-flash",
            "chat": "gemini-2.5-flash",
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
            reasoning_content = None
            for block in content:
                if isinstance(block, dict):
                    if block.get("reasoning_content"):
                        reasoning_content = block["reasoning_content"]
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
            # DeepSeek em modo thinking exige este campo de volta no turno
            # seguinte (ver comentário em NormalizedResponse.reasoning_content).
            # Outros provedores OpenAI-compat simplesmente ignoram o campo extra.
            if reasoning_content:
                oai_msg["reasoning_content"] = reasoning_content
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
                file=sys.stderr,
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
    # getattr cobre o caso "atributo não existe no schema do SDK"; model_extra
    # cobre o caso "existe mas o Pydantic do SDK só expõe campos fora do
    # schema oficial via extra" -- reasoning_content é extensão do DeepSeek,
    # não faz parte do schema oficial da OpenAI que o SDK openai modela.
    reasoning_content = getattr(msg, "reasoning_content", None)
    if not reasoning_content:
        extra = getattr(msg, "model_extra", None) or {}
        reasoning_content = extra.get("reasoning_content")
    return NormalizedResponse(
        content=content, stop_reason=stop_reason, raw_stop_reason=str(finish or ""),
        reasoning_content=reasoning_content,
    )


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
            file=sys.stderr,
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
            file=sys.stderr,
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

    @staticmethod
    def _strip_reasoning_content(messages: list) -> list:
        """Remove a chave reasoning_content dos blocos antes de mandar pra
        Anthropic -- ela só existe pra ecoar de volta pro DeepSeek (ver
        NormalizedResponse.reasoning_content) e a API da Anthropic é estrita
        sobre chaves desconhecidas em content blocks."""
        out = []
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, list):
                content = [
                    {k: v for k, v in b.items() if k != "reasoning_content"}
                    if isinstance(b, dict) else b
                    for b in content
                ]
                msg = {**msg, "content": content}
            out.append(msg)
        return out

    def _call_anthropic(
        self, *, model, max_tokens, system, tools, messages
    ) -> NormalizedResponse:
        messages = self._strip_reasoning_content(messages)
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
        return NormalizedResponse(
            content=content, stop_reason=stop_reason,
            raw_stop_reason=str(getattr(resp, "stop_reason", "") or ""),
        )

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

# TODO NADA aqui vai para stdout. Descoberto em 18/08/2026: estas linhas eram
# impressas em stdout, e em analise_rapida_ia.py o stdout é CONTRATUALMENTE do
# JSON final (o Node o parseia). O efeito era duplo e os dois lados doíam:
#
#   - o diagnóstico sumia. Toda investigação daquele dia -- "por que o
#     Anthropic não respondeu?" -- esbarrou num `grep [provider]` vazio,
#     porque as linhas estavam misturadas ao JSON, não no log;
#   - e poluíam o pipe. O "parse resiliente" da rota (tentar a última linha
#     não-vazia quando o bloco todo falha) foi escrito para sobreviver a
#     exatamente esta poluição -- tratava o sintoma, com a causa aqui.
#
# Regra do projeto, sem exceção: stdout é do resultado, stderr é do diagnóstico.

# Order to try when a provider fails. Can be overridden via AGENT_PROVIDER_ORDER env var.
_DEFAULT_ORDER = ["anthropic", "deepseek", "gemini", "openrouter", "openai", "kimi"]


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


def _is_model_not_found(exc: Exception) -> bool:
    """Modelo configurado não existe / não está mais disponível para esta chave.

    Categoria diferente de quota e de erro transitório: nenhuma espera resolve e
    nenhum outro provedor é a correção -- o conserto é editar PROVIDERS. Existe
    porque isso aconteceu de verdade e ficou invisível: o `gemini-2.5-pro` do
    tier "full" passou a responder 404 ("no longer available to new users") e o
    log só mostrava o blob do erro seguido de "trying anthropic...", que era
    exatamente o provedor que o teto de custo tentava evitar. Nomear a causa é
    o que separa "provedor instável" de "configuração quebrada".
    """
    msg = str(exc).lower()
    if "404" not in msg and "not_found" not in msg and "not found" not in msg:
        return False
    return any(k in msg for k in ["model", "publisher", "not available", "no longer available"])


# Sinais de conta esgotada/suspensa. Diferente de "429 rate limit", que passa
# em segundos: aqui não há saldo, e nenhuma espera dentro desta run resolve.
_SEM_SALDO = (
    "insufficient_quota",
    "insufficient balance",
    "suspended",
    "credit balance is too low",
    "exceeded_current_quota",
)


def _is_falha_permanente(exc: Exception) -> bool:
    """Erro que condena o provedor pelo RESTO da run, não só por esta chamada.

    Duas famílias, ambas vistas em produção 03/08 na mesma cascata:
      - modelo inexistente (gemini-2.5-pro 404; llama-3.3-70b:free saiu do
        tier grátis do OpenRouter);
      - conta sem saldo (openai insufficient_quota; kimi suspensa).

    Por que separar de _is_quota_error: aquele trata "429/rate limit" junto com
    "sem crédito", e os dois têm prazos opostos. Rate limit passa em segundos e
    merece nova tentativa; conta suspensa não passa hoje. Marcar um provedor
    como morto por causa de um rate limit passageiro seria pior que o problema.
    """
    if _is_model_not_found(exc):
        return True
    msg = str(exc).lower()
    return any(k in msg for k in _SEM_SALDO)


# Teto por resultado no resumo do fallback. Resultado de ferramenta aqui é JSON
# de cadeia de opções, série de preços, lista de manchetes -- alguns passam de
# 100k caracteres sozinhos. O corte guarda o começo, que é onde ficam os campos
# resumidos que o modelo de fato cita no relatório.
_FALLBACK_RESULTADO_MAX_CHARS = 3000
# Teto do resumo inteiro. O provider de fallback costuma ser o mais fraco da
# cadeia, e um resumo sem limite viraria um contexto que ele não aguenta.
_FALLBACK_RESUMO_MAX_CHARS = 120_000


def _blocos_de(msg) -> list:
    conteudo = msg.get("content") if isinstance(msg, dict) else None
    return conteudo if isinstance(conteudo, list) else []


def _resumo_do_trabalho_ja_feito(messages: list) -> str:
    """Ferramentas já executadas e o que devolveram, na ordem em que rodaram."""
    resultados: dict[str, str] = {}
    for msg in messages:
        for bloco in _blocos_de(msg):
            if not (isinstance(bloco, dict) and bloco.get("type") == "tool_result"):
                continue
            conteudo = bloco.get("content")
            if not isinstance(conteudo, str):
                conteudo = json.dumps(conteudo, ensure_ascii=False, default=str)
            resultados[bloco.get("tool_use_id")] = conteudo

    partes: list[str] = []
    total = 0
    for msg in messages:
        for bloco in _blocos_de(msg):
            if not (isinstance(bloco, dict) and bloco.get("type") == "tool_use"):
                continue
            entrada = json.dumps(bloco.get("input") or {}, ensure_ascii=False, default=str)
            saida = resultados.get(bloco.get("id"), "(chamada sem resultado registrado)")
            if len(saida) > _FALLBACK_RESULTADO_MAX_CHARS:
                saida = saida[:_FALLBACK_RESULTADO_MAX_CHARS] + " ...(cortado)"
            trecho = f"### {bloco.get('name')}({entrada})\n{saida}"
            if total + len(trecho) > _FALLBACK_RESUMO_MAX_CHARS:
                partes.append("(resumo cortado aqui -- limite de tamanho atingido)")
                return "\n\n".join(partes)
            partes.append(trecho)
            total += len(trecho)
    return "\n\n".join(partes)


def _condense_history_for_fallback(messages: list) -> list:
    """
    Histórico CONDENSADO ao trocar de provider no meio da chamada.

    O formato de tool_use/tool_result do provider original não é aproveitável
    direto pelo novo -- daí a versão anterior mandar só a primeira mensagem e
    deixar o novo recomeçar. O custo disso não é o formato, é o TRABALHO: as
    chamadas de rede já feitas, já pagas, e que o novo provider ia refazer.

    Produção 04/08: a anthropic deu timeout no turno 11, o histórico foi de 21
    mensagens para 1, e o gemini reexecutou a FASE 1 inteira -- exatamente os
    mesmos 7 tools do turno 1. Foram US$ 0,74 de coleta jogados fora, e os
    turnos restantes não deram pra terminar: a run acabou em 188 caracteres.

    Aqui o histórico vira TEXTO: uma mensagem só, com as ferramentas que já
    rodaram e o que elas devolveram. Some o problema de formato (é prosa, não
    protocolo) e o novo provider continua de onde parou em vez de recomeçar.
    """
    if len(messages) <= 1:
        return messages

    resumo = _resumo_do_trabalho_ja_feito(messages)
    if not resumo:
        # Nenhuma ferramenta rodou ainda -- não há trabalho a preservar, e o
        # comportamento antigo já era o certo.
        return messages[:1]

    aviso = (
        "CONTEXTO: esta sessão já estava em andamento com outro modelo, que "
        "ficou indisponível. As ferramentas abaixo JÁ FORAM EXECUTADAS e os "
        "resultados continuam válidos -- NÃO chame nenhuma delas de novo com "
        "os mesmos argumentos. Continue de onde a sessão parou.\n\n"
        + resumo
    )

    primeira = messages[0]
    papel = primeira.get("role", "user") if isinstance(primeira, dict) else "user"
    conteudo = primeira.get("content") if isinstance(primeira, dict) else None
    if isinstance(conteudo, str):
        return [{"role": papel, "content": conteudo + "\n\n" + aviso}]
    return [{
        "role": papel,
        "content": list(conteudo or []) + [{"type": "text", "text": aviso}],
    }]


class FallbackClient:
    """Percorre os provedores em ordem, caindo pro próximo quando um falha.

    Mantém uma lista de provedores CONDENADOS na run (`_mortos`): modelo
    inexistente ou conta sem saldo não voltam a funcionar no meio da mesma
    execução, e re-tentá-los a cada turno é latência pura antes de falhar
    igual.

    Medido em produção 03/08: a cascata gemini(404) -> openrouter(404) ->
    openai(429 sem cota) -> kimi(conta suspensa) levou ~15s pra chegar em
    "All providers exhausted". Sem o disjuntor, esses mesmos 15s se repetem em
    CADA turno que precise do fallback, dentro de uma run que tem prazo.
    """

    def __init__(self):
        self._order = [p for p in _provider_order() if _has_key(p)]
        if not self._order:
            raise RuntimeError(
                "No provider API keys found. Add at least one of: ANTHROPIC_API_KEY, DEEPSEEK_API_KEY, GEMINI_API_KEY, OPENROUTER_API_KEY, OPENAI_API_KEY, KIMI_API_KEY"
            )
        self._clients: dict[str, ProviderClient] = {}
        self._current_idx = 0
        # Prazo do CHAMADOR, em time.monotonic(). None = sem prazo, que é o
        # comportamento do agente diário (roda em janela de 10 min e prefere
        # percorrer a cadeia inteira a voltar sem resposta).
        self._prazo: float | None = None
        self._custo_tentativa_s: float = 0.0
        # nome -> motivo. Só entra aqui por falha PERMANENTE (ver
        # _is_falha_permanente); rate limit passageiro nunca condena.
        self._mortos: dict[str, str] = {}

    def definir_orcamento(self, prazo_monotonic: float, custo_por_tentativa_s: float) -> None:
        """Prazo além do qual a cadeia para de tentar provedores novos.

        Existe porque `create()` percorre a cadeia INTEIRA por dentro: um
        provedor que estoura o timeout, outro que estoura, e uma única chamada
        já consumiu 2x o teto por tentativa sem nunca devolver o controle a
        quem chamou.

        Quem contava tentativas de fora contava errado. Em analise_rapida_ia a
        conta era "coleta + 2 x 55s cabem em 135s", e o teste que a fixava
        passava -- mas ela descrevia um mundo em que uma chamada é uma
        tentativa. Produção 18/08/2026: anthropic estourou 55s, a cadeia caiu
        para o deepseek por dentro, e o processo foi morto pelo Node em 150s
        com stdoutParcial=0. Com seis provedores configurados, uma chamada
        pode custar 330s.

        O prazo é do chamador porque só ele sabe quanto tempo tem: rota
        interativa tem o timeout do Node na frente; o agente diário, não.
        """
        self._prazo = prazo_monotonic
        self._custo_tentativa_s = max(0.0, custo_por_tentativa_s)

    def _cabe_outra_tentativa(self) -> bool:
        """Sem prazo definido, sempre cabe -- o default preserva o agente."""
        if self._prazo is None:
            return True
        return (time.monotonic() + self._custo_tentativa_s) <= self._prazo

    def _condenar(self, name: str, motivo: str) -> None:
        if name in self._mortos:
            return
        self._mortos[name] = motivo
        print(
            f"[provider] {name} fora desta run (falha permanente) -- "
            f"não será tentado de novo até o próximo processo.",
            file=sys.stderr,
            flush=True,
        )
        # Linha estruturada pro runner.ts (mesmo padrão de USAGE:/STEP:/
        # REPORT:): sem ela, "conta sem crédito" morria no log do processo e o
        # usuário só descobria quando a cadeia INTEIRA esgotasse. O runner
        # transforma isso em aviso no topo do e-mail do relatório e no stepLog
        # da tela de Runs. `motivo` passa por mask_sensitive_data antes de
        # chegar aqui (ver call site em create()).
        # ÚNICO print deste arquivo que fica em stdout, e de propósito: não é
        # diagnóstico humano, é canal legível por máquina. runner.ts e
        # report-preflight.ts fazem parse de PROVIDER_DOWN:{json} no stdout
        # CRU para montar o banner de "provedor caído" no relatório diário.
        # Mover para stderr quebraria esse banner em silêncio.
        print(
            "PROVIDER_DOWN:" + json.dumps(
                {"provider": name, "motivo": motivo[:300]}, ensure_ascii=False
            ),
            flush=True,
        )

    def pular_provedor_atual(self, motivo: str) -> bool:
        """Avança para o próximo provedor SEM condenar o atual.

        Diferente de `_condenar`: um toco (resposta curta demais, tool-call
        alucinado como texto) não é falha permanente. O provedor respondeu --
        respondeu mal DESTA vez. Condená-lo tiraria da cadeia, pelo resto do
        processo, alguém que provavelmente funciona no próximo pedido, e o
        disjuntor existe para erro que não volta (modelo inexistente, conta
        sem saldo), não para qualidade ruim pontual.

        Devolve False quando não há próximo provedor disponível -- aí quem
        chamou precisa desistir com erro legível em vez de repetir o mesmo.
        """
        atual = self.provider_name
        for idx in range(self._current_idx + 1, len(self._order)):
            nome = self._order[idx]
            if nome in self._mortos:
                continue
            self._current_idx = idx
            print(f"[provider] pulando {atual} ({motivo}) -> {nome}", file=sys.stderr, flush=True)
            return True
        print(
            f"[provider] pulando {atual} ({motivo}) -- sem próximo provedor disponível",
            file=sys.stderr,
            flush=True,
        )
        return False

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
        # Fora do laço: a checagem de orçamento cita o último erro, e o primeiro
        # provedor pode ser pulado por `continue` (condenado) antes de qualquer
        # atribuição -- aí a citação daria UnboundLocalError.
        last_exc: Exception | None = None
        for idx in range(self._current_idx, len(self._order)):
            name = self._order[idx]
            if name in self._mortos:
                # Já condenado nesta run: pular sem gastar round-trip.
                continue
            # Só para as tentativas de FALLBACK (idx > o provedor da vez): a
            # primeira é a que o chamador já orçou antes de chamar. Barrar essa
            # seria recusar trabalho que ele decidiu que cabia.
            if idx > self._current_idx and not self._cabe_outra_tentativa():
                restante = (self._prazo or 0) - time.monotonic()
                print(
                    f"[provider] sem orçamento para tentar {name}: restam "
                    f"{restante:.0f}s e uma tentativa custa até "
                    f"{self._custo_tentativa_s:.0f}s",
                    file=sys.stderr, flush=True,
                )
                raise RuntimeError(
                    f"Orçamento de tempo esgotado antes de tentar {name} "
                    f"(restavam {restante:.0f}s, tentativa custa até "
                    f"{self._custo_tentativa_s:.0f}s). Último erro: "
                    f"{mask_sensitive_data(str(last_exc)) if last_exc else 'n/d'}"
                )
            c = self._get_client(name)
            tier = _resolve_tier(model)
            resolved_model = c.models.get(tier, model) if tier else model
            resolved_system = system_fn(name) if system_fn else system
            resolved_tools = tools_fn(name) if tools_fn else tools

            if name != primary_name:
                # Trocando de provider no meio desta chamada: o histórico em
                # formato de tool_use/tool_result não é aproveitável direto pelo
                # novo, então vira TEXTO -- preservando o trabalho já pago em
                # vez de mandar o novo recomeçar (ver _condense_history_for_fallback).
                resolved_messages = _condense_history_for_fallback(messages)
                if len(messages) > 1:
                    chars = sum(
                        len(m.get("content", "")) if isinstance(m.get("content"), str) else 0
                        for m in resolved_messages
                    )
                    print(
                        f"[provider] histórico condensado para {name} "
                        f"({len(messages)} mensagens -> 1 de {chars} chars, "
                        f"com o trabalho já feito)",
                        file=sys.stderr,
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
            last_exc = None
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
                        print(f"[provider] switched to {name}", file=sys.stderr, flush=True)
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
                            file=sys.stderr,
                            flush=True,
                        )
                        time.sleep(delay)
                        continue
                    break

            safe_exc = mask_sensitive_data(str(last_exc))
            if last_exc is not None and _is_falha_permanente(last_exc):
                self._condenar(name, safe_exc)
            if last_exc is not None and _is_model_not_found(last_exc):
                # Erro de configuração, não de capacidade -- cair pro próximo
                # provedor não conserta e esconde a causa. Nomeia o modelo pra
                # o conserto ser óbvio no log.
                print(
                    f"[provider] {name}: modelo '{resolved_model}' não existe ou não está "
                    f"disponível para esta chave — ERRO DE CONFIGURAÇÃO, corrija "
                    f"PROVIDERS['{name}']['models'] em provider.py. Detalhe: {safe_exc}",
                    file=sys.stderr,
                    flush=True,
                )
            else:
                print(f"[provider] {name} failed: {safe_exc}", file=sys.stderr, flush=True)
            proximos = [p for p in self._order[idx + 1:] if p not in self._mortos]
            if proximos:
                print(f"[provider] trying {proximos[0]}...", file=sys.stderr, flush=True)
            else:
                # Diagnóstico junto do erro: sem isso o operador só vê o último
                # motivo e não sabe que a cadeia INTEIRA está fora, nem por quê.
                # Em 03/08 os quatro provedores de fallback estavam mortos ao
                # mesmo tempo (dois com modelo 404, dois com conta sem saldo) e
                # o erro só citava o último.
                resumo = " | ".join(f"{p}: {m}" for p, m in self._mortos.items())
                raise RuntimeError(
                    f"All providers exhausted. Last error: {safe_exc}"
                    + (f" -- condenados nesta run: {resumo}" if resumo else "")
                ) from last_exc
        raise RuntimeError(
            "No providers available"
            + (f" -- todos condenados: {', '.join(self._mortos)}" if self._mortos else "")
        )


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
