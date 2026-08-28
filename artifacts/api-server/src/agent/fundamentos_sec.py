"""
fundamentos_sec.py — múltiplos TTM calculados dos próprios arquivos da SEC.

Por que existe: `get_fundamentals_valuation()` (tools.py) depende da FMP para
P/L, P/VP, ROE e EV/EBITDA, e a FMP responde 402 nesses endpoints -- o plano
da conta não os cobre (ver `_FMP_RECUSAS` em news_sources.py: a chave é
válida, o acesso é que não vem junto). Trocar de provedor pago move a
dependência de lugar; calcular dos números publicados a elimina.

O dado vem do `companyfacts` da SEC, que é XBRL do próprio arquivamento --
a mesma fonte que o auditor da empresa assinou. E a encanação já existe no
repo: `EDGAR_HEADERS`, `_resolve_cik()` (dict fixo + mapa oficial da SEC) e
o `SESSION` com retry já são usados por três outros pontos.

## O que este módulo NÃO faz

Não calcula DCF. Valor justo exige WACC, que exige beta, prêmio de risco e
custo de dívida -- suposição empilhada em suposição, e uma superfície grande
para produzir exatamente o que este repo passa o tempo caçando: um número
plausível e errado. Ficou para decisão separada.

## As armadilhas, e o que cada uma faria se não fosse tratada

Todas produzem número PLAUSÍVEL. É isso que as torna caras: nenhuma levanta
exceção, todas entregam um múltiplo com cara de certo.

1. **YTD somado como trimestre.** No 10-Q, muito emissor publica acumulado do
   ano, não o trimestre isolado. Somar quatro "trimestres" desses conta o Q1
   até quatro vezes e infla a receita TTM. Tratado em `_trimestres_de()`:
   duração de cada fato é medida, e trimestre embutido em acumulado sai por
   DIFERENÇA contra o acumulado anterior do mesmo ano fiscal.

2. **Reapresentação.** O mesmo período aparece mais de uma vez quando a
   empresa republica. Pegar o primeiro da lista devolve o número velho.
   Tratado em `_mais_recente()`: desempate por `filed`, depois `accn`.

3. **Patrimônio médio no ROE.** Lucro do ANO inteiro sobre patrimônio de UM
   instante mistura fluxo com estoque. Usa média entre o atual e o de um ano
   atrás quando os dois existem.

4. **P/VP não é TTM.** Patrimônio é estoque: vem do balanço mais recente, sem
   soma de trimestre nenhuma.

5. **Ações em circulação, não média diluída.** Capitalização é preço de hoje
   × ações de hoje (`dei:EntityCommonStockSharesOutstanding`, da capa do
   arquivamento). A média ponderada diluída do período é outro número, e usá-la
   data a capitalização no passado.

6. **EBITDA reconstruído.** Não existe tag de EBITDA: é operacional + D&A, e
   D&A às vezes só aparece no fluxo de caixa, às vezes com tag que não bate.
   Sem D&A confiável o múltiplo sai `indisponivel` com motivo -- não estimado.

7. **Foreign private issuer.** Quem arquiva 20-F/40-F reporta em IFRS, com
   outro conjunto de tags. Adaptar em silêncio produziria número calculado
   sobre conceito diferente. Estes ficam explicitamente NÃO SUPORTADOS até
   existir uma etapa de IFRS -- é o caso de BABA e SKHY (o repo já tem nota
   sobre a SKHY ser foreign private issuer em tools.py).

## Proveniência não é enfeite

Cada métrica sai com o período, o formulário, o accession number, a data do
arquivamento e as tags XBRL usadas. Sem isso, conferir um múltiplo contra o
10-Q obriga a adivinhar de onde ele veio -- e um número que não dá para
conferir é indistinguível de um número errado.
"""
from __future__ import annotations

import datetime as _dt
import sys
from typing import Any, Iterable

try:
    from cache import cached
    from http_retry import SESSION
    from security import sanitize_ticker
except ImportError:  # pragma: no cover - caminho de pacote
    from .cache import cached
    from .http_retry import SESSION
    from .security import sanitize_ticker


COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

# Arquivamento de foreign private issuer. Ver armadilha 7 no cabeçalho.
FORMULARIOS_ESTRANGEIROS = frozenset({"20-F", "40-F", "6-K"})

# Duração, em dias, que faz um fato ser tratado como trimestre ou como ano.
# Trimestre "de 90 dias" varia com o calendário fiscal (13 semanas dão 91;
# ano fiscal de 52/53 semanas empurra mais), então a janela é generosa. O que
# ela precisa garantir é reconhecer o que TEM cara de trimestre -- não
# uma medição exata.
TRIMESTRE_MIN_DIAS, TRIMESTRE_MAX_DIAS = 80, 100

# Tags por conceito, em ordem de preferência. Lista em vez de tag única
# porque emissor troca de tag entre exercícios (Revenues -> RevenueFrom
# ContractWithCustomerExcludingAssessedTax é a migração mais comum), e um
# nome só devolveria "indisponível" para metade da cesta.
TAGS: dict[str, tuple[str, ...]] = {
    "receita": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ),
    "lucro_liquido": (
        "NetIncomeLoss",
        "ProfitLoss",
    ),
    "operacional": (
        "OperatingIncomeLoss",
    ),
    "dep_amort": (
        "DepreciationDepletionAndAmortization",
        "DepreciationAmortizationAndAccretionNet",
        "DepreciationAndAmortization",
    ),
    "caixa_operacional": (
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ),
    "capex": (
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
    ),
    "patrimonio": (
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ),
    "caixa": (
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ),
    "divida_curta": (
        "LongTermDebtCurrent",
        "DebtCurrent",
    ),
    "divida_longa": (
        "LongTermDebtNoncurrent",
        "LongTermDebt",
    ),
}


class SemDado(Exception):
    """Conceito ausente ou não confiável. Vira `indisponivel` com motivo --
    nunca um número estimado."""


def _dia(s: str) -> _dt.date:
    return _dt.date.fromisoformat(str(s)[:10])


def _mais_recente(fatos: Iterable[dict]) -> dict | None:
    """O fato que vale quando o mesmo período aparece mais de uma vez.

    Reapresentação (armadilha 2): desempate por `filed` e, em empate, por
    `accn`. Pegar o primeiro da lista devolveria o número anterior à
    republicação -- plausível, auditável em lugar nenhum, e errado.
    """
    melhor = None
    for f in fatos:
        chave = (str(f.get("filed") or ""), str(f.get("accn") or ""))
        if melhor is None or chave > melhor[0]:
            melhor = (chave, f)
    return melhor[1] if melhor else None


def _por_periodo(fatos: list[dict]) -> dict[tuple[str, str], dict]:
    """Um fato por (start, end), já resolvido contra reapresentação."""
    grupos: dict[tuple[str, str], list[dict]] = {}
    for f in fatos:
        if f.get("start") is None or f.get("end") is None:
            continue
        grupos.setdefault((str(f["start"]), str(f["end"])), []).append(f)
    saida = {}
    for k, grupo in grupos.items():
        escolhido = _mais_recente(grupo)
        if escolhido is not None:
            saida[k] = escolhido
    return saida


def _duracao(chave: tuple[str, str]) -> int:
    return (_dia(chave[1]) - _dia(chave[0])).days


