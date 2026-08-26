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


@pytest.mark.parametrize("resposta", [None, "", "   ", "```\n{'a': 1}\n```", 42])
def test_resposta_nao_utilizavel_e_ERRO_e_nao_aprovacao(resposta):
    """O buraco mais perigoso que a auditoria de 26/08/2026 encontrou: lista
    vazia de achados é lida por quem chama como "nada destoa". Resposta vazia,
    timeout convertido em string e resposta só-com-bloco-de-código eram
    APROVADAS -- falha de geração publicada como texto conferido."""
    achados = validar_leitura(resposta, [_ticker("NVDA")])
    assert "LEITURA_TEXTO_VAZIO" in _codigos(achados)
    assert erros(achados), "tem que impedir publicação, não só avisar"


def test_resposta_invalida_nao_estoura_mesmo_sem_resultados():
    assert "LEITURA_TEXTO_VAZIO" in _codigos(validar_leitura(None, None))


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


# ═══ auditorias de 26/08/2026 — os casos que se reproduziram ════════════════
#
# Sete checagens desta tela apontavam contra o modelo OBEDECENDO o SYSTEM. Cada
# caso vem no par: o texto correto que era recusado e o erro real que não pode
# sumir junto.

# ── correlação: negar a promoção é obediência ──────────────────────────────

def test_negar_que_a_correlacao_e_padrao_nao_e_apontamento():
    """O SYSTEM manda declarar que a correlação não sobrevive. Recusar quem
    declara é recusar quem acertou — e na retentativa o modelo obediente seria
    recusado outra vez."""
    achados = validar_leitura(
        "A correlação de AVGO não é um padrão estatisticamente relevante.",
        [_ticker("AVGO", corr=-0.60, sobrevive=False, p_corrigido=0.462)])
    assert "LEITURA_CORRELACAO_SEM_SUPORTE" not in _codigos(achados)


# ── lei absoluta: negação, conjunção e um erro por texto ───────────────────

@pytest.mark.parametrize("frase", [
    "AVGO nem sempre sobe.",
    "O dado não garante reversão de AVGO.",
    "Nada garante que AVGO suba.",
])
def test_negar_a_lei_e_o_oposto_de_afirma_la(frase):
    assert "LEITURA_LEI_ABSOLUTA" not in _codigos(
        validar_leitura(frase, [_ticker("AVGO")]))


def test_sempre_que_e_conjuncao_nao_lei():
    """"Sempre que possível, declare o n" é orientação de método, não
    afirmação de invariância sobre o ativo."""
    assert "LEITURA_LEI_ABSOLUTA" not in _codigos(validar_leitura(
        "Sempre que possível, declare o n da amostra.", [_ticker("AVGO")]))


def test_um_erro_de_lei_por_texto_e_nao_um_por_palavra():
    """Três "sempre" numa retentativa devolviam três apontamentos idênticos e
    enchiam o bloco de correção com repetição."""
    achados = validar_leitura(
        "AVGO sempre cai. SMCI garantidamente sobe. NVDA certamente reverte.",
        [_ticker("AVGO"), _ticker("SMCI"), _ticker("NVDA")])
    assert len([a for a in achados if a["codigo"] == "LEITURA_LEI_ABSOLUTA"]) == 1


def test_lei_que_predica_o_papel_continua_erro():
    assert "LEITURA_LEI_ABSOLUTA" in _codigos(
        validar_leitura("AVGO sempre cai depois do balanço.", [_ticker("AVGO")]))


# ── co-movimento: dois papéis, correlação legível, sem negação ─────────────

def test_negar_o_mesmo_trade_nao_e_apontamento():
    assert "LEITURA_COMOVIMENTO_FORTE_DEMAIS" not in _codigos(validar_leitura(
        "SMCI e AVGO não são praticamente o mesmo trade.",
        [_ticker("SMCI"), _ticker("AVGO")], {"SMCI|AVGO": 0.51}))


def test_comparar_com_a_media_do_setor_nao_e_afirmar_co_movimento():
    """"praticamente o mesmo desempenho da MÉDIA" não fala de dois papéis da
    cesta — a checagem exige dois tickers na frase (ou anáfora plural)."""
    assert "LEITURA_COMOVIMENTO_FORTE_DEMAIS" not in _codigos(validar_leitura(
        "AVGO teve praticamente o mesmo desempenho da média do setor.",
        [_ticker("AVGO")], {"AVGO|SMCI": 0.41}))


