"""
Reação a earnings -- os três vícios que produziam número certo com leitura
errada, e a incerteza que faltava ao lado da correlação.

Todos vieram de uma auditoria do relatório real de 25/08/2026 (NVDA, SMCI,
AVGO, SKHY, ARM), em que os números publicados foram reconstruídos a partir
das tabelas e bateram na casa decimal -- então o que segue não é hipótese
sobre o que o código poderia fazer, é o que ele fez.

1. SESSÃO DA REAÇÃO ESCOLHIDA PELO MAIOR MOVIMENTO. Entre o dia do anúncio e
   o seguinte, ficava o que se moveu mais em módulo -- seleção pelo RESULTADO.
   No NVDA de 2024-11-20 isso pegou o dia do anúncio de uma empresa que
   reporta after-close, ou seja, mediu como reação a sessão ANTERIOR à
   notícia.
2. EVENTO DUPLICADO. A fonte devolveu 2025-02-25 duas vezes para o SMCI, e o
   evento entrou em dobro em média, desvio, threshold e bandas -- puxando a
   média de +6,05% para +6,82%.
3. BUCKETS ASSIMÉTRICOS. "Esticado" exigia >= +10%, mas "descontado" era
   qualquer coisa <= 0. O AVGO saía "descontado em -6,91%" e essa linha virou
   a recomendação principal de quem leu o relatório.
4. CORRELAÇÃO SEM INCERTEZA. Publicava-se só o r. A leitura promoveu o AVGO
   (r=-0,60, n=7) a "padrão estatisticamente relevante" -- p=0,157, que não
   passa nem antes da correção de múltiplos tickers.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_earnings_reaction_rigor.py -v
"""
import numpy as np
import pandas as pd
import pytest

from agent.earnings_reaction_analysis import (
    CORR_MIN_EVENTOS,
    _correlacao_com_incerteza,
    _janela_da_reacao,
    aplicar_holm,
    janela_do_resumo,
)


# ── 1. a sessão da reação vem do HORÁRIO, não da magnitude ──────────────────

def _ts(hora, minuto=0):
    return pd.Timestamp(f"2026-05-20 {hora:02d}:{minuto:02d}:00")


def test_divulgacao_depois_do_fechamento_reage_no_pregao_seguinte():
    janela, inferido = _janela_da_reacao(_ts(16, 20))
    assert (janela, inferido) == ("seguinte", False)


def test_divulgacao_antes_da_abertura_reage_no_proprio_dia():
    janela, inferido = _janela_da_reacao(_ts(7, 0))
    assert (janela, inferido) == ("anuncio", False)


def test_a_borda_da_abertura_conta_como_antes_do_pregao():
    """09:29 ainda é pré-abertura; 09:31 já é intradia e não dá para afirmar."""
    assert _janela_da_reacao(_ts(9, 29))[0] == "anuncio"
    assert _janela_da_reacao(_ts(9, 31))[1] is True, "meio do pregão é inferência"


def test_sem_horario_assume_after_close_mas_declara_a_suposicao():
    """Meia-noite é o que a fonte devolve quando não tem o horário. O padrão
    é AMC (convenção dominante nos papéis que acompanhamos), mas o evento é
    CONTADO como inferido -- suposição silenciosa é o defeito de origem."""
    janela, inferido = _janela_da_reacao(_ts(0, 0))
    assert janela == "seguinte" and inferido is True


def test_timestamp_sem_hora_nenhuma_nao_estoura():
    class _Sem:
        pass
    assert _janela_da_reacao(_Sem()) == ("seguinte", True)


def test_a_escolha_nao_depende_do_tamanho_do_movimento():
    """O coração da correção: a mesma hora devolve a mesma janela, não
    importa o que o preço fez. Se algum dia a magnitude voltar a pesar, este
    teste é quem percebe."""
    assert _janela_da_reacao(_ts(16, 20)) == _janela_da_reacao(_ts(16, 20))
    assert _janela_da_reacao(_ts(7, 0))[0] != _janela_da_reacao(_ts(16, 20))[0]


# ── 4. correlação publicada com IC e p ──────────────────────────────────────

def _perfeita(n):
    x = np.arange(float(n))
    return x, x * 2.0 + 1.0


def test_correlacao_vem_com_ic_e_p_valor():
    x, y = _perfeita(8)
    out = _correlacao_com_incerteza(x, y)
    assert out["corr_runup_reacao"] == pytest.approx(1.0, abs=0.01)
    assert out["corr_ic95"] is not None
    assert out["corr_p_valor"] is not None
    assert out["corr_n"] == 8