def _trimestres_de(fatos: list[dict]) -> list[dict]:
    """Trimestres isolados, em ordem de fim, tirando o acumulado por diferença.

    O coração da armadilha 1. Um 10-Q pode publicar só o acumulado do ano
    ("start" no início do exercício), e somar quatro desses contaria o
    primeiro trimestre quatro vezes.

    O agrupamento é por INÍCIO, não por duração: o mesmo `start` repetido com
    fins diferentes É a assinatura do acumulado -- e é o único sinal que
    distingue "12 meses porque é o exercício" de "12 meses porque é o quarto
    acumulado da série". A primeira versão classificava só por duração e
    descartava o fato do ano inteiro como "não é trimestre"; num emissor que
    só publica acumulado, era justamente ele que faltava para derivar o Q4, e
    o TTM saía com três trimestres. Pego pelo teste do YTD.

      • início que aparece uma vez só: fato solto -- entra se durar um
        trimestre, e o exercício inteiro (10-K) cai fora por aqui;
      • início repetido: série acumulada. O primeiro é o período dele mesmo;
        cada seguinte vira trimestre subtraindo o anterior, que é como a
        própria empresa chegaria ao número do trimestre.

    Em ambos os ramos o resultado só é emitido se o intervalo REAL couber numa
    janela de trimestre -- assim uma série com buraco (Q1 e depois o ano) não
    vira um "trimestre" de nove meses.
    """
    porperiodo = _por_periodo(fatos)
    por_inicio: dict[str, list[tuple[str, dict]]] = {}
    for (inicio, fim), fato in porperiodo.items():
        por_inicio.setdefault(inicio, []).append((fim, fato))

    def _e_trimestre(dias: int) -> bool:
        return TRIMESTRE_MIN_DIAS <= dias <= TRIMESTRE_MAX_DIAS

    diretos: dict[str, dict] = {}
    derivados: dict[str, dict] = {}

    for inicio, lista in por_inicio.items():
        lista.sort(key=lambda par: _dia(par[0]))
        anterior: tuple[str, dict] | None = None
        for fim, fato in lista:
            if anterior is None:
                # Primeiro do início: o período dele mesmo, publicado assim.
                if _e_trimestre(_duracao((inicio, fim))):
                    diretos[fim] = dict(fato, _derivado_de=None)
            elif _e_trimestre((_dia(fim) - _dia(anterior[0])).days):
                derivados[fim] = dict(
                    fato,
                    val=fato["val"] - anterior[1]["val"],
                    start=anterior[0],
                    end=fim,
                    # Marca de que este número não foi publicado assim: saiu de
                    # uma subtração. Vai para a proveniência.
                    _derivado_de=(inicio, anterior[0]),
                )
            # Base da próxima diferença mesmo quando não foi emitido: um
            # primeiro período de seis meses não é trimestre, mas o acumulado
            # seguinte menos ele é.
            anterior = (fim, fato)

    # O publicado como trimestre ganha do derivado: menos aritmética nossa
    # entre o arquivamento e o número exibido.
    juntos = {**derivados, **diretos}
    return [juntos[fim] for fim in sorted(juntos, key=_dia)]


def _ttm(fatos: list[dict]) -> tuple[float, dict]:
    """Soma dos últimos 4 trimestres, com a proveniência de cada um.

    Levanta `SemDado` quando não há quatro trimestres seguidos -- meia janela
    não é TTM, e completar com o que houver produziria um número menor
    disfarçado de anual.
    """
    tris = _trimestres_de(fatos)
    if len(tris) < 4:
        raise SemDado(f"só {len(tris)} trimestre(s) utilizável(is), TTM precisa de 4")
    janela = tris[-4:]

    # Os quatro têm que ser CONTÍGUOS. Buraco no meio (trimestre que a fonte
    # não trouxe) daria uma soma de 9 meses com nome de 12.
    for antes, depois in zip(janela, janela[1:]):
        vao = (_dia(depois["start"]) - _dia(antes["end"])).days
        if not -3 <= vao <= 5:
            raise SemDado(
                f"buraco entre {antes['end']} e {depois['start']} — "
                "os quatro trimestres não são contíguos")

    total = float(sum(float(t["val"]) for t in janela))
    proveniencia = {
        "periodo": f"{janela[0]['start']}..{janela[-1]['end']}",
        "trimestres": [
            {
                "fim": t["end"],
                "form": t.get("form"),
                "accn": t.get("accn"),
                "filed": t.get("filed"),
                "derivado_por_diferenca": bool(t.get("_derivado_de")),
            }
            for t in janela
        ],
    }
    return total, proveniencia


