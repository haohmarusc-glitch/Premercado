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


def resultado_realizado(top: int = 7, incluir_simulado: bool = False) -> dict:
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
            custo, _proceeds, lucro = r_lote
            t["lucroRealizado"] += lucro
            t["custoVendido"] += custo
            t["lotesVendidos"] += 1
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

    resultado: dict = {
        "tickers": ranking,
        "tickersComVenda": len(vendidos),
        "totalLucroRealizadoUsd": round(sum(x["lucroRealizadoUsd"] for x in vendidos), 2),
        "criterio": ("lucro realizado em USD dos lotes com venda registrada; "
                     "menorPrecoPagoUsd/maiorPrecoPagoUsd cobrem TODOS os lotes "
                     "do ticker, vendidos e abertos"),
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
