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


def test_o_apontamento_cita_o_trecho_que_disparou():
    """PDD, 28/08/2026: este ERRO saiu SEM citar trecho, e duas auditorias
    externas independentes concluíram, cada uma por conta própria, que era
    falso positivo. Uma delas chegou a transcrever a frase que dispara --
    "indicando que o papel não chega esticado" -- enquanto afirmava que o
    gatilho não existia no texto.

    Era verdadeiro: "chega" é presente sobre um balanço de 4 pregões atrás.
    Todo outro apontamento deste validador já citava trecho; este mandava o
    leitor procurar sozinho, e achado sem trecho é indistinguível de regex
    frouxa."""
    frase = "O run-up ex-evento é de -0.54%, indicando que o papel não chega esticado."
    achados = validar_analise(_texto_completo(frase), _DADOS_BALANCO)
    msg = next(a["mensagem"] for a in achados
               if a["codigo"] == "ANALISE_BALANCO_NO_FUTURO")
    assert "Trecho:" in msg
    assert "chega esticad" in msg          # o fragmento exato do gatilho
    assert "nao chega esticado" in msg or "não chega esticado" in msg
    # E diz qual seria a redação certa, senão o leitor não sabe o que mudar.
    assert "chegou esticado" in msg


def test_a_frase_real_do_pdd_dispara():
    """A sentença verbatim da produção, que as duas auditorias leram como
    inofensiva."""
    achados = validar_analise(
        _texto_completo("Quatro pregões se passaram desde o último balanço. "
                        "O run-up atual, excluindo o impacto do evento, é de "
                        "-0.54%, indicando que o papel não chega esticado."),
        _DADOS_BALANCO)
    assert "ANALISE_BALANCO_NO_FUTURO" in _codigos(achados)


def test_a_mesma_frase_no_passado_nao_dispara():
    """A correção que o apontamento pede: 'chegou' em vez de 'chega'."""
    achados = validar_analise(
        _texto_completo("Quatro pregões se passaram desde o último balanço. "
                        "O run-up ex-evento é de -0.54%, indicando que o papel "
                        "não chegou esticado."),
        _DADOS_BALANCO)
    assert "ANALISE_BALANCO_NO_FUTURO" not in _codigos(achados)


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


# ═══ ADI, 26/08/2026 — a rodada que passou LIMPA e tinha erro ═══════════════
#
# Depois de fechar 37 defeitos das auditorias, a Análise Rápida do ADI saiu sem
# nenhum apontamento. O texto publicado dizia:
#
#   "O preço de US$ 373,66 está apenas 0,72% ACIMA da MM20 (US$ 376,36),
#    mas 3,55% abaixo da MM50 (US$ 386,91)"
#
# 373,66 é MENOR que 376,36. Magnitude certa, sinal invertido — e a frase até
# se contradizia sozinha ("acima da MM20, MAS abaixo da MM50", como se fosse
# contraste, quando está abaixo das duas). Nenhuma checagem olhava DIREÇÃO.
#
# A lição: caixa amarela vazia prova que os alarmes falsos sumiram, não que o
# texto está certo.

_DADOS_ADI = {
    "precoAtual": {"valor": 373.66},
    "snapshot": {"price": 373.66, "sma50": 386.91, "sma200": 343.15},
    "technicals": {"sma20": 376.36, "vwap": 372.54},
}


def test_o_incidente_do_adi_reproduzido():
    achados = validar_analise(_texto_completo(
        "O preço de US$ 373,66 está apenas 0,72% acima da MM20 (US$ 376,36), "
        "mas 3,55% abaixo da MM50 (US$ 386,91)."), _DADOS_ADI)
    assert "ANALISE_DIRECAO_INVERTIDA" in _codigos(achados)
    msg = next(a["mensagem"] for a in achados
               if a["codigo"] == "ANALISE_DIRECAO_INVERTIDA")
    assert "MM20" in msg and "abaixo" in msg


@pytest.mark.parametrize("frase", [
    "O preço está 3,55% acima da MM50.",
    "O preço está 8,90% abaixo da MM200.",
    "O papel fechou 0,30% abaixo da VWAP.",
])
def test_qualquer_direcao_invertida_cai(frase):
    assert "ANALISE_DIRECAO_INVERTIDA" in _codigos(
        validar_analise(_texto_completo(frase), _DADOS_ADI))


@pytest.mark.parametrize("frase", [
    "O preço de US$ 373,66 está 0,72% abaixo da MM20 (US$ 376,36).",
    "O preço está 3,55% abaixo da MM50.",
    "O preço está 8,90% acima da MM200.",
    "O preço está 0,30% acima da VWAP.",
    "O setor subiu 55,29% acima da média histórica.",
])
def test_direcao_correta_nao_cai(frase):
    assert "ANALISE_DIRECAO_INVERTIDA" not in _codigos(
        validar_analise(_texto_completo(frase), _DADOS_ADI))


def test_a_magnitude_e_a_guarda_do_sujeito_da_frase():
    """"A MM50 está 12,75% acima da MM200" fala de duas MÉDIAS, não do preço.
    Não há como saber o sujeito por regex — mas 12,75 não é a distância do
    PREÇO à MM200, e é a magnitude que denuncia isso."""
    assert "ANALISE_DIRECAO_INVERTIDA" not in _codigos(
        validar_analise(_texto_completo("A MM50 está 12,75% acima da MM200."),
                        _DADOS_ADI))


def test_sem_preco_no_payload_a_checagem_se_cala():
    assert "ANALISE_DIRECAO_INVERTIDA" not in _codigos(validar_analise(
        _texto_completo("O preço está 0,72% acima da MM20."),
        {"technicals": {"sma20": 376.36}}))


# ── banda arrolada entre os níveis ─────────────────────────────────────────
#
# O mesmo texto do ADI trazia "o S1 (banda de reação) ... configuram-se como
# suportes críticos". A checagem de banda não pegava: `configurar-se como` não
# estava entre os verbos e o vão de 40 caracteres não alcançava o predicado.

@pytest.mark.parametrize("frase", [
    "O S1 em US$ 357,14 e a MM200 configuram-se como suportes críticos.",
    "As resistências imediatas incluem a MM50 e o R1 em US$ 390,18.",
    "Os suportes compreendem a MM200 e o S1.",
    "R1 constitui a resistência mais próxima.",
    "S2 forma o piso do movimento.",
    "R1 aparece como resistência relevante.",
])
def test_predicados_alem_da_copula_simples(frase):
    assert "ANALISE_BANDA_COMO_NIVEL_TECNICO" in _codigos(
        validar_analise(_texto_completo(frase)))


@pytest.mark.parametrize("frase", [
    "Abaixo do preço, o S1 (banda de reação) em US$ 357,14 e a MM200 "
    "configuram-se como suportes críticos.",
    "As resistências imediatas incluem a MM50 e o R1 (banda de reação).",
])
def test_rotular_a_banda_na_frase_e_o_bastante(frase):
    """Usar a palavra proibida enquanto se declara "(banda de reação)" ao lado
    não engana o leitor sobre a origem do número. Apontar isto seria o quarto
    alarme falso desta checagem em quatro rodadas reais — e alarme falso é o
    que mata a credibilidade da caixa amarela."""
    assert "ANALISE_BANDA_COMO_NIVEL_TECNICO" not in _codigos(
        validar_analise(_texto_completo(frase)))


def test_verbo_de_lista_nao_pega_frase_que_distingue():
    """"a resistência é a máxima de julho E R1 fica acima" cita os dois sem
    confundi-los — por isso o verbo da lista é restrito a incluir/compreender,
    e não um `ser` genérico."""
    for frase in ("A resistência do gráfico é a máxima de julho e R1 fica acima.",
                  "R1 e R2 ficam acima; a resistência é a máxima de julho."):
        assert "ANALISE_BANDA_COMO_NIVEL_TECNICO" not in _codigos(
            validar_analise(_texto_completo(frase)))


# ═══ INTC, 26/08/2026 — a segunda rodada limpa com erro dentro ═════════════
#
# Mesmo padrão do ADI: nenhum apontamento na tela, dois problemas no texto.
#
#   "Abaixo, o suporte imediato É A S1 em US$ 79,30"
#   "um descolamento (gap) médio de 8,25% na abertura"
#
# O primeiro é a cópula REVERSA (nível → banda); só o sentido banda → nível
# estava coberto. O segundo é o número certo de OUTRA linha do resumo com o
# rótulo do gap: os oito gaps da tabela ficam abaixo de 2,2% e a média
# absoluta é 0,83%.

# ── cópula reversa: "o suporte é a S1" ─────────────────────────────────────

@pytest.mark.parametrize("frase", [
    "Abaixo, o suporte imediato é a S1 em US$ 79,30, seguido pela MM200.",
    "A resistência mais próxima é a R1.",
    "O piso do movimento está em S2.",
    "A zona de defesa fica no S1.",
])
def test_nivel_identificado_COM_a_banda_tambem_cai(frase):
    assert "ANALISE_BANDA_COMO_NIVEL_TECNICO" in _codigos(
        validar_analise(_texto_completo(frase)))


@pytest.mark.parametrize("frase", [
    # Níveis de gráfico de verdade — máxima e MM são suporte/resistência
    # legítimos, e chamá-los assim é o que o SYSTEM manda.
    "A principal resistência é a máxima de US$ 142,35.",
    "O suporte da MM200 está em US$ 72,00.",
    # Distingue os dois na mesma frase.
    "A resistência do gráfico é a máxima de julho e R1 fica acima.",
    "R1 e R2 ficam acima; a resistência é a máxima de julho.",
    # Rotula a banda ao lado.
    "O suporte imediato é a S1 (banda de reação) em US$ 79,30.",
])
def test_a_copula_reversa_nao_pega_nivel_legitimo(frase):
    assert "ANALISE_BANDA_COMO_NIVEL_TECNICO" not in _codigos(
        validar_analise(_texto_completo(frase)))


# ── estatística com o rótulo de outra ──────────────────────────────────────

_RESUMO_INTC = {"reaction": {"summary": {
    "gap_pct_mean": 0.03, "gap_pct_abs_mean": 0.83,
    "close_pct_mean": -1.42, "close_pct_abs_mean": 9.35,
    "intraday_range_pct_mean": 8.25, "runup": {}}}}


def test_o_incidente_do_intc_reproduzido():
    achados = validar_analise(_texto_completo(
        "Historicamente, INTC mostra um descolamento (gap) médio de 8,25% "
        "na abertura."), _RESUMO_INTC)
    assert "ANALISE_ESTATISTICA_TROCADA" in _codigos(achados)


def test_a_mensagem_nomeia_o_campo_que_o_numero_realmente_e():
    """O erro típico não é inventar número, é pegar o CERTO de outro campo.
    Sem dizer qual, quem lê o apontamento não sabe se corrige o número ou o
    rótulo."""
    achados = validar_analise(_texto_completo(
        "O gap médio na abertura é de 8,25%."), _RESUMO_INTC)
    msg = next(a["mensagem"] for a in achados
               if a["codigo"] == "ANALISE_ESTATISTICA_TROCADA")
    assert "intraday_range_pct_mean" in msg
    assert "0.83" in msg, "e tem que dizer qual era o valor certo"


@pytest.mark.parametrize("frase", [
    "INTC mostra um gap médio de 0,83% na abertura.",
    "O gap médio na abertura é de 0,03%.",
    "A volatilidade média de 9,35% no fechamento do dia do balanço.",
    "O fechamento médio foi de -1,42%.",
    # Evento específico, não a média do resumo.
    "O gap de +2,19% em abril foi o maior da série.",
    # O rótulo certo para o número certo.
    "A amplitude intradiária média é de 8,25%.",
])
def test_estatistica_com_o_rotulo_certo_passa(frase):
    assert "ANALISE_ESTATISTICA_TROCADA" not in _codigos(
        validar_analise(_texto_completo(frase), _RESUMO_INTC))


def test_a_troca_vale_nos_dois_sentidos():
    for frase in ("O gap médio na abertura é de 9,35%.",
                  "O fechamento médio do dia foi de 0,83%."):
        assert "ANALISE_ESTATISTICA_TROCADA" in _codigos(
            validar_analise(_texto_completo(frase), _RESUMO_INTC))