def _instantaneo(fatos: list[dict], quando: _dt.date | None = None) -> tuple[float, dict]:
    """Valor de estoque (patrimônio, caixa, dívida) do balanço mais recente.

    Estoque não se soma ao longo do tempo -- é a armadilha 4. `quando` pede o
    mais recente ATÉ aquela data, que é como se obtém o patrimônio de um ano
    atrás para a média do ROE.
    """
    porfim: dict[str, list[dict]] = {}
    for f in fatos:
        if f.get("end") is None or f.get("start") is not None:
            continue  # instantâneo não tem start
        porfim.setdefault(str(f["end"]), []).append(f)
    if not porfim:
        raise SemDado("nenhum fato instantâneo (todos têm período)")

    candidatos = sorted(porfim, key=_dia)
    if quando is not None:
        candidatos = [d for d in candidatos if _dia(d) <= quando]
        if not candidatos:
            raise SemDado(f"nenhum balanço até {quando}")
    fim = candidatos[-1]
    fato = _mais_recente(porfim[fim])
    assert fato is not None
    return float(fato["val"]), {
        "data": fim,
        "form": fato.get("form"),
        "accn": fato.get("accn"),
        "filed": fato.get("filed"),
    }


def _fatos(dados: dict, conceito: str) -> tuple[list[dict], str]:
    """Fatos do primeiro tag disponível para o conceito, e qual tag foi usado.

    A ordem de `TAGS` é preferência, não alfabética: emissor troca de tag
    entre exercícios, e travar num nome só devolveria "indisponível" para
    metade da cesta.
    """
    facts = ((dados.get("facts") or {}).get("us-gaap") or {})
    for tag in TAGS[conceito]:
        unidades = (facts.get(tag) or {}).get("units") or {}
        for unidade in ("USD", "shares"):
            if unidades.get(unidade):
                return list(unidades[unidade]), tag
    raise SemDado(f"nenhum tag conhecido para '{conceito}' "
                  f"(tentados: {', '.join(TAGS[conceito])})")


def _acoes_em_circulacao(dados: dict) -> tuple[float, dict]:
    """Ações da CAPA do arquivamento, não a média ponderada diluída.

    Armadilha 5. `dei:EntityCommonStockSharesOutstanding` é a contagem na data
    da capa; a média diluída do período é outro conceito e ancoraria a
    capitalização no passado.
    """
    dei = ((dados.get("facts") or {}).get("dei") or {})
    unidades = (dei.get("EntityCommonStockSharesOutstanding") or {}).get("units") or {}
    fatos = unidades.get("shares") or []
    if not fatos:
        raise SemDado("dei:EntityCommonStockSharesOutstanding ausente")
    # Aqui o fato tem `end` (data da capa) e normalmente não tem `start`.
    fato = _mais_recente(sorted(fatos, key=lambda f: str(f.get("end") or "")) [-3:])
    assert fato is not None
    return float(fato["val"]), {
        "data": fato.get("end"), "form": fato.get("form"),
        "accn": fato.get("accn"), "filed": fato.get("filed"),
        "tag": "dei:EntityCommonStockSharesOutstanding",
    }


def emissor_estrangeiro(dados: dict) -> str | None:
    """Motivo pelo qual o emissor NÃO é suportado, ou None.

    Armadilha 7: 20-F/40-F reporta em IFRS, com outro conjunto de tags.
    Adaptar em silêncio daria número calculado sobre conceito diferente --
    pior que não ter número. Detecta pelos DOIS lados: formulário estrangeiro
    entre os fatos recentes, ou ausência de `us-gaap` com `ifrs-full` presente.
    """
    facts = dados.get("facts") or {}
    if not facts.get("us-gaap") and facts.get("ifrs-full"):
        return ("emissor reporta em IFRS (ifrs-full), não US-GAAP — "
                "as tags são outras e adaptar em silêncio calcularia sobre "
                "conceito diferente")
    for tag_dados in (facts.get("dei") or {}).values():
        for lista in (tag_dados.get("units") or {}).values():
            for f in lista or []:
                if str(f.get("form") or "") in FORMULARIOS_ESTRANGEIROS:
                    return (f"emissor arquiva {f['form']} (foreign private "
                            "issuer) — fora do escopo até existir etapa de IFRS")
    return None