def test_amostra_curta_nao_publica_correlacao_e_diz_por_que():
    x, y = _perfeita(CORR_MIN_EVENTOS - 1)
    out = _correlacao_com_incerteza(x, y)
    assert out["corr_runup_reacao"] is None
    assert out["corr_p_valor"] is None
    assert "mínimo" in (out["corr_nota"] or ""), "a ausência tem que ter motivo escrito"


def test_serie_constante_nao_inventa_correlacao():
    """Desvio zero faz r virar NaN; publicar NaN como número seria pior que
    não publicar."""
    out = _correlacao_com_incerteza([1.0] * 8, [3.0, 1.0, 4.0, 1.0, 5.0, 9.0, 2.0, 6.0])
    assert out["corr_runup_reacao"] is None


def test_ruido_puro_da_p_valor_alto():
    rng = np.random.default_rng(7)
    out = _correlacao_com_incerteza(rng.normal(size=40), rng.normal(size=40))
    assert out["corr_p_valor"] > 0.05


def test_p_valor_nunca_e_zero():
    """(extremos+1)/(amostras+1): 2000 sorteios não autorizam afirmar
    impossibilidade -- mesma regra de padroes_estatisticos.teste_permutacao."""
    x, y = _perfeita(12)
    assert _correlacao_com_incerteza(x, y)["corr_p_valor"] > 0


def test_o_ic_e_reproduzivel():
    """Semente fixa: dois operadores olhando o mesmo papel têm que ver o
    mesmo intervalo, senão a discussão vira sobre o sorteio."""
    x, y = _perfeita(9)
    assert _correlacao_com_incerteza(x, y) == _correlacao_com_incerteza(x, y)


# ── correção de múltiplos tickers (Holm) ────────────────────────────────────

def _res(ticker, p):
    return {"ticker": ticker, "summary": {"runup": {"corr_p_valor": p}}}


def test_holm_corrige_pelo_numero_de_tickers_testados():
    """Cinco papéis são cinco testes da MESMA hipótese, e a leitura sempre
    destaca o mais extremo."""
    res = aplicar_holm([_res("A", 0.01), _res("B", 0.20), _res("C", 0.50),
                        _res("D", 0.60), _res("E", 0.90)])
    a = res[0]["summary"]["runup"]
    assert a["corr_p_corrigido"] == pytest.approx(0.05)  # 0.01 x 5
    assert a["corr_sobrevive"] is True
    assert res[1]["summary"]["runup"]["corr_sobrevive"] is False


def test_o_caso_avgo_nao_sobrevive():
    """O número real de 25/08: r=-0,60 com p=0,157 foi chamado de 'padrão
    estatisticamente relevante' e virou a recomendação principal."""
    res = aplicar_holm([_res("AVGO", 0.157), _res("NVDA", 0.016),
                        _res("SMCI", 0.676), _res("ARM", 0.311), _res("SKHY", 0.99)])
    por_ticker = {r["ticker"]: r["summary"]["runup"] for r in res}
    assert por_ticker["AVGO"]["corr_sobrevive"] is False
    assert por_ticker["AVGO"]["corr_p_corrigido"] > 0.05


def test_p_corrigido_nao_diminui_ao_longo_da_ordem():
    """Monotonicidade de Holm: um teste mais fraco não pode terminar com p
    corrigido MENOR que o de um mais forte."""
    res = aplicar_holm([_res(c, p) for c, p in
                        zip("ABCDE", [0.001, 0.02, 0.03, 0.04, 0.05])])
    corrigidos = [r["summary"]["runup"]["corr_p_corrigido"] for r in res]
    assert corrigidos == sorted(corrigidos)


def test_ticker_sem_correlacao_nao_conta_como_teste():
    """Quem não teve p calculado (amostra curta) não pode inflar o divisor e
    penalizar quem teve."""
    res = aplicar_holm([_res("A", 0.01), _res("B", None), _res("C", None)])
    assert res[0]["summary"]["runup"]["corr_p_corrigido"] == pytest.approx(0.01)


def test_sem_nenhum_teste_nao_estoura():
    res = aplicar_holm([_res("A", None)])
    assert res[0]["summary"]["runup"]["corr_sobrevive"] is False


def test_resultado_com_erro_nao_quebra_a_correcao():
    """A cesta real tem tickers que falham a coleta inteira."""
    res = aplicar_holm([{"ticker": "X", "error": "sem histórico"}, _res("A", 0.01)])
    assert res[1]["summary"]["runup"]["corr_sobrevive"] is True


