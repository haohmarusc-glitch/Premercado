"""Síntese com IA da tela Análise Rápida — os números viram uma leitura.

Roda como `python -m agent.analise_rapida_ia` (import de pacote, igual
entry_exit_sentiment.py) porque provider.py usa import relativo — não dá
pra spawnar por caminho de arquivo.

Diferente do sentimento (checker de fundo, tier flash), aqui o usuário
CLICOU pedindo a análise e vai ler o texto inteiro: latência de LLM é
aceitável e a qualidade importa, então o tier é "full". O custo de cada
clique volta no campo `usage` da resposta e a tela o exibe — gasto de
token nunca é silencioso.

O modelo NÃO recebe liberdade de inventar número: o prompt manda citar
apenas os valores presentes no JSON e proibi recomendação de compra/venda
("análise, não recomendação" — mesma linha do Veredito).

## A camada fundamental

Os painéis da tela são técnicos. Uma análise que só olha gráfico é rasa,
então este script BUSCA a camada fundamental antes do prompt, das fontes
que o app já tem — não é pesquisa web livre, é dado estruturado com fonte
identificada:

  - alvos de analistas (`get_stock_data`/yfinance): consenso, alvo médio/
    alto/baixo, nº de analistas, upside implícito;
  - valuation (`get_fundamentals_valuation`/FMP): DCF, P/L, P/VP, ROE,
    EV/EBITDA — fail-open, sem FMP_API_KEY a seção some;
  - manchetes recentes (`get_news`), sanitizadas.

Cada bloco é opcional e falha em silêncio: fonte fora do ar vira ausência
no prompt, e o system manda não mencionar o que não veio. `fontes` na
resposta diz o que entrou de fato — a tela mostra, para o leitor saber a
profundidade daquela análise.

Input (stdin JSON):
  {"ticker": "INTC", "benchmark": "SMH",
   "trend": {...} | null, "technicals": {...} | null,
   "snapshot": {...} | null, "reaction": {...} | null}
Output (stdout JSON):
  {"markdown": "...", "usage": {...}}  ou  {"error": "..."}
"""
import json
import os
import re
import sys
import time

# ── Orçamento de tempo: MENOR que o timeout do Node ─────────────────────────
#
# Playbook §3: nenhuma camada interna pode ter orçamento MAIOR que o timeout
# externo -- senão o Node só descobre o problema matando o processo, e o
# usuário recebe um 500 genérico em vez de um erro legível.
#
# Era exatamente esse o caso aqui. Defaults do provider.py, por PROVEDOR:
#   API_TIMEOUT_SECONDS=60 × AGENT_MAX_RETRIES=1 (2 tentativas do SDK)
#   × AGENT_TRANSIENT_RETRIES=1 (2 tentativas do fallback) + backoff
#   = até ~245s, contra 90s de teto em routes/analysis.ts.
#
# Visto em produção (17/08/2026): uma análise levou 57,5s e passou; as duas
# seguintes bateram 90s cravados e viraram 500 ("Failed: /analise-rapida/ia").
# Os 57,5s já eram sintoma -- encostavam no timeout de 60s da própria API.
#
# Aqui o processo é dedicado a UMA análise interativa, então fixamos o
# orçamento em vez de herdar o do agente (que roda em janela de 10 min):
# uma tentativa por provedor, sem retry no mesmo, deixando a cadeia de
# fallback trocar de provedor em vez de insistir num que está lento.
_LLM_TIMEOUT_S = float(os.environ.get("ANALISE_IA_LLM_TIMEOUT_S", "55"))
os.environ["API_TIMEOUT_SECONDS"] = str(_LLM_TIMEOUT_S)
os.environ["AGENT_MAX_RETRIES"] = "0"
os.environ["AGENT_TRANSIENT_RETRIES"] = "0"

# Teto do processo inteiro, incluindo imports, camada fundamental e LLM.
# Tem que caber no timeout do Node com folga -- test_orcamento_analise_ia.py
# lê os dois e falha se a invariante quebrar.
_ORCAMENTO_TOTAL_S = float(os.environ.get("ANALISE_IA_ORCAMENTO_S", "135"))
_INICIO = time.monotonic()

from agent.startup_probe import boot as _probe_boot, imports_prontos as _probe_imports

_probe_boot()

import yfinance as yf

from agent.provider import get_client, get_run_usage, texto_da_resposta
from agent.provider import _DEFAULT_ORDER as _ORDEM_PADRAO

