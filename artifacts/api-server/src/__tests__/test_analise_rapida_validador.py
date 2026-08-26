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


@pytest.mark.parametrize("resposta", [None, "", "   ", "```\n{'a': 1}\n```", 42])
def test_resposta_nao_utilizavel_e_ERRO_e_nao_aprovacao(resposta):
    """O buraco mais perigoso que a auditoria de 26/08/2026 encontrou: lista
    vazia de achados é lida por quem chama como "nada destoa". Resposta vazia,
    timeout convertido em string e resposta só-com-bloco-de-código eram
    APROVADAS — falha de geração publicada como análise conferida."""
    achados = validar_analise(resposta, {})
    assert "ANALISE_TEXTO_VAZIO" in _codigos(achados)
    assert erros(achados), "tem que impedir publicação, não só avisar"


def test_entrada_degenerada_nao_estoura():
    """Devolve lista, nunca exceção — mas com o ERRO dentro, não vazia."""
    for texto, dados in (("", {}), (None, None), ("", None)):
        achados = validar_analise(texto, dados)
        assert isinstance(achados, list)
        assert "ANALISE_TEXTO_VAZIO" in _codigos(achados)


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


# ── a rodada de ADI (26/08/2026): o coringa do arredondamento ───────────────
#
# Segundo falso positivo do validador em duas rodadas reais, e desta vez a
# culpa era do casamento numérico: run-up de 1,38% gerava a grafia "1", e a
# checagem usava SUBSTRING. Qualquer frase com verbo de reação e um algarismo
# 1 -- "reagiu com -2,15%", "a reação de 21%", "em 2026-08-19" -- virava
# apontamento.

_RUNUP_ADI = {"reaction": {"summary": {"runup": {
    "janela_contem_earnings": True, "pregoes_desde_earnings": 2,
    "runup_atual_ex_evento_pct": 1.38}}}}


@pytest.mark.parametrize("frase", [
    "O papel reagiu com -2,15% no dia do balanço.",
    "A reação média foi de 21% nos últimos eventos.",
    "A reação ocorreu em 2026-08-19.",
    "A reação foi de 11,38% no evento anterior.",
])
def test_arredondamento_nao_vira_coringa(frase):
    """Nenhuma destas cita 1,38 — mas todas contêm o algarismo 1."""
    achados = validar_analise(_texto_completo(frase), _RUNUP_ADI)
    assert "ANALISE_RUNUP_COMO_REACAO" not in _codigos(achados)


def test_o_valor_de_verdade_continua_sendo_pego():
    achados = validar_analise(
        _texto_completo("O papel reagiu com 1,38% de alta."), _RUNUP_ADI)
    assert "ANALISE_RUNUP_COMO_REACAO" in _codigos(achados)


def test_fronteira_impede_casar_dentro_de_numero_maior():
    """1,38 não pode casar dentro de 11,38 nem de 1,385."""
    from agent.analise_rapida_validator import _cita_numero
    assert _cita_numero("reagiu com 1,38%", 1.38) is True
    assert _cita_numero("reagiu com 11,38%", 1.38) is False
    assert _cita_numero("reagiu com 1,385%", 1.38) is False


def test_preco_ainda_aceita_a_grafia_inteira():
    """Para PREÇO o inteiro é escrita legítima: 'US$ 180' é 180,00. As duas
    checagens não podem compartilhar a mesma régua."""
    from agent.analise_rapida_validator import _cita_numero
    assert _cita_numero("US$ 180 nos níveis", 180.0, inteiro_ok=True) is True
    assert _cita_numero("US$ 180 nos níveis", 180.0, inteiro_ok=False) is False


def test_divergencia_continua_funcionando_com_a_fronteira():
    achados = validar_analise(
        _texto_completo("O valuation usa US$ 225,01 contra os US$ 180 dos níveis."),
        _DADOS_DIVERGENCIA)
    assert "ANALISE_DIVERGENCIA_OMITIDA" not in _codigos(achados)