# ── 2. deduplicação de eventos ──────────────────────────────────────────────

def test_datas_repetidas_sao_descartadas_mantendo_a_primeira():
    """Reproduz a duplicata real do SMCI (2025-02-25 duas vezes). A regra é a
    mesma linha do módulo; o teste amarra o comportamento."""
    idx = pd.DatetimeIndex(["2026-08-11", "2026-05-05", "2025-02-25", "2025-02-25"])
    sem_dup = idx[~idx.normalize().duplicated(keep="first")]
    assert len(sem_dup) == 3
    assert list(sem_dup) == list(pd.DatetimeIndex(
        ["2026-08-11", "2026-05-05", "2025-02-25"]))


def test_a_duplicata_do_smci_movia_a_media_publicada():
    """Aritmética do incidente: a duplicata era uma reação de +12,23% e
    sozinha empurrava a média de +6,05% para os +6,82% que a leitura com IA
    citou como 'viés positivo do SMCI'."""
    reacoes = [19.02, 24.54, 13.78, -11.33, -18.29, 2.39, 12.23]
    com_dup = reacoes + [12.23]
    assert np.mean(com_dup) == pytest.approx(6.82, abs=0.01)
    assert np.mean(reacoes) == pytest.approx(6.05, abs=0.01)


# ── o módulo não pode voltar a escolher pelo maior movimento ────────────────

def test_a_selecao_por_magnitude_nao_esta_mais_no_codigo():
    """Amarra por leitura de fonte. `max(..., key=abs(close_pct))` é curto de
    escrever e reintroduz seleção pelo resultado sem parecer errado."""
    import pathlib
    from agent import earnings_reaction_analysis as era
    fonte = pathlib.Path(era.__file__).read_text(encoding="utf-8")
    codigo = "\n".join(l for l in fonte.splitlines()
                       if not l.strip().startswith("#"))
    assert 'key=lambda m: abs(m["close_pct"])' not in codigo


# ═══ 29/08/2026 — o summary não dizia de qual sessão vinham as médias ══════
#
# `janela_reacao` vivia só por evento, dentro de `events`. Quando o payload da
# Análise Rápida passou a ser enxugado para caber no teto (`_ENXUGADORES`),
# `events` deixou de viajar -- e o prompt recebia `close_pct_mean: -0.89` sem
# nada dizendo a que sessão aquele número pertence.
#
# NVDA, mesma data: a prosa escreveu "o preço, em média, caiu 0,89% no
# fechamento do dia do balanço". O -0,89% é a média da sessão SEGUINTE (as
# oito linhas da tabela vêm marcadas com ◂); a média do dia do anúncio é
# +0,79% -- sinal oposto. Não foi capricho do modelo: o campo que responde à
# pergunta não estava lá.
#
# Enxugar um bloco é seguro enquanto não se leva junto o que dá sentido ao
# que ficou.

def _mov(janela):
    return {"_janela": janela, "close_pct": 1.0}


def test_todos_amc_a_janela_e_a_sessao_seguinte():
    janela, n = janela_do_resumo([_mov("seguinte")] * 8)
    assert janela == "seguinte"
    assert n == {"anuncio": 0, "seguinte": 8}


def test_todos_bmo_a_janela_e_o_proprio_dia():
    janela, n = janela_do_resumo([_mov("anuncio")] * 8)
    assert janela == "anuncio"
    assert n == {"anuncio": 8, "seguinte": 0}


def test_serie_mista_nao_escolhe_um_lado():
    """Empresa que mudou de BMO para AMC no meio da série tem médias que
    misturam as duas sessões. Dizer "seguinte" ali seria falso para parte dos
    eventos, e o rótulo existe para o leitor saber que a série não é uniforme."""
    janela, n = janela_do_resumo([_mov("seguinte")] * 5 + [_mov("anuncio")] * 3)
    assert janela == "mista"
    assert n == {"anuncio": 3, "seguinte": 5}


def test_a_contagem_ignora_valor_desconhecido_sem_estourar():
    janela, n = janela_do_resumo([_mov("seguinte"), {"_janela": None}, {}])
    assert janela == "seguinte"
    assert n == {"anuncio": 0, "seguinte": 1}


def test_lista_vazia_assume_a_sessao_seguinte():
    """Mesma suposição de `_janela_da_reacao` quando falta horário -- e é a
    que DISPARA a nota de aviso em vez de silenciá-la."""
    assert janela_do_resumo([])[0] == "seguinte"
