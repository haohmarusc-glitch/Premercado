"""
Validador da leitura da cesta -- o texto só pode afirmar o que o número banca.

Em 25/08/2026 três vícios de MEDIÇÃO foram corrigidos no
earnings_reaction_analysis, e os números passaram a sair certos: a correlação
do AVGO com `p corrigido = 0,462` e `corr_sobrevive = False`. O erro mesmo
assim chegou ao leitor, porque a PROSA gerada em cima deles não passava por
conferência nenhuma -- ela chamou aquela correlação de "padrão estatisticamente
relevante" e a transformou na recomendação principal.

O Veredito já tinha esse anteparo; esta tela não tinha. Esta suíte existe para
que a diferença não volte.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_reacao_earnings_validador.py -v
"""
import pytest

from agent.reacao_earnings_validator import (
    CORR_MESMO_TRADE,
    bloco_de_correcao,
    erros,
    resumo_legivel,
    validar_leitura,
)


def _ticker(nome, *, corr=None, sobrevive=False, p_corrigido=None,
            estado=None, n_events=8, erro=None):
    if erro:
        return {"ticker": nome, "error": erro}
    runup = {}
    if corr is not None:
        runup = {"corr_runup_reacao": corr, "corr_sobrevive": sobrevive,
                 "corr_p_corrigido": p_corrigido}
    if estado:
        runup["estado_atual"] = estado
    return {"ticker": nome, "summary": {"n_events": n_events, "runup": runup}}


def _codigos(achados):
    return {a["codigo"] for a in achados}


# ── o incidente, reproduzido ────────────────────────────────────────────────

_TEXTO_DO_INCIDENTE = (
    "AVGO mostra a correlação mais forte (negativa, -0.60) entre o run-up "
    "pré-earnings e a reação do papel, indicando um padrão de reversão. "
    "É um padrão estatisticamente relevante."
)


def test_correlacao_que_nao_sobrevive_nao_pode_virar_padrao():
    achados = validar_leitura(
        _TEXTO_DO_INCIDENTE,
        [_ticker("AVGO", corr=-0.60, sobrevive=False, p_corrigido=0.462)])
    assert "LEITURA_CORRELACAO_SEM_SUPORTE" in _codigos(achados)
    assert erros(achados), "isto é ERRO, não aviso: o dado contradiz a frase"


def test_a_mensagem_traz_o_p_corrigido():
    """O apontamento tem que dar o número, senão quem lê não sabe o quanto
    a afirmação estava longe."""
    achados = validar_leitura(
        _TEXTO_DO_INCIDENTE,
        [_ticker("AVGO", corr=-0.60, sobrevive=False, p_corrigido=0.462)])
    assert "0.462" in achados[0]["mensagem"]


def test_correlacao_que_sobrevive_pode_ser_chamada_de_padrao():
    """O validador não pode virar mordaça: quando o número banca, a frase
    passa."""
    achados = validar_leitura(
        _TEXTO_DO_INCIDENTE,
        [_ticker("AVGO", corr=-0.60, sobrevive=True, p_corrigido=0.012)])
    assert "LEITURA_CORRELACAO_SEM_SUPORTE" not in _codigos(achados)


def test_descrever_o_numero_sem_promove_lo_passa():
    """"nos casos esticados a reação média foi X" é legítimo -- é o que
    aconteceu, não o que acontece."""
    texto = ("AVGO mostra correlação de -0.60 entre run-up e reação; nos 3 "
             "casos esticados a reação média foi -9.67%.")
    achados = validar_leitura(
        texto, [_ticker("AVGO", corr=-0.60, sobrevive=False, p_corrigido=0.462)])
    assert "LEITURA_CORRELACAO_SEM_SUPORTE" not in _codigos(achados)


def test_a_checagem_e_por_frase_e_nao_contamina_o_vizinho():
    """"AVGO tem correlação" e "SMCI é um padrão relevante" no mesmo
    parágrafo não podem ser confundidos."""
    texto = ("AVGO mostra correlação de -0.60. SMCI tem um padrão "
             "estatisticamente relevante.")
    achados = validar_leitura(texto, [
        _ticker("AVGO", corr=-0.60, sobrevive=False, p_corrigido=0.462),
        _ticker("SMCI", corr=0.20, sobrevive=True, p_corrigido=0.01),
    ])
    assert "LEITURA_CORRELACAO_SEM_SUPORTE" not in _codigos(achados)


# ── lei absoluta a partir de ~8 eventos ─────────────────────────────────────

@pytest.mark.parametrize("palavra", ["sempre", "toda vez", "garantido",
                                     "invariavelmente", "certamente"])