# Provedores que não CONVERGEM nesta tarefa -- não que estejam fora do ar.
#
# O deepseek (v4-pro e v4-flash) gasta o max_tokens inteiro raciocinando e
# nunca chega à resposta. Medido quatro vezes em 18-19/08/2026, com duas
# versões de modelo e duas versões do prompt:
#
#   v4-pro    teto 12.000   142,2s   0 chars   (17.806 chars de raciocínio)
#   v4-flash  teto  6.000    54,2s   0 chars   (esgotou os 6.000 tokens)
#   v4-flash  teto  6.000    52,7s   0 chars   (com o prompt 27% menor)
#
# Num prompt trivial ele responde em 1s. O problema é ESTA tarefa: redigir uma
# análise longa e estruturada. Dobrar o teto dobrou o tempo sem produzir texto
# -- o raciocínio se expande para preencher o que houver.
#
# Fica FORA daqui e DENTRO da cadeia global de propósito: o v4-flash é forte em
# tool-calling, que é o formato do agente diário, e esse uso nunca falhou.
# Excluí-lo lá puniria um caminho que funciona por causa de outro que não.
#
# Deriva de _DEFAULT_ORDER em vez de listar a ordem aqui: uma terceira cópia da
# sequência divergiria das outras duas (provider.py e agent-budget.ts) na
# primeira mudança -- é o padrão do playbook §10, e já mordeu neste repo.
_SEM_CONVERGENCIA_AQUI = {"deepseek"}

if not os.environ.get("AGENT_PROVIDER_ORDER"):
    # Respeita override explícito: é assim que se testa um provedor excluído
    # sem editar código (AGENT_PROVIDER_ORDER=deepseek ...).
    os.environ["AGENT_PROVIDER_ORDER"] = ",".join(
        p for p in _ORDEM_PADRAO if p not in _SEM_CONVERGENCIA_AQUI
    )
from agent.security import sanitize_for_llm
from agent import tools

_probe_imports()

MAX_NOTICIAS = 6
# Abaixo disto não é análise, é toco: o texto pedido tem 6 seções e 400-700
# palavras. Serve de gatilho para trocar de provedor, não de validação de
# qualidade — um texto de 250 chars também é ruim, mas aí o problema é o
# prompt, não o provedor.
MIN_TEXTO_CHARS = 200

# Teto da camada fundamental (opcional) dentro do orcamento total.
#
# Ela e fail-open por projeto: fonte fora do ar vira ausencia no prompt. Mas
# "opcional" sem teto de TEMPO nao e opcional -- yfinance.info sozinho ja
# levou dezenas de segundos em producao, e o que ela consome sai do LLM, que
# e obrigatorio.
#
# O numero vem da aritmetica do orcamento, nao do gosto: para caber DUAS
# tentativas de provedor (o fallback da Tarefa 0 so serve se houver tempo
# para a segunda), precisa valer
#     teto_fundamento + 2 x _LLM_TIMEOUT_S <= _ORCAMENTO_TOTAL_S
# 25 + 2x55 = 135, exatamente o orcamento. Visto em producao (18/08/2026):
# a primeira chamada consumiu o orcamento inteiro e a troca de provedor --
# que existia justamente para esse caso -- ficou inalcancavel.
_TETO_FUNDAMENTO_S = float(os.environ.get("ANALISE_IA_FUNDAMENTO_S", "25"))
REC_LABELS = {
    "strongBuy": "compra forte", "buy": "compra", "hold": "manter",
    "sell": "venda", "strongSell": "venda forte",
}

# Teto de SEGURANÇA, não de projeto. Histórico: 2500 cortou (16/08, INTC);
# 4500 cortou de novo no mesmo ponto — porque o modelo escrevia até o teto,
# fosse ele qual fosse, ignorando as "400 a 700 palavras" enterradas no fim
# da lista de regras. A correção real foi o SYSTEM (limite por seção, no
# topo, com o motivo); este número só existe para o caso de o modelo
# desobedecer mesmo assim, e aí o corte vai MARCADO (truncado=true).
#
# Não subir mais sem antes checar o texto: teto alto com prompt fraco vira
# custo alto (4500 tokens de saída ≈ US$ 0,045 por análise, contra ~0,015
# de uma análise no tamanho pedido).
MAX_TOKENS = 6000

# Folga de raciocínio, para modelo que PENSA antes de responder.
#
# Em modelo thinking o max_tokens cobre raciocínio + resposta, não só a
# resposta. O mesmo 6000 significa coisas diferentes conforme o provedor, e
# ninguém percebeu porque o primeiro da cadeia (Anthropic) não gasta orçamento
# visível pensando.
#
# Medido em produção 18/08/2026, deepseek-v4-pro: 17.147 caracteres de
# raciocínio -- perto de 4.300 tokens -- e `stop_reason=length` com a resposta
# VAZIA. O modelo pensou até o teto e não sobrou espaço para escrever nada.
#
# 6000 de folga cobre aquele caso com margem. Não é generosidade: o teto de
# baixo continua valendo para a resposta, que é o que o usuário lê e o que
# custa em texto útil.
MAX_TOKENS_RACIOCINIO = 6000