def _tenta(fn, *a, **k):
    """(valor, proveniencia, None) ou (None, None, motivo)."""
    try:
        v, p = fn(*a, **k)
        return v, p, None
    except SemDado as e:
        return None, None, str(e)
    except Exception as e:  # noqa: BLE001 — dado torto vira indisponível, não crash
        return None, None, f"{type(e).__name__}: {e}"


def _metrica(valor, prov: dict, *, motivo: str | None = None) -> dict:
    if valor is None:
        return {"valor": None, "indisponivel": motivo or "sem dado"}
    return {"valor": round(float(valor), 4), "proveniencia": prov}


def multiplos(dados: dict, preco: float | None) -> dict:
    """Os oito múltiplos a partir do companyfacts já baixado.

    Separado da rede de propósito: assim o teste exercita as armadilhas com
    fixture, sem depender da SEC estar no ar -- e o modo sombra pode rodar o
    mesmo cálculo contra o arquivamento real sem duplicar lógica.
    """
    bloqueio = emissor_estrangeiro(dados)
    if bloqueio:
        return {"suportado": False, "motivo": bloqueio, "metricas": {}}

    saida: dict[str, Any] = {"suportado": True, "metricas": {}}
    tags_usadas: dict[str, str] = {}

    def ttm(conceito):
        fatos, tag = _fatos(dados, conceito)
        tags_usadas[conceito] = tag
        return _ttm(fatos)

    def instante(conceito, quando=None):
        fatos, tag = _fatos(dados, conceito)
        tags_usadas[conceito] = tag
        return _instantaneo(fatos, quando)

    receita, prov_rec, err_rec = _tenta(ttm, "receita")
    lucro, prov_luc, err_luc = _tenta(ttm, "lucro_liquido")
    patrimonio, prov_pat, err_pat = _tenta(instante, "patrimonio")
    acoes, prov_acoes, err_acoes = _tenta(_acoes_em_circulacao, dados)

    # Capitalização: preço de HOJE x ações de HOJE (armadilha 5).
    cap = None
    if preco is not None and acoes:
        cap = float(preco) * float(acoes)
        saida["market_cap"] = _metrica(cap, {
            "preco": preco, "acoes": acoes, "acoes_de": prov_acoes,
            "formula": "preco_atual * acoes_em_circulacao",
        })
    else:
        saida["market_cap"] = _metrica(
            None, {}, motivo=err_acoes or "preço atual não informado")

    m = saida["metricas"]

    m["pl"] = (_metrica(cap / lucro, {
        "formula": "market_cap / lucro_liquido_TTM",
        "lucro_liquido_TTM": lucro, "de": prov_luc,
        "tags": [tags_usadas.get("lucro_liquido")],
    }) if cap and lucro else _metrica(
        None, {}, motivo=err_luc or "sem capitalização"
        if not cap else "lucro TTM zero ou ausente"))

    # P/VP usa patrimônio do balanço MAIS RECENTE -- não é TTM (armadilha 4).
    m["pvp"] = (_metrica(cap / patrimonio, {
        "formula": "market_cap / patrimonio_do_balanco_mais_recente",
        "patrimonio": patrimonio, "de": prov_pat,
        "tags": [tags_usadas.get("patrimonio")],
        "nota": "estoque, não TTM",
    }) if cap and patrimonio else _metrica(
        None, {}, motivo=err_pat or "sem capitalização"))

    # ROE com patrimônio MÉDIO (armadilha 3): lucro é fluxo do ano, patrimônio
    # é estoque -- dividir por um instante só mistura as duas naturezas.
    if lucro is not None and patrimonio and prov_pat:
        um_ano_antes = _dia(prov_pat["data"]) - _dt.timedelta(days=350)
        pat_antes, prov_antes, _ = _tenta(instante, "patrimonio", um_ano_antes)
        if pat_antes:
            medio = (patrimonio + pat_antes) / 2
            m["roe"] = _metrica(lucro / medio, {
                "formula": "lucro_liquido_TTM / patrimonio_MEDIO",
                "patrimonio_atual": patrimonio, "patrimonio_ha_um_ano": pat_antes,
                "de": prov_pat, "anterior_de": prov_antes,
                "tags": [tags_usadas.get("lucro_liquido"), tags_usadas.get("patrimonio")],
            })
        else:
            m["roe"] = _metrica(None, {}, motivo=(
                "sem patrimônio de um ano atrás para a média — usar só o atual "
                "misturaria fluxo (lucro do ano) com estoque (um instante)"))
    else:
        m["roe"] = _metrica(None, {}, motivo=err_luc or err_pat or "sem dado")

    # EBITDA é RECONSTRUÍDO (armadilha 6): não existe tag para ele.
    operacional, prov_op, err_op = _tenta(ttm, "operacional")
    da, prov_da, err_da = _tenta(ttm, "dep_amort")
    ebitda = None
    if operacional is not None and da is not None:
        ebitda = operacional + da

    caixa, prov_caixa, err_caixa = _tenta(instante, "caixa")
    dc, _, _ = _tenta(instante, "divida_curta")
    dl, prov_dl, err_dl = _tenta(instante, "divida_longa")
    divida = (dc or 0.0) + (dl or 0.0) if (dc is not None or dl is not None) else None
    divida_liquida = (divida - caixa) if (divida is not None and caixa is not None) else None

    ev = (cap + divida - caixa) if (cap and divida is not None and caixa is not None) else None

    m["ev_ebitda"] = (_metrica(ev / ebitda, {
        "formula": "(market_cap + divida_total - caixa) / (operacional_TTM + D&A_TTM)",
        "ev": ev, "ebitda_TTM": ebitda,
        "ebitda_reconstruido_de": {"operacional_TTM": operacional, "dep_amort_TTM": da},
        "operacional_de": prov_op, "dep_amort_de": prov_da,
        "tags": [tags_usadas.get("operacional"), tags_usadas.get("dep_amort")],
    }) if ev and ebitda else _metrica(None, {}, motivo=(
        err_da and f"D&A não reconstruível ({err_da}) — EBITDA sem D&A confiável "
                   "não é EBITDA"
        or err_op or err_caixa or err_dl or "sem componente do EV")))

    m["divida_liquida_ebitda"] = (_metrica(divida_liquida / ebitda, {
        "formula": "(divida_total - caixa) / EBITDA_TTM",
        "divida_liquida": divida_liquida, "ebitda_TTM": ebitda,
        "caixa_de": prov_caixa,
    }) if divida_liquida is not None and ebitda else _metrica(
        None, {}, motivo=err_da or err_caixa or "sem dívida ou EBITDA"))

    cfo, prov_cfo, err_cfo = _tenta(ttm, "caixa_operacional")
    capex, prov_capex, err_capex = _tenta(ttm, "capex")
    if cfo is not None and capex is not None and cap:
        # capex vem POSITIVO no XBRL (é um pagamento); FCF subtrai.
        fcf = cfo - abs(capex)
        m["fcf_yield"] = _metrica(fcf / cap, {
            "formula": "(caixa_operacional_TTM - capex_TTM) / market_cap",
            "fcf_TTM": fcf, "caixa_operacional_TTM": cfo, "capex_TTM": abs(capex),
            "cfo_de": prov_cfo, "capex_de": prov_capex,
            "tags": [tags_usadas.get("caixa_operacional"), tags_usadas.get("capex")],
        })
    else:
        m["fcf_yield"] = _metrica(
            None, {}, motivo=err_cfo or err_capex or "sem capitalização")

    m["margem_liquida"] = (_metrica(lucro / receita, {
        "formula": "lucro_liquido_TTM / receita_TTM",
        "lucro_liquido_TTM": lucro, "receita_TTM": receita,
        "de": prov_rec,
        "tags": [tags_usadas.get("lucro_liquido"), tags_usadas.get("receita")],
    }) if lucro is not None and receita else _metrica(
        None, {}, motivo=err_luc or err_rec or "receita TTM zero"))

    # Crescimento: TTM contra os 4 trimestres ANTERIORES (8 no total).
    crescimento, prov_cres, err_cres = _tenta(_crescimento_ttm, dados)
    m["crescimento_receita"] = (_metrica(crescimento, prov_cres)
                                if crescimento is not None
                                else _metrica(None, {}, motivo=err_cres or "sem dado"))
    return saida