def test_correlacao_aninhada_e_lida_e_nao_tratada_como_ausente():
    """{"AVGO": {"SMCI": 0.91}} fazia values() devolver dicionários, nenhum
    passava por número, e a checagem inventava "nenhuma correlação medida"."""
    achados = validar_leitura("AVGO e SMCI são o mesmo trade.",
                              [_ticker("AVGO"), _ticker("SMCI")],
                              {"AVGO": {"SMCI": 0.91}})
    assert "LEITURA_COMOVIMENTO_SEM_DADO" not in _codigos(achados)
    assert "LEITURA_COMOVIMENTO_FORTE_DEMAIS" not in _codigos(achados)


def test_nan_na_correlacao_nao_aprova_o_mesmo_trade_em_silencio():
    """`max([nan]) < 0.70` é False — a checagem simplesmente não apontava."""
    assert "LEITURA_COMOVIMENTO_SEM_DADO" in _codigos(validar_leitura(
        "AVGO e SMCI são o mesmo trade.", [_ticker("AVGO"), _ticker("SMCI")],
        {"AVGO|SMCI": float("nan")}))


# ── estado: o dado cru também precisa ser normalizado ──────────────────────

def test_estado_com_capitalizacao_diferente_nao_inventa_contradicao():
    """A prosa passava por _sem_acento e o dado não — "Esticado" no JSON
    contradizia "esticado" no texto."""
    for cru in ("Esticado", "ESTICADO", " esticado "):
        assert "LEITURA_ESTADO_CONTRADITO" not in _codigos(validar_leitura(
            "AVGO está esticado.", [_ticker("AVGO", estado=cru)]))


# ── ticker sem estatística: o percentual tem que ser DELE ──────────────────

def test_percentual_de_outro_ticker_na_mesma_frase_nao_acusa():
    """"ARM não foi analisada, enquanto AVGO caiu 5%" — os 5% são do AVGO."""
    assert "LEITURA_TICKER_SEM_DADO" not in _codigos(validar_leitura(
        "ARM não foi analisada, enquanto AVGO caiu 5%.",
        [_ticker("ARM", erro="sem histórico"), _ticker("AVGO")]))


def test_a_ressalva_de_nao_analisado_e_o_que_o_system_pede():
    assert "LEITURA_TICKER_SEM_DADO" not in _codigos(validar_leitura(
        "SKHY não produziu estatística nesta rodada, com 0% de cobertura.",
        [_ticker("SKHY", erro="sem histórico")]))


def test_atribuir_percentual_ao_ticker_sem_dado_continua_erro():
    assert "LEITURA_TICKER_SEM_DADO" in _codigos(validar_leitura(
        "SKHY caiu 8,98% na reação.", [_ticker("SKHY", erro="sem histórico")]))


# ── amostra curta: a regra é declarar o N, não dizer uma palavra ───────────

def test_falar_em_evento_sem_dar_o_numero_nao_cumpre_a_ressalva():
    """"AVGO teve comportamento diferente no evento anterior" continha
    "evento" e passava, sem informar que a amostra era de três."""
    assert "LEITURA_AMOSTRA_CURTA_OMITIDA" in _codigos(validar_leitura(
        "AVGO teve comportamento diferente no evento anterior.",
        [_ticker("AVGO", n_events=3)]))


@pytest.mark.parametrize("frase", [
    "AVGO, com amostra de 3 eventos, mostrou alta.",
    "AVGO, baseado em 3 balanços, mostrou alta.",
    "AVGO, em 3 ocasiões, mostrou alta.",
])
def test_declarar_o_numero_da_amostra_cumpre_a_ressalva(frase):
    assert "LEITURA_AMOSTRA_CURTA_OMITIDA" not in _codigos(
        validar_leitura(frase, [_ticker("AVGO", n_events=3)]))


def test_amostra_de_um_aceita_a_forma_por_extenso():
    assert "LEITURA_AMOSTRA_CURTA_OMITIDA" not in _codigos(validar_leitura(
        "SKHY, com apenas um evento analisado, teve forte queda.",
        [_ticker("SKHY", n_events=1)]))


# ── payload torto vira achado, não exceção ────────────────────────────────

@pytest.mark.parametrize("resultados", [
    [{"ticker": "A", "summary": "n/d"}],
    [{"ticker": "A", "summary": {"runup": "n/d"}}],
    ["string no lugar do dict"],
    [None], None, "string",
])
def test_resultados_malformados_nao_derrubam_a_validacao(resultados):
    assert isinstance(validar_leitura("AVGO está esticado.", resultados), list)