# Heurística por nome, e o detector de truncamento é a rede de segurança.
#
# Uma lista de modelos envelhece -- por isso ela não é a única defesa: quando
# um modelo novo pensar sem estar aqui, o laço de retry ainda vai reconhecer
# `stop_reason=length` e trocar de provedor em vez de devolver texto vazio.
_MODELO_PENSA_RE = re.compile(r"deepseek-v4-pro|reasoner|thinking|-r1\b", re.I)


def teto_de_tokens(modelo: str) -> int:
    """Teto a pedir para ESTE modelo, já contando o raciocínio quando houver."""
    if _MODELO_PENSA_RE.search(modelo or ""):
        return MAX_TOKENS + MAX_TOKENS_RACIOCINIO
    return MAX_TOKENS
# Teto do JSON de dados no prompt — a tela manda o que coletou, mas um
# payload anômalo não pode virar um prompt gigante cobrado por token.
MAX_DADOS_CHARS = 14_000

# O prompt carrega a REGRA e o EXEMPLO; este comentário carrega por que a regra
# existe. Até 19/08/2026 as duas coisas estavam misturadas dentro do SYSTEM --
# várias regras traziam a data e a narrativa do incidente que as motivou, o que
# o modelo não precisa saber e que competia por atenção com o resto.
#
# As 17 regras viraram 5 grupos POR TIPO DE ERRO. Lista plana dá o mesmo peso a
# "não invente números" e a "RVOL nos primeiros 30 minutos"; agrupada, o modelo
# vê primeiro a classe do erro e depois o caso.
#
# Incidentes que originaram cada grupo, para quem for revisar:
#
#   procedência   NBIS 17/08/2026 -- "a MM200 (US$ 146,46) fica ENTRE S1 e S2
#                 (US$ 189,07)". Os três valores estavam certos no JSON; o que
#                 falhou foi a ordenação, a única conta que a regra permitia.
#   unidades      SNDK 18/08/2026 -- "momentum do setor é 106,56% anualizado"
#                 lido como retorno de 90 dias; e volAnnual escrito cru (1,17)
#                 ao lado do beta, que É adimensional.
#   preço         NVDA 18/08/2026 -- o gemini leu US$ 180 (níveis) e US$ 225,01
#                 (valuation) juntos e concluiu que a diferença era espaço EXTRA
#                 de alta. Virou tese de compra sobre dois números incompatíveis.
#   semântica     R1/R2/S1/S2 descritos como "zona de defesa"; run-up pós-balanço
#                 comentado no futuro ("chega esticado ao balanço").
#   tamanho       2500 e 4500 tokens cortados no mesmo ponto: o modelo escrevia
#                 até o teto porque o limite estava no ÚLTIMO item da lista.
SYSTEM = (
    "Você é um analista de mercado escrevendo em português do Brasil, para o "
    "dono de uma carteira que também é o operador do sistema que gerou os "
    "dados.\n\n"

    "TAMANHO — a regra mais desrespeitada, por isso vem primeiro:\n"
    "cada seção tem NO MÁXIMO 2 parágrafos, cada parágrafo NO MÁXIMO 4 linhas, "
    "e o texto inteiro fica entre 400 e 700 palavras. Análise que passa disso é "
    "cortada pelo sistema no meio da frase. Faltando espaço, corte adjetivo, "
    "repetição e paráfrase de número (o leitor vê a tabela ao lado) — nunca os "
    "cruzamentos. A Síntese importa mais que as outras seções e é a primeira a "
    "se perder quando o texto estica: confira o limite antes de escrevê-la.\n\n"

    "Você recebe um JSON com os números que o sistema calculou para UM ticker: "
    "tendência, técnica (RSI, MACD, médias, VWAP, RVOL), níveis (faixa de 52 "
    "semanas, MM50/MM200, vol anual, beta, momentum do setor), estatística de "
    "reação a earnings (médias, R1/R2/S1/S2, run-up) e, quando disponível, a "
    "camada fundamental (alvos de analistas, valuation, manchetes).\n\n"

    "Escreva em markdown com EXATAMENTE estas seções:\n"
    "## Quadro geral\n## Fundamento e valuation\n## Leitura técnica\n"
    "## Níveis que importam\n## Earnings e volatilidade\n## Síntese\n\n"

    "## 1. Todo número vem do JSON\n"
    "Não invente preço, data, resultado ou estatística; campo ausente ou null "
    "não se menciona. NÃO CALCULE número novo — o que não está no JSON "
    "descreve-se em palavras ('perto do topo da faixa', nunca 'a 89% da "
    "faixa'). A única conta permitida é comparar dois valores que estão lá "
    "(maior/menor/acima/abaixo).\n"
    "Para qualquer posição relativa ('X fica entre Y e Z', 'o suporte mais "
    "próximo'), use `niveisOrdenados`, que já vem do maior para o menor com a "
    "distância até o preço. Não ordene de cabeça: ordenar três números é onde a "
    "análise erra, e o erro sai com cara de fato apurado pelo sistema.\n\n"

    "## 2. Unidades — errar aqui transforma número certo em afirmação falsa\n"
    "| campo | como chega | como escrever |\n"
    "| --- | --- | --- |\n"
    "| valores monetários | ativos listados nos EUA | US$ 277,68 — nunca R$, "
    "não converta |\n"
    "| `momentumAnnualPct` | taxa ANUALIZADA, extrapolada de `lookbackDays` "
    "pregões | '106% anualizado (janela de 90 pregões)', nunca '106% em 90 "
    "dias' |\n"
    "| `volAnnual`/`volAnual` | FRAÇÃO decimal (1,169 = 116,9% ao ano) | '117% "
    "ao ano', nunca '1,17' |\n"
    "| beta, RVOL, razão de volume, correlação | adimensionais | sem %, não "
    "converta |\n\n"

    "## 3. Qual preço ancora cada afirmação\n"
    "Preço atual é APENAS `precoAtual.valor`. Os painéis buscam preço em "
    "instantes diferentes, e misturá-los produz texto que se contradiz. Se "
    "`precoAtual.divergenciaPct` existir, eles discordam mais do que o "
    "intervalo entre as buscas explica. Diga isso em uma linha na Leitura "
    "técnica ESCREVENDO OS DOIS PREÇOS e de onde vem cada um, com os valores "
    "de `porPainel` ('o valuation usa US$ 225,01 contra os US$ 180,00 dos "
    "níveis'), e trate o painel mais distante com ressalva. Dizer só que \"os "
    "painéis divergem\", sem os números, deixa o leitor sem saber qual "
    "indicador do texto está apoiado em qual preço.\n"
    "`dcf_implied_upside_pct` é calculado contra `valuation.current_price`, NÃO "
    "contra `precoAtual.valor`. Diferindo os dois, nomeie a base ('6,7% sobre a "
    "base de US$ 225,01 do valuation'). Apresentá-lo como distância até o preço "
    "atual soma dois números incompatíveis.\n\n"

    "## 4. Campos que não significam o que o nome sugere\n"
    "- R1/R2/S1/S2 são bandas estatísticas de volatilidade (preço ± reação "
    "média a earnings), NÃO suporte e resistência do gráfico. Não os chame de "
    "'piso' ou 'zona de defesa', nem os compare com alvo de analista como se "
    "medissem a mesma coisa. Suporte e resistência de verdade só a partir de "
    "máximas/mínimas e médias móveis presentes no JSON.\n"
    "- `rvolSignal` igual a `indefinido_abertura`: o pregão tem menos de 30 "
    "minutos e o RVOL está inflado pelo leilão de abertura. Não conclua nada "
    "sobre força compradora ou realização a partir dele; se mencionar, diga que "
    "ainda não é conclusivo.\n"
    "- `reacaoEarnings.summary.runup.janela_contem_earnings` igual a true: o "
    "balanço JÁ ocorreu, há `pregoes_desde_earnings` pregões, e o próximo está "
    "distante. Escreva no passado ('reagiu com +X%'), NUNCA no futuro ('chega "
    "esticado ao balanço'). Use `runup_atual_ex_evento_pct`, não o "
    "`runup_atual_pct` bruto, que inclui o próprio salto do balanço.\n\n"

    "## 5. Postura\n"
    "O valor da análise está nos CRUZAMENTOS, não em repetir a tabela: nível de "
    "reação que coincide com média móvel, run-up atual contra o padrão "
    "histórico, beta contra momentum do setor, upside do consenso contra a "
    "tendência do gráfico, DCF acima do preço com o papel abaixo da MM200.\n"
    "Use só a camada que veio: sem valuation nem alvos, diga em uma linha que a "
    "fundamental não estava disponível e siga — nunca preencha de memória.\n"
    "NÃO recomende comprar ou vender. Descreva cenários e níveis de "
    "invalidação; a decisão é do leitor. Sem juridiquês e sem disclaimer "
    "genérico no fim — o leitor sabe o que é."
)


