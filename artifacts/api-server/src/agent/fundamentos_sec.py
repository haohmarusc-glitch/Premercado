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

# Formulários que valem como FONTE de número. Só demonstração financeira
# periódica e suas emendas.
#
# Visto no modo sombra (MRVL e NVDA, 28/08/2026): o trimestre encerrado em
# janeiro veio de um `DEF 14A` -- a procuração de assembleia. Ela entrou
# porque `_mais_recente()` desempata por `filed`, e a procuração é arquivada
# DEPOIS do 10-K do mesmo período. Desde a regra de "pay versus performance"
# da SEC, essas procurações trazem `NetIncomeLoss` etiquetado em XBRL, então
# o fato existe lá e vencia o 10-K por ser mais recente.
#
# O número pode até coincidir, e nos dois casos parecia plausível -- é
# exatamente por isso que precisa de trava: a tabela de PvP não é a
# demonstração auditada, não há garantia de mesmo escopo, e "coincidiu nas
# duas empresas que eu olhei" não é garantia nenhuma.
FORMULARIOS_ACEITOS = frozenset({"10-K", "10-Q", "10-K/A", "10-Q/A"})


def _de_formulario_aceito(fatos: Iterable[dict]) -> list[dict]:
    """Descarta fato que não veio de demonstração periódica.

    Filtra no FUNIL (`_fatos`), não em cada métrica: são oito métricas e uma
    dúzia de conceitos, e um filtro por chamador dependeria de quem escreve a
    nona lembrar de repetir -- mesmo motivo de `sem_barra_incompleta` viver na
    fonte em market_data_provider.py.
    """
    return [f for f in fatos if str(f.get("form") or "") in FORMULARIOS_ACEITOS]

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


# Conceitos que alguns emissores publicam PARTIDOS em mais de um tag, e a
# soma que os reconstitui. Só é tentado quando nenhum tag único de `TAGS`
# rendeu TTM -- não substitui o caminho normal, cobre o buraco dele.
#
# Diagnosticado na MRVL (28/08/2026), pelo modo sombra: `DepreciationAnd
# Amortization` existe mas não rende trimestre nenhum, e a lista de pistas
# mostrou oito candidatos. SEIS deles são armadilha, e vale registrar quais,
# porque todos têm "Depreciation" ou "Amortization" no nome:
#
#   AccumulatedDepreciationDepletionAndAmortizationPropertyPlantAndEquipment
#   FiniteLivedIntangibleAssetsAccumulatedAmortization
#       -> ACUMULADO de balanço, não despesa do período. Somar isso daria
#          um "D&A" de ordem de grandeza inteiramente errada.
#   FiniteLivedIntangibleAssetsAmortizationExpenseNextTwelveMonths
#   FiniteLivedIntangibleAssetsAmortizationExpenseAfterYearFive
#   FiniteLivedIntangibleAssetsAmortizationExpenseRemainderOfFiscalYear
#       -> CRONOGRAMA FUTURO divulgado em nota, não o que já correu.
#   AmortizationOfFinancingCostsAndDiscounts
#       -> amortização de custo de dívida, que vive no resultado financeiro;
#          não é o D&A operacional que o EBITDA readiciona.
#
# Sobram os dois que são despesa operacional do período, e é justamente
# assim que a MRVL publica -- separado, porque a amortização de intangível
# das aquisições (Cavium, Inphi) é grande demais para ficar embutida.
TAGS_COMPOSTOS: dict[str, tuple[tuple[str, ...], ...]] = {
    "dep_amort": (("Depreciation", "AmortizationOfIntangibleAssets"),),
}


# Palavras que identificam um tag como "da mesma família" de um conceito.
# Servem só para o DIAGNÓSTICO: quando um conceito sai indisponível, a
# mensagem lista os tags parecidos que existem no payload e não estão em
# `TAGS`, para a próxima lacuna de cobertura se resolver com uma leitura em
# vez de um round-trip de investigação.
#
# Motivada pela MRVL (28/08/2026): o D&A saiu "0 trimestre(s) utilizável(is)"
# e a mensagem não dizia nem qual tag foi usado nem o que mais havia -- para
# descobrir era preciso rodar um script à parte contra a SEC.
PISTAS: dict[str, tuple[str, ...]] = {
    "receita": ("Revenue", "Sales"),
    "lucro_liquido": ("NetIncome", "ProfitLoss"),
    "operacional": ("OperatingIncome",),
    "dep_amort": ("Depreciation", "Amortization"),
    "caixa_operacional": ("OperatingActivities",),
    "capex": ("PaymentsToAcquire",),
    "patrimonio": ("StockholdersEquity",),
    "caixa": ("Cash",),
    "divida_curta": ("Debt",),
    "divida_longa": ("Debt",),
}