# ── a rodada de ADI (26/08/2026): banda x nível pedia AFIRMAÇÃO, não token ──
#
# Terceiro falso positivo em três rodadas reais, e o segundo por casar TOKEN
# em vez de AFIRMAÇÃO. A checagem exigia só que "R1" e "resistência"
# aparecessem na mesma frase -- e o SYSTEM manda o modelo escrever exatamente
# essa distinção ("suporte e resistência de verdade só a partir de
# máximas/mínimas e médias móveis"). Obedecer e errar davam o mesmo apontamento.

@pytest.mark.parametrize("frase", [
    "R1 em US$ 245 é banda de volatilidade, não resistência do gráfico.",
    "S1 e S2 não são suporte; a resistência de verdade é a máxima de US$ 251.",
    "R1 (US$ 245) marca a banda de reação, enquanto a resistência técnica fica na MM200.",
    "R1 e R2 ficam acima; a resistência do gráfico é a máxima de julho.",
])
def test_negar_a_identificacao_e_obediencia_ao_system(frase):
    achados = validar_analise(_texto_completo(frase))
    assert "ANALISE_BANDA_COMO_NIVEL_TECNICO" not in _codigos(achados)


def test_piso_nao_casa_dentro_de_episodio():
    """Mesma família do coringa do arredondamento: substring sem fronteira.
    'episódio' contém 'piso'."""
    achados = validar_analise(
        _texto_completo("O episódio de agosto levou o papel até R1."))
    assert "ANALISE_BANDA_COMO_NIVEL_TECNICO" not in _codigos(achados)


@pytest.mark.parametrize("frase", [
    "R1 em US$ 245 funciona como resistência.",
    "R1 é a resistência mais próxima.",
    "S1 atua como piso do movimento.",
    "O suporte S1 segura o papel.",
    "A resistência em R2 trava a alta.",
    "S1 e S2 são os suportes relevantes.",
    "R1 vira zona de defesa se perder o nível.",
])
def test_identificar_a_banda_com_o_nivel_continua_sendo_erro(frase):
    """A reescrita não pode virar mordaça: o erro que a checagem existe para
    pegar tem que continuar caindo."""
    achados = validar_analise(_texto_completo(frase))
    assert "ANALISE_BANDA_COMO_NIVEL_TECNICO" in _codigos(achados)


def test_a_mensagem_mostra_o_trecho_apontado():
    """Sem o trecho, quem lê a caixa amarela não sabe QUAL frase revisar --
    foi o que custou uma rodada de diagnóstico nos dois falsos positivos
    anteriores."""
    achados = validar_analise(
        _texto_completo("R1 em US$ 245 funciona como resistência."))
    msg = next(a["mensagem"] for a in achados
               if a["codigo"] == "ANALISE_BANDA_COMO_NIVEL_TECNICO")
    assert "funciona como resistência" in msg


def test_acento_separa_a_copula_da_conjuncao():
    """A checagem roda sobre o texto COM acento de propósito: sem ele "é" e
    "e" viram a mesma letra, e "R1 E a resistência" (lista) não teria como se
    distinguir de "R1 É a resistência" (identificação)."""
    assert "ANALISE_BANDA_COMO_NIVEL_TECNICO" in _codigos(
        validar_analise(_texto_completo("R1 é a resistência do papel.")))
    assert "ANALISE_BANDA_COMO_NIVEL_TECNICO" not in _codigos(
        validar_analise(_texto_completo(
            "R1 e a resistência de julho ficam acima do preço.")))


# ═══ auditorias de 26/08/2026 — os casos que se reproduziram ════════════════
#
# Quatro auditorias independentes leram o validador. Cada alegação foi RODADA
# contra o código antes de qualquer correção; o que segue é o que se
# reproduziu, sempre no par que importa: o texto CORRETO que era apontado e o
# erro DE VERDADE que não pode sumir junto com o alarme falso.

# ── recomendação: a regra que o prompt trata como dura cobria seis formas ───

@pytest.mark.parametrize("frase", [
    "Compre WOLF agora.",
    "Venda antes do balanço.",
    "Minha recomendação de compra é clara.",
    "Eu compraria neste nível.",
    "Melhor vender antes do balanço.",
    "Rating: BUY.",
    "O investidor deveria montar posição.",
    "É uma boa oportunidade de compra.",
])
def test_recomendacao_disfarcada_continua_sendo_recomendacao(frase):
    assert "ANALISE_RECOMENDACAO" in _codigos(validar_analise(_texto_completo(frase)))