def _buscar_fundamento(ticker: str) -> tuple[dict, list[str]]:
    """Camada fundamental das fontes do app. Cada bloco é opcional: fonte
    fora do ar (ou sem chave de API) vira ausência no prompt, nunca erro —
    a análise técnica sozinha ainda vale. Devolve (dados, fontes_usadas)."""
    fundamento: dict = {}
    fontes: list[str] = []

    def _estourou() -> bool:
        """Teto proprio da camada opcional, conferido ENTRE os blocos.

        Sem thread nem sinal, mesmo padrao de bounded_parallel.
        deadline_exceeded: cada bloco que ainda cabe roda inteiro, e o
        primeiro que nao cabe simplesmente nao comeca. Bloco que nao rodou
        vira ausencia no prompt -- que e o comportamento que esta camada ja
        tinha para fonte fora do ar."""
        gasto = time.monotonic() - _inicio_fundamento
        if gasto < _TETO_FUNDAMENTO_S:
            return False
        print(f"[analise_rapida_ia] camada fundamental atingiu o teto de "
              f"{_TETO_FUNDAMENTO_S:.0f}s ({gasto:.0f}s gastos); seguindo com o que ja tem",
              file=sys.stderr, flush=True)
        return True

    _inicio_fundamento = time.monotonic()

    try:
        # Mesma extração do get_fundamentals.py (que não dá pra importar
        # daqui: ele usa import plano `from security import`, que não
        # resolve no contexto de pacote deste script).
        info = yf.Ticker(ticker).info or {}
        alvo_medio = info.get("targetMeanPrice")
        preco = info.get("regularMarketPrice") or info.get("currentPrice")
        analistas = {
            "consenso": REC_LABELS.get(info.get("recommendationKey", "")) or None,
            "alvoMedio": alvo_medio,
            "alvoAlto": info.get("targetHighPrice"),
            "alvoBaixo": info.get("targetLowPrice"),
            "numAnalistas": info.get("numberOfAnalystOpinions"),
            "upsidePct": (
                round((alvo_medio - preco) / preco * 100, 1)
                if alvo_medio and preco else None
            ),
        }
        analistas = {k: v for k, v in analistas.items() if v is not None}
        if analistas:
            fundamento["alvosAnalistas"] = analistas
            fontes.append("alvos de analistas (yfinance)")
    except Exception as e:  # noqa: BLE001
        print(f"[analise_rapida_ia] alvos indisponíveis: {e}", file=sys.stderr)

    if _estourou():
        return fundamento, fontes

    try:
        val = tools.get_fundamentals_valuation(ticker) or {}
        if val.get("configured") and not val.get("error"):
            limpo = {k: v for k, v in val.items()
                     if k not in ("configured", "ticker") and v is not None}
            if limpo:
                fundamento["valuation"] = limpo
                fontes.append("valuation/DCF (FMP)")
    except Exception as e:  # noqa: BLE001
        print(f"[analise_rapida_ia] valuation indisponível: {e}", file=sys.stderr)

    if _estourou():
        return fundamento, fontes

    try:
        noticias = (tools.get_news([ticker], max_items=MAX_NOTICIAS) or {}).get(ticker) or []
        manchetes = [
            {"title": sanitize_for_llm(str(n.get("title") or "")),
             "summary": sanitize_for_llm(str(n.get("summary") or ""))[:280]}
            for n in noticias[:MAX_NOTICIAS] if n.get("title")
        ]
        if manchetes:
            fundamento["manchetes"] = manchetes
            fontes.append("notícias do feed")
    except Exception as e:  # noqa: BLE001
        print(f"[analise_rapida_ia] notícias indisponíveis: {e}", file=sys.stderr)

    return fundamento, fontes