def test_p_corrigido_como_string_nao_quebra_a_mensagem():
    """`f"{pc:.3f}"` com pc = "0.462" levantava ValueError DENTRO do validador,
    no meio de reportar o achado."""
    achados = validar_leitura(
        "AVGO mostra um padrão estatisticamente relevante.",
        [{"ticker": "AVGO", "summary": {"n_events": 8, "runup": {
            "corr_runup_reacao": -0.6, "corr_sobrevive": False,
            "corr_p_corrigido": "0.462"}}}])
    assert "LEITURA_CORRELACAO_SEM_SUPORTE" in _codigos(achados)
    assert "0.462" in achados[0]["mensagem"]


def test_corr_sobrevive_como_string_false_nao_vira_sobrevivente():
    """`bool("false")` é True — e era assim que a afirmação estatística que a
    checagem existe para barrar passava direto."""
    assert "LEITURA_CORRELACAO_SEM_SUPORTE" in _codigos(validar_leitura(
        "AVGO mostra um padrão estatisticamente relevante.",
        [{"ticker": "AVGO", "summary": {"n_events": 8, "runup": {
            "corr_runup_reacao": -0.6, "corr_sobrevive": "false"}}}]))


# ═══ auditoria de 26/08/2026 — "esticado" como balde, não como estado ══════
#
# A tela de Reação a Earnings saiu com DOIS [ERRO] LEITURA_ESTADO_CONTRADITO
# num texto que acertava o estado dos dois papéis. O texto dizia, literalmente,
# 'NVDA (6,42%) e AVGO (-6,65%) estão classificados como "neutro"' -- e mesmo
# assim caiu, porque em OUTRA frase a palavra "esticado" aparecia nomeando os
# baldes históricos da amostra.

def test_bucket_historico_nao_e_estado_do_papel():
    """A frase do NVDA, verbatim. "bucket de esticado/descontado" conta eventos
    passados; não afirma nada sobre hoje."""
    frase = ("Para NVDA, nos 6 eventos analisados, houve uma correlação "
             "positiva forte (0,92) entre o run-up e a reação, embora com "
             "apenas 1 evento em cada bucket de esticado/descontado.")
    achados = validar_leitura(frase, [_ticker("NVDA", estado="neutro")])
    assert "LEITURA_ESTADO_CONTRADITO" not in _codigos(achados)


def test_contagem_de_casos_esticados_nao_e_estado_do_papel():
    """A frase do AVGO, verbatim. "3 dos 3 casos esticados" é a contagem do
    balde, e o rótulo vem no plural justamente porque fala de eventos."""
    frase = ("AVGO, por sua vez, demonstrou uma correlação negativa moderada "
             "(-0,60), com 3 dos 3 casos esticados reagindo negativamente em "
             "média -9,67%.")
    achados = validar_leitura(frase, [_ticker("AVGO", estado="neutro")])
    assert "LEITURA_ESTADO_CONTRADITO" not in _codigos(achados)


def test_o_texto_inteiro_da_auditoria_nao_gera_estado_contradito():
    """As duas frases + a que declara o estado certo, como saiu na tela."""
    texto = (
        "Para NVDA, nos 6 eventos analisados, houve uma correlação positiva "
        "forte (0,92) entre o run-up e a reação, embora com apenas 1 evento "
        "em cada bucket de esticado/descontado. AVGO, por sua vez, demonstrou "
        "uma correlação negativa moderada (-0,60), com 3 dos 3 casos "
        "esticados reagindo negativamente em média -9,67%. Atualmente, SMCI "
        "(31,42%) e ARM (11,64%) estão na categoria \"esticado\". NVDA "
        "(6,42%) e AVGO (-6,65%) estão classificados como \"neutro\"."
    )
    achados = validar_leitura(texto, [
        _ticker("NVDA", estado="neutro"), _ticker("AVGO", estado="neutro"),
        _ticker("SMCI", estado="esticado"), _ticker("ARM", estado="esticado"),
    ])
    assert "LEITURA_ESTADO_CONTRADITO" not in _codigos(achados)


