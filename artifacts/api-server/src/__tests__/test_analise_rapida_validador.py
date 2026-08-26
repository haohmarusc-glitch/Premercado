"""
Validador da Análise Rápida -- confere a SAÍDA, não o prompt.

O teste que já existia (`test_analise_rapida_ia.py`) verifica que as 18 regras
continuam ESCRITAS no SYSTEM. Isso protege contra alguém apagá-las ao
consolidar o prompt, e não contra o modelo desobedecê-las -- que é a forma
exata que a leitura da cesta tinha até 25/08/2026, quando um texto chamou de
"padrão estatisticamente relevante" uma correlação com p corrigido de 0,462.

Duas coisas esta suíte cobra tanto quanto as recusas: que o texto CERTO passe
limpo, e que a exceção do modelo dizendo "não recomendo" não seja punida.
Validador que grita à toa ensina o leitor a ignorar o bloco amarelo, e aí fica
pior que nenhum.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_analise_rapida_validador.py -v
"""
import pytest

from agent.analise_rapida_validator import (
    SECOES_OBRIGATORIAS,
    bloco_de_correcao,
    erros,
    resumo_legivel,
    validar_analise,
)


def _texto_completo(extra: str = "") -> str:
    corpo = "\n\n".join(f"## {s}\nTexto da seção." for s in SECOES_OBRIGATORIAS)
    return corpo + ("\n\n" + extra if extra else "")


def _codigos(achados):
    return {a["codigo"] for a in achados}


# ── as seis seções ──────────────────────────────────────────────────────────

def test_texto_com_as_seis_secoes_passa():
    assert validar_analise(_texto_completo()) == []


def test_secao_faltando_e_erro():
    texto = _texto_completo().replace("## Síntese\nTexto da seção.", "")
    achados = validar_analise(texto)
    assert "ANALISE_SECAO_FALTANDO" in _codigos(achados)
    assert "Síntese" in achados[0]["mensagem"]


def test_a_sintese_faltando_ganha_o_motivo_no_apontamento():
    """O próprio SYSTEM prevê que ela é 'a primeira a se perder quando o texto
    estica' -- uma previsão de falha que estava esperando conferência."""
    texto = _texto_completo().replace("## Síntese\nTexto da seção.", "")
    assert "primeira a se perder" in validar_analise(texto)[0]["mensagem"]


# ── moeda ───────────────────────────────────────────────────────────────────

def test_real_em_ativo_americano_e_erro():
    achados = validar_analise(_texto_completo("O papel vale R$ 1.200,00."))
    assert "ANALISE_MOEDA_ERRADA" in _codigos(achados)


def test_dolar_passa():
    achados = validar_analise(_texto_completo("O papel vale US$ 213,05."))
    assert "ANALISE_MOEDA_ERRADA" not in _codigos(achados)


# ── momentum anualizado ─────────────────────────────────────────────────────

_DADOS_MOMENTUM = {"technicals": {"momentumAnnualPct": 106.0}}


def test_momentum_anualizado_escrito_como_periodo_e_erro():
    """Número certo virando afirmação falsa: 106% é taxa ANUALIZADA
    extrapolada da janela, não o que o papel fez nela."""
    achados = validar_analise(
        _texto_completo("O setor acumula 106% em 90 pregões."), _DADOS_MOMENTUM)
    assert "ANALISE_MOMENTUM_COMO_PERIODO" in _codigos(achados)


def test_momentum_escrito_como_anualizado_passa():
    achados = validar_analise(
        _texto_completo("O setor roda a 106% anualizado (janela de 90 pregões)."),
        _DADOS_MOMENTUM)
    assert "ANALISE_MOMENTUM_COMO_PERIODO" not in _codigos(achados)


def test_variacao_de_preco_em_periodo_nao_e_apontada():
    """'caiu 5% em 3 dias' é descrição legítima -- a checagem casa o VALOR do
    campo, justamente para não punir isto."""
    achados = validar_analise(
        _texto_completo("O papel caiu 5% em 3 pregões."), _DADOS_MOMENTUM)
    assert "ANALISE_MOMENTUM_COMO_PERIODO" not in _codigos(achados)


