"""
Interpretação com IA da tela Reação a Earnings — leitura da CESTA, não do papel.

A tela já interpreta cada ticker por regra (`interpretResult` no front): classe
de volatilidade, viés direcional, se o movimento amplia ao longo do pregão.
Repetir isso em prosa gastaria tokens para dizer o que está escrito ao lado.

O que a regra NÃO alcança é comparação: ela é por ticker por construção. Quem
reage mais violento entre os cinco, onde o run-up pré-earnings acertou a direção
e onde não acertou, o que a semana inteira de resultados implica para quem
carrega mais de um papel do mesmo grupo. É isso que este módulo pede.

## Uma chamada para a cesta inteira

Um botão por ticker viraria cinco chamadas para produzir cinco textos que o
`interpretResult` já resume. Aqui é uma chamada só, custo fixo
(~US$ 0,003 no gemini) independente do tamanho da cesta.

## O tamanho da amostra é o risco central deste prompt

São ~8 eventos por ticker. Com oito pontos, "5 de 6 esticados caíram" é uma
observação; correlação de Pearson é indício; e qualquer coisa dita com cara de
lei sai errada. O SYSTEM cobra isso explicitamente, e é a regra que mais importa
aqui -- diferente da Análise Rápida, onde o risco principal era inventar número.

Uso:
    python3 -m agent.reacao_earnings_ia    # payload JSON no stdin
"""
from __future__ import annotations

import json
import os
import sys
import time

# Playbook §3: nenhuma camada interna pode ter orçamento MAIOR que o timeout
# externo, senão o Node descobre o problema matando o processo e o usuário
# recebe um 500 genérico em vez de erro legível.
#
# Mesma aritmética da Análise Rápida, sem a parcela de coleta: aqui os dados
# CHEGAM prontos no stdin (a tela já rodou o script determinístico), então não
# há rede antes do LLM. Sobra:
#
#     2 x _LLM_TIMEOUT_S <= _ORCAMENTO_TOTAL_S < timeout da rota
#     2 x 85             <= 175                 < 195
#
# O 2x é o piso de sempre: a cadeia precisa conseguir trocar de provedor ao
# menos uma vez, senão um provedor lento derruba a interpretação inteira.
_LLM_TIMEOUT_S = float(os.environ.get("REACAO_IA_LLM_TIMEOUT_S", "85"))
os.environ["API_TIMEOUT_SECONDS"] = str(_LLM_TIMEOUT_S)
os.environ["AGENT_MAX_RETRIES"] = "0"
os.environ["AGENT_TRANSIENT_RETRIES"] = "0"

_ORCAMENTO_TOTAL_S = float(os.environ.get("REACAO_IA_ORCAMENTO_S", "175"))
_INICIO = time.monotonic()

from agent.ordem_das_telas import aplicar_na_env as _aplicar_ordem_na_env

# Antes do primeiro get_client(): o FallbackClient lê AGENT_PROVIDER_ORDER no
# construtor, e depois disso mudá-la não move mais nada.
_aplicar_ordem_na_env()

from agent.provider import get_client, get_run_usage, texto_da_resposta  # noqa: E402
from agent.teto_tokens import teto_de_tokens  # noqa: E402

# Abaixo disto não é interpretação, é toco. O texto pedido tem 4 seções.
MIN_TEXTO_CHARS = 200

# Teto do JSON que vai no prompt. A cesta traz `events` por ticker (8 eventos x
# 5 papéis, cada um com trajetória dia a dia) e isso sozinho passa de 40k chars.
# O corte guarda o começo, onde ficam ticker e `summary` -- que é o que a
# comparação usa.
MAX_DADOS_CHARS = 24_000