def test_sem_resumo_de_earnings_a_checagem_se_cala():
    assert "ANALISE_ESTATISTICA_TROCADA" not in _codigos(validar_analise(
        _texto_completo("O gap médio na abertura é de 8,25%."), {}))


def test_o_texto_correto_do_intc_nao_produz_apontamento():
    """As frases do INTC que estavam certas continuam passando — a rodada não
    pode virar uma tela cheia de amarelo.

    Os dois percentuais das médias FORAM CORRIGIDOS em 26/08/2026. A versão
    original deste teste trazia "22,14% abaixo da MM50" e "17,7% acima da
    MM200", e as duas saíram de dividir pelo PREÇO:

        MM50  : 87,48/106,85-1 = -18,13%   (106,85/87,48-1 = +22,14%)
        MM200 : 87,48/72,00-1  = +21,50%   (72,00/87,48-1  = -17,70%)

    Ninguém tinha conferido a base das médias, então a frase entrou aqui como
    "correta" — e o teste passou a AFIRMAR que o erro era aceitável. Foi
    `ANALISE_DISTANCIA_DA_MEDIA` que trouxe isso à tona.

    A VWAP da mesma frase (-1,12%) estava certa, e é o detalhe que explica o
    resto: 88,47 está a 1% do preço, então as duas bases dão praticamente o
    mesmo número e o modelo acerta por acidente. Quanto mais longe o nível,
    maior a divergência -- MM50 e MM200 estão a 22% e 21%. É a mesma
    assinatura em INTC, ARM e NVDA: VWAP certa, médias invertidas.
    """
    achados = _codigos(validar_analise(_texto_completo(
        "O papel está em US$ 87,48, 18,13% abaixo da MM50 e 21,50% acima da "
        "MM200 (US$ 72,00). O preço está abaixo da VWAP de US$ 88,47 (-1,12%). "
        "O preço atual está posicionado entre a VWAP e a banda de reação S1 "
        "(US$ 79,30)."),
        {"precoAtual": {"valor": 87.48},
         "snapshot": {"price": 87.48, "sma50": 106.85, "sma200": 72.00},
         "technicals": {"sma20": 95.54, "vwap": 88.47}}))
    assert achados == set()


# ═══ INTC, segunda rodada — níveis descritos fora de ordem ═════════════════
#
# Terceira tela seguida sem apontamento e com erro dentro. Desta vez:
#
#   "encontra seu primeiro nível técnico significativo na MM20 a US$ 95,78
#    (+10,33%), SEGUIDA de perto pela banda R1 a US$ 94,97 (+9,4%)"
#
# Subindo de US$ 86,81 você encontra a R1 ANTES da MM20. O prompt entrega a
# lista de níveis já ordenada exatamente para o modelo não ter de ordenar --
# e foi essa etapa que a leitura refez errado.

@pytest.mark.parametrize("frase", [
    "INTC encontra seu primeiro nível na MM20 a US$ 95,78 (+10,33%), "
    "seguida de perto pela banda R1 a US$ 94,97 (+9,4%).",
    "O suporte na MM200 (-17,06%), seguido pelo S1 (-9,31%).",
    "A resistência em (+23,08%), em seguida a de (+21,82%).",
])
def test_sequencia_com_distancia_decrescente_e_erro(frase):
    assert "ANALISE_ORDEM_DOS_NIVEIS" in _codigos(
        validar_analise(_texto_completo(frase)))


@pytest.mark.parametrize("frase", [
    # Ordem certa.
    "A R1 a US$ 94,97 (+9,4%), seguida da MM20 a US$ 95,78 (+10,33%).",
    # Lista sem afirmar ordem — "e" não ordena nada.
    "O S1 (-9,31%) e a MM200 (-17,06%) são os próximos patamares.",
    "Acima, a MM50 (+23,08%) e a banda R2 (+21,82%) são obstáculos distantes.",
    # Lados opostos: não é sequência de distâncias.
    "A MM20 (+10,33%), seguida abaixo pelo S1 (-9,31%).",
    # Sem sinal explícito não dá para saber o lado — a checagem se cala de
    # propósito: "subiu 2% e depois caiu 5%" pareceria crescente.
    "O papel subiu 2% e depois caiu 5%.",
    # Sequência sem percentual nenhum.
    "A MM20 fica em US$ 95,78, seguida da R1 em US$ 94,97.",
])
def test_o_que_nao_afirma_ordem_de_distancia_passa(frase):
    assert "ANALISE_ORDEM_DOS_NIVEIS" not in _codigos(
        validar_analise(_texto_completo(frase)))


def test_a_mensagem_traz_as_duas_distancias():
    achados = validar_analise(_texto_completo(
        "A MM20 a US$ 95,78 (+10,33%), seguida de perto pela R1 (+9,4%)."))
    msg = next(a["mensagem"] for a in achados
               if a["codigo"] == "ANALISE_ORDEM_DOS_NIVEIS")
    assert "+10.33%" in msg and "+9.40%" in msg


# ═══ SMCI, 26/08/2026 — a checagem de rótulo trocado trocou o rótulo ═══════
#
# Primeiro falso positivo desta checagem, e com a ironia exata: ela existe
# para pegar "número certo com rótulo errado" e foi isso que fez. O texto
# dizia, com os DOIS números corretos:
#
#   "reação média absoluta de 15,09% no fechamento e um gap médio absoluto
#    de 12,16% na abertura"
#
# A versão anterior via a palavra "gap" na frase e varria TODOS os percentuais
# procurando um que não batesse com os campos de gap. Pegava o 15,09%, que é
# do fechamento, e acusava.
#
# A regra que resolve não é distância: em pt-BR o rótulo tanto precede ("o
# FECHAMENTO médio foi de 2,30%") quanto segue ("15,09% no FECHAMENTO") o
# número. É a fronteira de ORAÇÃO.

_RESUMO_SMCI = {"reaction": {"summary": {
    "gap_pct_mean": 1.73, "gap_pct_abs_mean": 12.16,
    "close_pct_mean": 2.30, "close_pct_abs_mean": 15.09,
    "intraday_range_pct_mean": 10.29, "runup": {}}}}


@pytest.mark.parametrize("frase", [
    # O incidente, nas duas ordens.
    "Reação média absoluta de 15,09% no fechamento e um gap médio absoluto "
    "de 12,16% na abertura.",
    "Gap médio absoluto de 12,16% na abertura e reação média de 15,09% "
    "no fechamento.",
    # Rótulo ANTES do número, nas duas ordens.
    "O fechamento médio foi de 2,30% e o gap médio de 1,73%.",
    "O gap médio de 1,73% e o fechamento médio de 2,30%.",
    # Orações separadas por vírgula.
    "O fechamento médio é de 2,30%, com gap médio de 1,73%.",
    # Uma estatística só, correta.
    "O gap médio absoluto é de 12,16% na abertura.",
    "A volatilidade intraday média é de 10,29%.",
])
def test_duas_estatisticas_na_mesma_frase_nao_se_contaminam(frase):
    assert "ANALISE_ESTATISTICA_TROCADA" not in _codigos(
        validar_analise(_texto_completo(frase), _RESUMO_SMCI))


@pytest.mark.parametrize("frase", [
    "O gap médio na abertura é de 15,09%.",
    "O fechamento médio do dia foi de 12,16%.",
    "Reação média absoluta de 12,16% no fechamento e um gap médio de 1,73%.",
    "O fechamento médio é de 12,16%, com gap médio de 1,73%.",
])
def test_a_troca_de_verdade_continua_caindo(frase):
    """A correção não pode virar mordaça: trocar os dois números entre si é
    exatamente o que a checagem existe para pegar."""
    assert "ANALISE_ESTATISTICA_TROCADA" in _codigos(
        validar_analise(_texto_completo(frase), _RESUMO_SMCI))


def test_a_virgula_decimal_nao_parte_o_numero():
    """Separar oração por vírgula sem guarda parte "15,09" em "15" e "09%" —
    um número inventado dentro da checagem que existe para pegar número
    trocado."""
    from agent.analise_rapida_validator import _estatistica_trocada
    resumo = _RESUMO_SMCI["reaction"]["summary"]
    assert _estatistica_trocada(
        "reação média absoluta de 15,09% no fechamento", resumo) is None


def test_o_acento_e_o_que_permite_separar_a_oracao():
    """A separação divide em " e ". Sem acento, "é" vira "e" e "o gap médio É
    de 15,09%" seria partido no meio, deixando o número órfão do rótulo — e a
    troca de verdade passaria."""
    assert "ANALISE_ESTATISTICA_TROCADA" in _codigos(validar_analise(
        _texto_completo("O gap médio na abertura é de 15,09%."), _RESUMO_SMCI))


# ═══ SMCI, segunda rodada — dois "número certo, leitura errada" ════════════
#
# A caixa amarela saiu limpa (o falso positivo da rodada anterior estava
# corrigido) e o texto trazia dois erros de DERIVAÇÃO:
#
#   "48,57% acima da mínima ... 55,17% da máxima ... indicando que o ativo
#    negocia na METADE SUPERIOR da sua faixa anual"
#
#   "a ação CHEGOU AO EVENTO com um run-up de 32,46%"
#
# No primeiro, os dois números da própria frase já dizem o contrário: 48,57 é
# MENOR que 55,17, logo o papel está mais perto da mínima. No segundo, 32,46%
# é o run-up de AGORA (11 pregões depois); o de chegada foi +11,13%.

_SMCI = {
    "precoAtual": {"valor": 37.88},
    "snapshot": {"price": 37.88, "yearLow": 19.48, "yearHigh": 58.78,
                 "sma50": 30.71, "sma200": 31.37},
    "technicals": {"sma20": 34.22, "vwap": 37.89},
    "reaction": {"summary": {"runup": {
        "janela_contem_earnings": True, "pregoes_desde_earnings": 11,
        "runup_atual_ex_evento_pct": 32.46}}},
}


# ── posição na faixa de 52 semanas ─────────────────────────────────────────

def test_o_incidente_da_metade_superior():
    """US$ 37,88 numa faixa de 19,48 a 58,78 está a 46,8% dela."""
    achados = validar_analise(_texto_completo(
        "O ativo negocia na metade superior da sua faixa anual."), _SMCI)
    assert "ANALISE_POSICAO_NA_FAIXA" in _codigos(achados)
    msg = next(a["mensagem"] for a in achados
               if a["codigo"] == "ANALISE_POSICAO_NA_FAIXA")
    assert "46.8%" in msg, "a mensagem tem que dar a posição real"


@pytest.mark.parametrize("frase", [
    "O ativo negocia na metade superior da sua faixa anual.",
    "O papel está próximo da máxima dentro da faixa de 52 semanas.",
    "O ativo opera no topo da faixa.",
])
def test_metade_errada_da_faixa_cai(frase):
    assert "ANALISE_POSICAO_NA_FAIXA" in _codigos(
        validar_analise(_texto_completo(frase), _SMCI))


@pytest.mark.parametrize("frase", [
    # A leitura certa.
    "O ativo negocia na metade inferior da sua faixa anual.",
    "O papel está mais perto da mínima na faixa de 52 semanas.",
    # "metade superior" de outra coisa que não a faixa.
    "O ativo negocia na metade superior do setor.",
    # Só declara os extremos, não a posição.
    "A faixa de 52 semanas vai de US$ 19,48 a US$ 58,78.",
])
def test_a_leitura_certa_da_faixa_passa(frase):
    assert "ANALISE_POSICAO_NA_FAIXA" not in _codigos(
        validar_analise(_texto_completo(frase), _SMCI))


def test_sem_a_faixa_no_payload_a_checagem_se_cala():
    assert "ANALISE_POSICAO_NA_FAIXA" not in _codigos(validar_analise(
        _texto_completo("O ativo negocia na metade superior da sua faixa anual."),
        {"precoAtual": {"valor": 37.88}}))


# ── run-up de HOJE atribuído à chegada num balanço passado ─────────────────

@pytest.mark.parametrize("frase", [
    'A ação chegou ao evento com um "run-up" de 32,46%, considerada "esticada".',
    "O papel veio ao balanço com run-up de 32,46%.",
])
def test_run_up_atual_atribuido_a_chegada_cai(frase):
    assert "ANALISE_RUNUP_ATUAL_COMO_CHEGADA" in _codigos(
        validar_analise(_texto_completo(frase), _SMCI))