# Divergência a partir da qual os painéis não estão mais "só" defasados por
# segundos de fetch: 1% num papel de US$270 é US$2,70, muito acima do que a
# diferença de timing entre quatro requisições explica.
_DIVERGENCIA_PRECO_PCT = 1.0


def _preco_canonico(dados: dict) -> dict | None:
    """UM preço para o texto inteiro citar, com as divergências expostas.

    Os quatro painéis buscam preço de forma independente -- get_trend e
    get_technicals do último candle diário, o snapshot do fast_info ao vivo, a
    reação a earnings do próprio histórico. Com o mercado aberto eles NÃO
    coincidem, e o modelo escolhia qualquer um.

    Visto em produção (NBIS, 17/08/2026 10:37 BRT): quatro preços no mesmo
    retrato -- $270,28 na Técnica, $269,87 nos Níveis, $269,98 na reação e
    $277,68 na Tendência (cache pré-abertura). A análise abriu o "Quadro
    geral" com os $277,68 dizendo que o papel estava colado na máxima, e a
    "Leitura técnica" três parágrafos abaixo disse que ele caía 2,66% a
    $270,28. O leitor do primeiro parágrafo sai com a impressão oposta à
    realidade.

    Canônico é o snapshot (`fast_info.last_price`): é o único buscado ao vivo
    de propósito, o painel que responde "onde o papel está AGORA" (ver a
    docstring de get_ticker_snapshot.py). Sem ele, cai para a Técnica, depois
    a reação, e a Tendência por último -- justamente a que pode vir de cache.
    """
    # A camada fundamental entra por ÚLTIMO, e de propósito.
    #
    # Ela não estava aqui até 18/08/2026, e essa era a maior cegueira do
    # detector: o preço da valuation vem da FMP (ou do fast_info como reserva)
    # numa requisição própria, e foi ele quem produziu a divergência que
    # apareceu em TODAS as análises daquele dia -- US$ 225,01 na valuation
    # contra o preço dos painéis ao vivo. Sem estar na lista, `divergenciaPct`
    # nunca era calculado, e o modelo ficava por conta própria: o anthropic
    # sinalizou a contradição, o gemini a usou como argumento altista
    # ("comparado ao preço atual, o DCF aponta espaço ainda maior").
    #
    # Último na ordem porque `validos[0]` vira o preço canônico: a valuation é
    # a menos indicada para responder "onde o papel está agora", já que a FMP
    # atualiza em ritmo próprio. Ela entra para SER COMPARADA, não para mandar.
    candidatos = [
        ("niveis", ((dados.get("snapshot") or {}).get("price"))),
        ("tecnica", ((dados.get("technicals") or {}).get("price"))),
        ("reacaoEarnings", (((dados.get("reaction") or {}).get("summary") or {}).get("current_price"))),
        ("tendencia", ((dados.get("trend") or {}).get("price"))),
        ("valuation", (((dados.get("_fundamento") or {}).get("valuation") or {}).get("current_price"))),
    ]
    validos = [(fonte, float(p)) for fonte, p in candidatos
               if isinstance(p, (int, float)) and p > 0]
    if not validos:
        return None

    fonte, valor = validos[0]
    out: dict = {"valor": round(valor, 2), "fonte": fonte}

    precos = [p for _, p in validos]
    if len(precos) > 1:
        espalhamento = (max(precos) - min(precos)) / min(precos) * 100
        if espalhamento >= _DIVERGENCIA_PRECO_PCT:
            out["divergenciaPct"] = round(espalhamento, 2)
            out["porPainel"] = {f: round(p, 2) for f, p in validos}
    return out