def test_palavra_de_lei_e_recusada(palavra):
    achados = validar_leitura(f"O papel {palavra} cai depois do balanço.",
                              [_ticker("NVDA")])
    assert "LEITURA_LEI_ABSOLUTA" in _codigos(achados)


def test_linguagem_de_tendencia_passa():
    achados = validar_leitura(
        "Nos últimos 8 resultados o papel tem tendido a cair no dia seguinte.",
        [_ticker("NVDA")])
    assert "LEITURA_LEI_ABSOLUTA" not in _codigos(achados)


def test_palavra_dentro_de_outra_nao_dispara():
    """'sempre' não pode casar dentro de 'dessemprego' e afins -- alarme falso
    ensina o leitor a ignorar o validador."""
    achados = validar_leitura("O quadro de desemprego pesa no setor.",
                              [_ticker("NVDA")])
    assert "LEITURA_LEI_ABSOLUTA" not in _codigos(achados)


def test_bloco_de_codigo_nao_e_lintado():
    """Número e palavra dentro de ```bloco``` são dado citado, não afirmação."""
    achados = validar_leitura("```\nsempre = True\n```\nO papel tem tendido a cair.",
                              [_ticker("NVDA")])
    assert "LEITURA_LEI_ABSOLUTA" not in _codigos(achados)


# ── estado de run-up contradito ─────────────────────────────────────────────

def test_dizer_descontado_de_quem_esta_neutro_e_erro():
    """O caso AVGO depois do corte simétrico: -6,91% deixou de ser desconto."""
    achados = validar_leitura("AVGO está atualmente descontado.",
                              [_ticker("AVGO", estado="neutro")])
    assert "LEITURA_ESTADO_CONTRADITO" in _codigos(achados)


def test_estado_que_bate_com_o_dado_passa():
    achados = validar_leitura("SMCI está esticado no mês pré-earnings.",
                              [_ticker("SMCI", estado="esticado")])
    assert "LEITURA_ESTADO_CONTRADITO" not in _codigos(achados)


# ── co-movimento afirmado forte demais ──────────────────────────────────────

def test_mesmo_trade_sem_correlacao_de_070_e_erro():
    """O relatório real dizia, corretamente, que NENHUM par chegava a 0,70 --
    a frase forte não pode aparecer com esse dado."""
    achados = validar_leitura("NVDA e SMCI são na prática o mesmo trade.",
                              [_ticker("NVDA"), _ticker("SMCI")],
                              correlacoes={"NVDA|SMCI": 0.51})
    assert "LEITURA_COMOVIMENTO_FORTE_DEMAIS" in _codigos(achados)
    assert f"{CORR_MESMO_TRADE:.2f}" in achados[0]["mensagem"]


def test_mesmo_trade_com_correlacao_alta_passa():
    achados = validar_leitura("NVDA e SMCI são na prática o mesmo trade.",
                              [_ticker("NVDA"), _ticker("SMCI")],
                              correlacoes={"NVDA|SMCI": 0.82})
    assert "LEITURA_COMOVIMENTO_FORTE_DEMAIS" not in _codigos(achados)


def test_mesmo_trade_sem_nenhuma_correlacao_medida_e_erro():
    """Par ausente é correlação NÃO MEDIDA, não correlação alta."""
    achados = validar_leitura("Os dois são o mesmo trade.",
                              [_ticker("NVDA"), _ticker("SMCI")], correlacoes=None)
    assert "LEITURA_COMOVIMENTO_SEM_DADO" in _codigos(achados)


# ── ticker sem estatística ──────────────────────────────────────────────────

def test_percentual_atribuido_a_ticker_sem_dado_e_erro():
    achados = validar_leitura("SKHY caiu 8.98% na reação.",
                              [_ticker("SKHY", erro="sem histórico de earnings")])
    assert "LEITURA_TICKER_SEM_DADO" in _codigos(achados)


def test_citar_o_ticker_como_nao_analisado_passa():
    achados = validar_leitura("SKHY não produziu estatística nesta rodada.",
                              [_ticker("SKHY", erro="sem histórico")])
    assert "LEITURA_TICKER_SEM_DADO" not in _codigos(achados)


# ── amostra curta ───────────────────────────────────────────────────────────

def test_amostra_curta_citada_sem_a_ressalva_vira_aviso():
    achados = validar_leitura("SKHY teve reação forte de queda.",
                              [_ticker("SKHY", n_events=1)])
    assert "LEITURA_AMOSTRA_CURTA_OMITIDA" in _codigos(achados)
    assert not erros(achados), "é AVISO: o dado não contradiz, só falta ressalva"