def _crescimento_ttm(dados: dict) -> tuple[float, dict]:
    """Receita TTM contra a TTM de um ano atrás. Precisa de 8 trimestres."""
    fatos, tag = _fatos(dados, "receita")
    tris = _trimestres_de(fatos)
    if len(tris) < 8:
        raise SemDado(f"só {len(tris)} trimestre(s); crescimento TTM precisa de 8")
    atual = sum(float(t["val"]) for t in tris[-4:])
    anterior = sum(float(t["val"]) for t in tris[-8:-4])
    if not anterior:
        raise SemDado("receita TTM do período anterior é zero")
    return atual / anterior - 1.0, {
        "formula": "receita_TTM / receita_TTM_ha_um_ano - 1",
        "receita_TTM": atual, "receita_TTM_anterior": anterior,
        "periodo_atual": f"{tris[-4]['start']}..{tris[-1]['end']}",
        "periodo_anterior": f"{tris[-8]['start']}..{tris[-5]['end']}",
        "tags": [tag],
    }


@cached("companyfacts:{0}", ttl=86400)
def companyfacts(cik: str) -> dict:
    """XBRL do emissor. TTL de 1 dia: arquivamento passado não muda, e o
    próximo só chega com o próximo trimestre."""
    # Import tardio: tools.py importa PESADO (yfinance, pandas), e este módulo
    # precisa ser importável num teste que só exercita aritmética.
    try:
        from tools import EDGAR_HEADERS
    except ImportError:  # pragma: no cover
        from .tools import EDGAR_HEADERS
    r = SESSION.get(COMPANYFACTS_URL.format(cik=cik), headers=EDGAR_HEADERS, timeout=20)
    r.raise_for_status()
    return r.json()