SYSTEM = (
    "Você é um analista de mercado escrevendo em português do Brasil, para o "
    "dono de uma carteira que também é o operador do sistema que gerou os "
    "dados.\n\n"

    "TAMANHO — a regra mais desrespeitada, por isso vem primeiro:\n"
    "cada seção tem NO MÁXIMO 2 parágrafos, cada parágrafo NO MÁXIMO 4 linhas, "
    "e o texto inteiro fica entre 300 e 550 palavras. Texto que passa disso é "
    "cortado pelo sistema no meio da frase.\n\n"

    "Você recebe o resultado de uma análise ESTATÍSTICA de reação a earnings "
    "para uma CESTA de tickers. Para cada um: médias de gap de abertura, "
    "variação até o fechamento, range intradiário, razão de volume, um "
    "`suggested_threshold_pct` (|média| + 1 desvio), níveis R1/R2/S1/S2 "
    "projetados sobre o preço atual, o `runup` (o papel esticado ou descontado "
    "no mês pré-earnings previu a direção da reação?) e a `trajetoria` "
    "acumulada nos pregões seguintes.\n\n"

    "## 1. Compare — não descreva um por um\n"
    "A tela JÁ mostra, ao lado de cada papel, a classe de volatilidade, o viés "
    "direcional e se o movimento amplia ao longo do pregão. Repetir isso em "
    "prosa não acrescenta nada. Escreva o que só se enxerga olhando os papéis "
    "JUNTOS: quem reage mais e quem reage menos, onde o padrão de run-up se "
    "repete e onde ele falha, quais papéis se movem na mesma direção (e "
    "portanto não diversificam quem carrega os dois).\n\n"

    "## 2. Oito eventos não são uma lei\n"
    "A amostra por ticker é de ~8 earnings. Nesse tamanho, contagem por bucket "
    "('5 de 6 esticados caíram') é observação e correlação é indício — nunca "
    "prova. Escreva com essa força e não mais: 'nos últimos 8 resultados', "
    "'esse papel tem tendido a', nunca 'sempre', 'toda vez' ou 'o papel cai "
    "quando'. Se `n_events` de um ticker for menor que 5, diga isso ao citá-lo.\n\n"

    "## 3. Todo número vem do JSON\n"
    "Não invente nem calcule. Campo ausente ou null não se menciona. A única "
    "conta permitida é comparar dois valores que estão lá (maior/menor/acima/"
    "abaixo). Não some, não tire média, não converta.\n"
    "Ticker com `error` no lugar de `summary` não foi analisado: cite-o como "
    "ausente da comparação, com o motivo, e não tire conclusão sobre ele.\n\n"

    "## 4. Unidades\n"
    "Valores monetários são de ativos listados nos EUA: US$ 277,68 — nunca R$, "
    "não converta. Percentuais já vêm em pontos percentuais (4.2 = 4,2%). "
    "`volume_ratio_mean` é adimensional (1,8 = 1,8x a média), sem %. "
    "`close_pct_std` está em pontos percentuais.\n\n"

    "## 5. R1/R2/S1/S2 não são suporte e resistência\n"
    "São projeção estatística da magnitude histórica de reação sobre o preço "
    "atual, não estrutura de preço. Escreva 'a reação média levaria a ~US$ X' "
    "e nunca 'a resistência está em US$ X'. Confundir os dois transforma "
    "estatística descritiva em nível técnico que ninguém mediu.\n\n"

    "## 6. Dado marcado como velho\n"
    "Ticker com `stale: true` teve a agenda de earnings servida de cache "
    "vencido. Diga isso na Ressalvas ao citá-lo.\n\n"

    "Escreva em markdown com EXATAMENTE estas seções, começando direto no "
    "primeiro cabeçalho, sem nenhuma frase de abertura antes dele:\n"
    "## O que a cesta mostra\n## Quem se move junto\n"
    "## O sinal de run-up\n## Ressalvas\n"
)


def _compactar(resultados: list) -> str:
    """Só o que a comparação usa. `events` fica de fora inteiro.

    Cada ticker traz ~8 eventos com trajetória dia a dia; a cesta passa de 40k
    chars, e o corte cego por `MAX_DADOS_CHARS` deixaria os últimos tickers de
    fora sem avisar -- comparação silenciosamente incompleta é pior que
    comparação menor.
    """
    enxuto = []
    for r in resultados:
        if not isinstance(r, dict):
            continue
        item = {"ticker": r.get("ticker")}
        if r.get("error"):
            item["error"] = r["error"]
        if r.get("summary"):
            item["summary"] = r["summary"]
        if r.get("stale"):
            item["stale"] = True
        enxuto.append(item)
    return json.dumps(enxuto, ensure_ascii=False)[:MAX_DADOS_CHARS]


