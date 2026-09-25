"""
Resultado REALIZADO por ticker -- o que as vendas já fechadas deram, e a
faixa de preço pago em cada papel.

## Por que existe

O chat não conseguia ver ticker vendido nenhum. A única ferramenta de carteira
que ele tinha, `get_portfolio_snapshot`, começa por

    if qty <= 0.00001:
        continue

e posição totalmente vendida fica com quantity = 0 (ver `recomputePosition` em
routes/portfolio.ts). Ou seja: a linha continua no banco, com todo o histórico
de compra e venda, o frontend a usa para montar a seção "Ações Vendidas" -- e
o chat recebia a carteira sem ela. Perguntado sobre o que foi vendido, ele não
tinha o dado, então respondia que não havia.

Pedido que expôs isso (25/09/2026): "os 7 tickers que mais deram lucro, com o
menor e o maior valor pago em cada". Nada disso estava ao alcance do chat --
nem o lucro realizado, nem o preço dos lotes, só `avgCost`, que é a média dos
lotes AINDA ABERTOS.

## De onde vem cada número

`GET /portfolio` traz TODAS as posições (inclusive as zeradas) e
`GET /portfolio/:id/purchases` traz os lotes de cada uma, com
`purchasePrice`, `saleDate` e `salePrice`. O lucro sai lote por lote:

    qty      = amount / purchasePrice          (amount é o valor investido em USD)
    proceeds = qty * salePrice
    lucro    = proceeds - amount

Não é `(salePrice - purchasePrice) * quantity_da_posicao`: cada lote tem seu
próprio preço, e usar um preço médio para todos apaga justamente a informação
que o pedido quer (o menor e o maior pago).

## O que este módulo NÃO faz

Não adivinha. Lote vendido sem `salePrice`, ou com `purchasePrice` nulo, não
entra na conta e é CONTADO em `lotes_incomputaveis`, com o motivo. Uma soma de
lucro que ignora em silêncio os lotes que não sabe calcular é pior que
nenhuma: parece completa. O mesmo vale para a faixa de preço -- `precoMin` e
`precoMax` só saem de lotes que têm preço.

Simulado fica FORA por padrão. Misturar posição simulada com dinheiro real
inflaria o ranking com lucro que ninguém recebeu; `incluir_simulado=True`
existe para quem quer olhar o simulado, e o retorno sempre diz quantas
posições foram excluídas.
"""
from __future__ import annotations

import os

from .http_retry import SESSION


def _internal_headers() -> dict:
    key = os.environ.get("OPERATOR_API_KEY", "")
    return {"Authorization": f"Bearer {key}"} if key else {}


def _api_url() -> str:
    return os.environ.get("INTERNAL_API_URL", "http://localhost:5000")


def _num(v) -> float | None:
    """O driver pg devolve `numeric` como string; None e "" não são zero."""
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # NaN != NaN


def _lucro_do_lote(lote: dict) -> tuple[float, float, float] | str:
    """(custo, proceeds, lucro) ou o motivo de não dar para calcular."""
    amount = _num(lote.get("amount"))
    preco_compra = _num(lote.get("purchasePrice"))
    preco_venda = _num(lote.get("salePrice"))
    if amount is None or amount <= 0:
        return "lote sem valor investido"
    if preco_compra is None or preco_compra <= 0:
        return "lote sem preço de compra"
    if preco_venda is None or preco_venda <= 0:
        return "lote vendido sem preço de venda registrado"
    qty = amount / preco_compra
    proceeds = qty * preco_venda
    return (amount, proceeds, proceeds - amount)


def _preco_atual(ticker: str) -> tuple[float, str] | None:
    """(preço, de onde veio) ou None.

    A fonte vem junto de propósito. Preço sem dizer se é negociação ao vivo,
    último fechamento ou pré-mercado é o mesmo problema que apareceu no Plano
    de Saída em 21/09: dois números certos de instantes diferentes lidos como
    contradição. Aqui é pior, porque o número vai ao lado de preços de compra
    históricos, e a comparação é o ponto do relatório.

    Import tardio: tools.py importa ESTE módulo, então importá-lo no topo
    fecharia o ciclo. Mesmo padrão de portfolio_snapshot.
    """
    from .tools import get_stock_data
    try:
        q = get_stock_data(ticker)
    except Exception:
        return None
    if not isinstance(q, dict):
        return None
    for campo, fonte in (
        ("regular_market_price", "mercado"),
        ("last_close", "último fechamento"),
        ("pre_market_price", "pré-mercado"),
    ):
        v = _num(q.get(campo))
        if v is not None and v > 0:
            return (v, fonte)
    return None


