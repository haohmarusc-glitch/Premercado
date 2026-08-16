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

from agent.provider import get_client, get_run_usage
from agent.security import sanitize_for_llm
from agent import tools

_probe_imports()

MAX_NOTICIAS = 6
REC_LABELS = {
    "strongBuy": "compra forte", "buy": "compra", "hold": "manter",
    "sell": "venda", "strongSell": "venda forte",
}

MAX_TOKENS = 2500
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
    "Escreva uma análise em markdown com EXATAMENTE estas seções:\n"
    "## Quadro geral\n## Fundamento e valuation\n## Leitura técnica\n"
    "## Níveis que importam\n## Earnings e volatilidade\n## Síntese\n\n"
    "Regras invioláveis:\n"
    "- Cite SOMENTE números presentes no JSON. Não invente preço, data, "
    "resultado ou estatística. Campo ausente ou null = não mencione.\n"
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
    "- Extensão alvo: 400 a 700 palavras."
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
    texto = " ".join(
        b.get("text", "") for b in resp.content
        if isinstance(b, dict) and b.get("type") == "text"
    ).strip()
    if len(texto) < 200:
        # Modelo fraco da cadeia devolvendo tocos é falha conhecida (ver
        # playbook §4) — melhor erro explícito que "análise" de uma linha.
        return {"error": "O modelo devolveu uma resposta curta demais; tente de novo"}
    return {"markdown": texto, "usage": get_run_usage(), "fontes": fontes}


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