def interpretar(dados: dict) -> dict:
    resultados = dados.get("results")
    if not isinstance(resultados, list) or not resultados:
        return {"error": "Rode a análise de reação a earnings antes de pedir a interpretação"}

    # Ticker sem `summary` não é comparável. Se NENHUM tiver, não há cesta --
    # e chamar o LLM para ele dizer "não há dados" custaria tokens para
    # produzir a mesma frase que este `if` produz de graça.
    com_dados = [r for r in resultados if isinstance(r, dict) and r.get("summary")]
    if not com_dados:
        return {"error": (
            "Nenhum ticker da cesta produziu estatística — não há o que "
            "comparar. Veja o erro de cada papel na própria tela."
        )}

    tickers = [str(r.get("ticker") or "?") for r in resultados]
    client = get_client()
    # O prazo vive no CLIENTE: `create()` percorre a cadeia inteira por dentro,
    # sem devolver o controle, então contar tentativas daqui de fora descreveria
    # um mundo em que uma chamada é uma tentativa -- e nunca foi esse.
    client.definir_orcamento(_INICIO + _ORCAMENTO_TOTAL_S, _LLM_TIMEOUT_S)

    lookback = dados.get("lookback")
    benchmark = dados.get("benchmark")
    cabecalho = f"Cesta analisada: {', '.join(tickers)}."
    if lookback:
        cabecalho += f" Janela: últimos {lookback} earnings por ticker."
    if benchmark:
        cabecalho += f" Excesso medido contra {benchmark}."
    conteudo = f"{cabecalho}\n\n{_compactar(resultados)}"

    # Toco (resposta de uma linha) é falha conhecida — playbook §4. Sem este
    # laço, o toco virava erro na tela, o usuário clicava de novo e caía no
    # MESMO provedor: toco não condena ninguém, então a cadeia nunca andava
    # sozinha e o botão simplesmente não funcionava.
    texto = ""
    while True:
        _antes_llm = time.monotonic()
        resp = client.create(
            model=client.models["full"],
            max_tokens=teto_de_tokens(client.models["full"]),
            system=SYSTEM,
            tools=[],
            messages=[{"role": "user", "content": conteudo}],
        )
        texto = texto_da_resposta(resp)
        # DOIS relógios: `_llm_s` mede o create() inteiro, que percorre a cadeia
        # por dentro. Atribuir esse tempo ao provedor que respondeu carimbaria
        # no vencedor o tempo gasto por quem falhou antes dele.
        _llm_s = time.monotonic() - _antes_llm
        _provedor_s = getattr(client, "ultimo_tempo_provedor_s", None)
        _tempo = (f"respondeu em {_provedor_s:.1f}s (cadeia inteira: {_llm_s:.1f}s)"
                  if _provedor_s is not None and _llm_s - _provedor_s >= 0.5
                  else f"respondeu em {_llm_s:.1f}s")
        print(f"[reacao_earnings_ia] {client.provider_name}/{client.models['full']} "
              f"{_tempo} ({len(texto)} chars)", file=sys.stderr, flush=True)
        if len(texto) >= MIN_TEXTO_CHARS:
            break

        provedor, modelo = client.provider_name, client.models["full"]
        # Toco e truncamento chegam iguais daqui (texto curto) e são problemas
        # OPOSTOS: toco é o modelo respondendo de menos; truncamento é ele
        # produzir tanto raciocínio que não sobrou espaço para a resposta.
        razao = (getattr(resp, "raw_stop_reason", "") or "").lower()
        n_raciocinio = len(getattr(resp, "reasoning_content", None) or "")
        truncado = razao in ("length", "max_tokens") or (not texto and n_raciocinio > 0)
        diagnostico = "truncou antes da resposta" if truncado else "devolveu toco"
        print(f"[reacao_earnings_ia] {provedor}/{modelo} {diagnostico} "
              f"({len(texto)} chars, stop_reason={razao or 'n/d'}, "
              f"raciocinio={n_raciocinio} chars): {texto[:120]!r}",
              file=sys.stderr, flush=True)

        gasto = time.monotonic() - _INICIO
        if gasto + _LLM_TIMEOUT_S > _ORCAMENTO_TOTAL_S:
            return {"error": (
                f"{provedor}/{modelo} {diagnostico} ({len(texto)} chars) e não sobra "
                f"tempo no orçamento ({gasto:.0f}s de {_ORCAMENTO_TOTAL_S:.0f}s) "
                f"para tentar outro provedor."
            )}
        if not client.pular_provedor_atual(f"{diagnostico} ({len(texto)} chars)"):
            return {"error": (
                f"Todos os provedores falharam em produzir a interpretação. "
                f"Último: {provedor}/{modelo} {diagnostico} com {len(texto)} chars."
            )}

    saida = {"markdown": texto, "usage": get_run_usage(), "tickers": tickers}
    # Texto cortado por teto de tokens tem que ser DITO: um texto que para no
    # meio da frase, sem aviso, parece conclusão do modelo.
    if str(getattr(resp, "raw_stop_reason", "")).lower() in ("max_tokens", "length"):
        saida["truncado"] = True
    return saida


if __name__ == "__main__":
    try:
        args = json.loads(sys.stdin.read() or "{}")
    except Exception:
        args = {}
    try:
        saida = interpretar(args)
    except Exception as e:  # noqa: BLE001 — quota/chave/provedor viram erro legível
        saida = {"error": str(e) or e.__class__.__name__}
    print(json.dumps(saida, ensure_ascii=False))