def resultado_realizado(top: int = 7, incluir_simulado: bool = False,
                        incluir_preco_atual: bool = True) -> dict:
    """
    Lucro REALIZADO por ticker (vendas já fechadas), do maior para o menor, com
    o menor e o maior preço pago em cada papel.

    Use quando a pergunta for sobre o que JÁ FOI VENDIDO, lucro/prejuízo
    realizado, quais papéis deram mais dinheiro, ou preço de compra histórico.
    `get_portfolio_snapshot` NÃO serve para isso: ele só devolve posição aberta,
    e papel totalmente vendido fica com quantidade zero e desaparece dele.

    top: quantos tickers no ranking (0 = todos). incluir_simulado: por padrão
    só dinheiro real.
    """
    try:
        r = SESSION.get(f"{_api_url()}/api/portfolio",
                        headers=_internal_headers(), timeout=10)
        r.raise_for_status()
        posicoes = r.json()
    except Exception as e:
        return {"error": f"não foi possível ler a carteira: {e}", "tickers": []}

    if not isinstance(posicoes, list):
        return {"error": "resposta inesperada da API de carteira", "tickers": []}

    por_ticker: dict[str, dict] = {}
    simuladas_fora = 0
    posicoes_sem_lote = []

    for p in posicoes:
        if not isinstance(p, dict):
            continue
        if bool(p.get("isSimulated")) and not incluir_simulado:
            simuladas_fora += 1
            continue
        ticker = str(p.get("ticker") or "").upper()
        pid = p.get("id")
        if not ticker or pid is None:
            continue
        try:
            lr = SESSION.get(f"{_api_url()}/api/portfolio/{pid}/purchases",
                             headers=_internal_headers(), timeout=10)
            lr.raise_for_status()
            lotes = lr.json()
        except Exception as e:
            posicoes_sem_lote.append({"ticker": ticker, "motivo": str(e)[:120]})
            continue
        if not isinstance(lotes, list):
            posicoes_sem_lote.append({"ticker": ticker, "motivo": "resposta inesperada"})
            continue

        t = por_ticker.setdefault(ticker, {
            "ticker": ticker,
            "lucroRealizado": 0.0,
            "custoVendido": 0.0,
            "recebido": 0.0,
            "qtdVendida": 0.0,
            "lotesVendidos": 0,
            "lotesAbertos": 0,
            "lotesIncomputaveis": [],
            "precos": [],
            "primeiraVenda": None,
            "ultimaVenda": None,
        })

        for lote in lotes:
            if not isinstance(lote, dict):
                continue
            preco = _num(lote.get("purchasePrice"))
            if preco is not None and preco > 0:
                t["precos"].append(preco)
            vendido = bool(lote.get("saleDate"))
            if not vendido:
                t["lotesAbertos"] += 1
                continue
            r_lote = _lucro_do_lote(lote)
            if isinstance(r_lote, str):
                t["lotesIncomputaveis"].append({
                    "compra": lote.get("purchaseDate"),
                    "venda": lote.get("saleDate"),
                    "motivo": r_lote,
                })
                continue
            custo, proceeds, lucro = r_lote
            t["lucroRealizado"] += lucro
            t["custoVendido"] += custo
            t["lotesVendidos"] += 1
            # Para o preço médio de VENDA: recebido / quantidade vendida. É o
            # único jeito de responder "vendi cedo?" -- ver a nota em
            # precoAtualVsVendaPct.
            t["recebido"] += proceeds
            preco_compra = _num(lote.get("purchasePrice")) or 0
            if preco_compra > 0:
                t["qtdVendida"] += custo / preco_compra
            data = str(lote.get("saleDate"))
            if t["primeiraVenda"] is None or data < t["primeiraVenda"]:
                t["primeiraVenda"] = data
            if t["ultimaVenda"] is None or data > t["ultimaVenda"]:
                t["ultimaVenda"] = data

    saida = []
    for t in por_ticker.values():
        precos = t.pop("precos")
        linha = {
            "ticker": t["ticker"],
            "lucroRealizadoUsd": round(t["lucroRealizado"], 2),
            "custoDoQueFoiVendidoUsd": round(t["custoVendido"], 2),
            "lucroRealizadoPct": (
                round(t["lucroRealizado"] / t["custoVendido"] * 100, 2)
                if t["custoVendido"] > 0 else None
            ),
            "menorPrecoPagoUsd": round(min(precos), 4) if precos else None,
            "maiorPrecoPagoUsd": round(max(precos), 4) if precos else None,
            # Preço médio de VENDA, ponderado pela quantidade (recebido /
            # quantidade vendida), não média simples dos preços de venda: lote
            # de 10 ações e lote de 1 não pesam igual.
            "precoVendaMedioUsd": (
                round(t["recebido"] / t["qtdVendida"], 4)
                if t["qtdVendida"] > 0 else None
            ),
            "lotesComPreco": len(precos),
            "lotesVendidos": t["lotesVendidos"],
            "lotesAbertos": t["lotesAbertos"],
            "primeiraVenda": t["primeiraVenda"],
            "ultimaVenda": t["ultimaVenda"],
        }
        if t["lotesIncomputaveis"]:
            # Fica NA LINHA do ticker, não num rodapé: quem lê o lucro dele
            # precisa ver ali que a soma está incompleta.
            linha["lotesIncomputaveis"] = t["lotesIncomputaveis"]
        saida.append(linha)

    # Só quem realmente vendeu entra no ranking de lucro realizado. Papel com
    # lote 100% aberto tem lucro realizado 0, e 0 no meio de um ranking de
    # "quem deu mais lucro" se lê como "vendi e não ganhei nada".
    vendidos = [x for x in saida if x["lotesVendidos"] > 0]
    vendidos.sort(key=lambda x: x["lucroRealizadoUsd"], reverse=True)
    ranking = vendidos[:top] if top and top > 0 else vendidos

    # O preço atual é buscado DEPOIS do corte, só para quem está no ranking:
    # uma cotação por ticker exibido, não por ticker da carteira. Com 11
    # vendedores e top=7, buscar antes pagaria 4 cotações que ninguém vê.
    if incluir_preco_atual:
        for linha in ranking:
            atual = _preco_atual(linha["ticker"])
            if atual is None:
                # Cotação que não veio é DITA. Sem isto, o campo ausente é
                # indistinguível de "papel sem preço" e o modelo preenche de
                # memória.
                linha["precoAtualUsd"] = None
                linha["precoAtualNota"] = "cotação não disponível agora"
                continue
            preco, fonte = atual
            linha["precoAtualUsd"] = round(preco, 4)
            linha["precoAtualFonte"] = fonte
            maior = linha.get("maiorPrecoPagoUsd")
            menor = linha.get("menorPrecoPagoUsd")
            venda = linha.get("precoVendaMedioUsd")
            # Contra o MAIOR pago: é a compra que mais doeu, e é dela que sai a
            # pergunta útil ("o papel já passou do meu pior preço?").
            if maior:
                linha["precoAtualVsMaiorPagoPct"] = round((preco / maior - 1) * 100, 2)
            if menor:
                linha["precoAtualVsMenorPagoPct"] = round((preco / menor - 1) * 100, 2)
            # Contra o preço de VENDA -- e este é o único que responde "vendi
            # cedo?". Positivo = o papel subiu depois que você saiu; negativo =
            # a venda pegou um preço melhor que o de hoje.
            #
            # Existe porque o relatório de 25/09/2026 concluiu "META e INTC
            # continuam subindo após vendidas" a partir da coluna
            # precoAtualVsMaiorPagoPct, que compara com o PREÇO PAGO. Pode ser
            # que a conclusão estivesse certa; o número citado não a
            # sustentava, e sem este campo não havia como sustentá-la.
            if venda:
                linha["precoAtualVsVendaPct"] = round((preco / venda - 1) * 100, 2)

    resultado: dict = {
        "tickers": ranking,
        "tickersComVenda": len(vendidos),
        # DOIS totais, com o escopo no nome. Antes havia só
        # `totalLucroRealizadoUsd`, que cobre TODOS os que venderam -- e o
        # relatório de 25/09/2026 o publicou como "Total de lucro realizado
        # (top 7)". Os sete exibidos somavam US$ 385,88; o campo trazia US$
        # 401,52, dos onze. O modelo não tinha como saber a diferença, e somar
        # sete linhas de cabeça para conferir é justamente o que ele não deve
        # fazer.
        "somaDoRankingUsd": round(sum(x["lucroRealizadoUsd"] for x in ranking), 2),
        "totalLucroRealizadoTodosOsTickersUsd": round(
            sum(x["lucroRealizadoUsd"] for x in vendidos), 2),
        "criterio": (
            "lucro realizado em USD dos lotes com venda registrada. "
            "somaDoRankingUsd = só os tickers desta lista; "
            "totalLucroRealizadoTodosOsTickersUsd = os "
            f"{len(vendidos)} que venderam. Não troque um pelo outro. "
            "menorPrecoPagoUsd/maiorPrecoPagoUsd cobrem TODOS os lotes do "
            "ticker, vendidos e abertos. precoAtualUsd vem com "
            "precoAtualFonte (mercado / último fechamento / pré-mercado) -- "
            "cite a fonte junto do preço. Para dizer se o papel subiu ou caiu "
            "DEPOIS da venda, use precoAtualVsVendaPct; "
            "precoAtualVsMaiorPagoPct compara com o preço PAGO e não responde "
            "essa pergunta"),
    }
    if not incluir_simulado and simuladas_fora:
        resultado["posicoesSimuladasExcluidas"] = simuladas_fora
    if posicoes_sem_lote:
        resultado["posicoesSemLotes"] = posicoes_sem_lote
    if not vendidos:
        resultado["nota"] = (
            "Nenhum lote com venda registrada na carteira real. Isto NÃO "
            "significa que nada foi vendido -- significa que nenhuma venda foi "
            "lançada no app (data e preço de venda no lote)."
        )
    return resultado