def test_sem_o_campo_no_json_nao_ha_o_que_checar():
    achados = validar_analise(_texto_completo("Subiu 106% em 90 dias."), {})
    assert "ANALISE_MOMENTUM_COMO_PERIODO" not in _codigos(achados)


# ── divergência de preço ────────────────────────────────────────────────────

_DADOS_DIVERGENCIA = {"precoAtual": {
    "valor": 180.0, "fonte": "niveis", "divergenciaPct": 25.0,
    "porPainel": {"niveis": 180.0, "valuation": 225.01}}}


def test_divergencia_sem_os_dois_precos_e_erro():
    """O incidente de 18/08/2026: US$ 225,01 na valuation contra o preço dos
    painéis ao vivo, e um provedor usou a contradição como argumento altista."""
    achados = validar_analise(
        _texto_completo("Os painéis divergem entre si."), _DADOS_DIVERGENCIA)
    assert "ANALISE_DIVERGENCIA_OMITIDA" in _codigos(achados)


def test_divergencia_com_os_dois_precos_passa():
    achados = validar_analise(
        _texto_completo("O valuation usa US$ 225,01 contra os US$ 180,00 dos níveis."),
        _DADOS_DIVERGENCIA)
    assert "ANALISE_DIVERGENCIA_OMITIDA" not in _codigos(achados)


def test_preco_escrito_com_ponto_tambem_conta():
    """O JSON traz 225.01 e o modelo escreve em pt-BR; checar só uma grafia
    apontaria contra texto correto."""
    achados = validar_analise(
        _texto_completo("Valuation em US$ 225.01 e níveis em US$ 180.00."),
        _DADOS_DIVERGENCIA)
    assert "ANALISE_DIVERGENCIA_OMITIDA" not in _codigos(achados)


def test_sem_divergencia_nao_se_cobra_nada():
    dados = {"precoAtual": {"valor": 180.0, "fonte": "niveis"}}
    achados = validar_analise(_texto_completo("Preço em US$ 180,00."), dados)
    assert "ANALISE_DIVERGENCIA_OMITIDA" not in _codigos(achados)


# ── recomendação ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("frase", [
    "Recomendo comprar na faixa atual.",
    "Sugiro vender antes do balanço.",
    "Vale a pena comprar agora.",
    "É hora de comprar.",
])
def test_recomendacao_explicita_e_erro(frase):
    achados = validar_analise(_texto_completo(frase))
    assert "ANALISE_RECOMENDACAO" in _codigos(achados)


def test_o_modelo_dizendo_que_nao_recomenda_nao_e_punido():
    """Obediência à regra não pode virar apontamento -- seria o validador
    punindo justamente quem acertou."""
    achados = validar_analise(
        _texto_completo("Não recomendo comprar nem vender; seguem os níveis."))
    assert "ANALISE_RECOMENDACAO" not in _codigos(achados)


def test_cenario_com_nivel_de_invalidacao_passa():
    achados = validar_analise(_texto_completo(
        "Perder US$ 205 invalida a leitura de continuidade; acima de US$ 221 "
        "o cenário de alta segue de pé."))
    assert "ANALISE_RECOMENDACAO" not in _codigos(achados)


# ── bandas x nível técnico ──────────────────────────────────────────────────

def test_banda_chamada_de_suporte_e_erro():
    achados = validar_analise(_texto_completo("S1 funciona como suporte firme."))
    assert "ANALISE_BANDA_COMO_NIVEL_TECNICO" in _codigos(achados)


def test_banda_chamada_de_piso_e_erro():
    achados = validar_analise(_texto_completo("R2 é o teto e S2 o piso do papel."))
    assert "ANALISE_BANDA_COMO_NIVEL_TECNICO" in _codigos(achados)


