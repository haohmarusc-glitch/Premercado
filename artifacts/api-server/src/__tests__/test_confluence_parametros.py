"""
Parâmetro impossível tem de falhar ALTO, não virar motor mudo.

Grid search de 19/08/2026, testando min_votes (4, 5, 6) em MU/AVGO/MRVL nos
dois regimes. A linha do 6 saiu assim nas SEIS combinações:

    min_votes  total_return_pct  num_trades  win_rate
            6          0.000000           0       0.0

Com a mesma cara de "a estratégia ficou de fora do mercado" -- leitura
legítima, e neste caso completamente falsa. `buy_votes` tem teto de 5 (são 5
sinais votantes; o catalisador é veto, não voto), então pedir 6 é pedir mais
votos do que existem eleitores. A condição NUNCA pode ser verdadeira.

E `min_votes` chega do corpo da requisição sem validação
(routes/confluence.ts: `minVotes ?? 4`), então qualquer chamada com 6 recebia
um motor permanentemente em "flat" -- sem erro, sem sinal, sem nada que
dissesse por quê.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_confluence_parametros.py -v
"""
import sys
import pathlib

import pytest

# confluence_engine usa imports planos (from security import ...), então
# precisa do diretório do agente no path -- diferente dos módulos que rodam
# como pacote. Ver convenção 11 do README.
_AGENT = pathlib.Path(__file__).resolve().parent.parent / "agent"
if str(_AGENT) not in sys.path:
    sys.path.insert(0, str(_AGENT))

from agent.confluence_engine import ConfluenceEngine, SIGNAL_NAMES  # noqa: E402


# ── min_votes ───────────────────────────────────────────────────────────────

def test_mais_votos_do_que_eleitores_e_recusado():
    """O caso exato do grid: 6 votos de 5 sinais."""
    with pytest.raises(ValueError, match="impossível"):
        ConfluenceEngine(min_votes=len(SIGNAL_NAMES) + 1)


def test_a_mensagem_diz_o_maximo_e_lista_os_sinais():
    """Erro que só diz "inválido" manda o operador adivinhar qual é o teto."""
    with pytest.raises(ValueError) as e:
        ConfluenceEngine(min_votes=6)
    msg = str(e.value)
    assert "5" in msg                      # o máximo
    assert "trend" in msg and "sector" in msg   # quem vota
    assert "1..5" in msg                   # o intervalo aceito


def test_zero_votos_tambem_e_recusado():
    """Aceitar 0 faria o motor disparar em TODO pregão, que é o oposto do
    problema e igualmente silencioso."""
    with pytest.raises(ValueError):
        ConfluenceEngine(min_votes=0)


@pytest.mark.parametrize("v", range(1, len(SIGNAL_NAMES) + 1))
def test_o_intervalo_valido_passa(v):
    assert ConfluenceEngine(min_votes=v).min_votes == v


def test_o_limite_acompanha_a_lista_de_sinais():
    """Se alguém adicionar um sexto sinal votante, 6 passa a ser válido sozinho
    -- a validação deriva de total_signals, não de um 5 escrito à mão."""
    e = ConfluenceEngine(min_votes=6, total_signals=6)
    assert e.min_votes == 6


# ── kelly_fraction ──────────────────────────────────────────────────────────
#
# Problema espelhado, e mais caro: acima de 1 ela não falha nem cala --
# kelly_position_size satura em min(1.0, ...) e devolve 100% do capital numa
# posição só, calada.

def test_fracao_de_kelly_acima_de_um_e_recusada():
    with pytest.raises(ValueError, match="fora de"):
        ConfluenceEngine(kelly_fraction=5.0)


def test_fracao_zero_ou_negativa_e_recusada():
    for f in (0.0, -0.3):
        with pytest.raises(ValueError):
            ConfluenceEngine(kelly_fraction=f)


def test_kelly_cheio_ainda_e_permitido():
    """1.0 é agressivo, não impossível -- quem escolhe assume. O que não pode é
    passar disso sem saber."""
    assert ConfluenceEngine(kelly_fraction=1.0).kelly_fraction == 1.0


def test_o_recomendado_passa():
    assert ConfluenceEngine(kelly_fraction=0.3).kelly_fraction == 0.3


# ── o grid não testa mais o impossível ──────────────────────────────────────

def test_o_grid_do_backtest_nao_gasta_um_terco_no_impossivel():
    """Testar min_votes=6 desperdiçava um terço de cada rodada produzindo
    linhas de zero que PARECIAM resultado."""
    fonte = (_AGENT / "scripts" / "backtest_confluence.py").read_text(encoding="utf-8")
    linha = next(l for l in fonte.splitlines() if l.startswith("MIN_VOTES_GRID"))
    valores = [int(n) for n in __import__("re").findall(r"\d+", linha)]
    assert max(valores) <= len(SIGNAL_NAMES)
    assert all(v >= 1 for v in valores)