@pytest.mark.parametrize("frase", [
    # O número certo para o momento certo.
    "O run-up atual, ex-evento, é de 32,46%.",
    "O papel está esticado com run-up de 32,46% nos 11 pregões desde o "
    "último balanço.",
    # O run-up de CHEGADA de verdade, que está na tabela de eventos.
    "A ação chegou ao evento com um run-up de 11,13%.",
])
def test_o_run_up_no_momento_certo_passa(frase):
    assert "ANALISE_RUNUP_ATUAL_COMO_CHEGADA" not in _codigos(
        validar_analise(_texto_completo(frase), _SMCI))


def test_as_duas_checagens_de_run_up_nao_se_confundem():
    """A checagem 7 excusa o PASSADO ("chegou esticado" descrevendo histórico
    é a redação certa). Esta aqui aceita o tempo verbal e recusa o NÚMERO, que
    é de outro momento. As duas têm que conviver."""
    achados = _codigos(validar_analise(_texto_completo(
        "A ação chegou ao evento com um run-up de 32,46%."), _SMCI))
    assert "ANALISE_RUNUP_ATUAL_COMO_CHEGADA" in achados
    assert "ANALISE_BALANCO_NO_FUTURO" not in achados


# ═══ WOLF, 26/08/2026 — três apontamentos, dois deles meus ════════════════
#
# A tela publicou três ERROS. Conferindo um a um: 1 verdadeiro, 2 falsos.
#
#   FALSO  "um sinal de VENDA"  — o painel imprime "Sinal: venda", e o texto
#          está REPORTANDO o rótulo do sistema. Em pt-BR "venda" é também
#          substantivo; minha guarda excluía "venda DE" e aqui o "de" vem
#          ANTES ("sinal DE venda").
#   FALSO  "o primeiro suporte ESTATÍSTICO é S1" — o SYSTEM proíbe chamar as
#          bandas de suporte DO GRÁFICO, e o adjetivo faz essa distinção.
#   REAL   "chegou ao balanço com ganho de 31,21%" — é o run-up de agora; o
#          de chegada em 2026-08-19 foi +7,08%.
#
# E um QUARTO erro passou batido: a ordem das resistências, escrita em dólar.

_WOLF = {"precoAtual": {"valor": 26.50},
         "snapshot": {"price": 26.50, "sma50": 34.17, "sma200": 28.48,
                      "yearLow": 8.05, "yearHigh": 80.82},
         "technicals": {"price": 26.57, "sma20": 28.16, "vwap": 26.46},
         "reaction": {"summary": {"runup": {
             "janela_contem_earnings": True, "pregoes_desde_earnings": 5,
             "runup_atual_ex_evento_pct": 31.21}}}}


# ── "venda" substantivo não é imperativo ──────────────────────────────────

@pytest.mark.parametrize("frase", [
    "O ticker mostra um score de -65 e um sinal de venda, impulsionado por "
    "análises técnicas de baixa.",
    "A pressão de venda aumentou no pregão.",
    "O sistema emitiu ordem de compra automática.",
    "O volume de venda superou o de compra.",
])
def test_venda_como_substantivo_nao_e_recomendacao(frase):
    """O painel imprime "Sinal: venda" e o texto que reporta isso está
    OBEDECENDO. O imperativo tem que abrir a oração."""
    assert "ANALISE_RECOMENDACAO" not in _codigos(
        validar_analise(_texto_completo(frase), _WOLF))


@pytest.mark.parametrize("frase", [
    "Compre WOLF agora.",
    "Venda antes do balanço.",
    "Dado o quadro, compre o papel.",
])
def test_o_imperativo_de_verdade_continua_caindo(frase):
    assert "ANALISE_RECOMENDACAO" in _codigos(
        validar_analise(_texto_completo(frase), _WOLF))


# ── "suporte estatístico" é a distinção pedida ────────────────────────────

@pytest.mark.parametrize("frase", [
    "O primeiro suporte estatístico é S1 em US$ 22,90.",
    "Os suportes estatísticos S1 e S2 podem ser testados.",
    "A resistência de reação é R1.",
])
def test_nivel_qualificado_como_estatistico_passa(frase):
    """O SYSTEM proíbe chamar as bandas de "suporte e resistência DO
    GRÁFICO". Qualificar como estatístico é exatamente a distinção pedida."""
    assert "ANALISE_BANDA_COMO_NIVEL_TECNICO" not in _codigos(
        validar_analise(_texto_completo(frase), _WOLF))


def test_sem_o_qualificador_continua_erro():
    assert "ANALISE_BANDA_COMO_NIVEL_TECNICO" in _codigos(
        validar_analise(_texto_completo("O primeiro suporte é S1 em US$ 22,90."),
                        _WOLF))


# ── ordem dos níveis escrita em DÓLAR ─────────────────────────────────────

def test_ordem_invertida_em_dolar_agora_cai():
    """"US$ 28,48 (MM200) atua como resistência IMEDIATA, seguido pela MM20 em
    US$ 28,16" — a MM20 está mais perto. A checagem só olhava percentual com
    sinal e não alcançava valores em dólar."""
    achados = validar_analise(_texto_completo(
        "O nível de US$ 28,48, onde está a MM200, atua como uma resistência "
        "imediata, seguido pela MM20 em US$ 28,16."), _WOLF)
    assert "ANALISE_ORDEM_DOS_NIVEIS" in _codigos(achados)
    msg = next(a["mensagem"] for a in achados
               if a["codigo"] == "ANALISE_ORDEM_DOS_NIVEIS")
    assert "US$ 1.98" in msg and "US$ 1.66" in msg, "a unidade tem que aparecer"


@pytest.mark.parametrize("frase", [
    # Ordem certa em dólar.
    "O nível de US$ 28,16 (MM20) é a resistência imediata, seguido pela "
    "MM200 em US$ 28,48.",
    "O primeiro suporte é S1 em US$ 22,90, seguido por S2 em US$ 18,76.",
    # "e" não ordena.
    "A resistência em US$ 28,16 e a de US$ 28,48 estão próximas.",
    # Alvo de analista não se ordena por distância — e a frase não fala de
    # nível, então os valores em dólar nem entram na conta.
    "O alvo de US$ 40 foi cortado, seguido pelo de US$ 35.",
    # Lados opostos.
    "O suporte da MM200 em US$ 28,48 fica acima do S1 em US$ 22,90.",
])
def test_ordem_em_dolar_nao_vira_coringa(frase):
    assert "ANALISE_ORDEM_DOS_NIVEIS" not in _codigos(
        validar_analise(_texto_completo(frase), _WOLF))


def test_percentual_e_dolar_nao_se_misturam():
    """Comparar "+10,33%" com "US$ 1,66" seria comparar grafias diferentes —
    a checagem só confronta duas distâncias da MESMA unidade."""
    from agent.analise_rapida_validator import _ordem_invertida
    assert _ordem_invertida(
        "a mm20 (+10,33%), seguida pela mm200 em us$ 28,48", 26.50) is None


# ── a base da distância à máxima/mínima anual ──────────────────────────────
#
# Incidente real (SNDK, 26/08/2026). A caixa amarela apontou UMA coisa --
# seção faltando -- e deixou passar duas contas erradas na mesma frase:
#
#   "está 58,51% abaixo da máxima de 52 semanas (US$ 2354,39) e
#    96,81% acima da mínima (US$ 47,40)"
#
# Preço US$ 1485,30. O certo é -36,91% e +3033,54%. Os dois números vieram de
# dividir pelo PREÇO em vez de pela referência.
#
# O que faz isto merecer checagem própria em vez de uma linha em
# `_REFERENCIAS`: `_bate_a_magnitude` aceita as duas bases DE PROPÓSITO,
# porque preço e média móvel andam perto e a convenção não muda o número o
# bastante. Com a mínima anual, US$ 1485,30 é 31 vezes US$ 47,40.
#
# E dividir pelo preço produz um número que nunca passa de 100%. "96,81%
# acima da mínima" se lê como "quase no teto da faixa": plausível,
# arredondado, e a 3000% da verdade.

_SNDK = {
    "precoAtual": {"valor": 1485.30},
    "snapshot": {"price": 1485.30, "yearLow": 47.40, "yearHigh": 2354.39,
                 "sma50": 1636.42, "sma200": 952.03},
    "technicals": {"sma20": 1434.71, "vwap": 1475.89},
}


def test_o_incidente_da_distancia_ao_teto_e_ao_fundo():
    achados = validar_analise(_texto_completo(
        "O SNDK está 58,51% abaixo da máxima de 52 semanas (US$ 2354,39) e "
        "96,81% acima da mínima (US$ 47,40)."), _SNDK)
    msgs = [a["mensagem"] for a in achados
            if a["codigo"] == "ANALISE_DISTANCIA_DA_FAIXA"]
    assert len(msgs) == 2, "as DUAS contas estão erradas, não só a primeira"
    assert any("-36.91%" in m and "máxima" in m for m in msgs)
    assert any("+3033.54%" in m and "mínima" in m for m in msgs)


@pytest.mark.parametrize("frase", [
    # A conta certa, nas duas direções.
    "O papel está 36,91% abaixo da máxima de 52 semanas (US$ 2354,39).",
    "O papel está 3033,54% acima da mínima de 52 semanas (US$ 47,40).",
    # Máxima do DIA é outro dado -- apontar contra o anual seria acusar o
    # texto de dizer o que ele não disse.
    "O preço ficou 58,51% abaixo da máxima do dia.",
    "A máxima intraday está 96,81% acima da mínima da sessão.",
    # Número que não bate com NENHUMA das duas bases é outra conversa: o
    # silêncio aqui é o mesmo da checagem 8.
    "O papel está 12,00% abaixo da máxima de 52 semanas.",
    # Frase neutra com base no preço: "de distância" não afirma direção
    # contra a referência, e é a redação correta para "quanto teria de cair".
    "A MM200, em US$ 952,03, está a 35,90% de distância.",
])
def test_distancia_da_faixa_bem_escrita_passa(frase):
    assert "ANALISE_DISTANCIA_DA_FAIXA" not in _codigos(
        validar_analise(_texto_completo(frase), _SNDK))


def test_sem_faixa_no_payload_nao_inventa_achado():
    sem = {"precoAtual": {"valor": 1485.30}, "snapshot": {"price": 1485.30}}
    assert "ANALISE_DISTANCIA_DA_FAIXA" not in _codigos(validar_analise(
        _texto_completo("Está 58,51% abaixo da máxima de 52 semanas."), sem))


# ── MRVL, 27/08/2026 (2ª rodada): "distante" e "sua" escaparam do padrão ────
#
# A 1ª rodada do MESMO ticker, mesmo número trocado (36,62%), tinha caído com
# "abaixo da máxima". A 2ª rodada disse a mesma coisa errada com "distante de
# sua máxima" e passou batido -- a checagem tinha que dizer o MESMO erro
# duas vezes com fraseados diferentes, e só dizia com um deles.

_MRVL = {
    "precoAtual": {"valor": 241.45},
    "snapshot": {"price": 241.45, "yearLow": 61.44, "yearHigh": 329.88,
                 "sma50": 229.99, "sma200": 147.97},
}


def test_distante_de_sua_tambem_cai():
    achados = validar_analise(_texto_completo(
        "O preço atual de US$ 241.45 posiciona o MRVL 36.62% distante de "
        "sua máxima de 52 semanas, que é de US$ 329.88."), _MRVL)
    msg = next(a["mensagem"] for a in achados
               if a["codigo"] == "ANALISE_DISTANCIA_DA_FAIXA")
    assert "-26.81%" in msg


@pytest.mark.parametrize("frase", [
    "O preço está 26,81% distante da máxima de 52 semanas (US$ 329,88).",
    "O preço está 26,81% distante de sua máxima de 52 semanas.",
    "O preço está 26,81% distante da própria máxima de 52 semanas.",
])
def test_distante_com_a_conta_certa_nao_cai(frase):
    assert "ANALISE_DISTANCIA_DA_FAIXA" not in _codigos(
        validar_analise(_texto_completo(frase), _MRVL))


