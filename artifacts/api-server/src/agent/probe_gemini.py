#!/usr/bin/env python3
"""
Mede quais modelos do Gemini servem para o tier "full" da cadeia de fallback.

POR QUE MEDIR EM VEZ DE ESCOLHER PELO NOME
------------------------------------------
"Aparece na listagem" não é o mesmo que "serve para o nosso fluxo", e este
repo já foi mordido pelas duas pontas:

- `gemini-2.5-pro` passou a responder 404 ("no longer available to new users").
  Falha barata: quebra na hora.
- `gemini-2.5-flash` estava listado, respondia normalmente, e mesmo assim
  completou as 12 rodadas do relatório diário SEM NUNCA chamar
  save_observation -- relatório vazio que ainda voltava como run "success".
  Falha cara: gasta a run inteira antes de aparecer.

A segunda é o motivo deste script existir. Um modelo só entra no tier "full"
se sustentar o que o fluxo exige: devolver tool_call ESTRUTURADO, aceitar o
tool_result de volta e fechar com texto -- dois turnos, não um.

O QUE ELE FAZ
-------------
Para cada candidato, uma ida e volta real de tool calling pelo MESMO caminho
de código da produção (provider.ProviderClient), então o que ele mede é o que
o agente vai encontrar -- incluindo a recuperação de chamada vazada como
texto, que é aplicada de verdade em runs reais.

USO
---
    export GEMINI_API_KEY=...
    python artifacts/api-server/src/agent/probe_gemini.py
    python artifacts/api-server/src/agent/probe_gemini.py --models gemini-2.5-flash,gemini-3-pro
    python artifacts/api-server/src/agent/probe_gemini.py --json

Precisa de rede até generativelanguage.googleapis.com (não roda de dentro de
sandbox com proxy fechado).
"""

import argparse
import json
import os
import sys
import time

# Este é um script de MÃO: alguém digita o caminho dele e aperta enter. Não
# pode exigir PYTHONPATH montado (como get_bounce_alerts.py, que é spawnado
# pelo Node com o env pronto) nem aceitar o import achatado de get_alt_data.py
# -- provider.py usa imports relativos e só carrega como `agent.provider`.
#
# O insert(0) é deliberado: o Python já coloca o diretório do script em
# sys.path[0], e lá dentro existe um `agent.py`. Sem colocar o diretório PAI na
# frente, `import agent` acha esse arquivo em vez do pacote e quebra num erro
# de import relativo que não explica nada.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.provider import (  # noqa: E402
    MODEL_PRICING,
    PROVIDERS,
    ProviderClient,
    TextBlock,
    ToolUseBlock,
)

BASE_URL = PROVIDERS["gemini"]["base_url"]
API_KEY_ENV = PROVIDERS["gemini"]["api_key_env"]

# Modelos que nunca serviriam para o tier "full": não são de chat com
# ferramentas. Filtrar aqui evita gastar uma chamada só para levar 400.
PADROES_IGNORADOS = (
    "embedding", "aqa", "imagen", "veo", "tts", "image-generation",
    "learnlm", "gemma",
)

# Ferramenta de teste em formato Anthropic -- ProviderClient converte para o
# formato OpenAI sozinho, que é exatamente o que acontece em produção.
FERRAMENTA = [{
    "name": "get_stock_data",
    "description": "Cotação atual de um ticker. Use sempre que precisar de preço.",
    "input_schema": {
        "type": "object",
        "properties": {"ticker": {"type": "string", "description": "Ex: NVDA"}},
        "required": ["ticker"],
    },
}]

SISTEMA = (
    "Você é um analista de mercado. Use as ferramentas disponíveis para obter "
    "dados antes de responder. Nunca invente cotação."
)
PEDIDO = "Qual o preço da NVDA agora? Use a ferramenta para descobrir."
RESULTADO_FALSO = json.dumps({"ticker": "NVDA", "price": 181.42, "change_pct": 1.2})

# Resposta final curta demais é o sintoma do modelo que "reconhece" em vez de
# responder ("Entendido, vou verificar..."). O loop de produção usa a mesma
# ideia (agent.py::_min_report_chars), aqui num piso bem menor porque a
# pergunta é de uma linha só.
MIN_CHARS_RESPOSTA = 40


def listar_modelos(api_key: str) -> list[str]:
    """Ids servidos pela camada compatível com OpenAI.

    É essa a camada que o provider.py usa -- a API nativa do Gemini pode
    listar modelo que a compat não serve, então perguntar para a nativa daria
    uma lista otimista demais.
    """
    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url=BASE_URL)
    return sorted(m.id.removeprefix("models/") for m in client.models.list())


def candidatos(ids: list[str]) -> list[str]:
    return [
        m for m in ids
        if not any(p in m.lower() for p in PADROES_IGNORADOS)
    ]


def _texto_de(resp) -> str:
    return " ".join(b.text for b in resp.content if isinstance(b, TextBlock)).strip()


def _tool_uses(resp) -> list:
    return [b for b in resp.content if isinstance(b, ToolUseBlock)]


