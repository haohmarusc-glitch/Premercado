"""
Dois problemas do retrato das 15h (NBIS, 17/08/2026), já com a #307 no ar.

1. ORDENAÇÃO DE NÍVEIS. A análise escreveu "a MM200 (US$ 146,46) fica ENTRE S1
   e S2 (US$ 189,07)" -- a MM200 está ABAIXO das duas (S1 233,75 > S2 189,07 >
   MM200 146,46). Os três valores estavam corretos no JSON: o que falhou foi a
   comparação, justamente a única operação que a regra "não calcule números"
   permite. Ordenar três ou mais valores é frágil demais para delegar ao
   modelo, então passa a vir ordenado do Python (mesmo princípio do
   veredito_validator: recalcular antes do prompt e entregar como fato).

2. SENTIMENTO POR SUBSTRING. `"gains" in title` casava dentro de "aGAINSt" --
   toda manchete com "bet against"/"lawsuit against" ganhava um ponto
   POSITIVO. Também "stops" contém "tops", e "mission"/"commission"/
   "permission" contêm "miss" (cobertura de infraestrutura de IA fala em
   "mission-critical" o tempo todo).

   O efeito mais perverso não era o rótulo errado: era o EMPATE. Com p == n a
   manchete é descartada, nem positiva nem negativa -- some da contagem sem
   deixar rastro.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_niveis_e_sentimento.py -v
"""
import pytest

from agent.analise_rapida_ia import _niveis_ordenados
from agent.get_trend import _NEGATIVE_RE, _POSITIVE_RE


# ── 1. Níveis ordenados ─────────────────────────────────────────────────────

def _retrato():
    """O retrato real do NBIS às 15h, com preço canônico US$ 274,26."""
    return {
        "technicals": {"sma20": 213.0, "sma50": 222.40, "sma200": None, "vwap": 271.16},
        "snapshot": {"sma50": 222.24, "sma200": 146.46, "yearHigh": 299.86, "yearLow": 62.01},
        "reaction": {"summary": {
            "r1_price": 314.77, "r2_price": 359.44,
            "s1_price": 233.75, "s2_price": 189.07,
        }},
    }


def test_ordena_do_maior_para_o_menor():
    niveis = _niveis_ordenados(_retrato(), 274.26)
    valores = [n["valor"] for n in niveis]
    assert valores == sorted(valores, reverse=True)


def test_o_erro_do_nbis_fica_impossivel_de_cometer():
    """S1 > S2 > MM200. Com a lista ordenada, "MM200 entre S1 e S2" não tem
    como ser lido do payload."""
    niveis = _niveis_ordenados(_retrato(), 274.26)
    ordem = [n["rotulo"] for n in niveis]
    assert ordem.index("S1 (banda de reação)") < ordem.index("S2 (banda de reação)")
    assert ordem.index("S2 (banda de reação)") < ordem.index("MM200")


def test_traz_distancia_e_lado_do_preco():
    niveis = {n["rotulo"]: n for n in _niveis_ordenados(_retrato(), 274.26)}
    assert niveis["MM50"]["ladoDoPreco"] == "abaixo"
    assert niveis["MM50"]["distanciaPct"] == pytest.approx(-18.97, abs=0.02)
    assert niveis["máxima 52 semanas"]["ladoDoPreco"] == "acima"
    assert niveis["máxima 52 semanas"]["distanciaPct"] == pytest.approx(9.33, abs=0.02)


def test_prefere_o_snapshot_para_as_medias_longas():
    """MM50 aparece nos dois painéis com valores diferentes (222,24 × 222,40).
    O snapshot é a mesma fonte do preço canônico -- misturar as duas faria a
    distância sair inconsistente com o preço."""
    niveis = {n["rotulo"]: n for n in _niveis_ordenados(_retrato(), 274.26)}
    assert niveis["MM50"]["valor"] == pytest.approx(222.24)


def test_cai_para_a_tecnica_quando_o_snapshot_nao_tem_o_nivel():
    dados = _retrato()
    dados["snapshot"]["sma200"] = None
    dados["technicals"]["sma200"] = 150.0
    niveis = {n["rotulo"]: n for n in _niveis_ordenados(dados, 274.26)}
    assert niveis["MM200"]["valor"] == pytest.approx(150.0)


def test_nivel_ausente_nao_entra_na_lista():
    dados = {"technicals": {"sma20": 213.0, "vwap": None, "sma50": None, "sma200": None},
             "snapshot": {}, "reaction": {}}
    rotulos = [n["rotulo"] for n in _niveis_ordenados(dados, 274.26)]
    assert rotulos == ["MM20"]


def test_sem_nenhum_nivel_devolve_none():
    assert _niveis_ordenados({"technicals": {}, "snapshot": {}, "reaction": {}}, 274.26) is None


def test_sem_preco_ainda_ordena():
    """Painel de níveis sem preço canônico: a ordem continua útil, só não dá
    para medir distância."""
    niveis = _niveis_ordenados(_retrato(), None)
    assert [n["valor"] for n in niveis] == sorted((n["valor"] for n in niveis), reverse=True)
    assert "distanciaPct" not in niveis[0]


# ── 2. Sentimento por palavra inteira ───────────────────────────────────────

def _conta(titulo: str) -> tuple[int, int]:
    t = titulo.lower()
    return len(set(_POSITIVE_RE.findall(t))), len(set(_NEGATIVE_RE.findall(t)))


def test_against_nao_conta_mais_como_gains():
    """O caso que motivou a correção: "bet against" dava ponto positivo porque
    "against" contém "gains"."""
    p, n = _conta("Michael Burry's Bearish Bet Against Nebius Sends a Warning")
    assert p == 0
    assert n == 2  # bearish, warning


def test_manchete_neutra_com_against_nao_e_mais_classificada_como_positiva():
    """Sem palavra de sentimento nenhuma, o substring antigo achava "gains"
    e mandava a manchete para o balde positivo."""
    p, n = _conta("Court hearing scheduled in case against the company")
    assert p == 0 and n == 0


def test_mission_critical_nao_conta_como_miss():
    p, n = _conta("Nebius wins mission-critical datacenter contract")
    assert n == 0
    assert p == 1  # wins


def test_stops_nao_conta_como_tops():
    p, n = _conta("Trading stops after circuit breaker")
    assert p == 0


def test_singular_e_plural_nao_contam_duas_vezes():
    """"surges" casava "surge" E "surges" no substring -- dois pontos para uma
    palavra só, inflando manchetes com plural."""
    assert _conta("Stock surges on record demand")[0] == 2  # surges + record


def test_palavras_de_sentimento_continuam_sendo_pegas():
    """Guarda-corpo: o \\b não pode ter quebrado o casamento normal."""
    p, n = _conta("Analyst upgrades stock to outperform after strong beat")
    assert p >= 3
    p2, n2 = _conta("Shares plunge after earnings miss and downgrade")
    assert n2 >= 3


def test_expressao_de_duas_palavras_continua_funcionando():
    assert _conta("Firm issues buy rating on the stock")[0] == 1
    assert _conta("Firm issues sell rating on the stock")[1] == 1