# ── o texto NEGA dado que recebeu ──────────────────────────────────────────
#
# Incidente real (AMD, 26/08/2026), publicado com a caixa vazia. A linha de
# fontes da tela dizia que as TRÊS camadas chegaram:
#
#   "camada fundamental: alvos de analistas (yfinance), valuation/DCF (FMP),
#    notícias do feed"
#
# e a seção Fundamento e valuation dizia:
#
#   "Informações fundamentais e de valuation, como alvos de analistas e
#    métricas de avaliação, não estavam disponíveis para AMD nesta análise."
#
# É o inverso exato do caso SNDK do mesmo dia: lá o dado faltava e a tela não
# dizia por quê; aqui o dado veio e o texto o nega. Os dois saem da mesma
# lacuna — ninguém conferia as afirmações do texto sobre a DISPONIBILIDADE do
# dado, só sobre o valor dele.
#
# E negar dado presente é pior que omitir: quem lê "não estava disponível"
# para de procurar. A informação estava a uma seção de distância.

# Nomes REAIS das chaves. A fixture usava `pe`/`dcf`, que o payload nunca
# produz -- `_buscar_fundamento` copia as chaves de `get_fundamentals_valuation`
# tal como saem de `_MULTIPLOS_DA_SEC`. Com nome inventado, uma checagem por
# CAMPO passa a olhar para chave que não existe e concorda com qualquer coisa.
_COM_FUNDAMENTO = {
    "precoAtual": {"valor": 482.76},
    "snapshot": {"price": 482.76},
    "_fundamento": {"alvosAnalistas": {"alvoMedio": 600.0, "consenso": "Buy"},
                    "valuation": {"pe_ratio_ttm": 90.1, "dcf_fair_value": 300.0}},
}

# O caso MRVL de 30/08/2026: metade dos múltiplos veio, o DCF e os dois que
# dependem de EBITDA não. `_buscar_fundamento` filtra `v is not None`, então
# métrica ausente não vira chave -- é por isso que a checagem é por presença.
_VALUATION_PARCIAL = {
    "precoAtual": {"valor": 216.62},
    "snapshot": {"price": 216.62},
    "_fundamento": {
        "valuation": {
            "current_price": 216.62,
            "pe_ratio_ttm": 71.96,
            "pb_ratio": 10.25,
            "revenue_growth_pct_ttm": 30.62,
            "net_margin_pct_ttm": 27.93,
            "dcf_indisponivel": "o plano da conta não cobre o endpoint",
            "multiplos_indisponiveis": {
                "ev_to_ebitda_ttm": "sem D&A trimestral para compor EBITDA",
                "net_debt_to_ebitda_ttm": "sem D&A trimestral para compor EBITDA",
            },
        },
    },
}
_SEM_FUNDAMENTO = {"precoAtual": {"valor": 482.76}, "snapshot": {"price": 482.76},
                   "_fundamento": {}}

_FRASE_AMD = ("Informações fundamentais e de valuation, como alvos de analistas "
              "e métricas de avaliação, não estavam disponíveis para AMD nesta "
              "análise.")


def test_o_incidente_do_amd():
    achados = validar_analise(_texto_completo(_FRASE_AMD), _COM_FUNDAMENTO)
    assert "ANALISE_NEGA_DADO_PRESENTE" in _codigos(achados)
    msg = next(a["mensagem"] for a in achados
               if a["codigo"] == "ANALISE_NEGA_DADO_PRESENTE")
    assert "alvos de analistas" in msg and "valuation/DCF" in msg, \
        "a mensagem tem que dizer O QUE estava na mão"


# ── o caso MRVL: campo ausente dentro de bloco presente ───────────────────
#
# 30/08/2026, na tela. O texto disse, corretamente:
#
#   "O DCF (fluxo de caixa descontado) não estava disponível; e múltiplos como
#    EV/EBITDA e Dívida Líquida/EBITDA não puderam ser calculados devido à
#    indisponibilidade de dados trimestrais para Depreciação e Amortização."
#
# ...e levou ERRO. Os três campos faltaram DE VERDADE, e o payload ainda
# trazia `multiplos_indisponiveis` com o motivo de cada um -- o modelo estava
# repetindo o que o próprio payload lhe deu.
#
# A regra perguntava pelo BLOCO ("`valuation` veio?" -- veio, por causa do P/L
# e do P/VP) enquanto a frase negava CAMPOS. É a mesma lição do recorte de
# `_blocosOmitidos`, um nível mais fundo: presença do bloco não é presença da
# métrica, porque as duas metades têm fontes independentes (SEC e FMP) e é
# rotina uma vir sem a outra.

_FRASE_MRVL = ("O DCF (fluxo de caixa descontado) não estava disponível; e "
               "múltiplos como EV/EBITDA e Dívida Líquida/EBITDA não puderam "
               "ser calculados devido à indisponibilidade de dados trimestrais "
               "para Depreciação e Amortização.")


def test_o_caso_mrvl_nao_e_erro():
    """Nomeia três métricas, e as três faltaram. Não há dado presente sendo
    negado -- há o motivo da ausência sendo transmitido, que é o que o
    payload pede que o texto faça."""
    assert "ANALISE_NEGA_DADO_PRESENTE" not in _codigos(
        validar_analise(_texto_completo(_FRASE_MRVL), _VALUATION_PARCIAL))


def test_negar_metrica_que_veio_continua_erro():
    """O outro lado, e o que impede a correção de virar buraco: no MESMO
    payload, negar o P/L (que veio, 71,96x) é exatamente o defeito de
    origem."""
    frase = "O P/L não estava disponível para este papel nesta rodada."
    achados = validar_analise(_texto_completo(frase), _VALUATION_PARCIAL)
    assert "ANALISE_NEGA_DADO_PRESENTE" in _codigos(achados)


def test_negar_metrica_ausente_e_presente_na_mesma_frase_e_erro():
    """Uma verdadeira não compra a falsa. A frase nomeia o DCF (ausente) e o
    P/VP (presente); o P/VP basta para condenar.

    A forma "Nem o DCF nem o P/VP estavam disponíveis" NÃO é pega, e não por
    causa deste recorte: `_NEGA_DISPONIBILIDADE` exige "não" adjacente ao
    verbo, e "nem ... nem ..." não casa. É falso negativo pré-existente, de
    outra natureza (vocabulário de negação, não presença de campo). Fica
    anotado aqui em vez de virar um alargamento do gatilho de carona --
    o comentário da própria regra avisa que ampliar o gatilho sem estreitar
    o alvo troca um falso negativo por um falso positivo."""
    frase = ("O DCF e o P/VP não estavam disponíveis para compor a "
             "avaliação deste papel.")
    assert "ANALISE_NEGA_DADO_PRESENTE" in _codigos(
        validar_analise(_texto_completo(frase), _VALUATION_PARCIAL))


def test_negar_o_bloco_inteiro_continua_erro_mesmo_com_metrica_faltando():
    """Sem nomear métrica, quem decide é a regra de bloco -- e o bloco veio.
    "o valuation não veio" é falso mesmo com três métricas ausentes."""
    frase = "Dados de valuation não estavam disponíveis para este papel."
    assert "ANALISE_NEGA_DADO_PRESENTE" in _codigos(
        validar_analise(_texto_completo(frase), _VALUATION_PARCIAL))


def test_a_palavra_avaliacao_sozinha_nao_condena_frase_verdadeira():
    """`_FUNDAMENTO_GENERICO` casa com "avaliação", e "múltiplos de avaliação"
    é como se escreve valuation em português. Antes do recorte, essa palavra
    disparava a regra mesmo quando a frase nomeava só métrica ausente."""
    # "não ESTAVAM DISPONÍVEIS", e não "não puderam ser calculados": a
    # segunda forma não casa com `_NEGA_DISPONIBILIDADE` (nem antes nem
    # depois desta correção), então o teste passaria sem exercitar nada.
    frase = ("Os múltiplos de avaliação EV/EBITDA e Dívida Líquida/EBITDA "
             "não estavam disponíveis.")
    assert "ANALISE_NEGA_DADO_PRESENTE" not in _codigos(
        validar_analise(_texto_completo(frase), _VALUATION_PARCIAL))


def test_a_mesma_frase_passa_quando_e_verdade():
    """O prompt MANDA dizer isso quando a camada não vem ("sem valuation nem
    alvos, diga em uma linha que a fundamental não estava disponível e siga").
    A frase é legítima — esta checagem é o que separa os dois casos."""
    assert "ANALISE_NEGA_DADO_PRESENTE" not in _codigos(
        validar_analise(_texto_completo(_FRASE_AMD), _SEM_FUNDAMENTO))


@pytest.mark.parametrize("frase", [
    "Não foi possível obter os alvos de analistas para este papel.",
    "Métricas de valuation indisponíveis nesta rodada.",
    "Não vieram dados de valuation da fonte.",
    "Sem dados de fundamentos, a leitura fica só técnica.",
])
def test_outras_formas_de_negar_a_camada_caem(frase):
    assert "ANALISE_NEGA_DADO_PRESENTE" in _codigos(
        validar_analise(_texto_completo(frase), _COM_FUNDAMENTO))


@pytest.mark.parametrize("frase", [
    # Negação sobre OUTRO dado: o sujeito não é a camada fundamental.
    "O RSI não estava disponível no painel técnico.",
    "Não há dados de volume intradiário para esta sessão.",
    # Afirmação com a palavra "disponíveis" dentro: sem negação não é o caso.
    "Os dados de valuation disponíveis mostram P/L de 90,1.",
    "O consenso de analistas está disponível e aponta alvo de US$ 600,00.",
    # Fala do valuation sem afirmar nada sobre disponibilidade.
    "O valuation está esticado frente à média do setor.",
])
def test_frase_que_nao_nega_a_camada_passa(frase):
    assert "ANALISE_NEGA_DADO_PRESENTE" not in _codigos(
        validar_analise(_texto_completo(frase), _COM_FUNDAMENTO))


def test_uma_camada_presente_ja_basta():
    """"Não estavam disponíveis" é falso se QUALQUER bloco veio -- o leitor
    para de procurar os dois."""
    so_alvos = {**_COM_FUNDAMENTO,
                "_fundamento": {"alvosAnalistas": {"alvoMedio": 600.0}}}
    assert "ANALISE_NEGA_DADO_PRESENTE" in _codigos(
        validar_analise(_texto_completo(_FRASE_AMD), so_alvos))


# ── MRVL, 27/08/2026: o verbo de entrega que faltava no vocabulário ────────
#
# A prosa negou a camada fundamental com `alvosAnalistas` NO PAYLOAD, em duas
# rodadas seguidas do mesmo ticker, e a checagem não pegou nenhuma das duas:
# "veio/chegou/retornou" estavam no vocabulário, "trouxe/forneceu/apresentou"
# não. Uma auditoria externa leu o sintoma ao contrário -- achou que o dado
# estava faltando sem ser reportado -- mas apontou para o lugar certo.

_SO_ALVOS = {"precoAtual": {"valor": 241.45}, "snapshot": {"price": 241.45},
             "_fundamento": {"alvosAnalistas": {"alvoMedio": 300.0,
                                                "consenso": "Buy"}}}


@pytest.mark.parametrize("frase", [
    # a 2a rodada do MRVL, verbatim: auxiliar + particípio
    "Dados explícitos de valuation, como alvos de analistas e avaliação por "
    "fluxo de caixa descontado (DCF), não foram fornecidos neste relatório.",
    # a 1a rodada do MRVL, verbatim: verbo direto
    "O JSON não trouxe camada fundamental para este ticker — sem alvos de "
    "analistas, múltiplos ou DCF disponíveis.",
    # as outras formas do mesmo verbo de entrega
    "O consenso de analistas não foi apresentado nesta rodada.",
    "Os alvos de analistas não foram disponibilizados.",
])
def test_verbo_de_entrega_tambem_nega_a_camada(frase):
    assert "ANALISE_NEGA_DADO_PRESENTE" in _codigos(
        validar_analise(_texto_completo(frase), _SO_ALVOS))


@pytest.mark.parametrize("frase", [
    # O bloco NEGADO é o que de fato faltou (valuation), e o que veio (alvos)
    # nem é citado. Antes esta frase CORRETA viraria ERRO, porque a checagem
    # só perguntava "fala de fundamento?" -- ampliar o gatilho sem estreitar
    # o alvo trocaria um falso negativo por um falso positivo.
    "A FMP não forneceu o DCF, então sigo só com técnica.",
    "O valuation não foi fornecido nesta rodada.",
    "Os múltiplos não vieram da fonte.",
])
def test_negar_so_o_bloco_ausente_continua_correto(frase):
    assert "ANALISE_NEGA_DADO_PRESENTE" not in _codigos(
        validar_analise(_texto_completo(frase), _SO_ALVOS))


