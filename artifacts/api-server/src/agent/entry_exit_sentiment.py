"""Sentimento das manchetes de um Estudo de Entrada e Saída, via LLM barato.

Roda como `python -m agent.entry_exit_sentiment` (import de pacote, igual
get_bounce_alerts.py) porque provider.py usa import relativo -- não dá pra
spawnar por caminho de arquivo como entry_exit_study.py.

Chamado pelo checker diário (lib/entry-exit-study-checker.ts), NUNCA pela
rota POST: o custo é pequeno (uma chamada tier "flash" cobrindo todos os
estudos), mas a latência de LLM não pertence ao caminho em que o usuário
está esperando a tela responder. Falhou? O checker segue sem sentimento --
isto é um rótulo informativo, não um dado do cálculo.

O rótulo NÃO entra no cálculo da probabilidade, de propósito: quantificar
sentimento de notícia de forma confiável é um problema em aberto (mesma
limitação documentada em scenario-math.ts). O que o LLM entrega aqui é uma
LEITURA -- "o clima das manchetes de hoje é negativo, por causa de X" -- pro
usuário cruzar com o número, não um input escondido dentro dele.

Input (stdin JSON):
  {"studies": [{"ticker": "NVDA", "news": [{"title": "...", "summary": "..."}, ...]}, ...]}
Output (stdout JSON):
  {"sentiments": {"NVDA": {"sentimento": "negativo", "justificativa": "..."}}}

Tickers sem manchete ficam de fora do resultado. Qualquer falha (provedor,
JSON malformado do modelo) devolve {"sentiments": {}} com o motivo em
stderr -- o chamador nunca precisa distinguir falha de "sem notícias".
"""
import sys
import json
import re

from agent.startup_probe import boot as _probe_boot, imports_prontos as _probe_imports

_probe_boot()

from agent.provider import get_client, texto_da_resposta
from agent.security import sanitize_for_llm

_probe_imports()

VALIDOS = frozenset({"positivo", "neutro", "negativo"})
# Teto de manchetes por ticker no prompt -- mais que isso é ruído pro rótulo
# de um dia só, e infla o custo da chamada sem melhorar a leitura.
MAX_NOTICIAS_POR_TICKER = 6

SYSTEM = (
    "Você é um analista que classifica o TOM AGREGADO das manchetes do dia "
    "sobre cada ação, do ponto de vista de um acionista.\n"
    "Responda SOMENTE com JSON válido, sem markdown, no formato:\n"
    '{"TICKER": {"sentimento": "positivo|neutro|negativo", "justificativa": "uma frase curta em português"}}\n'
    "Regras:\n"
    "- Um objeto por ticker pedido; use exatamente os tickers fornecidos como chaves.\n"
    "- 'sentimento' é o tom agregado de TODAS as manchetes daquele ticker, não da mais chamativa.\n"
    "- Manchetes mistas ou meramente factuais (agenda, datas) => neutro.\n"
    "- 'justificativa' cita o fato dominante, sem opinião de investimento e sem recomendar nada."
)


def _montar_prompt(studies: list[dict]) -> tuple[str, list[str]]:
    blocos = []
    tickers = []
    for s in studies:
        ticker = str(s.get("ticker") or "").strip().upper()
        noticias = [n for n in (s.get("news") or []) if n.get("title")]
        if not ticker or not noticias:
            continue
        tickers.append(ticker)
        linhas = []
        for n in noticias[:MAX_NOTICIAS_POR_TICKER]:
            titulo = sanitize_for_llm(str(n.get("title") or ""))
            resumo = sanitize_for_llm(str(n.get("summary") or ""))
            linhas.append(f"- {titulo}" + (f" — {resumo}" if resumo else ""))
        blocos.append(f"{ticker}:\n" + "\n".join(linhas))
    return "\n\n".join(blocos), tickers


def _extrair_json(texto: str) -> dict:
    """O modelo às vezes embrulha o JSON em cerca de markdown ou prosa --
    pega o primeiro objeto {...} balanceado em vez de confiar no texto cru."""
    texto = texto.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", texto)
    if fence:
        texto = fence.group(1).strip()
    inicio = texto.find("{")
    if inicio < 0:
        raise ValueError("resposta sem objeto JSON")
    profundidade = 0
    for i in range(inicio, len(texto)):
        if texto[i] == "{":
            profundidade += 1
        elif texto[i] == "}":
            profundidade -= 1
            if profundidade == 0:
                return json.loads(texto[inicio:i + 1])
    raise ValueError("JSON não balanceado na resposta")


def analisar(studies: list[dict]) -> dict:
    prompt, tickers = _montar_prompt(studies)
    if not tickers:
        return {}

    client = get_client()
    resp = client.create(
        model=client.models["flash"],
        max_tokens=600,
        system=SYSTEM,
        tools=[],
        messages=[{"role": "user", "content": f"Manchetes de hoje:\n\n{prompt}"}],
    )
    # Extração tolerante ao formato do bloco: provider.py devolve dataclasses
    # TextBlock (acesso por atributo) nos DOIS caminhos, então a checagem
    # antiga por `isinstance(b, dict)` extraía string vazia sempre — e como
    # este módulo engole a falha ({"sentiments": {}}), o sentimento sumia
    # sem erro nenhum no log.
    texto = texto_da_resposta(resp)
    bruto = _extrair_json(texto)

    # Valida cada entrada -- rótulo fora do vocabulário ou ticker que o modelo
    # inventou não passam. Melhor snapshot sem sentimento que com lixo.
    saida: dict = {}
    for ticker in tickers:
        item = bruto.get(ticker)
        if not isinstance(item, dict):
            continue
        sentimento = str(item.get("sentimento") or "").strip().lower()
        justificativa = str(item.get("justificativa") or "").strip()
        if sentimento in VALIDOS:
            saida[ticker] = {"sentimento": sentimento, "justificativa": justificativa[:280]}
    return saida


if __name__ == "__main__":
    try:
        args = json.loads(sys.stdin.read() or "{}")
    except Exception:
        args = {}
    try:
        sentiments = analisar(args.get("studies") or [])
    except Exception as e:
        print(f"[entry_exit_sentiment] falha, seguindo sem sentimento: {e}", file=sys.stderr)
        sentiments = {}
    print(json.dumps({"sentiments": sentiments}, ensure_ascii=False))