def test_banda_descrita_como_banda_passa():
    achados = validar_analise(_texto_completo(
        "S1 em US$ 205,06 projeta a reação média a earnings sobre o preço atual."))
    assert "ANALISE_BANDA_COMO_NIVEL_TECNICO" not in _codigos(achados)


def test_suporte_de_media_movel_nao_e_apontado():
    """Suporte de VERDADE (média móvel, mínima) é permitido pelo prompt -- só
    não vale chamar as bandas assim."""
    achados = validar_analise(_texto_completo(
        "A MM200 em US$ 190 é o suporte mais próximo."))
    assert "ANALISE_BANDA_COMO_NIVEL_TECNICO" not in _codigos(achados)


# ── balanço já ocorrido ─────────────────────────────────────────────────────

_DADOS_BALANCO = {"reaction": {"summary": {"runup": {
    "janela_contem_earnings": True, "pregoes_desde_earnings": 3}}}}


def test_balanco_ja_ocorrido_escrito_no_futuro_e_erro():
    """Incidente NBIS de 17/08/2026: a janela engolia o pregão de reação e o
    texto dizia que o papel 'chega esticado ao balanço' -- que já tinha
    acontecido."""
    achados = validar_analise(
        _texto_completo("O papel chega esticado ao balanço."), _DADOS_BALANCO)
    assert "ANALISE_BALANCO_NO_FUTURO" in _codigos(achados)
    assert "3 pregão" in achados[0]["mensagem"]


def test_balanco_no_passado_passa():
    achados = validar_analise(
        _texto_completo("O papel reagiu com +34% ao balanço de três pregões atrás."),
        _DADOS_BALANCO)
    assert "ANALISE_BALANCO_NO_FUTURO" not in _codigos(achados)


def test_sem_a_marca_no_json_a_frase_futura_e_legitima():
    """Quando o balanço realmente está À FRENTE, 'chega esticado' é a leitura
    correta -- a checagem depende da marca do dado, não da frase sozinha."""
    achados = validar_analise(_texto_completo("O papel chega esticado ao balanço."),
                              {"reaction": {"summary": {"runup": {}}}})
    assert "ANALISE_BALANCO_NO_FUTURO" not in _codigos(achados)


# ── contrato do módulo ──────────────────────────────────────────────────────

def test_bloco_de_codigo_nao_e_lintado():
    achados = validar_analise(_texto_completo("```\nprecoBRL = 'R$'\n```"))
    assert "ANALISE_MOEDA_ERRADA" not in _codigos(achados)


def test_texto_vazio_nao_estoura():
    assert validar_analise("", {}) == []
    assert validar_analise(None, None) == []


def test_bloco_de_correcao_so_leva_os_erros():
    achados = validar_analise(_texto_completo("Recomendo comprar. Vale R$ 10."))
    bloco = bloco_de_correcao(achados)
    assert "R$" in bloco and "recomenda" in bloco.lower()


def test_sem_erro_nao_ha_bloco_de_correcao():
    assert bloco_de_correcao([]) == ""


def test_resumo_legivel_traz_nivel_e_codigo():
    achados = validar_analise(_texto_completo("Vale R$ 10."))
    assert resumo_legivel(achados)[0].startswith("[ERRO] ANALISE_MOEDA_ERRADA")


def test_o_gerador_chama_o_validador():
    """Amarra por leitura de fonte: anteparo que ninguém invoca é o mesmo que
    não existir -- foi assim que esta tela ficou sem o dela enquanto o
    Veredito, o Relatório e a cesta tinham os deles."""
    import pathlib
    from agent import analise_rapida_ia as gerador
    fonte = pathlib.Path(gerador.__file__).read_text(encoding="utf-8")
    codigo = "\n".join(l for l in fonte.splitlines()
                       if not l.strip().startswith("#"))
    assert "validar_analise(" in codigo
    assert "bloco_de_correcao_analise(" in codigo, "o erro tem que voltar ao modelo"
    assert '"avisos"' in codigo, "e o apontamento tem que chegar à tela"