def test_a_mensagem_nomeia_o_bloco_que_a_frase_negou():
    """Com só um bloco citado, a mensagem aponta ELE -- não a lista inteira
    do payload, que mandaria o leitor procurar no lugar errado."""
    msg = next(a["mensagem"] for a in validar_analise(_texto_completo(
        "Os alvos de analistas não foram fornecidos."), _SO_ALVOS)
        if a["codigo"] == "ANALISE_NEGA_DADO_PRESENTE")
    assert "alvos de analistas" in msg


def test_blocos_espelham_os_coletores():
    """As chaves aqui e em `analise_rapida_ia.COLETORES` descrevem os MESMOS
    três blocos. Divergir faria a checagem ignorar em silêncio um bloco que a
    coleta passou a trazer."""
    from agent.analise_rapida_ia import COLETORES
    from agent.analise_rapida_validator import _BLOCOS_FUNDAMENTAIS
    assert {c for c, _ in _BLOCOS_FUNDAMENTAIS} == set(COLETORES)


# ═══ auditorias de ARM e NVDA (26/08/2026) ══════════════════════════════════

_TECNICA_ARM = {
    "precoAtual": {"valor": 251.06},
    "technicals": {"sma20": 260.55, "sma50": 294.28,
                   "sma200": 198.40, "vwap": 245.54},
}


# ── distância à média com a base trocada ────────────────────────────────────
#
# Incidente real (ARM), publicado sem apontamento:
#
#     "O preço atual de US$ 251,06 está 3,78% abaixo da MM20 (US$ 260,55)
#      [...] e 17,22% abaixo da MM50 (US$ 294,28)"
#
# O certo é -3,64% e -14,69%. Os dois vieram de dividir pelo PREÇO. E o painel
# Técnica, na mesma tela, imprime "-14,69% de distância" para a MM50 --
# a prosa não escolheu outra convenção, contradisse o painel ao lado.

def test_distancia_da_media_sobre_o_preco_e_erro():
    texto = _texto_completo(
        "O preço atual de US$ 251,06 está 3,78% abaixo da MM20 "
        "(US$ 260,55) e 17,22% abaixo da MM50 (US$ 294,28).")
    achados = validar_analise(texto, _TECNICA_ARM)
    assert "ANALISE_DISTANCIA_DA_MEDIA" in _codigos(achados)


def test_as_duas_medias_da_mesma_frase_sao_reportadas():
    """A forma típica é citar MM20 e MM50 juntas com a mesma base trocada.
    Reportar só uma deixaria a outra parecendo conferida."""
    texto = _texto_completo(
        "O preço atual de US$ 251,06 está 3,78% abaixo da MM20 "
        "(US$ 260,55) e 17,22% abaixo da MM50 (US$ 294,28).")
    msgs = [a["mensagem"] for a in validar_analise(texto, _TECNICA_ARM)
            if a["codigo"] == "ANALISE_DISTANCIA_DA_MEDIA"]
    assert len(msgs) == 2
    assert any("MM20" in m for m in msgs) and any("MM50" in m for m in msgs)


def test_a_mensagem_traz_o_numero_certo():
    texto = _texto_completo(
        "O preço de US$ 251,06 está 17,22% abaixo da MM50 (US$ 294,28).")
    msg = next(a["mensagem"] for a in validar_analise(texto, _TECNICA_ARM)
               if a["codigo"] == "ANALISE_DISTANCIA_DA_MEDIA")
    assert "-14.69%" in msg


def test_distancia_da_media_correta_passa():
    texto = _texto_completo(
        "O preço está 3,64% abaixo da MM20 e 14,69% abaixo da MM50.")
    achados = validar_analise(texto, _TECNICA_ARM)
    assert "ANALISE_DISTANCIA_DA_MEDIA" not in _codigos(achados)


def test_vwap_com_a_base_certa_passa():
    """Na mesma análise de ARM esta frase estava correta: 251,06/245,54-1 =
    +2,25%. Apontar contra ela seria o falso positivo mais caro possível --
    o trecho certo dentro do parágrafo errado."""
    texto = _texto_completo(
        "o preço se encontra 2,25% acima da VWAP de US$ 245,54")
    achados = validar_analise(texto, _TECNICA_ARM)
    assert "ANALISE_DISTANCIA_DA_MEDIA" not in _codigos(achados)


def test_media_como_sujeito_nao_e_apontada():
    """"A MM20 está X% abaixo da MM50" mede outra coisa: quem é o sujeito
    muda qual conta é a certa."""
    texto = _texto_completo("A MM20 está 11,46% abaixo da MM50.")
    achados = validar_analise(texto, _TECNICA_ARM)
    assert "ANALISE_DISTANCIA_DA_MEDIA" not in _codigos(achados)


def test_sujeito_indeterminado_fica_em_silencio():
    texto = _texto_completo("Ficou 17,22% abaixo da MM50 nesta semana.")
    achados = validar_analise(texto, _TECNICA_ARM)
    assert "ANALISE_DISTANCIA_DA_MEDIA" not in _codigos(achados)


def test_numero_que_nao_bate_com_nenhuma_das_duas_contas_passa():
    """Mesmo critério da checagem 9b: só aponta quando o citado bate com a
    conta ERRADA. Número de outra origem é outra conversa."""
    texto = _texto_completo("O preço está 40,00% abaixo da MM50.")
    achados = validar_analise(texto, _TECNICA_ARM)
    assert "ANALISE_DISTANCIA_DA_MEDIA" not in _codigos(achados)


def test_contas_indistinguiveis_ficam_em_silencio():
    """Quando as duas bases caem dentro da folga uma da outra, não dá para
    atribuir o número a nenhuma. Apontar afirmaria saber de onde veio um
    número compatível com as duas origens."""
    dados = {"precoAtual": {"valor": 100.0}, "technicals": {"sma50": 100.03}}
    texto = _texto_completo("O preço está 0,03% abaixo da MM50.")
    achados = validar_analise(texto, dados)
    assert "ANALISE_DISTANCIA_DA_MEDIA" not in _codigos(achados)


# ── run-up de AGORA descrito como run-up de CHEGADA ─────────────────────────
#
# Terceira e quarta ocorrência do mesmo erro, em telas diferentes no mesmo dia:
#
#   ARM : "havia se valorizado 11,64% nos 21 pregões que o antecederam"
#         (chegou ao balanço de 29/07 caindo -26,78% -- sinal trocado)
#   NVDA: "Antes deste evento, o papel apresentava um run-up de 8,14% nos 21
#          pregões anteriores" (a tabela diz +8,42% para o evento de 26/08)
#
# `_CHEGADA_AO_EVENTO` cobria só "chegou ao balanço", "veio ao evento" e
# "antes do balanço com". Duas construções novas no mesmo dia dizem que a
# lista precisa cobrir a IDEIA, não as frases já vistas.

_EARNINGS_COM_RUNUP = {
    "reaction": {"summary": {
        # `janela_contem_earnings` é o portão do bloco inteiro: só faz
        # sentido confundir run-up de agora com run-up de chegada quando o
        # balanço caiu DENTRO da janela de 21 pregões. É o aviso que a tela
        # mostra ("balanço há N pregão(ões), dentro da janela").
        "runup": {"janela_contem_earnings": True,
                  "runup_atual_ex_evento_pct": 11.64,
                  "pregoes_desde_earnings": 20},
    }},
}


def _com_runup(frase: str):
    return _codigos(validar_analise(_texto_completo(frase),
                                    _EARNINGS_COM_RUNUP))


@pytest.mark.parametrize("frase", [
    # a de ARM, verbatim
    "Para o último evento de balanço, o papel havia se valorizado 11,64% "
    "nos 21 pregões que o antecederam, sendo considerado esticado.",
    # a de NVDA, com o número deste payload
    "Antes deste evento, o papel apresentava um run-up de 11,64% nos 21 "
    "pregões anteriores.",
    # as três que já eram cobertas, para não regredir
    "A ação chegou ao evento com um run-up de 11,64%.",
    "O papel veio ao balanço com 11,64% de alta.",
    "Antes do balanço com 11,64% acumulados, a tese já estava esticada.",
])
def test_runup_de_agora_como_chegada_e_erro(frase):
    assert "ANALISE_RUNUP_ATUAL_COMO_CHEGADA" in _com_runup(frase)


@pytest.mark.parametrize("frase", [
    # "nos 21 pregões anteriores" SEM âncora no evento descreve o run-up
    # atual, que é medido exatamente assim -- anteriores a HOJE. Apontar aqui
    # seria acusar a redação correta.
    "O run-up atual é de 11,64% nos 21 pregões anteriores.",
    "Nos 21 pregões anteriores o papel acumula 11,64%.",
    # circunstância, não atribuição
    "O próximo balanço sai em novembro.",
    "A reação ao balanço foi de -8,11% no dia do anúncio.",
])
def test_redacao_correta_do_runup_continua_passando(frase):
    assert "ANALISE_RUNUP_ATUAL_COMO_CHEGADA" not in _com_runup(frase)


def test_sem_citar_o_numero_do_campo_a_checagem_se_cala():
    """A guarda é o percentual: a frase tem que citar EXATAMENTE o valor de
    `runup_atual_ex_evento_pct`. Sem isso, ela fala de outra coisa."""
    assert "ANALISE_RUNUP_ATUAL_COMO_CHEGADA" not in _com_runup(
        "A ação chegou ao evento com um run-up de 3,20%.")


# ── beta lido como razão de volatilidade ────────────────────────────────────
#
# Duas telas, o mesmo erro conceitual (26/08/2026). Beta é inclinação de
# regressão -- cov(ticker, bench)/var(bench), em get_scenario_params.py. Em
# termos de volatilidade, beta = rho * (sigma_a/sigma_m): só quando a
# correlação é 1 ele iguala a razão de volatilidades. A NVDA é o próprio
# contraexemplo -- 37% de vol anual com beta 0,64.

@pytest.mark.parametrize("frase", [
    # ARM, verbatim
    "O beta da ARM em relação ao benchmark SMH é de 1,2861, indicando que o "
    "papel tem uma volatilidade 28,61% maior que a do setor.",
    # NVDA, verbatim
    "O beta setorial de 0,6407 indica que NVDA tende a ser menos volátil que "
    "o benchmark SMH.",
    "Beta 1,29, ou seja, volatilidade 29% acima da do setor.",
    "O beta de 1,29 significa que o papel oscila mais que o benchmark em "
    "volatilidade.",
])
def test_derivar_volatilidade_do_beta_e_erro(frase):
    assert "ANALISE_BETA_COMO_VOLATILIDADE" in _codigos(
        validar_analise(_texto_completo(frase), {}))


@pytest.mark.parametrize("frase", [
    # os dois números lado a lado, sem concluir um do outro: é a redação certa
    "O beta é 0,64 e a volatilidade anual é de 37%.",
    # explicar o conceito não é cometê-lo
    "Beta mede sensibilidade ao benchmark, não volatilidade.",
    # a leitura CORRETA do beta
    "O beta de 0,64 indica menor sensibilidade aos movimentos do SMH.",
    "A volatilidade anual é de 37% ao ano.",
])
def test_mencionar_beta_e_volatilidade_sem_derivar_passa(frase):
    assert "ANALISE_BETA_COMO_VOLATILIDADE" not in _codigos(
        validar_analise(_texto_completo(frase), {}))


# ── significância afirmada sem o campo que a banca ──────────────────────────
#
# NVDA: "Nota-se uma correlação forte e ESTATISTICAMENTE SIGNIFICATIVA de
# 0,92". O payload desta tela traz `corr_runup_reacao` e mais nada --
# `aplicar_holm` não roda aqui, porque a correção de múltiplos só existe ENTRE
# tickers e a Análise Rápida olha um papel só.
#
# `LEITURA_CORRELACAO_SEM_SUPORTE` já existia para isto, e só no
# `reacao_earnings_validator`. Quarta vez no mesmo dia que uma defesa mora num
# validador só.

_CORR_SEM_SIGNIFICANCIA = {
    "reaction": {"summary": {"runup": {"corr_runup_reacao": 0.92,
                                       "corr_n": 6}}},
}