def probe(model: str) -> dict:
    """Dois turnos reais contra o modelo. Nunca levanta: erro vira resultado."""
    r = {
        "model": model, "ok": False, "chamou_ferramenta": False,
        "vazou_como_texto": False, "fechou_com_texto": False,
        "tem_preco": model in MODEL_PRICING, "segundos": None, "erro": None,
    }
    inicio = time.monotonic()
    try:
        client = ProviderClient("gemini")

        # --- turno 1: precisa pedir a ferramenta -------------------------------
        messages = [{"role": "user", "content": PEDIDO}]
        resp1 = client.create(
            model=model, max_tokens=1024, system=SISTEMA,
            tools=FERRAMENTA, messages=messages,
        )
        usos = _tool_uses(resp1)
        r["chamou_ferramenta"] = any(b.name == "get_stock_data" for b in usos)
        # id com prefixo é marca das chamadas resgatadas de texto pelo
        # provider.py -- funciona, mas é sinal de modelo que não segue o
        # protocolo, e vale registrar em vez de mascarar.
        r["vazou_como_texto"] = any(
            b.id.startswith(("leaked_", "recovered_")) for b in usos
        )
        if not r["chamou_ferramenta"]:
            r["erro"] = f"não chamou a ferramenta (stop_reason={resp1.stop_reason})"
            return r

        # --- turno 2: precisa aceitar o tool_result e fechar -------------------
        messages.append({"role": "assistant", "content": [
            {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
            for b in usos
        ]})
        messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": b.id, "content": RESULTADO_FALSO}
            for b in usos
        ]})
        resp2 = client.create(
            model=model, max_tokens=1024, system=SISTEMA,
            tools=FERRAMENTA, messages=messages,
        )
        texto = _texto_de(resp2)
        r["fechou_com_texto"] = len(texto) >= MIN_CHARS_RESPOSTA
        if not r["fechou_com_texto"]:
            r["erro"] = f"resposta final curta demais ({len(texto)} chars)"
            return r

        r["ok"] = True
        return r
    except Exception as e:  # noqa: BLE001 - qualquer falha é resultado, não crash
        r["erro"] = f"{type(e).__name__}: {e}"[:300]
        return r
    finally:
        r["segundos"] = round(time.monotonic() - inicio, 1)


def _marca(v: bool) -> str:
    return "sim" if v else "NÃO"


def imprimir(resultados: list[dict]) -> None:
    print()
    print(f"{'modelo':<34} {'2 turnos':<9} {'tool':<6} {'preço':<6} {'s':>5}  observação")
    print("-" * 100)
    for r in resultados:
        obs = r["erro"] or ""
        if r["ok"] and r["vazou_como_texto"]:
            obs = "funciona, mas vazou a chamada como texto (recuperada)"
        if r["ok"] and not r["tem_preco"]:
            obs = (obs + "; " if obs else "") + "sem preço em MODEL_PRICING"
        print(
            f"{r['model']:<34} {_marca(r['ok']):<9} {_marca(r['chamou_ferramenta']):<6} "
            f"{_marca(r['tem_preco']):<6} {r['segundos'] or 0:>5}  {obs}"
        )

    aprovados = [r for r in resultados if r["ok"]]
    print()
    if not aprovados:
        print("Nenhum candidato sustentou os dois turnos. NÃO troque o tier 'full' "
              "às cegas -- sem modelo que feche o fluxo, o rebaixamento por "
              "orçamento continua caindo no openrouter.")
        return

    limpos = [r for r in aprovados if not r["vazou_como_texto"]]
    escolha = (limpos or aprovados)[0]
    print(f"Candidatos que sustentaram os dois turnos: "
          f"{', '.join(r['model'] for r in aprovados)}")
    print(f"Sugestão para PROVIDERS['gemini']['models']['full']: {escolha['model']}")
    if not escolha["tem_preco"]:
        print()
        print(f"ANTES DE TROCAR: adicione {escolha['model']} em MODEL_PRICING "
              "(provider.py). Modelo sem preço reporta custo None, e custo None "
              "soma ZERO no teto diário -- é um furo conhecido do teto.")
    print()
    print("Lembrete: dois turnos aqui é o piso, não a prova completa. O fluxo "
          "diário tem ~12 rodadas, e já houve modelo que passou num teste curto "
          "e abandonou o fluxo longo no meio.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--models", help="lista separada por vírgula; padrão = descobre pela API")
    ap.add_argument("--json", action="store_true", help="saída em JSON")
    args = ap.parse_args()

    api_key = os.environ.get(API_KEY_ENV, "").strip()
    if not api_key:
        print(f"Defina {API_KEY_ENV} no ambiente.", file=sys.stderr)
        return 2

    if args.models:
        alvos = [m.strip() for m in args.models.split(",") if m.strip()]
    else:
        try:
            todos = listar_modelos(api_key)
        except Exception as e:  # noqa: BLE001
            print(f"Falha ao listar modelos: {type(e).__name__}: {e}", file=sys.stderr)
            return 1
        alvos = candidatos(todos)
        print(f"{len(todos)} modelos servidos pela camada OpenAI-compat; "
              f"{len(alvos)} candidatos a chat com ferramenta.", file=sys.stderr)

    resultados = []
    for m in alvos:
        print(f"  testando {m}...", file=sys.stderr, flush=True)
        resultados.append(probe(m))

    if args.json:
        print(json.dumps(resultados, ensure_ascii=False, indent=2))
    else:
        imprimir(resultados)
    return 0


if __name__ == "__main__":
    sys.exit(main())