def _tags_parecidos(dados: dict, conceito: str) -> list[str]:
    """Tags da mesma família presentes no payload e fora de `TAGS`."""
    facts = ((dados.get("facts") or {}).get("us-gaap") or {})
    pistas = PISTAS.get(conceito, ())
    conhecidos = set(TAGS.get(conceito, ()))
    achados = [t for t in facts
               if t not in conhecidos and any(p in t for p in pistas)]
    return sorted(achados)[:8]


def _com_pistas(motivo: str, dados: dict, conceito: str) -> str:
    """Anexa ao motivo os tags parecidos disponíveis, quando houver."""
    outros = _tags_parecidos(dados, conceito)
    if not outros:
        return motivo
    return (f"{motivo} · tags parecidos presentes e fora da lista: "
            f"{', '.join(outros)}")


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

    # Conferência contra o número ANUAL publicado, quando a janela do TTM
    # coincide com um exercício.
    #
    # É o único ponto onde a nossa aritmética pode ser checada contra um valor
    # que a empresa publicou pronto -- e ele importa mais justamente onde o
    # risco é maior: o fluxo de caixa do 10-Q é SEMPRE acumulado, nunca
    # trimestre isolado, então quase todo trimestre de CFO e capex sai de uma
    # subtração nossa. Na AOSL, 3 dos 4 vieram assim, e o CFO somou -16,3 mi
    # -- caixa operacional negativo no ano é possível, mas é exatamente o tipo
    # de número que ninguém consegue distinguir de erro de diferenciação.
    #
    # Só dispara quando existe o anual do MESMO período: um TTM que atravessa
    # dois exercícios (o caso comum fora do 4o trimestre) não tem contra o que
    # conferir, e aí o silêncio é a resposta certa.
    anual = _anual_equivalente(fatos, janela[0]["start"], janela[-1]["end"])
    if anual is not None:
        publicado = float(anual["val"])
        folga = max(abs(publicado) * 0.001, 1.0)
        if abs(total - publicado) > folga:
            raise SemDado(
                f"a soma dos 4 trimestres ({total:,.0f}) não bate com o anual "
                f"publicado para o mesmo período ({publicado:,.0f}, "
                f"{anual.get('form')} {anual.get('accn')}) — a diferenciação "
                f"do acumulado saiu errada em algum trimestre")
        proveniencia["conferido_contra_anual"] = {
            "valor": publicado, "form": anual.get("form"),
            "accn": anual.get("accn"), "filed": anual.get("filed"),
        }
    return total, proveniencia


def _anual_equivalente(fatos: list[dict], inicio: str, fim: str) -> dict | None:
    """O fato anual publicado que cobre exatamente esta janela, se existir.

    Tolerância de poucos dias nas duas pontas: o primeiro trimestre da janela
    pode ter `start` derivado do fim do anterior, o que desloca a data em um
    dia sem mudar o período de fato.
    """
    alvo_ini, alvo_fim = _dia(inicio), _dia(fim)
    candidatos = []
    for chave, fato in _por_periodo(fatos).items():
        if abs((_dia(chave[0]) - alvo_ini).days) > 5:
            continue
        if abs((_dia(chave[1]) - alvo_fim).days) > 5:
            continue
        if _duracao(chave) < TRIMESTRE_MAX_DIAS * 2:
            continue  # é um dos trimestres da própria janela, não o anual
        candidatos.append(fato)
    return _mais_recente(candidatos) if candidatos else None


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
    """Fatos do tag MAIS ATUAL para o conceito, e qual tag foi usado.

    A ordem de `TAGS` é preferência, mas RECÊNCIA vence preferência -- e essa
    ordem importa muito mais do que parecia.

    Visto no modo sombra (NVDA, 28/08/2026), primeira execução contra dado
    real: a versão anterior devolvia o PRIMEIRO tag com qualquer dado, e para
    a NVDA isso era `RevenueFromContractWithCustomerExcludingAssessedTax`,
    que ela abandonou depois do FY2020. A receita TTM saiu como US$ 10,9 bi
    (correta... para o exercício encerrado em janeiro de 2020, seis anos
    antes), enquanto o lucro veio dos trimestres atuais. A margem líquida
    publicada foi 1766%.

    Nenhum teste de fixture pegaria isto: a aritmética estava certa, os dois
    TTM estavam certos cada um no seu período, e o defeito só existe quando a
    fonte real tem um tag descontinuado com histórico parado. É o que o modo
    sombra existe para achar.

    Empate no fim mais recente volta para a ordem de preferência.
    """
    facts = ((dados.get("facts") or {}).get("us-gaap") or {})
    candidatos: list[tuple[str, int, list[dict], str]] = []
    for posicao, tag in enumerate(TAGS[conceito]):
        unidades = (facts.get(tag) or {}).get("units") or {}
        for unidade in ("USD", "shares"):
            lista = _de_formulario_aceito(unidades.get(unidade) or [])
            if not lista:
                continue
            fim = max((str(f.get("end") or "") for f in lista), default="")
            candidatos.append((fim, -posicao, lista, tag))
            break
    if not candidatos:
        raise SemDado(f"nenhum tag conhecido para '{conceito}' "
                      f"(tentados: {', '.join(TAGS[conceito])})")
    _, _, lista, tag = max(candidatos, key=lambda c: (c[0], c[1]))
    return lista, tag