def test_afirmar_significancia_sem_o_campo_e_erro():
    achados = validar_analise(_texto_completo(
        "Nota-se uma correlação forte e estatisticamente significativa de "
        "0,92 entre o run-up pré-balanço e a reação subsequente do preço."),
        _CORR_SEM_SIGNIFICANCIA)
    assert "ANALISE_SIGNIFICANCIA_SEM_CAMPO" in _codigos(achados)


def test_a_mensagem_diz_o_n():
    msg = next(a["mensagem"] for a in validar_analise(_texto_completo(
        "A correlação é estatisticamente significativa em 0,92."),
        _CORR_SEM_SIGNIFICANCIA)
        if a["codigo"] == "ANALISE_SIGNIFICANCIA_SEM_CAMPO")
    assert "n = 6" in msg


@pytest.mark.parametrize("frase", [
    # descrever o coeficiente é o que o SYSTEM pede
    "Nota-se uma correlação de 0,92 entre o run-up e a reação — amostra "
    "pequena, indício, não prova.",
    # negar a significância é obediência, não erro
    "A correlação de 0,92 não é estatisticamente significativa com n=6.",
])
def test_descrever_a_correlacao_sem_afirmar_significancia_passa(frase):
    assert "ANALISE_SIGNIFICANCIA_SEM_CAMPO" not in _codigos(
        validar_analise(_texto_completo(frase), _CORR_SEM_SIGNIFICANCIA))


def test_com_corr_sobrevive_a_afirmacao_e_legitima():
    """Se o payload BANCA a significância, dizer isso é correto."""
    dados = {"reaction": {"summary": {"runup": {
        "corr_runup_reacao": 0.92, "corr_n": 6,
        "corr_sobrevive": True, "corr_p_corrigido": 0.01}}}}
    assert "ANALISE_SIGNIFICANCIA_SEM_CAMPO" not in _codigos(
        validar_analise(_texto_completo(
            "A correlação de 0,92 é estatisticamente significativa."), dados))


def test_sem_correlacao_no_payload_a_checagem_se_cala():
    assert "ANALISE_SIGNIFICANCIA_SEM_CAMPO" not in _codigos(
        validar_analise(_texto_completo(
            "A correlação é estatisticamente significativa."), {}))


# ── contagem do balde "chegou esticado" ─────────────────────────────────────
#
# ARM: "Historicamente, em 3 dos 7 eventos onde o papel chegou esticado, ele
# reagiu com uma média de 1,37% de alta."
#
# O dado diz: 3 eventos esticados, dos quais 2 CAÍRAM, média +1,37%. O modelo
# trocou o par e, de quebra, transformou "2 de 3 caíram" em "reagiu com média
# de alta" — some justamente a informação de que a maioria caiu.
#
# A auditoria propôs mandar agregados pré-calculados. Eles JÁ VÃO:
# `esticado_n`, `esticado_caiu_n` e `esticado_reacao_media` estão no
# `summary.runup` que o payload carrega inteiro. O modelo tinha os três na mão
# e contou assim mesmo — o que falta não é o dado, é a conferência.

_BALDE_ESTICADO = {
    "reaction": {"summary": {
        "n_events": 8,
        "runup": {"esticado_n": 3, "esticado_caiu_n": 2,
                  "esticado_reacao_media": 1.37},
    }},
}


def test_par_inventado_do_balde_e_erro():
    achados = validar_analise(_texto_completo(
        "Historicamente, em 3 dos 7 eventos onde o papel chegou esticado, "
        "ele reagiu com uma média de 1,37% de alta."), _BALDE_ESTICADO)
    assert "ANALISE_BUCKET_CONTADO_ERRADO" in _codigos(achados)


def test_a_mensagem_traz_o_par_certo():
    msg = next(a["mensagem"] for a in validar_analise(_texto_completo(
        "Em 3 dos 7 eventos esticados a reação foi positiva."),
        _BALDE_ESTICADO)
        if a["codigo"] == "ANALISE_BUCKET_CONTADO_ERRADO")
    assert "2 de 3" in msg and "8 eventos" in msg


@pytest.mark.parametrize("frase", [
    # o par "quantos caíram entre os esticados" -- como o card escreve
    "Em 2 de 3 balanços em que o papel chegou esticado, a reação foi de "
    "queda (média +1,37%).",
    # o par "quantos do total chegaram esticados" -- a outra leitura legítima
    "Dos 8 eventos, 3 chegaram esticados.",
    "Em 3 de 8 eventos o papel chegou esticado.",
    # sem par nenhum: nada a conferir
    "O papel chegou esticado ao último balanço.",
])
def test_as_duas_leituras_legitimas_passam(frase):
    assert "ANALISE_BUCKET_CONTADO_ERRADO" not in _codigos(
        validar_analise(_texto_completo(frase), _BALDE_ESTICADO))


def test_sem_o_balde_no_payload_a_checagem_se_cala():
    assert "ANALISE_BUCKET_CONTADO_ERRADO" not in _codigos(
        validar_analise(_texto_completo(
            "Em 3 dos 7 eventos esticados a reação foi positiva."), {}))


def test_frase_sem_esticado_nao_e_conferida():
    """"3 de 7" falando de outra coisa não é o balde."""
    assert "ANALISE_BUCKET_CONTADO_ERRADO" not in _codigos(
        validar_analise(_texto_completo(
            "Em 3 dos 7 eventos o gap de abertura foi positivo."),
            _BALDE_ESTICADO))


# ── sessão do anúncio citada como reação (AMC) ──────────────────────────────
#
# WOLF, 27/08/2026: "A reação do papel a este evento foi de -7.53% no dia do
# anúncio, e -9.42% no dia seguinte." WOLF reporta AMC -- `janela_reacao` do
# evento é "seguinte", ou seja, o -7,53% de 19/08 é a sessão ANTERIOR à
# divulgação, não a reação. A reação de verdade é o -9,42% do dia seguinte,
# que a própria tabela marca com ◂.

_EVENTO_AMC = {"reaction": {"events": [{
    "janela_reacao": "seguinte",
    "announcement_day": {"close_pct": -7.53},
    "next_day": {"close_pct": -9.42},
}]}}

_EVENTO_BMO = {"reaction": {"events": [{
    "janela_reacao": "anuncio",
    "announcement_day": {"close_pct": -7.53},
    "next_day": {"close_pct": -9.42},
}]}}


def test_sessao_do_anuncio_chamada_de_reacao_e_erro():
    """A frase real, verbatim."""
    achados = validar_analise(_texto_completo(
        "A reação do papel a este evento foi de -7.53% no dia do anúncio, "
        "e -9.42% no dia seguinte."), _EVENTO_AMC)
    assert "ANALISE_REACAO_SESSAO_ERRADA" in _codigos(achados)


def test_a_mensagem_explica_amc():
    msg = next(a["mensagem"] for a in validar_analise(_texto_completo(
        "A reação do papel a este evento foi de -7.53% no dia do anúncio."),
        _EVENTO_AMC)
        if a["codigo"] == "ANALISE_REACAO_SESSAO_ERRADA")
    assert "AMC" in msg or "fechamento" in msg


@pytest.mark.parametrize("frase", [
    # a reação atribuída ao número CERTO -- o do dia seguinte
    "A reação do papel a este evento foi de -9.42% no dia seguinte; o "
    "-7.53% do dia do anúncio é anterior à divulgação.",
    # o número errado aparece, mas sem ser chamado de "reação"
    "WOLF caiu 7,53% no dia do anúncio (sessão anterior à divulgação AMC) "
    "e reagiu com -9,42% no pregão seguinte.",
    # nem menciona reação
    "WOLF fechou -7,53% no dia do anúncio.",
])
def test_frases_corretas_ou_neutras_nao_caem(frase):
    assert "ANALISE_REACAO_SESSAO_ERRADA" not in _codigos(
        validar_analise(_texto_completo(frase), _EVENTO_AMC))


def test_empresa_que_reporta_bmo_nao_e_conferida():
    """`janela_reacao == "anuncio"`: para quem divulga ANTES da abertura, o
    dia do anúncio É a reação -- não há erro possível aqui."""
    achados = validar_analise(_texto_completo(
        "A reação do papel a este evento foi de -7.53% no dia do anúncio."),
        _EVENTO_BMO)
    assert "ANALISE_REACAO_SESSAO_ERRADA" not in _codigos(achados)


def test_sem_eventos_no_payload_a_checagem_se_cala():
    achados = validar_analise(_texto_completo(
        "A reação do papel a este evento foi de -7.53% no dia do anúncio."),
        {})
    assert "ANALISE_REACAO_SESSAO_ERRADA" not in _codigos(achados)


def test_numero_diferente_do_anunciado_nao_cai():
    """O texto cita OUTRO percentual perto de 'dia do anúncio' -- não é o
    caso que esta checagem cobre (seria alucinação de outro tipo)."""
    achados = validar_analise(_texto_completo(
        "A reação do papel a este evento foi de -12.00% no dia do anúncio."),
        _EVENTO_AMC)
    assert "ANALISE_REACAO_SESSAO_ERRADA" not in _codigos(achados)


# ── 16. amostra curta de earnings sem declarar o N ──────────────────────────
#
# WOLF, 27/08/2026: R1/R2/S1/S2 e a reação média citados com N=4 sem nenhuma
# menção ao tamanho da amostra -- mesma regra que a tela Reação a Earnings já
# aplica (LEITURA_AMOSTRA_CURTA_OMITIDA), portada aqui pela primeira vez.

_N4 = {"reaction": {"summary": {"n_events": 4}}}
_N1 = {"reaction": {"summary": {"n_events": 1}}}
_N8 = {"reaction": {"summary": {"n_events": 8}}}


def test_amostra_de_4_sem_declarar_e_aviso():
    achados = validar_analise(_texto_completo(
        "A reação média de earnings foi de -4,94%."), _N4)
    assert "ANALISE_AMOSTRA_CURTA_OMITIDA" in _codigos(achados)
    achado = next(a for a in achados
                  if a["codigo"] == "ANALISE_AMOSTRA_CURTA_OMITIDA")
    assert achado["nivel"] == "AVISO"


def test_amostra_de_4_declarada_no_texto_nao_cai():
    achados = validar_analise(_texto_completo(
        "Nos 4 eventos observados, a reação média foi de -4,94%."), _N4)
    assert "ANALISE_AMOSTRA_CURTA_OMITIDA" not in _codigos(achados)


def test_amostra_de_1_declarada_com_unico_nao_cai():
    achados = validar_analise(_texto_completo(
        "Com apenas um único balanço na amostra, a reação foi de -2,60%."),
        _N1)
    assert "ANALISE_AMOSTRA_CURTA_OMITIDA" not in _codigos(achados)


def test_amostra_grande_nao_precisa_declarar():
    achados = validar_analise(_texto_completo(
        "A reação média de earnings foi de -4,94%."), _N8)
    assert "ANALISE_AMOSTRA_CURTA_OMITIDA" not in _codigos(achados)


def test_sem_earnings_na_secao_a_checagem_se_cala():
    achados = validar_analise(_texto_completo(
        "Sem menção a earnings nesta seção."), _N4)
    assert "ANALISE_AMOSTRA_CURTA_OMITIDA" not in _codigos(achados)


# ── 17. número pré-reação comparado à média histórica da reação ────────────
#
# MRVL, 27/08/2026: "o padrão esticado teve reação média de -1,23%, próxima
# ao -1,49% observado agora." -1,49% é o fechamento do DIA DO ANÚNCIO --
# MRVL reporta AMC, a reação de verdade (D+1) ainda não aconteceu (a
# própria tabela mostra "— ◂" nesse evento). A frase compara um número
# PRÉ-reação com a média histórica de reações JÁ CONFIRMADAS.

_EVENTO_AMC_PENDENTE_ESTICADO = {"reaction": {
    "summary": {"n_events": 7, "runup": {
        "estado_atual": "esticado", "esticado_reacao_media": -1.23}},
    "events": [{"janela_reacao": "seguinte",
               "announcement_day": {"close_pct": -1.49}, "next_day": None}],
}}

