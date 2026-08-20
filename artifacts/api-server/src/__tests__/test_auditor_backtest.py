"""
O auditor independente do backtest, e a prova de que ele morde.

Duas coisas distintas são testadas:

1. A BATERIA passa limpa -- motor e implementação de referência contam a
   mesma história em todos os cenários (bordas de stop/gap, fricções, corte
   de payload em 30 trades, passeios aleatórios com o gerador de sinais
   real). Este teste É a auditoria contínua: a partir daqui, qualquer
   mudança no motor que altere a semântica de execução sem mudar a
   referência junto quebra o CI -- exatamente o alarme que faltou nos anos
   do look-ahead.

2. O comparador DETECTA adulteração. Um differ que devolve lista vazia para
   tudo passaria no item 1 sem auditar nada; aqui a gente planta defeitos
   (preço trocado, trade sumido, equity inflada, agregado mentindo) e exige
   que cada um seja apontado com nome. Auditor que não pega defeito plantado
   é decoração.

Import: mesmo padrão de test_diagnostico_confluence.py -- o script insere
agent/ no sys.path e o conftest já blinda o pacote `agent` contra essa
poluição.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_auditor_backtest.py -v
"""
import copy
import pathlib
import sys

import pandas as pd

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "agent" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import auditor_backtest as aud  # noqa: E402


# ── 1. a auditoria contínua ──────────────────────────────────────────────────

def test_bateria_inteira_sem_divergencia_e_sem_incoerencia():
    resultados = aud.rodar_bateria()
    problemas = {r["cenario"]: r["divergencias"] + r["incoerencias"]
                 for r in resultados if r["divergencias"] or r["incoerencias"]}
    assert problemas == {}, f"motor e auditor divergem: {problemas}"

def test_bateria_cobre_as_bordas_que_ja_esconderam_vies():
    """Se alguém enxugar a bateria, este teste lembra o que ela protege."""
    nomes = {r["cenario"] for r in aud.rodar_bateria()}
    assert {"gap_pelo_stop", "stop_e_target_no_mesmo_candle",
            "stop_no_dia_da_entrada", "muitos_trades"} <= nomes
    # E pelo menos um cenário exercita o corte do payload em 30 trades.
    assert any(r["trades"] > 30 for r in aud.rodar_bateria())


# ── 2. o differ morde ────────────────────────────────────────────────────────

def _um_resultado():
    """Um par (resultado do motor, referência) de cenário real da bateria."""
    nome, ohlc, buy, sell, extras = aud._cenarios()[5]  # com_friccoes: 1 trade
    params = dict(position_fraction=1.0, commission_pct=0.0, slippage_pct=0.0,
                  stop_loss_pct=None, take_profit_pct=None)
    params.update(extras)
    res = aud.motor._simulate("AUD", "auditoria", str(ohlc.index[0])[:10],
                              str(ohlc.index[-1])[:10], ohlc, buy, sell, **params)
    ref = aud.simular_referencia(ohlc, buy, sell, **params)
    return res, ref

def _campos(divergencias):
    return {d["campo"] for d in divergencias}

def test_preco_de_saida_adulterado_e_apontado():
    res, ref = _um_resultado()
    res = copy.deepcopy(res)
    res["trades"][0]["exitPrice"] += 1.0
    assert "exitPrice" in _campos(aud.comparar(res, ref))

def test_trade_sumido_e_apontado():
    res, ref = _um_resultado()
    res = copy.deepcopy(res)
    res["trades"] = []
    res["totalTrades"] = 0
    assert "totalTrades" in _campos(aud.comparar(res, ref))

def test_ponto_de_equity_inflado_e_apontado():
    res, ref = _um_resultado()
    res = copy.deepcopy(res)
    res["equityCurve"][3]["equity"] += 50.0
    assert "equity" in _campos(aud.comparar(res, ref))

def test_motivo_de_saida_trocado_e_apontado():
    res, ref = _um_resultado()
    res = copy.deepcopy(res)
    res["trades"][0]["exitReason"] = "stop_loss"
    assert "exitReason" in _campos(aud.comparar(res, ref))

def test_arredondamento_do_motor_nao_e_acusado():
    """A tolerância existe para exatamente isto: o motor publica 2 casas, a
    referência não arredonda. Zero falso positivo no caminho limpo."""
    res, ref = _um_resultado()
    assert aud.comparar(res, ref) == []


# ── 2b. a coerência interna também morde ─────────────────────────────────────

def test_final_value_desalinhado_da_equity_e_apontado():
    res, _ = _um_resultado()
    res = copy.deepcopy(res)
    res["finalValue"] += 100.0
    campos = _campos(aud.verificar_coerencia_interna(res))
    assert "finalValue vs equity final" in campos or "totalReturn" in campos

def test_drawdown_zerado_com_equity_que_caiu_e_apontado():
    res, _ = _um_resultado()
    res = copy.deepcopy(res)
    assert res["maxDrawdown"] < 0, "cenário precisa ter drawdown real"
    res["maxDrawdown"] = 0.0
    assert "maxDrawdown" in _campos(aud.verificar_coerencia_interna(res))

def test_win_rate_mentiroso_e_apontado():
    res, _ = _um_resultado()
    res = copy.deepcopy(res)
    res["winRate"] = 99.9
    assert "winRate" in _campos(aud.verificar_coerencia_interna(res))


# ── a referência é dela mesma, não um wrapper do motor ───────────────────────

def test_referencia_nao_chama_o_motor():
    """A independência é o produto: se simular_referencia delegar ao motor, o
    diff vira espelho. Leitura de fonte, como as amarras do grid."""
    fonte = (_SCRIPTS / "auditor_backtest.py").read_text(encoding="utf-8")
    corpo = fonte.split("def simular_referencia", 1)[1].split("\ndef ", 1)[0]
    # Só CÓDIGO conta -- a docstring cita "_simulate" de propósito (é o
    # contrato que ela reimplementa) e isso não é delegação.
    codigo = [l for l in corpo.splitlines()
              if not l.strip().startswith("#") and '"""' not in l]
    corpo_sem_doc = corpo.split('"""')[2] if corpo.count('"""') >= 2 else "\n".join(codigo)
    assert "motor." not in corpo_sem_doc
    assert "_simulate(" not in corpo_sem_doc