def _niveis_ordenados(dados: dict, preco: float | None) -> list[dict] | None:
    """Todos os níveis do retrato ordenados do MAIOR para o menor, cada um com
    a distância até o preço atual.

    Existe porque ordenar três ou mais números é onde o modelo erra. Visto em
    produção (NBIS, 17/08/2026): "a MM200 (US$ 146,46) fica ENTRE S1 e S2
    (US$ 189,07)" -- a MM200 está abaixo das duas. Os três valores estavam
    corretos no JSON; o que falhou foi a comparação, justamente a única
    operação que a regra de "não calcule" permite.

    Mesmo princípio do veredito_validator: recalcular ANTES do prompt e
    entregar como fato, em vez de pedir que o LLM deduza. Aqui ele descreve
    uma lista já ordenada, não ordena.

    MM50/MM200/52 semanas vêm do snapshot (fast_info, a mesma fonte do preço
    canônico); MM20 e VWAP só existem na Técnica. Nível ausente simplesmente
    não entra na lista.
    """
    tec = dados.get("technicals") or {}
    snap = dados.get("snapshot") or {}
    resumo = (dados.get("reaction") or {}).get("summary") or {}

    candidatos = [
        ("máxima 52 semanas", snap.get("yearHigh")),
        ("mínima 52 semanas", snap.get("yearLow")),
        ("MM20", tec.get("sma20")),
        ("MM50", snap.get("sma50") if snap.get("sma50") is not None else tec.get("sma50")),
        ("MM200", snap.get("sma200") if snap.get("sma200") is not None else tec.get("sma200")),
        ("VWAP", tec.get("vwap")),
        ("R2 (banda de reação)", resumo.get("r2_price")),
        ("R1 (banda de reação)", resumo.get("r1_price")),
        ("S1 (banda de reação)", resumo.get("s1_price")),
        ("S2 (banda de reação)", resumo.get("s2_price")),
    ]

    niveis = []
    for rotulo, valor in candidatos:
        if not isinstance(valor, (int, float)) or valor <= 0:
            continue
        item = {"rotulo": rotulo, "valor": round(float(valor), 2)}
        if preco:
            item["distanciaPct"] = round((float(valor) / preco - 1) * 100, 2)
            item["ladoDoPreco"] = "acima" if valor > preco else "abaixo"
        niveis.append(item)

    if not niveis:
        return None
    return sorted(niveis, key=lambda n: n["valor"], reverse=True)