_EVENTO_AMC_PENDENTE_DESCONTADO = {"reaction": {
    "summary": {"n_events": 7, "runup": {
        "estado_atual": "descontado", "descontado_reacao_media": 4.5}},
    "events": [{"janela_reacao": "seguinte",
               "announcement_day": {"close_pct": 4.6}, "next_day": None}],
}}


def test_mrvl_real_e_erro():
    achados = validar_analise(_texto_completo(
        "O padrão esticado teve reação média de -1,23%, próxima ao -1,49% "
        "observado agora."), _EVENTO_AMC_PENDENTE_ESTICADO)
    assert "ANALISE_PRE_REACAO_COMO_REACAO_HISTORICA" in _codigos(achados)


def test_bucket_descontado_tambem_cai():
    achados = validar_analise(_texto_completo(
        "O padrão descontado teve reação média de +4,50%, similar a +4,60% "
        "observado agora."), _EVENTO_AMC_PENDENTE_DESCONTADO)
    assert "ANALISE_PRE_REACAO_COMO_REACAO_HISTORICA" in _codigos(achados)


def test_sem_palavra_de_comparacao_nao_cai():
    """Os dois números aparecem, mas nada os conecta como equivalentes --
    duas menções soltas não são o erro que esta checagem cobre."""
    achados = validar_analise(_texto_completo(
        "O padrão esticado teve reação média de -1,23%. O pregão do "
        "anúncio fechou em -1,49%."), _EVENTO_AMC_PENDENTE_ESTICADO)
    assert "ANALISE_PRE_REACAO_COMO_REACAO_HISTORICA" not in _codigos(achados)


def test_so_o_numero_do_balde_nao_cai():
    achados = validar_analise(_texto_completo(
        "O padrão esticado teve reação média de -1,23%, um valor pequeno."),
        _EVENTO_AMC_PENDENTE_ESTICADO)
    assert "ANALISE_PRE_REACAO_COMO_REACAO_HISTORICA" not in _codigos(achados)


def test_so_o_numero_pre_reacao_nao_cai():
    achados = validar_analise(_texto_completo(
        "O pregão do anúncio fechou em -1,49%, próximo da abertura."),
        _EVENTO_AMC_PENDENTE_ESTICADO)
    assert "ANALISE_PRE_REACAO_COMO_REACAO_HISTORICA" not in _codigos(achados)


def test_bmo_com_reacao_ja_disponivel_nao_cai():
    """Reportador BMO: o dia do anúncio JÁ é a reação, não existe conceito
    de 'pré-reação' aqui -- e o `next_day` disponível já tira o gatilho de
    reação pendente."""
    dados = {"reaction": {
        "summary": {"n_events": 7, "runup": {
            "estado_atual": "esticado", "esticado_reacao_media": -1.23}},
        "events": [{"janela_reacao": "anuncio",
                   "announcement_day": {"close_pct": -1.49},
                   "next_day": {"close_pct": 2.0}}],
    }}
    achados = validar_analise(_texto_completo(
        "O padrão esticado teve reação média de -1,23%, próxima ao -1,49% "
        "observado agora."), dados)
    assert "ANALISE_PRE_REACAO_COMO_REACAO_HISTORICA" not in _codigos(achados)


def test_amc_com_reacao_ja_disponivel_nao_cai():
    """A reação (D+1) já existe -- não há mais número pré-reação para
    conflitar com o balde."""
    dados = {"reaction": {
        "summary": {"n_events": 7, "runup": {
            "estado_atual": "esticado", "esticado_reacao_media": -1.23}},
        "events": [{"janela_reacao": "seguinte",
                   "announcement_day": {"close_pct": -1.49},
                   "next_day": {"close_pct": -9.42}}],
    }}
    achados = validar_analise(_texto_completo(
        "O padrão esticado teve reação média de -1,23%, próxima ao -1,49% "
        "observado agora."), dados)
    assert "ANALISE_PRE_REACAO_COMO_REACAO_HISTORICA" not in _codigos(achados)


def test_estado_neutro_nao_cai():
    dados = {"reaction": {
        "summary": {"n_events": 7, "runup": {
            "estado_atual": "neutro", "esticado_reacao_media": -1.23}},
        "events": [{"janela_reacao": "seguinte",
                   "announcement_day": {"close_pct": -1.49}, "next_day": None}],
    }}
    achados = validar_analise(_texto_completo(
        "O padrão esticado teve reação média de -1,23%, próxima ao -1,49% "
        "observado agora."), dados)
    assert "ANALISE_PRE_REACAO_COMO_REACAO_HISTORICA" not in _codigos(achados)


def test_pre_reacao_sem_eventos_no_payload_a_checagem_se_cala():
    achados = validar_analise(_texto_completo(
        "O padrão esticado teve reação média de -1,23%, próxima ao -1,49% "
        "observado agora."), {})
    assert "ANALISE_PRE_REACAO_COMO_REACAO_HISTORICA" not in _codigos(achados)


def test_a_comparacao_negada_nao_cai():
    achados = validar_analise(_texto_completo(
        "O padrão esticado teve reação média de -1,23%, mas isso não é "
        "próximo ao -1,49% observado agora, que ainda é só o pregão do "
        "anúncio."), _EVENTO_AMC_PENDENTE_ESTICADO)
    assert "ANALISE_PRE_REACAO_COMO_REACAO_HISTORICA" not in _codigos(achados)


# ── 18. reação média atribuída ao "dia do anúncio" (AMC) ────────────────────
#
# MRVL, 27/08/2026, DUAS rodadas do mesmo ticker com fraseados bem
# diferentes:
#
#   "a reação média no dia do anúncio foi de +1.22% no fechamento"   (3ª)
#   "um fechamento diário médio de +1,22% no dia do anúncio"         (4ª)
#
# Na 4ª, "reação média" e "dia do anúncio" ficam a ~85 caracteres um do
# outro -- por isso a checagem ancora no NÚMERO perto de "dia do anúncio" e
# confere contra `close_pct_mean`/`gap_pct_mean`, não em distância de frase.

_EVENTO_AMC_COM_MEDIA = {"reaction": {
    "summary": {"close_pct_mean": 1.22, "gap_pct_mean": -0.29},
    "events": [{"janela_reacao": "seguinte",
               "announcement_day": {"close_pct": -7.53},
               "next_day": {"close_pct": -9.42}}],
}}
_EVENTO_BMO_COM_MEDIA = {"reaction": {
    "summary": {"close_pct_mean": 1.22, "gap_pct_mean": -0.29},
    "events": [{"janela_reacao": "anuncio",
               "announcement_day": {"close_pct": -7.53},
               "next_day": {"close_pct": -9.42}}],
}}


def test_reacao_media_no_dia_do_anuncio_e_erro_amc():
    """A frase real da 3ª rodada."""
    achados = validar_analise(_texto_completo(
        "Historicamente, a reação média no dia do anúncio foi de +1.22% no "
        "fechamento, com um gap médio de abertura de -0.29%."),
        _EVENTO_AMC_COM_MEDIA)
    assert "ANALISE_REACAO_MEDIA_ATRIBUIDA_AO_ANUNCIO" in _codigos(achados)


def test_reacao_media_e_dia_do_anuncio_longe_na_frase_tambem_cai():
    """A frase real da 4ª rodada -- "reação média" e "dia do anúncio"
    separados por ~85 caracteres, fora de qualquer janela de distância
    razoável. Só cai porque o NÚMERO (+1,22%) está perto de "dia do
    anúncio" e bate com `close_pct_mean`."""
    achados = validar_analise(_texto_completo(
        "Com base em 7 eventos de earnings, a reação média do papel "
        "resultou num gap de -0,29% e um fechamento diário médio de "
        "+1,22% no dia do anúncio, com uma volatilidade absoluta média de "
        "13,78%."), _EVENTO_AMC_COM_MEDIA)
    assert "ANALISE_REACAO_MEDIA_ATRIBUIDA_AO_ANUNCIO" in _codigos(achados)


def test_ordem_invertida_da_frase_tambem_cai():
    achados = validar_analise(_texto_completo(
        "No dia do anúncio, a reação média foi de +1.22%."),
        _EVENTO_AMC_COM_MEDIA)
    assert "ANALISE_REACAO_MEDIA_ATRIBUIDA_AO_ANUNCIO" in _codigos(achados)


def test_bmo_nao_cai_no_check_18():
    """Para quem reporta BMO, o dia do anúncio É a sessão de reação -- não
    há erro possível."""
    achados = validar_analise(_texto_completo(
        "Historicamente, a reação média no dia do anúncio foi de +1.22% no "
        "fechamento."), _EVENTO_BMO_COM_MEDIA)
    assert "ANALISE_REACAO_MEDIA_ATRIBUIDA_AO_ANUNCIO" not in _codigos(achados)


def test_rotulo_correto_sessao_seguinte_nao_cai():
    achados = validar_analise(_texto_completo(
        "Historicamente, a reação média na sessão seguinte foi de +1.22% "
        "no fechamento."), _EVENTO_AMC_COM_MEDIA)
    assert "ANALISE_REACAO_MEDIA_ATRIBUIDA_AO_ANUNCIO" not in _codigos(achados)


def test_numero_diferente_do_agregado_nao_cai():
    """Outro número perto de "dia do anúncio" -- não é o caso que esta
    checagem cobre (não é a estatística agregada sendo mal rotulada)."""
    achados = validar_analise(_texto_completo(
        "No dia do anúncio o papel fechou em -1,49%."),
        _EVENTO_AMC_COM_MEDIA)
    assert "ANALISE_REACAO_MEDIA_ATRIBUIDA_AO_ANUNCIO" not in _codigos(achados)


def test_reacao_media_sem_eventos_no_payload_se_cala():
    achados = validar_analise(_texto_completo(
        "A reação média no dia do anúncio foi de +1.22%."), {})
    assert "ANALISE_REACAO_MEDIA_ATRIBUIDA_AO_ANUNCIO" not in _codigos(achados)


def test_reacao_media_sem_summary_no_payload_se_cala():
    """Evento AMC presente, mas sem `summary` -- não há média agregada pra
    conferir contra nada."""
    sem_summary = {"reaction": {"events": [{"janela_reacao": "seguinte"}]}}
    achados = validar_analise(_texto_completo(
        "A reação média no dia do anúncio foi de +1.22%."), sem_summary)
    assert "ANALISE_REACAO_MEDIA_ATRIBUIDA_AO_ANUNCIO" not in _codigos(achados)


# ── 19. contagem do balde "esticado" citada solta, sem o par X de Y ────────
#
# MRVL, 27/08/2026 (4ª rodada): "em 6 eventos onde o papel chegou esticado,
# a reação média pós-earnings foi de -1,23%." O real esticado_n era 2; o
# modelo citou n_com_runup (6, a amostra TOTAL da correlação) como se fosse
# a contagem do balde esticado. `_PAR_CONTADO` (check 14) não captura isso
# porque não há "de Y" na frase -- é um número solto.

_ESTICADO_2 = {"reaction": {"summary": {"runup": {
    "esticado_n": 2, "esticado_caiu_n": 1}}}}


def test_contagem_solta_errada_e_erro():
    achados = validar_analise(_texto_completo(
        "Historicamente, em 6 eventos onde o papel chegou esticado, a "
        "reação média pós-earnings foi de -1,23%."), _ESTICADO_2)
    assert "ANALISE_BUCKET_ESTICADO_CONTAGEM_SOLTA" in _codigos(achados)


def test_contagem_solta_correta_nao_cai():
    achados = validar_analise(_texto_completo(
        "Historicamente, em 2 eventos onde o papel chegou esticado, a "
        "reação média pós-earnings foi de -1,23%."), _ESTICADO_2)
    assert "ANALISE_BUCKET_ESTICADO_CONTAGEM_SOLTA" not in _codigos(achados)


def test_ordem_invertida_da_contagem_solta_tambem_cai():
    achados = validar_analise(_texto_completo(
        "O papel chegou esticado em 6 eventos, com reação média de -1,23%."),
        _ESTICADO_2)
    assert "ANALISE_BUCKET_ESTICADO_CONTAGEM_SOLTA" in _codigos(achados)


def test_frase_com_par_x_de_y_nao_conflita_com_o_check_19():
    """"X de Y" é o padrão do check 14 -- essa frase não tem número solto
    perto de "eventos", então o check 19 fica de fora e o 14 já cobre."""
    achados = validar_analise(_texto_completo(
        "Nos 1 de 2 eventos esticados, o papel caiu."), _ESTICADO_2)
    assert "ANALISE_BUCKET_ESTICADO_CONTAGEM_SOLTA" not in _codigos(achados)


