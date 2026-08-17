"""
Cruzamento de médias com DIREÇÃO (get_trend.classificar_cruzamento).

"SMA20 acima ou abaixo da SMA50", sozinho, é leitura atrasada. Depois de uma
queda forte seguida de recuperação em V, a SMA20 fica abaixo da SMA50 por
semanas enquanto o preço já subiu muito.

Visto em produção (NBIS, ago/2026): caiu 48% em julho, recuperou 87% em 12
pregões. Com o preço 21,7% ACIMA da SMA50 e as duas médias subindo, o
componente marcava "baixa" e tirava 25 dos 100 pontos do score -- levando 65
para 40, o que rebaixa "alta forte" para "alta" e muda o `sinal` emitido. A
análise com IA repetia isso como "divergência interna que vale monitorar".

A regra agora exige que NÍVEL e DIREÇÃO concordem; quando discordam, o
componente vale ZERO em vez de ±25 -- não há informação de tendência ali, nem
pra um lado nem pro outro.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_cruzamento_medias.py -v
"""
import pytest

from agent.get_trend import CRUZAMENTO_JANELA, classificar_cruzamento


def _pontos(*args):
    return classificar_cruzamento(*args)[2]


def _estado(*args):
    return classificar_cruzamento(*args)[0]


def _nota(*args):
    return classificar_cruzamento(*args)[1]


# ── Casos que mantêm o comportamento antigo ─────────────────────────────────

def test_alta_consolidando_vale_25():
    """MM20 acima e subindo, gap alargando: cruzamento de alta legítimo."""
    assert _pontos(110.0, 100.0, 105.0, 100.0) == 25
    assert _estado(110.0, 100.0, 105.0, 100.0) == "alta"
    assert _nota(110.0, 100.0, 105.0, 100.0) is None


def test_baixa_confirmada_vale_menos_25():
    """MM20 abaixo e CAINDO: o cruzamento descreve o presente."""
    assert _pontos(95.0, 100.0, 98.0, 100.0) == -25
    assert _estado(95.0, 100.0, 98.0, 100.0) == "baixa"
    assert _nota(95.0, 100.0, 98.0, 100.0) is None


def test_baixa_com_mm20_subindo_mas_gap_ainda_alargando_vale_menos_25():
    """Subir não basta: se a distância para a MM50 continua crescendo, a MM50
    sobe mais rápido e o cruzamento não está revertendo."""
    # gap antes = -2%; agora = -5% (alargou), mesmo com a MM20 subindo.
    assert _pontos(95.0, 100.0, 94.08, 96.0) == -25


# ── O caso NBIS: defasagem, não sinal ───────────────────────────────────────

def test_baixa_em_reversao_vale_zero_e_traz_nota():
    """MM20 abaixo da MM50, mas subindo e fechando a distância."""
    estado, nota, pontos = classificar_cruzamento(95.0, 100.0, 90.0, 100.0)
    assert pontos == 0          # e não -25
    assert estado == "baixa"    # o fato continua sendo reportado
    assert nota is not None and "REVERSÃO" in nota


def test_o_caso_nbis_deixa_de_rebaixar_o_score():
    """Reprodução do formato real: recuperação em V com as duas médias subindo,
    MM20 ainda 5% abaixo da MM50 mas encostando rápido. Antes: -25."""
    assert _pontos(211.0, 222.0, 195.0, 224.0) == 0


# ── Simetria: o outro lado também perde os pontos ───────────────────────────

def test_alta_enfraquecendo_vale_zero():
    """MM20 acima mas caindo e encostando na MM50. Sem isto a correção
    embutiria viés altista permanente: descontaria o cruzamento de baixa
    ruim e manteria o de alta ruim."""
    estado, nota, pontos = classificar_cruzamento(110.0, 100.0, 115.0, 100.0)
    assert pontos == 0
    assert estado == "alta"
    assert nota is not None and "ENFRAQUECENDO" in nota


def test_alta_caindo_mas_com_gap_ainda_alargando_mantem_25():
    """MM20 caindo, mas a MM50 caindo mais: o gap alarga e a alta segue."""
    assert _pontos(110.0, 100.0, 112.0, 104.0) == 25


# ── Bordas ──────────────────────────────────────────────────────────────────

def test_sem_historico_cai_no_comportamento_de_dois_estados():
    """Primeiros dias da série (e primeiros dias do backtest): sem valor
    anterior, a regra antiga vale."""
    assert classificar_cruzamento(95.0, 100.0, None, None) == ("baixa", None, -25)
    assert classificar_cruzamento(110.0, 100.0, None, None) == ("alta", None, 25)


def test_nan_no_valor_anterior_e_tratado_como_ausencia():
    """rolling(50) devolve NaN antes de completar a janela; NaN em comparação
    é sempre False e passaria batido sem esta guarda."""
    nan = float("nan")
    assert classificar_cruzamento(95.0, 100.0, nan, 100.0) == ("baixa", None, -25)
    assert classificar_cruzamento(95.0, 100.0, 90.0, nan) == ("baixa", None, -25)


def test_sma50_anterior_zero_nao_divide_por_zero():
    assert classificar_cruzamento(95.0, 100.0, 90.0, 0.0) == ("baixa", None, -25)


def test_empate_conta_como_baixa():
    """`sma20 > sma50` estrito: igualdade exata não é cruzamento de alta.
    Preserva o comportamento anterior nessa borda."""
    assert _estado(100.0, 100.0, 99.0, 100.0) == "baixa"


def test_janela_e_de_cinco_pregoes():
    """Amarra a constante: mudá-la altera o score de todo ticker e do backtest
    junto, então não pode passar despercebido."""
    assert CRUZAMENTO_JANELA == 5