def _compactar(dados: dict) -> str:
    """JSON dos painéis com manchetes sanitizadas e teto de tamanho."""
    trend = dados.get("trend") or None
    if isinstance(trend, dict) and isinstance(trend.get("news"), dict):
        destaques = trend["news"].get("destaques") or []
        trend = dict(trend)
        trend["news"] = {
            **trend["news"],
            "destaques": [
                {"title": sanitize_for_llm(str(d.get("title") or "")), "tone": d.get("tone")}
                for d in destaques[:6]
            ],
        }
    preco_canonico = _preco_canonico(dados)
    payload = {
        "ticker": dados.get("ticker"),
        "benchmark": dados.get("benchmark"),
        # Primeiro campo de propósito: é o preço que o texto inteiro deve
        # citar, e vir no topo ajuda o modelo a ancorar nele.
        "precoAtual": preco_canonico,
        "niveisOrdenados": _niveis_ordenados(dados, (preco_canonico or {}).get("valor")),
        "tendencia": trend,
        "tecnica": dados.get("technicals") or None,
        "niveis": dados.get("snapshot") or None,
        "reacaoEarnings": dados.get("reaction") or None,
        "fundamento": dados.get("_fundamento") or None,
    }
    texto = json.dumps(payload, ensure_ascii=False)
    return texto[:MAX_DADOS_CHARS]