@pytest.mark.parametrize("frase", [
    "Dada a análise atual e todos os indicadores, não é hora de comprar AVGO.",
    "Não vou recomendar comprar nem vender.",
    "Isto não é recomendação de compra.",
    "A decisão é do leitor.",
])
def test_recusar_ou_negar_a_recomendacao_e_obediencia(frase):
    """A janela de negação é a FRASE inteira: a versão anterior olhava 40
    caracteres antes do match e perdia o "não" em frases longas."""
    assert "ANALISE_RECOMENDACAO" not in _codigos(
        validar_analise(_texto_completo(frase)))


# ── seções: substring aceitava título errado e recusava título certo ────────

@pytest.mark.parametrize("titulo", ["## Síntese", "**Síntese**", "### Síntese",
                                    "## 3. Síntese", "##Síntese", "## SÍNTESE",
                                    "## Síntese:"])
def test_cabecalho_da_secao_em_qualquer_forma_conta(titulo):
    from agent.analise_rapida_validator import _secao_presente
    assert _secao_presente(titulo, "Síntese") is True


def test_titulo_que_continua_nao_e_a_secao():
    """"## Síntese preliminar descartada" passava como se fosse a Síntese."""
    from agent.analise_rapida_validator import _secao_presente
    assert _secao_presente("## Síntese preliminar descartada", "Síntese") is False


# ── moeda: ecoar a regra não é desobedecê-la ───────────────────────────────

@pytest.mark.parametrize("frase", [
    "Não converter para R$.",
    "Nunca use R$ nesta tela.",
    "O câmbio está em R$ 5,40 por dólar.",
])
def test_citar_a_regra_ou_o_cambio_nao_e_erro_de_moeda(frase):
    assert "ANALISE_MOEDA_ERRADA" not in _codigos(
        validar_analise(_texto_completo(frase)))


def test_preco_do_ativo_em_real_continua_erro():
    assert "ANALISE_MOEDA_ERRADA" in _codigos(
        validar_analise(_texto_completo("O papel vale R$ 1.200,00.")))


# ── próximo balanço: o braço solto virava coringa ──────────────────────────

_RUNUP_OCORRIDO = {"reaction": {"summary": {"runup": {
    "janela_contem_earnings": True, "pregoes_desde_earnings": 4}}}}


def test_mencionar_o_proximo_balanco_sem_pendurar_esticamento_passa():
    """"O próximo balanço está previsto para novembro" é frase correta e
    informativa — e virava ERRO porque o regex tinha uma alternativa solta."""
    assert "ANALISE_ESTICAMENTO_NO_PROXIMO_BALANCO" not in _codigos(
        validar_analise(_texto_completo(
            "O próximo balanço está previsto para novembro."), _RUNUP_OCORRIDO))


def test_pendurar_o_esticamento_no_proximo_balanco_continua_erro():
    assert "ANALISE_ESTICAMENTO_NO_PROXIMO_BALANCO" in _codigos(
        validar_analise(_texto_completo(
            "O preço está esticado em relação ao próximo balanço."),
            _RUNUP_OCORRIDO))


@pytest.mark.parametrize("frase,cai", [
    ("O papel chegará esticado ao balanço.", True),
    ("O papel chega esticado ao balanço.", True),
    ("O papel chegou esticado ao balanço.", False),
    ("O papel chegava esticado ao balanço.", False),
    ("O papel chegara esticado ao balanço.", False),
])
def test_o_tempo_do_verbo_decide(frase, cai):
    """"chegará" (futuro, erro) e "chegara" (mais-que-perfeito, correto) viram
    a MESMA palavra sem acento — por isso esta checagem roda sobre o texto
    acentuado."""
    achados = _codigos(validar_analise(_texto_completo(frase), _RUNUP_OCORRIDO))
    assert ("ANALISE_BALANCO_NO_FUTURO" in achados) is cai


# ── run-up x reação: o número precisa ser o PREDICADO ──────────────────────