@pytest.mark.parametrize("frase", [
    "NVDA está esticado no mês pré-earnings.",
    "NVDA continua esticado apesar do recuo.",
    "NVDA permanece esticado.",
    "NVDA aparece esticado nesta janela.",
    "NVDA está na categoria \"esticado\".",
    "NVDA está classificado como esticado.",
    "NVDA segue esticado depois do salto.",
    "Estado atual de NVDA: esticado.",
])
def test_atribuir_o_rotulo_ao_papel_continua_erro(frase):
    """O gate de atribuição não pode virar mordaça: toda cópula real ainda
    cai quando o dado do dia diz outra coisa."""
    achados = validar_leitura(frase, [_ticker("NVDA", estado="neutro")])
    assert "LEITURA_ESTADO_CONTRADITO" in _codigos(achados), frase


def test_atribuidor_nao_atravessa_virgula():
    """"está neutro, longe de esticado" -- a cópula é do OUTRO rótulo."""
    achados = validar_leitura("NVDA está neutro, longe de esticado.",
                              [_ticker("NVDA", estado="neutro")])
    assert "LEITURA_ESTADO_CONTRADITO" not in _codigos(achados)


# ═══ auditoria de 26/08/2026 — "forte" descreve r, não promove r ════════════
#
# Duas gerações da MESMA tela, no mesmo dia, com a mesma afirmação sobre o
# mesmo número. Só a ordem do adjetivo mudou, e o veredito virou.

_CORR_FRACA = dict(corr=0.92, sobrevive=False, p_corrigido=0.31)


@pytest.mark.parametrize("frase", [
    # a que caiu como ERRO na tela
    "NVDA mostra uma forte correlação positiva (0.92) nos 6 eventos com run-up medido.",
    # a mesma coisa, com o adjetivo depois -- passava por acidente de regex
    "Para NVDA houve uma correlação positiva forte (0,92) nos 6 eventos analisados.",
    "NVDA tem correlação mais forte (0.92) entre run-up e reação, em 6 eventos.",
    "NVDA: forte correlação (0.92), amostra pequena.",
    "NVDA mostra forte correlação (0.92) — indício, não prova.",
])
def test_magnitude_com_amostra_declarada_nao_e_promocao(frase):
    """Descrever |r| E dizer sobre quantos eventos é ler o número, não
    promovê-lo. É exatamente o que o card faz na mesma tela."""
    achados = validar_leitura(frase, [_ticker("NVDA", **_CORR_FRACA)])
    assert "LEITURA_CORRELACAO_SEM_SUPORTE" not in _codigos(achados), frase


@pytest.mark.parametrize("frase", [
    "NVDA mostra uma forte correlação positiva entre run-up e reação.",
    "Há correlação forte entre o run-up de NVDA e a reação seguinte.",
    "NVDA tem correlação positiva forte entre run-up e reação.",
])
def test_magnitude_sem_amostra_continua_caindo(frase):
    """"forte correlação" com o número solto engana: quem lê não tem como
    saber que são 6 eventos e que ela não sobrevive ao Holm."""
    achados = validar_leitura(frase, [_ticker("NVDA", **_CORR_FRACA)])
    assert "LEITURA_CORRELACAO_SEM_SUPORTE" in _codigos(achados), frase


@pytest.mark.parametrize("frase", [
    "NVDA mostra um padrão estatisticamente relevante em 6 eventos.",
    "A correlação de NVDA é um sinal confiável, medido em 6 eventos.",
    "NVDA indica um padrão de reversão nos 6 eventos analisados.",
    "NVDA tem uma relação robusta entre run-up e reação, em 6 eventos.",
])
def test_afirmar_significancia_cai_mesmo_declarando_a_amostra(frase):
    """Declarar o n não compra licença para afirmar significância: o dado diz
    que ela NÃO sobrevive à correção de múltiplos tickers. A isenção da
    amostra vale só para o adjetivo de magnitude."""
    achados = validar_leitura(frase, [_ticker("NVDA", **_CORR_FRACA)])
    assert "LEITURA_CORRELACAO_SEM_SUPORTE" in _codigos(achados), frase


def test_as_duas_ordens_do_adjetivo_recebem_o_mesmo_veredito():
    """O bug em uma linha: o mesmo sentido não pode depender de onde o
    adjetivo caiu."""
    dados = [_ticker("NVDA", **_CORR_FRACA)]
    antes = "NVDA tem correlação positiva forte entre run-up e reação."
    depois = "NVDA tem forte correlação positiva entre run-up e reação."
    assert (("LEITURA_CORRELACAO_SEM_SUPORTE" in _codigos(validar_leitura(antes, dados)))
            == ("LEITURA_CORRELACAO_SEM_SUPORTE" in _codigos(validar_leitura(depois, dados))))