def _acoes_em_circulacao(dados: dict) -> tuple[float, dict]:
    """Ações da CAPA do arquivamento, não a média ponderada diluída.

    Armadilha 5. `dei:EntityCommonStockSharesOutstanding` é a contagem na data
    da capa; a média diluída do período é outro conceito e ancoraria a
    capitalização no passado.
    """
    dei = ((dados.get("facts") or {}).get("dei") or {})
    unidades = (dei.get("EntityCommonStockSharesOutstanding") or {}).get("units") or {}
    # Mesmo filtro de formulário do funil: a capa de uma procuração também
    # traz contagem de ações, e ela não é a fonte que queremos.
    fatos = _de_formulario_aceito(unidades.get("shares") or [])
    if not fatos:
        raise SemDado("dei:EntityCommonStockSharesOutstanding ausente "
                      "em 10-K/10-Q")
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


def _janela_incompativel(*provs) -> str | None:
    """Motivo pelo qual estes TTM NÃO podem ser combinados, ou None.

    A trava que faltava. Dividir dois TTM de janelas diferentes é a forma mais
    fácil de produzir um número plausível e absurdo ao mesmo tempo, e ela não
    depende de nenhum tag estar errado: basta um conceito ter histórico mais
    curto que o outro para a razão comparar eras distintas.

    Foi assim que a NVDA publicou margem de 1766% no modo sombra (lucro dos
    trimestres atuais sobre receita do exercício encerrado em jan/2020). A
    causa imediata era a escolha de tag (ver `_fatos`), mas a razão só virou
    NÚMERO porque nada conferia se os dois períodos eram o mesmo -- e essa
    segunda falha sobreviveria à correção da primeira.

    Tolerância de poucos dias porque o rótulo de um mesmo trimestre fiscal
    pode diferir por um dia entre conceitos; semanas ou anos, não.
    """
    periodos = [p.get("periodo") for p in provs if isinstance(p, dict)]
    periodos = [p for p in periodos if p]
    if len(periodos) < 2:
        return None
    partidas = []
    for p in periodos:
        try:
            ini, fim = str(p).split("..")
            partidas.append((_dia(ini), _dia(fim)))
        except Exception:  # noqa: BLE001 — rótulo estranho é incompatível
            return f"período ilegível ({p})"
    base = partidas[0]
    for outro, rotulo in zip(partidas[1:], periodos[1:]):
        if abs((outro[0] - base[0]).days) > 5 or abs((outro[1] - base[1]).days) > 5:
            return (f"janelas diferentes ({periodos[0]} vs {rotulo}) — "
                    "combinar TTM de períodos distintos compara eras, não "
                    "grandezas")
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

    # Toda falha sai NOMEANDO o tag usado e listando os parecidos que ficaram
    # de fora -- ver `PISTAS`. Sem isso, "0 trimestre(s) utilizável(is)" manda
    # quem lê descobrir sozinho, contra a SEC, qual conceito faltou.
    def ttm(conceito):
        try:
            fatos, tag = _fatos(dados, conceito)
        except SemDado:
            fatos, tag = None, None
        # Guardado numa variável PRÓPRIA: o nome ligado por `except ... as e`
        # é apagado ao sair do bloco, e usá-lo depois estoura UnboundLocalError
        # -- que foi o que aconteceu na primeira versão, transformando toda
        # mensagem de indisponível num traceback interno.
        falha_simples = "nenhum tag conhecido"
        if fatos is not None:
            try:
                tags_usadas[conceito] = tag
                return _ttm(fatos)
            except SemDado as e:
                falha_simples = str(e)

        # Só agora o composto: o emissor pode publicar o conceito PARTIDO em
        # mais de um tag (ver TAGS_COMPOSTOS). Exige que TODAS as partes
        # rendam TTM e que cubram a MESMA janela -- somar componentes de
        # períodos distintos seria a armadilha de eras, por outra porta.
        for partes in TAGS_COMPOSTOS.get(conceito, ()):
            total, provs, valores, ok = 0.0, [], [], True
            for parte in partes:
                unidades = (((dados.get("facts") or {}).get("us-gaap") or {})
                            .get(parte) or {}).get("units") or {}
                lista = _de_formulario_aceito(unidades.get("USD") or [])
                if not lista:
                    ok = False
                    break
                try:
                    v, p = _ttm(lista)
                except SemDado:
                    ok = False
                    break
                total += v
                valores.append(v)
                provs.append(p)
            if not ok or _janela_incompativel(*provs):
                continue
            tags_usadas[conceito] = " + ".join(partes)
            return total, {
                **provs[0],
                # Cada parcela com o seu valor: sem isso, o leitor vê a soma e
                # não tem como conferir de onde ela veio -- que é o defeito
                # que a proveniência inteira existe para não ter.
                "composto_de": [
                    {"tag": t, "valor": v, "periodo": p.get("periodo")}
                    for t, v, p in zip(partes, valores, provs)
                ],
                "nota": ("conceito reconstituído somando os tags acima -- o "
                         "emissor publica este número partido"),
            }
        raise SemDado(_com_pistas(
            f"{falha_simples}" + (f" (tag `{tag}`)" if tag else ""),
            dados, conceito))

    def instante(conceito, quando=None):
        fatos, tag = _fatos(dados, conceito)
        tags_usadas[conceito] = tag
        try:
            return _instantaneo(fatos, quando)
        except SemDado as e:
            raise SemDado(_com_pistas(f"{e} (tag `{tag}`)", dados, conceito))

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
    desalinho_ebitda = _janela_incompativel(prov_op, prov_da)
    if operacional is not None and da is not None and not desalinho_ebitda:
        ebitda = operacional + da
    if desalinho_ebitda:
        err_da = desalinho_ebitda

    caixa, prov_caixa, err_caixa = _tenta(instante, "caixa")
    dc, _, _ = _tenta(instante, "divida_curta")
    dl, prov_dl, err_dl = _tenta(instante, "divida_longa")
    # `LongTermDebt` é o SALDO TOTAL, já incluindo a parcela circulante;
    # `LongTermDebtNoncurrent` é só a parte não circulante. Somar a curta ao
    # primeiro contaria a parcela circulante duas vezes -- e uma dívida
    # inflada não estoura nada, só encarece o EV e o EV/EBITDA em silêncio.
    # Qual dos dois foi usado só se sabe pelo tag escolhido, então a conta
    # depende dele.
    divida_e_total = tags_usadas.get("divida_longa") == "LongTermDebt"
    if dl is not None and divida_e_total:
        divida = dl
    elif dc is not None or dl is not None:
        divida = (dc or 0.0) + (dl or 0.0)
    else:
        divida = None
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
    desalinho_fcf = _janela_incompativel(prov_cfo, prov_capex)
    if desalinho_fcf:
        err_capex = desalinho_fcf
    if cfo is not None and capex is not None and cap and not desalinho_fcf:
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

    # A trava que faltava quando a NVDA publicou 1766%: os dois TTM têm que
    # cobrir a MESMA janela, senão a razão compara eras, não grandezas.
    desalinho_margem = _janela_incompativel(prov_luc, prov_rec)
    m["margem_liquida"] = (_metrica(lucro / receita, {
        "formula": "lucro_liquido_TTM / receita_TTM",
        "lucro_liquido_TTM": lucro, "receita_TTM": receita,
        "de": prov_rec, "janela_do_lucro": (prov_luc or {}).get("periodo"),
        "tags": [tags_usadas.get("lucro_liquido"), tags_usadas.get("receita")],
    }) if lucro is not None and receita and not desalinho_margem
        else _metrica(None, {}, motivo=(
            desalinho_margem or err_luc or err_rec or "receita TTM zero")))

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

    def _preco(ticker: str) -> float | None:
        """Preço atual só para o modo sombra exercitar P/L, P/VP e FCF yield.

        Sem ele a primeira execução saiu com três métricas em "sem
        capitalização" -- não dava para conferir justamente as que dependem
        de preço. Falha vira None: preço ausente já tem tratamento.
        """
        try:
            import yfinance as yf
            return float(getattr(yf.Ticker(ticker).fast_info, "last_price", None))
        except Exception as e:  # noqa: BLE001
            print(f"[fundamentos_sec] preço de {ticker}: {e}",
                  file=sys.stderr, flush=True)
            return None

    print(json_seguro.dumps(
        {t: para_ticker(t, _preco(t)) for t in alvos},
        ensure_ascii=False, indent=2))