_RUNUP_WOLF = {"reaction": {"summary": {"runup": {
    "janela_contem_earnings": True, "pregoes_desde_earnings": 4,
    "runup_atual_ex_evento_pct": 14.92}}}}


@pytest.mark.parametrize("frase", [
    "Reação foi -7,53% após run-up de 14,92%.",
    "O run-up de 14,92% precedeu a reação.",
    "A reação foi medida 14,92 horas depois.",
    "A reação ocorreu em 2026-08-19.",
])
def test_distinguir_run_up_de_reacao_e_a_redacao_pedida(frase):
    """A frase que NOMEIA o run-up está fazendo a distinção que o SYSTEM pede;
    apontá-la é punir obediência. E sem o `%` colado, "14,92 horas" casava."""
    assert "ANALISE_RUNUP_COMO_REACAO" not in _codigos(
        validar_analise(_texto_completo(frase), _RUNUP_WOLF))


def test_apresentar_o_run_up_como_reacao_continua_erro():
    """O pior erro da rodada de WOLF: 14,92% é o run-up e a reação foi -7,53%,
    então citá-lo como reação INVERTE o sinal do que aconteceu."""
    assert "ANALISE_RUNUP_COMO_REACAO" in _codigos(
        validar_analise(_texto_completo("O papel reagiu com 14,92% de alta."),
                        _RUNUP_WOLF))


# ── momentum: zero é valor, não ausência ───────────────────────────────────

def test_momentum_zero_nao_cai_para_o_outro_painel():
    """`technicals or snapshot` tratava 0.0 como ausente e checava o texto
    contra um número que não era o do painel técnico."""
    achados = _codigos(validar_analise(
        _texto_completo("O papel subiu 80% em 3 dias."),
        {"technicals": {"momentumAnnualPct": 0.0},
         "snapshot": {"momentumAnnualPct": 80.0}}))
    assert "ANALISE_MOMENTUM_COMO_PERIODO" not in achados


def test_declarar_a_taxa_como_anualizada_passa():
    assert "ANALISE_MOMENTUM_COMO_PERIODO" not in _codigos(validar_analise(
        _texto_completo("O momentum anualizado é de 106% em 90 pregões."),
        {"technicals": {"momentumAnnualPct": 106.0}}))


# ── payload torto vira achado, não exceção ─────────────────────────────────

@pytest.mark.parametrize("dados", [
    {"reaction": {"summary": "n/d"}},
    {"precoAtual": {"divergenciaPct": 3.0, "porPainel": {"a": "N/A", "b": 190.0}}},
    {"precoAtual": "sem preço"},
    {"technicals": ["lista"]},
    ["lista no lugar do dict"],
    "string no lugar do dict",
    None,
])
def test_payload_malformado_nao_derruba_a_validacao(dados):
    """Validador que morre com payload torto não protege publicação nenhuma."""
    assert isinstance(validar_analise(_texto_completo("Texto qualquer."), dados),
                      list)


def test_preco_nao_numerico_e_ignorado_sem_calar_a_divergencia():
    """Um "N/A" no meio não pode nem explodir nem apagar a checagem: os dois
    preços legíveis continuam sendo cobrados."""
    achados = _codigos(validar_analise(
        _texto_completo("O papel está caro."),
        {"precoAtual": {"divergenciaPct": 5.0,
                        "porPainel": {"a": "N/A", "b": 180.0, "c": 190.0}}}))
    assert "ANALISE_DIVERGENCIA_OMITIDA" in achados


# ── banda: a negação vale na FRASE, não só no trecho casado ────────────────

def test_negar_o_nivel_com_a_banda_no_meio_passa():
    """"Não é o suporte R1" casava como "suporte r1", sem o "não" dentro do
    trecho — a antinegação da versão anterior olhava só `m.group(0)`."""
    for frase in ("Não é o suporte R1, é banda de volatilidade.",
                  "S1 e S2 não são suporte; a resistência é a máxima."):
        assert "ANALISE_BANDA_COMO_NIVEL_TECNICO" not in _codigos(
            validar_analise(_texto_completo(frase)))