def test_numero_de_eventos_sem_mencionar_esticado_nao_cai():
    achados = validar_analise(_texto_completo(
        "Com base em 7 eventos de earnings, a reação média foi de +1,22%."),
        _ESTICADO_2)
    assert "ANALISE_BUCKET_ESTICADO_CONTAGEM_SOLTA" not in _codigos(achados)


def test_sem_esticado_n_no_payload_a_checagem_se_cala():
    achados = validar_analise(_texto_completo(
        "Em 6 eventos onde o papel chegou esticado, a reação foi negativa."),
        {})
    assert "ANALISE_BUCKET_ESTICADO_CONTAGEM_SOLTA" not in _codigos(achados)


# ═══ 28/08/2026 — NVDA: coletado não é o mesmo que enviado ═════════════════
#
# A tela mostrava, na mesma página, a linha de fontes anunciando "valuation:
# múltiplos TTM (SEC/XBRL) + DCF (FMP)" e a prosa dizendo que os dados de
# fundamento não estavam disponíveis. O validador acusou o modelo.
#
# Ninguém mentiu. `_compactar` corta o payload quando ele não cabe em 14 mil
# chars, e a camada fundamental era a última chave do dicionário: o modelo
# nunca a recebeu. Esta checagem existe para pegar o modelo negando dado que
# RECEBEU -- sem o recorte por `_blocosOmitidos` ela vira o contrário, um
# detector que só dispara depois de o sistema já ter falhado, culpando quem
# disse a verdade.

_FRASE_NVDA = ("Os dados de fundamento e valuation, incluindo alvos de "
               "analistas e múltiplos de mercado, não estavam disponíveis "
               "para este ativo na análise atual.")


def test_bloco_omitido_do_prompt_nao_e_dado_presente():
    dados = {**_COM_FUNDAMENTO, "_blocosOmitidos": ["reacaoEarnings", "fundamento"]}
    assert "ANALISE_NEGA_DADO_PRESENTE" not in _codigos(
        validar_analise(_texto_completo(_FRASE_NVDA), dados))


def test_omitir_outro_bloco_nao_absolve_a_negacao_do_fundamento():
    """O recorte é do bloco NOMEADO, não uma anistia geral: cortar a reação a
    earnings não faz o modelo deixar de ter recebido o valuation."""
    dados = {**_COM_FUNDAMENTO, "_blocosOmitidos": ["reacaoEarnings"]}
    assert "ANALISE_NEGA_DADO_PRESENTE" in _codigos(
        validar_analise(_texto_completo(_FRASE_NVDA), dados))


def test_a_frase_da_nvda_cai_quando_o_payload_foi_inteiro():
    """Sem omissão, é o caso do AMD de novo: o dado chegou e o texto o negou."""
    assert "ANALISE_NEGA_DADO_PRESENTE" in _codigos(
        validar_analise(_texto_completo(_FRASE_NVDA), _COM_FUNDAMENTO))


@pytest.mark.parametrize("valor", ["fundamento", 42, None, {"fundamento": 1}])
def test_blocos_omitidos_malformado_nao_derruba_nem_absolve(valor):
    """`dados` vem de fora e já chegou torto antes. Uma string viraria conjunto
    de LETRAS, e "f" nunca casaria com "fundamento" -- mas o dia em que casar
    por acidente, a checagem some sem ninguém notar."""
    dados = {**_COM_FUNDAMENTO, "_blocosOmitidos": valor}
    assert "ANALISE_NEGA_DADO_PRESENTE" in _codigos(
        validar_analise(_texto_completo(_FRASE_NVDA), dados))


# ═══ 29/08/2026 — NVDA: a prosa negou o painel de reação impresso ao lado ══
#
# `ANALISE_NEGA_DADO_PRESENTE` vigia alvos, valuation e manchetes. O bloco de
# reação a earnings nunca esteve na lista -- e foi ele que o texto negou:
#
#   "Não há dados de reação a balanços (reacaoEarnings) disponíveis para
#    análise no momento."
#   "...a ausência de dados de reação a earnings impede uma análise completa
#    de seu histórico pós-balanço."
#
# enquanto o painel Níveis & Reações, na MESMA tela, trazia 8 eventos, bandas
# R1/R2/S1/S2 e correlação de 0,71.
#
# A causa foi o payload não caber. A checagem não pode depender disso:
# qualquer caminho que faça o texto negar este painel produz o mesmo estrago
# para quem lê.

_COM_REACAO = {"reaction": {"summary": {"n_events": 8, "r1": 226.30}}}
_SEM_REACAO = {"reaction": {"summary": {"n_events": 0}}}

_NEGACOES_DA_NVDA = [
    # Verbal -- a forma que `_NEGA_DISPONIBILIDADE` já conhecia.
    'Não há dados de reação a balanços ("reacaoEarnings") disponíveis para '
    "análise no momento.",
    # NOMINAL -- "a ausência de dados de". Não casa com o padrão do 0b, que é
    # todo construído em cima de verbo, e apareceu na MESMA análise.
    "A volatilidade é moderada e o momentum do setor é forte, mas a ausência "
    "de dados de reação a earnings impede uma análise completa de seu "
    "histórico pós-balanço.",
]


@pytest.mark.parametrize("frase", _NEGACOES_DA_NVDA)
def test_negar_a_reacao_que_veio_e_erro(frase):
    achados = validar_analise(_texto_completo(frase), _COM_REACAO)
    assert "ANALISE_NEGA_REACAO_PRESENTE" in _codigos(achados)
    msg = next(a["mensagem"] for a in achados
               if a["codigo"] == "ANALISE_NEGA_REACAO_PRESENTE")
    assert "8 evento" in msg, "a mensagem tem que dizer QUANTOS estavam na mão"


@pytest.mark.parametrize("frase", _NEGACOES_DA_NVDA)
def test_a_mesma_frase_passa_quando_nao_ha_evento(frase):
    """Papel recém-listado não tem histórico de balanço. Dizer isso é correto,
    e a checagem existe para separar os dois casos, não para proibir a frase."""
    assert "ANALISE_NEGA_REACAO_PRESENTE" not in _codigos(
        validar_analise(_texto_completo(frase), _SEM_REACAO))


@pytest.mark.parametrize("frase", _NEGACOES_DA_NVDA)
def test_bloco_omitido_do_prompt_nao_e_negacao_indevida(frase):
    """Se o bloco não coube, o modelo não o recebeu -- cobrar dele um dado que
    ficou fora do prompt é reprovar o texto por dizer a verdade."""
    dados = {**_COM_REACAO, "_blocosOmitidos": ["reacaoEarnings"]}
    assert "ANALISE_NEGA_REACAO_PRESENTE" not in _codigos(
        validar_analise(_texto_completo(frase), dados))


@pytest.mark.parametrize("frase", [
    # Negação sobre OUTRO dado: o sujeito não é a reação a balanços.
    "Não há dados de volume intradiário para esta sessão.",
    "O RSI não estava disponível no painel técnico.",
    # Fala da reação SEM negar nada.
    "A reação a balanços mostra 8 eventos com viés negativo.",
    "O histórico pós-balanço aponta fechamento médio de -0,89%.",
    # Ausência de OUTRA coisa.
    "A ausência de dados de opções limita a leitura de fluxo.",
])
def test_frase_que_nao_nega_a_reacao_passa(frase):
    assert "ANALISE_NEGA_REACAO_PRESENTE" not in _codigos(
        validar_analise(_texto_completo(frase), _COM_REACAO))


# ═══ 29/08/2026 — NVDA: a média da reação virou "dia do balanço" ═══════════
#
# O check 18 nasceu do MRVL e conhecia exatamente este erro. Não disparou por
# duas razões, e as duas ensinam a mesma coisa -- gatilho estreito demais:
#
#   1. VOCABULÁRIO. O padrão exigia o literal "dia do ANÚNCIO". A NVDA
#      escreveu "no fechamento do dia do BALANÇO". Mesmo conceito, outro
#      substantivo -- e o arquivo já tinha o conjunto de sinônimos em
#      `_EVENTO`, usado por meia dúzia de outras checagens.
#
#   2. SINAL. A prosa escreveu "caiu 0,89%": a direção estava no VERBO, não no
#      número. `abs(0.89 - (-0.89))` dá 1,78 e passava longe da tolerância de
#      0,05, mesmo sendo exatamente o valor do payload.
#
# O estrago: -0,89% é a média da sessão SEGUINTE (as oito linhas da tabela
# vêm marcadas com ◂). A média do dia do anúncio é +0,79% -- sinal oposto.
# Quem lê conclui que a NVDA cai no dia do balanço; historicamente ela SUBIU
# no dia do anúncio e caiu no dia seguinte.

_COD_18 = "ANALISE_REACAO_MEDIA_ATRIBUIDA_AO_ANUNCIO"

_AMC = {"reaction": {"summary": {"janela_reacao": "seguinte", "n_events": 8,
                                 "close_pct_mean": -0.89, "gap_pct_mean": 0.31}}}
_BMO = {"reaction": {"summary": {"janela_reacao": "anuncio", "n_events": 8,
                                 "close_pct_mean": -0.89, "gap_pct_mean": 0.31}}}

_FRASE_NVDA_MEDIA = (
    "A NVDA teve seus earnings divulgados há 1 pregão, e a reação histórica "
    "em 8 eventos mostra que o preço, em média, caiu 0,89% no fechamento do "
    "dia do balanço.")


@pytest.mark.parametrize("frase", [
    _FRASE_NVDA_MEDIA,
    # Os sinônimos que o literal deixava passar.
    "A reação média foi de -0,89% no dia do resultado.",
    "No dia da divulgação, o fechamento médio foi de -0,89%.",
    "O papel recuou 0,89% em média no dia do evento.",
    # E o fraseado original do MRVL continua caindo.
    "Um fechamento diário médio de -0,89% no dia do anúncio.",
])
def test_media_da_reacao_colada_ao_dia_do_evento_e_erro(frase):
    achados = validar_analise(_texto_completo(frase), _AMC)
    assert _COD_18 in _codigos(achados), frase


def test_direcao_no_verbo_conta_como_sinal():
    """"caiu 0,89%" é -0,89%. Comparar com sinal fazia a checagem passar longe
    do número que ela existe para reconhecer."""
    assert _COD_18 in _codigos(
        validar_analise(_texto_completo("O preço caiu 0,89% no dia do balanço."), _AMC))
    # E o inverso também é erro: citar +0,89% onde o dado é -0,89%.
    assert _COD_18 in _codigos(
        validar_analise(_texto_completo("O preço subiu 0,89% no dia do balanço."), _AMC))


def test_papel_bmo_pode_atribuir_ao_dia_do_balanco():
    """Quem divulga ANTES da abertura reage no próprio dia. A frase é certa, e
    a checagem existe para separar os dois casos."""
    assert _COD_18 not in _codigos(
        validar_analise(_texto_completo(_FRASE_NVDA_MEDIA), _BMO))


def test_a_janela_vem_do_summary_e_nao_dos_eventos():
    """`events` não sobrevive ao enxugamento do payload. Uma checagem que só
    funciona com o bloco inteiro falha exatamente na rodada em que o modelo
    tinha menos contexto -- que é quando ela mais importa."""
    sem_eventos = {"reaction": {"summary": dict(_AMC["reaction"]["summary"])}}
    assert "events" not in sem_eventos["reaction"]
    assert _COD_18 in _codigos(
        validar_analise(_texto_completo(_FRASE_NVDA_MEDIA), sem_eventos))


@pytest.mark.parametrize("frase", [
    # Número que não é o do payload.
    "O papel caiu 3,40% no dia do balanço.",
    # Negado.
    "Não foi no dia do balanço que o papel caiu 0,89%.",
    # Atribuição CERTA: nomeia a sessão seguinte.
    "A reação média foi de -0,89% na sessão seguinte ao anúncio.",
    # Fala do evento sem citar a média.
    "O balanço foi divulgado há 1 pregão.",
])
def test_frase_que_nao_erra_a_atribuicao_passa(frase):
    assert _COD_18 not in _codigos(validar_analise(_texto_completo(frase), _AMC))