def para_ticker(ticker: str, preco: float | None = None) -> dict:
    """Múltiplos do ticker. Falha vira `erro`, nunca exceção pro chamador."""
    try:
        ticker = sanitize_ticker(ticker)
    except ValueError as e:
        return {"ticker": str(ticker), "erro": str(e)}
    try:
        from tools import _resolve_cik
    except ImportError:  # pragma: no cover
        from .tools import _resolve_cik
    cik = _resolve_cik(ticker)
    if not cik:
        return {"ticker": ticker, "erro": f"CIK desconhecido para {ticker}"}
    try:
        dados = companyfacts(cik)
    except Exception as e:  # noqa: BLE001
        print(f"[fundamentos_sec] {ticker}: {e}", file=sys.stderr, flush=True)
        return {"ticker": ticker, "cik": cik, "erro": f"{type(e).__name__}: {e}"}
    return {"ticker": ticker, "cik": cik, **multiplos(dados, preco)}


# ── Modo sombra ───────────────────────────────────────────────────────────────
#
# ETAPA 1 do plano: calcular SEM substituir a FMP, e conferir cada número
# contra a demonstração real antes de qualquer métrica entrar no relatório.
# Só o que passar na conferência é ativado; o resto continua `indisponivel`.
#
# Roda onde há rede para a SEC (a VPS), porque o ambiente de desenvolvimento
# deste agente recebe 403 do proxy em data.sec.gov:
#
#     python3 -m agent.fundamentos_sec MRVL NVDA AOSL
#
# A saída traz, por métrica, o valor E a proveniência (período, formulário,
# accession, data do arquivamento e tags) -- é ela que se compara linha a
# linha com o 10-Q/10-K, sem precisar adivinhar de onde o número veio.
if __name__ == "__main__":  # pragma: no cover
    import json

    try:
        import json_seguro
    except ImportError:
        from . import json_seguro

    alvos = sys.argv[1:] or ["MRVL", "NVDA", "AOSL"]
    print(json_seguro.dumps(
        {t: para_ticker(t) for t in alvos}, ensure_ascii=False, indent=2))