# ── a rodada real de WOLF (26/08/2026) ──────────────────────────────────────
#
# Primeira vez que o validador rodou em produção. O resultado foi instrutivo
# nos dois sentidos: ele APONTOU o que estava certo e DEIXOU PASSAR os dois
# erros de verdade, no mesmo parágrafo.

_RUNUP_WOLF = {"reaction": {"summary": {"runup": {
    "janela_contem_earnings": True, "pregoes_desde_earnings": 4,
    "runup_atual_pct": 6.26, "runup_atual_ex_evento_pct": 14.92}}}}

# Verbatim do texto publicado.
_FRASE_ERRADA = ('O preço atual está "esticado" em relação ao próximo balanço, '
                 "pois nos 4 pregões desde o último evento, o papel reagiu com "
                 "14,92% de alta.")
_FRASE_CERTA = ("Historicamente, em dois eventos anteriores nos quais o papel "
                "chegou esticado (+10% nos 21 pregões pré-earnings), a reação "
                "média foi de -0,3%.")


def test_passado_historico_nao_e_mais_apontado():
    """O falso positivo: 'chegou esticado' descrevendo eventos HISTÓRICOS é a
    redação correta, e a primeira versão apontava contra ela."""
    achados = validar_analise(_texto_completo(_FRASE_CERTA), _RUNUP_WOLF)
    assert "ANALISE_BALANCO_NO_FUTURO" not in _codigos(achados)


@pytest.mark.parametrize("forma", [
    "O papel chega esticado ao balanço.",
    "O papel chegando esticado ao evento.",
    "O papel chegará esticado ao balanço.",
])
def test_presente_e_futuro_continuam_apontados(forma):
    achados = validar_analise(_texto_completo(forma), _RUNUP_WOLF)
    assert "ANALISE_BALANCO_NO_FUTURO" in _codigos(achados)


def test_esticamento_pendurado_no_proximo_balanco_e_erro():
    """O erro que escapou: a frase RECONHECE que o evento passou ('nos 4
    pregões desde o último evento') e mesmo assim pendura o esticamento no
    que vem."""
    achados = validar_analise(_texto_completo(_FRASE_ERRADA), _RUNUP_WOLF)
    assert "ANALISE_ESTICAMENTO_NO_PROXIMO_BALANCO" in _codigos(achados)
    assert "4 pregão" in [a["mensagem"] for a in achados
                          if a["codigo"] == "ANALISE_ESTICAMENTO_NO_PROXIMO_BALANCO"][0]


def test_runup_apresentado_como_reacao_e_erro():
    """O pior da rodada: 'o papel reagiu com 14,92% de alta', quando 14,92% é
    o run-up EX-EVENTO e a reação do dia foi -7,53%. Sinal invertido com cara
    de fato apurado."""
    achados = validar_analise(_texto_completo(_FRASE_ERRADA), _RUNUP_WOLF)
    assert "ANALISE_RUNUP_COMO_REACAO" in _codigos(achados)


def test_runup_citado_como_runup_passa():
    achados = validar_analise(_texto_completo(
        "O run-up ex-evento é de 14,92% nos 21 pregões."), _RUNUP_WOLF)
    assert "ANALISE_RUNUP_COMO_REACAO" not in _codigos(achados)


def test_reacao_com_o_numero_certo_passa():
    """A redação correta para o mesmo caso: a reação foi -7,53%."""
    achados = validar_analise(_texto_completo(
        "O papel reagiu com -7,53% no dia do balanço, quatro pregões atrás."),
        _RUNUP_WOLF)
    assert "ANALISE_RUNUP_COMO_REACAO" not in _codigos(achados)


def test_sem_balanco_na_janela_nada_disso_e_cobrado():
    """Quando o balanço realmente está à frente, falar do próximo é correto."""
    achados = validar_analise(_texto_completo(_FRASE_ERRADA),
                              {"reaction": {"summary": {"runup": {}}}})
    assert "ANALISE_ESTICAMENTO_NO_PROXIMO_BALANCO" not in _codigos(achados)
    assert "ANALISE_BALANCO_NO_FUTURO" not in _codigos(achados)
