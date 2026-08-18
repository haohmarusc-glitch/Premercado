"""
Serialização JSON que não quebra o consumidor com NaN.

`json.dumps` do Python emite os tokens `NaN`, `Infinity` e `-Infinity` por
extensão própria. Nenhum deles é JSON válido, e o `JSON.parse` do Node os
rejeita -- então UM campo não-finito torna a resposta INTEIRA ilegível, levando
junto todos os outros tickers que vieram certos.

Visto em produção (18/08/2026, /api/technicals):

    {"items": [{"ticker": "NVDA", "price": NaN, ...}]}

A rota devolveu 500 cinco vezes seguidas com "Parse error", e o painel Técnica
parou de funcionar por causa de um campo.

## Por que aqui e não na origem

O get_technicals já tapava UMA fonte de NaN (RSI com avg_loss=0, ver o
comentário lá). Mas NaN nasce em qualquer divisão por zero ou operação sobre
dado faltante, e tapar fonte por fonte é enxugar gelo: a próxima aparece num
campo que ninguém previu, com o mesmo estrago total.

A fronteira da serialização é o único lugar onde a garantia vale para o
payload inteiro, inclusive para os campos que ainda não existem.

## Vira null, não some

`None` (que o json escreve como `null`) em vez de omitir a chave: o front já
trata campo nulo -- é o que ele mostra quando um indicador não pôde ser
calculado -- enquanto chave AUSENTE viraria `undefined` e apareceria como
"—" ou quebraria um `.toFixed()`. Ausência de valor é o que queremos dizer,
e null é como se diz isso.
"""
from __future__ import annotations

import json
import math
from typing import Any


def _nativo(obj: Any) -> Any:
    """Escalar do numpy vira o tipo Python equivalente.

    Produção 18/08/2026, primeira coleta do risco macro:

        {"error": "Object of type bool is not JSON serializable"}

    A causa é uma assimetria fácil de não ver: `np.float64` É subclasse de
    `float` -- foi por isso que a limpeza de NaN funcionou sem ninguém pensar
    em numpy -- mas `np.bool_` NÃO é subclasse de `bool`, nem `np.int64` de
    `int`. Basta uma comparação sobre valor vindo do pandas (`preco <= -6`)
    para o resultado virar `np.bool_` e derrubar a resposta inteira.

    Fica aqui pelo mesmo motivo do NaN: a fronteira é o único ponto onde a
    garantia vale para o payload todo, inclusive para campos que ainda não
    existem. Qualquer script que deixe um escalar do numpy escapar de um
    cálculo com pandas passa por este ponto.
    """
    if isinstance(obj, (str, bytes, bool, int, float, type(None))):
        return obj
    item = getattr(obj, "item", None)
    if callable(item):
        try:
            return item()          # np.bool_/np.int64/np.float64 -> bool/int/float
        except Exception:          # noqa: BLE001 -- array com mais de um elemento
            return obj
    return obj


def limpar_nao_finitos(obj: Any) -> Any:
    """Troca NaN/Infinity por None e normaliza escalar do numpy, recursivamente.

    bool antes de float de propósito: `isinstance(True, float)` é False em
    Python, mas int/bool passam por caminhos diferentes e a ordem evita
    surpresa se alguém trocar a checagem por Number.
    """
    obj = _nativo(obj)
    if isinstance(obj, float):
        return None if not math.isfinite(obj) else obj
    if isinstance(obj, dict):
        return {k: limpar_nao_finitos(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [limpar_nao_finitos(v) for v in obj]
    return obj


def dumps(obj: Any, **kwargs: Any) -> str:
    """json.dumps com a garantia de que a saída é JSON de verdade.

    `allow_nan=False` faria o dumps LEVANTAR em vez de emitir NaN -- o que
    troca uma resposta ilegível por resposta nenhuma. Limpar antes é melhor:
    o consumidor recebe os campos bons e um null onde não havia número.
    """
    kwargs.setdefault("ensure_ascii", False)
    return json.dumps(limpar_nao_finitos(obj), **kwargs)
