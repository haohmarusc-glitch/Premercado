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
import sys

from agent.startup_probe import boot as _probe_boot, imports_prontos as _probe_imports

_probe_boot()

import yfinance as yf

from agent.provider import get_client, get_run_usage, texto_da_resposta
from agent.security import sanitize_for_llm
from agent import tools

_probe_imports()

MAX_NOTICIAS = 6
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
# Teto do JSON de dados no prompt — a tela manda o que coletou, mas um
# payload anômalo não pode virar um prompt gigante cobrado por token.
MAX_DADOS_CHARS = 14_000

SYSTEM = (
    "Você é um analista de mercado escrevendo em português do Brasil, para o dono "
    "de uma carteira que também é o operador do sistema que gerou os dados.\n\n"
    "Você receberá um JSON com os números calculados pelo sistema para UM ticker: "
    "tendência (score e componentes), técnica (RSI, MACD, médias, VWAP, RVOL), "
    "níveis (faixa de 52 semanas, MM50/MM200, vol anual, beta vs benchmark, momentum "
    "do setor) e estatística de reação a earnings (médias, níveis R1/R2/S1/S2, "
    "run-up, eventos passados). Quando disponíveis, também recebe a camada "
    "fundamental: alvos de analistas (consenso, alvo médio/alto/baixo, upside), "
    "valuation (DCF, P/L, P/VP, ROE, EV/EBITDA) e manchetes recentes.\n\n"
    "TAMANHO — a regra mais importante, e a que os modelos mais desrespeitam:\n"
    "cada seção tem NO MÁXIMO 2 parágrafos, e cada parágrafo NO MÁXIMO 4 "
    "linhas. O texto inteiro fica entre 400 e 700 palavras. Não é sugestão: "
    "análise que passa disso é cortada pelo sistema no meio da frase e chega "
    "truncada ao leitor. Prefira cortar adjetivo e repetição a cortar "
    "conteúdo; se faltar espaço, elimine a paráfrase dos números (o leitor "
    "vê a tabela ao lado) e mantenha os cruzamentos.\n\n"
    "Escreva uma análise em markdown com EXATAMENTE estas seções:\n"
    "## Quadro geral\n## Fundamento e valuation\n## Leitura técnica\n"
    "## Níveis que importam\n## Earnings e volatilidade\n## Síntese\n\n"
    "Regras invioláveis:\n"
    "- Cite SOMENTE números presentes no JSON. Não invente preço, data, "
    "resultado ou estatística. Campo ausente ou null = não mencione.\n"
    "- MOEDA: são ativos listados em bolsa dos EUA. Todo valor monetário é "
    "DÓLAR — escreva US$ 277,68 ou $277,68, nunca R$. Não converta para real.\n"
    "- PREÇO: cite como preço atual APENAS `precoAtual.valor`. Os painéis "
    "trazem preços próprios, buscados em instantes diferentes, e misturá-los "
    "produz texto que se contradiz. Se `precoAtual.divergenciaPct` existir, os "
    "painéis discordam mais do que o intervalo entre as buscas explica: diga "
    "isso em uma linha na Leitura técnica, citando `porPainel`, e trate os "
    "indicadores do painel mais distante com ressalva — provavelmente ele "
    "está defasado.\n"
    "- RVOL: se `rvolSignal` for `indefinido_abertura`, o pregão tem menos de "
    "30 minutos e o RVOL está inflado pelo leilão de abertura. NÃO o use como "
    "sinal de convicção nem conclua nada sobre força compradora ou realização "
    "de lucro a partir dele; se mencionar, diga que ainda não é conclusivo.\n"
    "- Os níveis R1/R2/S1/S2 NÃO são suporte e resistência técnicos: são "
    "bandas estatísticas de volatilidade (o preço atual ± a reação histórica "
    "média a earnings). Não os descreva como 'zona de defesa', 'piso' ou "
    "'resistência do gráfico', e não os compare com alvo de analista como se "
    "medissem a mesma coisa. Suporte/resistência de verdade só a partir de "
    "máximas/mínimas e médias móveis presentes no JSON.\n"
    "- EARNINGS JÁ OCORRIDO: se reacaoEarnings.summary.runup."
    "janela_contem_earnings for true, o último balanço aconteceu há "
    "`pregoes_desde_earnings` pregões e o próximo ainda está distante. Nesse "
    "caso escreva no passado ('reagiu com +X%'), NUNCA no futuro ('chega "
    "esticado ao balanço', 'o combustível para a reação já foi consumido'). "
    "Use `runup_atual_ex_evento_pct` — não o `runup_atual_pct` bruto, que "
    "inclui o próprio salto do balanço — ao comentar o quanto o papel está "
    "esticado.\n"
    "- Cruze os dados entre si (ex.: nível de reação que coincide com média "
    "móvel; run-up atual vs padrão histórico; beta vs momentum do setor) — o "
    "valor da análise está nos cruzamentos, não em repetir a tabela.\n"
    "- NÃO recomende comprar ou vender. Descreva cenários e níveis de "
    "invalidação; a decisão é do leitor.\n"
    "- A seção de fundamento usa SÓ o que veio: se não houver valuation nem "
    "alvos, diga em uma linha que a camada fundamental não estava disponível "
    "e siga — nunca preencha de memória.\n"
    "- Confronte fundamento com técnica quando ambos vierem (ex.: upside do "
    "consenso vs tendência do gráfico; DCF acima do preço vs papel abaixo da "
    "MM200) — é onde a análise ganha valor.\n"
    "- Sem juridiquês, sem disclaimer genérico no fim — o leitor sabe o que é.\n"
    "- Releia o limite de tamanho antes de escrever a Síntese: ela é a seção "
    "que mais importa e é a primeira a ser perdida se o texto esticar."
)


def _buscar_fundamento(ticker: str) -> tuple[dict, list[str]]:
    """Camada fundamental das fontes do app. Cada bloco é opcional: fonte
    fora do ar (ou sem chave de API) vira ausência no prompt, nunca erro —
    a análise técnica sozinha ainda vale. Devolve (dados, fontes_usadas)."""
    fundamento: dict = {}
    fontes: list[str] = []

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
    candidatos = [
        ("niveis", ((dados.get("snapshot") or {}).get("price"))),
        ("tecnica", ((dados.get("technicals") or {}).get("price"))),
        ("reacaoEarnings", (((dados.get("reaction") or {}).get("summary") or {}).get("current_price"))),
        ("tendencia", ((dados.get("trend") or {}).get("price"))),
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
    payload = {
        "ticker": dados.get("ticker"),
        "benchmark": dados.get("benchmark"),
        # Primeiro campo de propósito: é o preço que o texto inteiro deve
        # citar, e vir no topo ajuda o modelo a ancorar nele.
        "precoAtual": _preco_canonico(dados),
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

    client = get_client()
    resp = client.create(
        model=client.models["full"],
        max_tokens=MAX_TOKENS,
        system=SYSTEM,
        tools=[],
        messages=[{
            "role": "user",
            "content": f"Dados calculados para {ticker}:\n\n{_compactar(dados)}",
        }],
    )
    texto = texto_da_resposta(resp)
    if len(texto) < 200:
        # Modelo fraco da cadeia devolvendo tocos é falha conhecida (ver
        # playbook §4) — melhor erro explícito que "análise" de uma linha.
        return {"error": "O modelo devolveu uma resposta curta demais; tente de novo"}

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