def analisar(dados: dict) -> dict:
    ticker = str(dados.get("ticker") or "").strip().upper()
    if not ticker:
        return {"error": "ticker é obrigatório"}
    if not any(dados.get(k) for k in ("trend", "technicals", "snapshot", "reaction")):
        return {"error": "Rode ao menos um dos três painéis antes da análise com IA"}

    # Busca a camada fundamental ANTES do prompt (rede lenta, mas é o que
    # separa análise de gráfico de análise de empresa).
    fundamento, fontes = _buscar_fundamento(ticker)
    if fundamento:
        dados = {**dados, "_fundamento": fundamento}

    # A camada fundamental é rede: yfinance.info, FMP e notícias. Numa fonte
    # lenta ela sozinha pode comer o orçamento, e aí chamar o LLM é garantir
    # que o Node mate o processo no meio -- o usuário paga os tokens e não
    # recebe nada. Melhor devolver um erro legível com o tempo já gasto.
    gasto = time.monotonic() - _INICIO
    # A divisão do tempo, explícita. Sem ela o log traz só o total, e descobrir
    # se foram as fontes ou o LLM vira aritmética sobre um número só -- foi o
    # que aconteceu em 18/08/2026, com o erro dizendo "143s de 135s" e nada
    # sobre onde os 143s foram parar.
    print(f"[analise_rapida_ia] coleta terminou em {gasto:.1f}s "
          f"(teto {_TETO_FUNDAMENTO_S:.0f}s); orçamento total {_ORCAMENTO_TOTAL_S:.0f}s",
          file=sys.stderr, flush=True)
    if gasto + _LLM_TIMEOUT_S > _ORCAMENTO_TOTAL_S:
        return {"error": (
            f"A coleta de dados levou {gasto:.0f}s e não sobra tempo para a "
            f"análise dentro do orçamento de {_ORCAMENTO_TOTAL_S:.0f}s. "
            f"Tente de novo em alguns minutos — alguma fonte externa está lenta."
        )}

    client = get_client()
    # O prazo vive no CLIENTE, não numa contagem de tentativas aqui fora.
    #
    # `create()` percorre a cadeia inteira por dentro: anthropic estoura 55s,
    # cai para o deepseek, outro tanto -- tudo numa chamada só, sem devolver o
    # controle. Contar "duas tentativas de 55s" daqui descrevia um mundo em que
    # uma chamada é uma tentativa, e nunca foi esse. Com seis provedores
    # configurados, uma chamada pode custar 330s contra os 135s de orçamento.
    #
    # Produção 18/08/2026: anthropic deu timeout, a cadeia caiu para o deepseek
    # por dentro, e o Node matou o processo aos 150s com stdoutParcial=0 -- sem
    # análise e sem erro legível.
    client.definir_orcamento(_INICIO + _ORCAMENTO_TOTAL_S, _LLM_TIMEOUT_S)
    conteudo = f"Dados calculados para {ticker}:\n\n{_compactar(dados)}"

    # Modelo fraco da cadeia devolvendo TOCO (resposta de uma linha) é falha
    # conhecida — playbook §4. Antes isso virava erro na tela e o usuário
    # clicava de novo, caindo no MESMO provedor: o toco não condena ninguém,
    # então a cadeia nunca avançava sozinha e o botão simplesmente não
    # funcionava enquanto aquele provedor estivesse ruim.
    #
    # Agora o toco faz a cadeia andar. Não usamos _condenar de propósito:
    # responder mal uma vez não é falha permanente (ver
    # FallbackClient.pular_provedor_atual).
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
        # DOIS relógios, porque são duas perguntas diferentes.
        #
        # `_llm_s` mede o create() inteiro -- e o create() percorre a cadeia por
        # dentro, sem devolver o controle. Atribuir esse tempo a
        # `client.provider_name` (que já é o provedor NOVO depois da troca)
        # carimba no vencedor o tempo gasto por quem falhou antes dele.
        #
        # Produção 18/08/2026, com AGENT_PROVIDER=deepseek:
        #
        #   [provider] deepseek failed: Request timed out.
        #   anthropic/claude-sonnet-5 respondeu em 91.5s
        #
        # O anthropic levou ~40s (bateu com o run anterior, sem fallback); os
        # outros ~52s foram o deepseek sendo cortado pelo timeout. Quem lesse
        # isso amanhã iria investigar o anthropic, que era o único inocente.
        _llm_s = time.monotonic() - _antes_llm
        _provedor_s = getattr(client, "ultimo_tempo_provedor_s", None)
        _tempo = (f"respondeu em {_provedor_s:.1f}s (cadeia inteira: {_llm_s:.1f}s)"
                  if _provedor_s is not None and _llm_s - _provedor_s >= 0.5
                  else f"respondeu em {_llm_s:.1f}s")
        print(f"[analise_rapida_ia] {client.provider_name}/{client.models['full']} "
              f"{_tempo} ({len(texto)} chars)",
              file=sys.stderr, flush=True)
        if len(texto) >= MIN_TEXTO_CHARS:
            break

        provedor, modelo = client.provider_name, client.models["full"]

        # Toco e truncamento parecem iguais daqui -- os dois chegam como texto
        # curto -- e sao problemas OPOSTOS: toco e o modelo respondendo de
        # menos; truncamento e o modelo produzindo tanto que nao sobrou espaco
        # para a resposta visivel.
        #
        # Modelo em modo thinking (deepseek-v4-pro) conta os tokens de
        # RACIOCINIO contra o max_tokens. Raciocinio longo esgota o teto e o
        # `content` volta VAZIO -- 0 chars depois de uma chamada lenta, que foi
        # o que apareceu em producao em 18/08/2026. Sem nomear a diferenca, o
        # log dizia "devolveu toco" e mandava investigar o lado errado.
        razao = (getattr(resp, "raw_stop_reason", "") or "").lower()
        n_raciocinio = len(getattr(resp, "reasoning_content", None) or "")
        truncado = razao in ("length", "max_tokens") or (not texto and n_raciocinio > 0)
        diagnostico = "truncou antes da resposta" if truncado else "devolveu toco"

        # stderr: o stdout deste script é EXCLUSIVO do JSON final (o Node
        # parseia). Diagnóstico aqui nunca pode vazar para lá.
        print(
            f"[analise_rapida_ia] {provedor}/{modelo} {diagnostico} "
            f"({len(texto)} chars, stop_reason={razao or 'n/d'}, "
            f"raciocinio={n_raciocinio} chars): {texto[:120]!r}",
            file=sys.stderr, flush=True,
        )

        # Orçamento antes de tentar outro: sem esta checagem a troca de
        # provedor levaria o processo além do teto e o Node o mataria no meio
        # (playbook §3), trocando um erro legível por um 500 genérico.
        gasto = time.monotonic() - _INICIO
        if gasto + _LLM_TIMEOUT_S > _ORCAMENTO_TOTAL_S:
            return {"error": (
                f"{provedor}/{modelo} {diagnostico} ({len(texto)} chars) e não sobra "
                f"tempo no orçamento ({gasto:.0f}s de {_ORCAMENTO_TOTAL_S:.0f}s) "
                f"para tentar outro provedor."
            )}

        if not client.pular_provedor_atual(f"{diagnostico} ({len(texto)} chars)"):
            return {"error": (
                f"Todos os provedores falharam em produzir a análise. "
                f"Último: {provedor}/{modelo} {diagnostico} com {len(texto)} chars."
            )}

    saida = {"markdown": texto, "usage": get_run_usage(), "fontes": fontes}
    # Texto cortado por teto de tokens tem que ser DITO: uma análise que para
    # no meio da frase, sem aviso, parece conclusão do modelo. Anthropic diz
    # "max_tokens"; a camada OpenAI-compat diz "length".
    if str(getattr(resp, "raw_stop_reason", "")).lower() in ("max_tokens", "length"):
        saida["truncado"] = True
    return saida


if __name__ == "__main__":
    try:
        args = json.loads(sys.stdin.read() or "{}")
    except Exception:
        args = {}
    try:
        saida = analisar(args)
    except Exception as e:  # noqa: BLE001 — quota/chave/provedor viram erro legível
        saida = {"error": str(e) or e.__class__.__name__}
    print(json.dumps(saida, ensure_ascii=False))