def test_amostra_curta_com_a_ressalva_passa():
    achados = validar_leitura(
        "SKHY, com apenas um evento analisado, teve forte queda.",
        [_ticker("SKHY", n_events=1)])
    assert "LEITURA_AMOSTRA_CURTA_OMITIDA" not in _codigos(achados)


# ── contrato do módulo ──────────────────────────────────────────────────────

def test_texto_limpo_nao_produz_apontamento():
    """A condição que mais importa para o validador ser levado a sério."""
    texto = ("Nos últimos 8 resultados, NVDA tem tendido a devolver o ganho "
             "do dia. AVGO mostra correlação de -0.60 entre run-up e reação, "
             "que não sobrevive à correção de múltiplos tickers.")
    achados = validar_leitura(texto, [
        _ticker("NVDA", estado="neutro"),
        _ticker("AVGO", corr=-0.60, sobrevive=False, p_corrigido=0.462,
                estado="neutro"),
    ])
    assert achados == []


def test_texto_vazio_nao_estoura():
    assert validar_leitura("", [_ticker("NVDA")]) == []
    assert validar_leitura(None, None) == []


def test_bloco_de_correcao_so_leva_os_erros():
    """Gastar uma rodada de LLM para corrigir um AVISO é desperdício -- o
    leitor lê a ressalva no aviso publicado."""
    achados = validar_leitura(
        "SKHY teve queda. O papel sempre cai.",
        [_ticker("SKHY", n_events=1)])
    bloco = bloco_de_correcao(achados)
    assert "sempre" in bloco
    assert "amostra" not in bloco.lower() or "sempre" in bloco


def test_sem_erro_nao_ha_bloco_de_correcao():
    assert bloco_de_correcao([]) == ""


def test_resumo_legivel_traz_nivel_codigo_e_ticker():
    achados = validar_leitura(_TEXTO_DO_INCIDENTE,
                              [_ticker("AVGO", corr=-0.60, sobrevive=False)])
    linha = resumo_legivel(achados)[0]
    assert linha.startswith("[ERRO]") and "AVGO" in linha


def test_o_gerador_chama_o_validador():
    """Amarra por leitura de fonte: validador que existe mas ninguém chama é
    o mesmo que não existir -- e foi assim que esta tela ficou sem anteparo
    enquanto o Veredito tinha o dele."""
    import pathlib
    from agent import reacao_earnings_ia as gerador
    fonte = pathlib.Path(gerador.__file__).read_text(encoding="utf-8")
    codigo = "\n".join(l for l in fonte.splitlines()
                       if not l.strip().startswith("#"))
    assert "validar_leitura(" in codigo
    assert "bloco_de_correcao(" in codigo, "o erro tem que voltar ao modelo"
    assert '"avisos"' in codigo, "e o apontamento tem que chegar à tela"


# ── negar o rótulo é obediência, não erro ───────────────────────────────────
#
# Mesmo defeito que produziu três falsos positivos no validador da Análise
# Rápida: casar a PALAVRA em vez da AFIRMAÇÃO. Com `estado_atual = neutro`, a
# redação que o dado pede é justamente "não está descontado" -- e a primeira
# versão apontava contra ela.

@pytest.mark.parametrize("frase", [
    "AVGO não está descontado — o run-up de -6,91% é neutro.",
    "AVGO deixou de estar descontado nesta janela.",
    "AVGO está neutro, longe de esticado ou descontado.",
])
def test_negar_o_rotulo_nao_e_apontamento(frase):
    achados = validar_leitura(frase, [_ticker("AVGO", estado="neutro")])
    assert "LEITURA_ESTADO_CONTRADITO" not in _codigos(achados)


@pytest.mark.parametrize("frase", [
    "AVGO está atualmente descontado.",
    "AVGO não está neutro, está descontado.",
    "AVGO está esticado, não descontado.",
])
def test_afirmar_o_rotulo_contradito_continua_erro(frase):
    """A negação só vale COLADA ao rótulo (até duas palavras). 'não está
    neutro, está descontado' nega o outro termo e afirma este -- se a janela
    fosse larga, a reescrita teria virado mordaça."""
    achados = validar_leitura(frase, [_ticker("AVGO", estado="neutro")])
    assert "LEITURA_ESTADO_CONTRADITO" in _codigos(achados)


def test_o_rotulo_que_bate_com_o_dado_nao_cai_pela_negacao_do_outro():
    """'AVGO está esticado, não descontado' com dado 'esticado': o primeiro
    rótulo confere e o segundo está negado — nada a apontar."""
    achados = validar_leitura("AVGO está esticado, não descontado.",
                              [_ticker("AVGO", estado="esticado")])
    assert "LEITURA_ESTADO_CONTRADITO" not in _codigos(achados)
