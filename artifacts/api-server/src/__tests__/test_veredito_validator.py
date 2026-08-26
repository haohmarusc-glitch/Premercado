"""
Testes de agent/veredito_validator.py -- regressão dos erros encontrados na
análise manual do Veredito do Dia de 31/07/2026 (AVGO com sinal trocado,
SKHY com snapshot de datas misturadas, RSI do ARM defasado, dia da semana
errado, earnings fantasma da SMCI).

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_veredito_validator.py -v
(conftest.py no mesmo diretório já cuida do sys.path)
"""

from datetime import date, timedelta

import pytest

from agent.veredito_validator import (ValidationReport, lint_veredito,
                                      validar_bloco_estruturado,
                                      validate_snapshot)

SNAPSHOT = {
    "as_of": "2026-07-31",
    "quotes": {
        "AVGO": {"price": 389.28, "previous_close": 387.84, "open": 394.83,
                  "high": 399.92, "low": 379.71, "change_percent": -0.36,
                  "as_of": "2026-07-31"},
        "SKHY": {"price": 143.73, "previous_close": 149.00, "open": 159.875,
                  "high": 162.65, "low": 143.51, "change_percent": -6.61,
                  "as_of": "2026-07-31"},
        "SMCI": {"price": 28.40, "previous_close": 27.73, "open": 28.65,
                  "high": 29.30, "low": 27.21, "change_percent": 2.4162,
                  "as_of": "2026-07-31"},
        "ARM": {"price": 239.69, "previous_close": 241.54, "open": 258.95,
                 "high": 261.905, "low": 239.26, "change_percent": -0.7659,
                 "as_of": "2026-07-31"},
    },
    "technicals": {
        "ARM": {"rsi": 31.55, "rsi_date": "2026-07-29"},
        "SMCI": {"rsi": 48.91, "rsi_date": "2026-07-31"},
    },
    "earnings": {"SMCI": "2026-08-10", "NVDA": "2026-08-25",
                 "MRVL": "2026-08-26", "AVGO": "2026-09-02"},
}


def _codes(issues):
    return {i.code for i in issues}


def test_validate_snapshot_catches_all_known_issues():
    rep = validate_snapshot(SNAPSHOT)
    assert rep.has_errors
    assert _codes(rep.issues) == {"PCT_SIGN_FLIP", "PCT_MISMATCH", "RSI_STALE"}
    assert _codes(rep.signals) == {"INTRADAY_FADE"}
    fade_tickers = {s.ticker for s in rep.signals}
    assert fade_tickers == {"SKHY", "ARM"}


def test_validate_snapshot_pct_sign_flip_avgo():
    rep = validate_snapshot(SNAPSHOT)
    avgo = [i for i in rep.issues if i.ticker == "AVGO"]
    assert len(avgo) == 1
    assert avgo[0].code == "PCT_SIGN_FLIP"
    assert avgo[0].severity == "ERROR"


def test_validate_snapshot_clean_data_produces_no_issues():
    clean = {
        "as_of": "2026-07-31",
        "quotes": {
            "NVDA": {"price": 200.75, "previous_close": 198.00, "open": 198.5,
                      "high": 202.0, "low": 197.9, "change_percent": 1.39,
                      "as_of": "2026-07-31"},
        },
        "technicals": {"NVDA": {"rsi": 47.75, "rsi_date": "2026-07-31"}},
        "earnings": {},
    }
    rep = validate_snapshot(clean)
    assert not rep.has_errors
    assert not rep.issues
    assert not rep.signals


def test_lint_veredito_weekday_wrong():
    texto = "06/ago (sexta-feira): executar saidas."
    rep = lint_veredito(texto, SNAPSHOT)
    assert "WEEKDAY_WRONG" in _codes(rep.issues)


def test_lint_veredito_weekday_correct_no_false_positive():
    # 06/08/2026 e' de fato uma quinta-feira -- citar isso corretamente
    # (inclusive no formato "dd/mes (dia-da-semana)") nao pode disparar erro.
    texto = "06/ago (quinta-feira): executar saidas."
    rep = lint_veredito(texto, SNAPSHOT)
    assert "WEEKDAY_WRONG" not in _codes(rep.issues)


def test_lint_veredito_flat_claim_wrong():
    # SMCI subiu 2.4162% no snapshot -- "flat" e' um claim qualitativo sem
    # numero, que _TICKER_PCT sozinho nao pega (nenhum "%": no texto).
    texto = "SMCI está flat hoje, sem catalisador claro no radar."
    rep = lint_veredito(texto, SNAPSHOT)
    flat = [i for i in rep.issues if i.code == "TEXT_FLAT_MISMATCH"]
    assert len(flat) == 1
    assert flat[0].ticker == "SMCI"
    assert flat[0].severity == "WARN"


def test_lint_veredito_flat_claim_correct_no_false_positive():
    snap = {
        "as_of": "2026-07-31",
        "quotes": {"NVDA": {"price": 200.0, "previous_close": 199.8, "change_percent_verified": 0.1}},
        "technicals": {}, "earnings": {},
    }
    texto = "NVDA segue estável hoje, sem novidade relevante."
    rep = lint_veredito(texto, snap)
    assert "TEXT_FLAT_MISMATCH" not in _codes(rep.issues)


def test_lint_veredito_phantom_earnings():
    texto = "SMCI caiu 9,95% em 29/jul apos divulgacao de earnings."
    rep = lint_veredito(texto, SNAPSHOT)
    phantom = [i for i in rep.issues if i.code == "PHANTOM_EARNINGS"]
    assert len(phantom) == 1
    assert phantom[0].ticker == "SMCI"
    assert phantom[0].severity == "ERROR"


def test_lint_veredito_earnings_date_mismatch_is_warn_not_error():
    # Downgrade pedido apos o teste em producao: PHANTOM_EARNINGS e' o check
    # que realmente importa: EARNINGS_DATE_MISMATCH sozinho nao deve travar
    # o retry.
    texto = "SMCI earnings iminente em 11/ago traz risco."
    rep = lint_veredito(texto, SNAPSHOT)
    mismatches = [i for i in rep.issues if i.code == "EARNINGS_DATE_MISMATCH"]
    assert len(mismatches) == 1
    assert mismatches[0].severity == "WARN"
    assert not rep.has_errors


def test_lint_veredito_clean_text_no_issues():
    texto = "AVGO fecha em $389,28. SMCI tem earnings em 10/ago."
    rep = lint_veredito(texto, SNAPSHOT)
    assert not rep.has_errors
    assert not rep.issues


def test_prompt_block_lists_signals_only():
    rep = validate_snapshot(SNAPSHOT)
    block = rep.prompt_block()
    assert "SKHY" in block
    assert "ARM" in block
    assert "AVGO" not in block  # AVGO so' tem issue (ERROR), nao signal


# ------------------------------------------- distribuição invertida ---
#
# O prompt do veredito já trazia a instrução em prosa desde um incidente
# anterior, e ela não segurou: em 01/08 o veredito abriu com "padrão de
# distribuição confirmado" citando ARM (RSI 31.55, -26.17% vs SMA50) e MRVL
# (RSI 38.77, -21.66%) -- exatamente o perfil de FUNDO que a instrução
# descreve como o oposto de distribuição. E concluiu "vender ARM amanhã".


def _snap_distrib(rsi, pct_sma50, ticker="ARM"):
    return {
        "as_of": "2026-07-31",
        "quotes": {ticker: {"price": 239.69, "previous_close": 241.54,
                             "as_of": "2026-07-31"}},
        "technicals": {ticker: {"rsi": rsi, "rsi_date": "2026-07-31",
                                 "pct_above_sma50": pct_sma50}},
        "earnings": {},
    }


def test_distribuicao_em_perfil_de_fundo_e_erro():
    """O caso ARM de 01/08, com os números reais."""
    texto = "Padrão de distribuição confirmado em ARM, que rejeitou o gap de alta."
    rep = lint_veredito(texto, _snap_distrib(31.55, -26.17))
    assert rep.has_errors
    assert "DISTRIBUICAO_INVERTIDA" in rep.summary()
    assert "perfil de FUNDO" in rep.summary()


def test_distribuicao_em_mrvl_tambem():
    texto = "Distribuição institucional visível em MRVL após o gap."
    rep = lint_veredito(texto, _snap_distrib(38.77, -21.66, "MRVL"))
    assert rep.has_errors


def test_distribuicao_perto_do_topo_nao_e_erro():
    """RSI alto e acima da SMA50 é o cenário em que distribuição faz sentido."""
    texto = "Padrão de distribuição em ARM perto da máxima."
    rep = lint_veredito(texto, _snap_distrib(72.0, 8.5))
    assert not any(i.code == "DISTRIBUICAO_INVERTIDA" for i in rep.issues)


def test_ticker_de_lado_nao_dispara():
    """RSI 45 e -3% da SMA50 não é fundo nem topo -- não vira erro."""
    texto = "Sinais de distribuição em ARM."
    rep = lint_veredito(texto, _snap_distrib(45.0, -3.0))
    assert not any(i.code == "DISTRIBUICAO_INVERTIDA" for i in rep.issues)


def test_sem_a_palavra_distribuicao_nao_dispara():
    texto = "ARM segue em capitulação técnica após o resultado."
    rep = lint_veredito(texto, _snap_distrib(31.55, -26.17))
    assert not any(i.code == "DISTRIBUICAO_INVERTIDA" for i in rep.issues)


def test_sem_pct_sma50_no_snapshot_nao_dispara():
    """Ausência de dado nunca vira violação."""
    snap = _snap_distrib(31.55, -26.17)
    snap["technicals"]["ARM"]["pct_above_sma50"] = None
    rep = lint_veredito("Distribuição clara em ARM.", snap)
    assert not any(i.code == "DISTRIBUICAO_INVERTIDA" for i in rep.issues)


# ── regra 7: concentração por correlação (Radar IA 2026) ────────────────────

def _snap_concentracao():
    """Carteira com MU e SNDK (corr 0.82 no radar) -- o par crítico do guia."""
    return {
        "as_of": "2026-08-14",
        "quotes": {
            "MU": {"price": 300.0, "previous_close": 298.0, "as_of": "2026-08-14"},
            "SNDK": {"price": 200.0, "previous_close": 199.0, "as_of": "2026-08-14"},
            "CEG": {"price": 250.0, "previous_close": 251.0, "as_of": "2026-08-14"},
        },
        "technicals": {},
        "earnings": {},
    }


def test_concentracao_compra_dupla_no_mesmo_cluster_e_erro():
    texto = ("Vale comprar MU na abertura pela força do HBM. "
             "Também vale aumentar SNDK aproveitando o momento do NAND.")
    rep = lint_veredito(texto, _snap_concentracao())
    assert any(i.code == "CONCENTRACAO_CORRELACAO" for i in rep.issues)


def test_concentracao_nao_dispara_se_texto_ja_menciona_correlacao():
    texto = ("Vale comprar MU e também aumentar SNDK -- ciente da correlação "
             "alta entre os dois (mesmo cluster de memória), sizing dividido.")
    rep = lint_veredito(texto, _snap_concentracao())
    assert not any(i.code == "CONCENTRACAO_CORRELACAO" for i in rep.issues)


def test_concentracao_par_de_baixa_correlacao_nao_dispara():
    texto = ("Vale comprar MU pela força do HBM. "
             "Também vale aumentar CEG no tema de energia.")
    rep = lint_veredito(texto, _snap_concentracao())
    assert not any(i.code == "CONCENTRACAO_CORRELACAO" for i in rep.issues)


def test_concentracao_negacao_antes_do_verbo_nao_conta_como_compra():
    texto = ("Vale comprar MU na fraqueza. Em SNDK, não é hora de comprar -- "
             "esperar o resultado antes de qualquer entrada.")
    rep = lint_veredito(texto, _snap_concentracao())
    assert not any(i.code == "CONCENTRACAO_CORRELACAO" for i in rep.issues)


def test_concentracao_mencao_sem_verbo_de_compra_nao_conta():
    texto = "MU segue pressionada e SNDK acompanha o cluster de memória."
    rep = lint_veredito(texto, _snap_concentracao())
    assert not any(i.code == "CONCENTRACAO_CORRELACAO" for i in rep.issues)


# ═══ Veredito de 26/08/2026 — o dia errado e o lado errado ════════════════
#
# Duas famílias que passavam batidas nesta tela, ambas achadas lendo o texto
# publicado contra os próprios painéis dele.

_HOJE = {"as_of": "2026-08-26", "quotes": {}, "earnings": {}, "technicals": {}}


def _codigos(texto, snap=None):
    return sorted({i.code for i in lint_veredito(texto, dict(snap or _HOJE)).issues})


# ── "amanhã" num dia que é hoje ───────────────────────────────────────────
#
# O veredito abriu com "NVDA amanhã (26/ago)" num dia em que as_of ERA
# 26/08 -- o painel dizia "hoje". A seção inteira de "URGÊNCIAS DO PLANO
# (próximas 24h)" foi escrita em cima disso, mandando aguardar amanhã um
# resultado que sai hoje.

@pytest.mark.parametrize("texto", [
    "NVDA amanhã (26/ago): EPS consenso 2,09.",
    "NVDA earnings 26/ago (amanhã) — reavalie pós-abertura.",
    "MRVL hoje (27/ago): runup esticado.",
])
def test_prazo_relativo_que_contradiz_a_data_e_erro(texto):
    assert "PRAZO_RELATIVO_ERRADO" in _codigos(texto)


@pytest.mark.parametrize("texto", [
    "NVDA hoje (26/ago): EPS consenso 2,09.",
    "MRVL amanhã (27/ago): runup esticado.",
    # A adjacência é exigida: aqui o "hoje" fala de outra coisa.
    "O balanço de 22/out, mas hoje o papel caiu 2%.",
    # Data sem prazo relativo nenhum.
    "Earnings em 04/nov, sem catalisador próximo.",
])
def test_prazo_relativo_correto_ou_ausente_passa(texto):
    assert "PRAZO_RELATIVO_ERRADO" not in _codigos(texto)


def test_a_mensagem_diz_qual_era_a_data_certa():
    itens = [i for i in lint_veredito("NVDA amanhã (26/ago).", dict(_HOJE)).issues
             if i.code == "PRAZO_RELATIVO_ERRADO"]
    assert "2026-08-27" in itens[0].message


# ── o preço do lado errado do nível ───────────────────────────────────────
#
# "vender se quebrar suporte $126. Preço ainda $121, ACIMA do suporte" --
# 121 é MENOR que 126, o suporte estava rompido, e a leitura virou
# "aguardando consolidação". O JSON de saída saiu com BABA: MANTER.
#
# A checagem não consulta o plano: os dois números estão no próprio
# parágrafo, então a contradição é interna.

@pytest.mark.parametrize("texto", [
    "BABA: Plano: vender se quebrar suporte $126. Preço ainda $121, acima "
    "do suporte, aguardando consolidação.",
    "Stop em $370. O papel a $380, abaixo do stop, exige resgate.",
])
def test_lado_do_nivel_contradito_pelo_proprio_texto(texto):
    assert "NIVEL_LADO_INVERTIDO" in _codigos(texto)


@pytest.mark.parametrize("texto", [
    "BABA: vender se quebrar suporte $126. Preço $131, acima do suporte.",
    "Plano: suporte $126. Preço ainda $121, abaixo do suporte, venda exigida.",
    # "acima SMA50" não é "acima DO suporte" — média móvel não é o nível
    # nomeado, e comparar contra o suporte seria inventar a contradição.
    "EMA8 bullish acima SMA50 (+5,42%), com suporte em $126.",
    # Dois valores sem afirmação de lado.
    "ADI: take-profit em $390. Bollinger upper atual $393,62.",
])
def test_lado_coerente_ou_sem_afirmacao_passa(texto):
    assert "NIVEL_LADO_INVERTIDO" not in _codigos(texto)


def test_o_nivel_vem_do_paragrafo_e_nao_atravessa_linhas():
    """Cada posição é um bullet. O suporte de um papel não pode ser
    confrontado com o preço de outro."""
    texto = ("BABA: vender se quebrar suporte $126.\n"
             "ADI: preço $372, acima do suporte.")
    assert "NIVEL_LADO_INVERTIDO" not in _codigos(texto)


# ═══ Veredito de 26/08/2026, segunda leva ═════════════════════════════════
#
# Três divergências que sobraram da primeira leitura, todas conferíveis
# contra o snapshot -- e é por isso que valem checagem: o número CERTO passa
# e só o inventado cai.

_SNAP_2608 = {
    "as_of": "2026-08-26",
    "quotes": {"INTC": {}, "SKHY": {}, "ARM": {}},
    "earnings": {"INTC": "2026-10-22", "ARM": "2026-11-04"},
    "technicals": {
        "INTC": {"rsi": 36.0, "rsi_date": "2026-08-26", "pct_above_sma50": -19.11},
        "SKHY": {"rsi": 51.0, "rsi_date": "2026-08-26", "pct_above_sma50": -21.18},
        "ARM": {"rsi": 38.2, "rsi_date": "2026-08-26", "pct_above_sma50": -18.81},
    },
}


def _cods_2608(texto):
    return sorted({i.code
                   for i in lint_veredito(texto, dict(_SNAP_2608)).issues})


# ── distância à SMA50: o parágrafo do INTC deu DOIS números ───────────────

def test_o_numero_inventado_cai_e_o_certo_passa():
    """"$85,74 (-20,91% abaixo SMA50 em $106)" e "-19,11% abaixo média móvel
    de 50 dias" no MESMO parágrafo. Com $106 o certo é -19,11%. Conferir
    contra o dado (e não só contra si mesmo) é o que separa os dois."""
    assert "SMA50_DISTANCIA_ERRADA" in _cods_2608(
        "INTC: $85,74 (-20,91% abaixo SMA50 em $106), RSI 36,0.")
    assert "SMA50_DISTANCIA_ERRADA" not in _cods_2608(
        "INTC: EMA21 em 95,26, -19,11% abaixo média móvel de 50 dias.")


@pytest.mark.parametrize("texto", [
    "ARM: $238,91 (-18,81% abaixo SMA50 em $294).",
    "SKHY: -21,18% abaixo SMA50 — correção em progresso.",
])
def test_distancia_correta_a_sma50_passa(texto):
    assert "SMA50_DISTANCIA_ERRADA" not in _cods_2608(texto)


def test_a_palavra_carrega_o_sinal_quando_o_numero_nao_traz():
    """"21,18% ABAIXO" é o mesmo que -21,18%. Sem ler a palavra, o validador
    compararia +21,18 contra -21,18 e apontaria texto correto."""
    assert "SMA50_DISTANCIA_ERRADA" not in _cods_2608(
        "SKHY: 21,18% abaixo da SMA50.")
    assert "SMA50_DISTANCIA_ERRADA" in _cods_2608(
        "SKHY: 21,18% acima da SMA50.")


# ── a CONTAGEM de dias, com a data certa ──────────────────────────────────

def test_data_certa_com_conta_errada():
    """"INTC: Earnings 63 dias (22/out)" — a data confere e a conta não.
    De 26/08 a 22/10 são 57 dias. EARNINGS_DATE_MISMATCH só olha a data,
    então este erro passava inteiro."""
    assert "DIAS_ATE_EARNINGS_ERRADO" in _cods_2608(
        "INTC: Earnings 63 dias (22/out), sem catalisador próximo.")


@pytest.mark.parametrize("texto", [
    "INTC: Earnings 57 dias (22/out), sem catalisador próximo.",
    "ARM: Earnings 70 dias (4/nov), sem catalisador próximo.",
    # O texto pode contar em pregões e o snapshot em dias corridos.
    "INTC: Earnings 59 dias, sem catalisador próximo.",
])
def test_contagem_certa_ou_dentro_da_folga_passa(texto):
    assert "DIAS_ATE_EARNINGS_ERRADO" not in _cods_2608(texto)


# ── reason_code de tendência contra a SMA50 ───────────────────────────────

def _bloco(tk, codes, pct=None):
    snap = dict(_SNAP_2608)
    if pct is not None:
        snap = {**snap, "technicals": {**snap["technicals"],
                tk: {"rsi": 50, "rsi_date": "2026-08-26", "pct_above_sma50": pct}}}
    b = {"tickers": [{"ticker": tk, "action": "MANTER", "confidence": 0.5,
                      "reason_codes": codes}]}
    return [i.code for i in validar_bloco_estruturado(b, snap).issues]


def test_tendencia_alta_com_preco_muito_abaixo_da_sma50():
    """SKHY saiu com TENDENCIA_ALTA no bloco enquanto a prosa do mesmo
    veredito dizia "-21,18% abaixo SMA50 — correção em progresso". O rótulo
    é o que a máquina lê."""
    assert "BLOCO_REASON_CONTRADITO" in _bloco("SKHY", ["TENDENCIA_ALTA"])


def test_tendencia_baixa_com_preco_muito_acima_da_sma50():
    assert "BLOCO_REASON_CONTRADITO" in _bloco("ARM", ["TENDENCIA_BAIXA"], pct=12.0)


@pytest.mark.parametrize("tk,codes,pct", [
    ("SKHY", ["TENDENCIA_BAIXA"], None),
    ("ARM", ["TENDENCIA_ALTA"], 12.0),
    # Perto da média não há tendência a declarar, e exigir uma seria inventar.
    ("ARM", ["TENDENCIA_ALTA"], -2.0),
    ("ARM", ["TENDENCIA_BAIXA"], 2.0),
])
def test_tendencia_coerente_ou_na_faixa_morta_passa(tk, codes, pct):
    assert "BLOCO_REASON_CONTRADITO" not in _bloco(tk, codes, pct)


# ═══ Fear & Greed fixado no snapshot ══════════════════════════════════════
#
# O índice era buscado DUAS VEZES por caminhos independentes:
# `tools.get_fear_greed_index()` quando o agente chamava a ferramenta, e
# `get_macro.py::fear_greed()` quando a tela pedia /api/macro. Dois relógios
# sobre um índice que anda intradia -- em 26/08/2026 o texto saiu com 57,6 e
# o painel mostrou 57,3, e não havia como saber qual era "o" número.
#
# Fixando no snapshot, o valor entra no prompt como fato verificado, o texto
# passa a citar ESTE número, e só então faz sentido conferi-lo.

_SNAP_FG = {
    "as_of": "2026-08-26", "quotes": {}, "earnings": {}, "technicals": {},
    "sentimento": {"score": 57.3, "rating_pt": "ganância",
                   "lido_em": "2026-08-26T10:15:00-03:00"},
}


def test_o_valor_fixado_chega_ao_prompt_como_fato():
    """`prompt_block()` renderiza os sinais sob "use estes fatos, nao
    recalcule" -- exatamente o contrato aqui: o modelo cita ESTE número em
    vez de chamar a ferramenta e trazer outra leitura."""
    bloco = validate_snapshot(dict(_SNAP_FG)).prompt_block()
    assert "57.3" in bloco
    assert "ganância" in bloco
    assert "10:15" in bloco, "sem carimbo de hora não é reproduzível"


def test_sem_sentimento_no_snapshot_nao_ha_fato_inventado():
    sem = {k: v for k, v in _SNAP_FG.items() if k != "sentimento"}
    assert "Fear" not in validate_snapshot(dict(sem)).prompt_block()


def _cods_fg(texto, snap=None):
    return sorted({i.code
                   for i in lint_veredito(texto, dict(snap or _SNAP_FG)).issues})


@pytest.mark.parametrize("texto", [
    "Sentimento do mercado em 57,6 (ganância moderada).",
    "Fear & Greed em 62 (ganância) sinaliza risco de reversão.",
])
def test_score_diferente_do_fixado_e_erro(texto):
    assert "SENTIMENTO_ERRADO" in _cods_fg(texto)


@pytest.mark.parametrize("texto", [
    "Sentimento do mercado em 57,3 (ganância moderada).",
    "Fear & Greed em 57,3, sem extremos que sinalizem reversão.",
    # Outro momento não contradiz o de hoje.
    "Fear & Greed uma semana atrás estava em 45.",
    "O Fear & Greed do mês passado marcava 38.",
    # Nenhuma menção ao índice.
    "Curva 10y-2y em +0,47%, CPI em 332.8.",
])
def test_score_certo_ou_historico_passa(texto):
    assert "SENTIMENTO_ERRADO" not in _cods_fg(texto)


def test_sem_valor_fixado_a_checagem_se_cala():
    """Antes de fixar, qualquer diferença era deriva intradia e não erro --
    a checagem não pode existir sem o valor único."""
    sem = {k: v for k, v in _SNAP_FG.items() if k != "sentimento"}
    assert "SENTIMENTO_ERRADO" not in _cods_fg(
        "Sentimento do mercado em 57,6.", sem)


def test_o_snapshot_do_veredito_fixa_o_sentimento():
    """Amarra por leitura de fonte: valor que existe no validador mas ninguém
    põe no snapshot é o mesmo que não existir."""
    import pathlib
    from agent import agent as gerador
    fonte = pathlib.Path(gerador.__file__).read_text(encoding="utf-8")
    codigo = "\n".join(l for l in fonte.splitlines()
                       if not l.strip().startswith("#"))
    assert "_sentimento_do_snapshot" in codigo
    assert 'snapshot["sentimento"]' in codigo


# ═══ Veredito de 26/08/2026 11:54 — o dado citado x o dado do snapshot ════
#
# Segunda geração do dia. O erro de prazo relativo SUMIU (a correção pegou),
# o Fear & Greed bateu exato (57,6/57,3 de manhã contra 56,3/56,3 agora --
# confirmando que a diferença anterior era deriva entre duas leituras), e
# apareceram três famílias novas.

_SNAP_1154 = {
    "as_of": "2026-08-26",
    "quotes": {"ARM": {"price": 240.77}, "INTC": {"price": 86.61},
               "BABA": {"price": 121.08}, "WOLF": {"price": 26.57},
               "SKHY": {"price": 159.68}},
    "technicals": {
        "ARM": {"rsi": 38.92, "rsi_date": "2026-08-26", "pct_above_sma50": -18.1},
        "BABA": {"rsi": 48.9, "rsi_date": "2026-08-26", "pct_above_sma50": 5.5},
        "WOLF": {"rsi": 44.2, "rsi_date": "2026-08-26", "pct_above_sma50": -21.4},
    },
    "earnings": {"ARM": "2026-11-04"},
}


def _cods_1154(texto):
    return sorted({i.code
                   for i in lint_veredito(texto, dict(_SNAP_1154)).issues})


# ── a janela por ticker: dois furos que a segmentação fecha ───────────────

def test_o_ponto_decimal_nao_corta_mais_o_trecho_do_ticker():
    """A janela era `{tk}[^.\\n]{{0,120}}` -- e "$121.08" tem PONTO. Num
    veredito com decimal americano (o de 11:54 veio todo assim) a janela
    morria no meio do preço e as checagens por ticker viravam letra morta."""
    from agent.veredito_validator import _segmentos_por_ticker
    seg = _segmentos_por_ticker(
        "BABA: preço $121.08, ABOVE stop-loss de $126, RSI 31.78 bearish.",
        ["BABA"])["BABA"][0]
    assert "31.78" in seg, "o trecho tem que chegar ao fim da frase"


def test_o_trecho_alcanca_o_que_vem_tres_frases_depois():
    """Tamanho fixo não alcançava: o bullet do ARM cita a data de earnings
    bem depois do preço, e a janela nunca chegava lá -- por isso o
    EARNINGS_DATE_MISMATCH não disparou em produção."""
    assert "EARNINGS_DATE_MISMATCH" in _cods_1154(
        "ARM: Stop-loss em $275, preço atual $240.77 (BREACHED). RSI 38.92, "
        "MACD bearish. Earnings vem apenas 11/nov — sem catalisador.")


def test_a_fronteira_de_linha_separa_os_papeis():
    """Numa linha com quatro tickers, cada um fica com o trecho da sua
    menção até a do próximo."""
    from agent.veredito_validator import _segmentos_por_ticker
    segs = _segmentos_por_ticker(
        "MRVL mantém rally, SKHY em $159.68 com EMA bullish. ADI beat "
        "ontem, RSI 46.69. WOLF em $65.48, RSI 51.52 neutro.",
        ["MRVL", "SKHY", "ADI", "WOLF"])
    assert "159.68" in segs["SKHY"][0] and "159.68" not in segs["ADI"][0]
    assert "65.48" in segs["WOLF"][0] and "65.48" not in segs["ADI"][0]


# ── RSI citado x snapshot ─────────────────────────────────────────────────

@pytest.mark.parametrize("texto,tk", [
    ("BABA: RSI 31.78, MACD bearish.", "BABA"),
    ("WOLF em $26.57, RSI 51.52 neutro.", "WOLF"),
])
def test_rsi_citado_fora_do_snapshot_e_erro(texto, tk):
    """Seis dos oito tickers vieram com o RSI certo e dois com número de
    lugar nenhum. RSI_STALE só olhava a DATA do indicador."""
    assert any(i.code == "RSI_CITADO_ERRADO" and i.ticker == tk
               for i in lint_veredito(texto, dict(_SNAP_1154)).issues)


@pytest.mark.parametrize("texto", [
    "ARM: RSI 38.92, MACD bearish.",
    "BABA: RSI 48.9 neutro.",
    # Limiar é regra, não afirmação sobre o número de hoje.
    "ARM está em capitulação (RSI <40, preço abaixo da SMA50).",
    "WOLF: RSI abaixo de 50 sugere fraqueza.",
])
def test_rsi_certo_ou_limiar_passa(texto):
    assert "RSI_CITADO_ERRADO" not in _cods_1154(texto)


# ── preço citado x snapshot ───────────────────────────────────────────────

def test_preco_citado_muito_fora_e_erro():
    """"WOLF em $65.48" num dia em que o papel negociava a $26,57. Um preço
    errado envenena tudo que vem depois: distância à média, stop, tese."""
    achados = [i for i in lint_veredito("WOLF em $65.48, RSI 44.2.",
                                        dict(_SNAP_1154)).issues
               if i.code == "PRECO_CITADO_ERRADO"]
    assert achados and "146%" in achados[0].message


@pytest.mark.parametrize("texto", [
    # O stop e o alvo não são a cotação — e o "em $" casa nos dois.
    "ARM: Stop-loss em $275, preço atual $240.77 (BREACHED).",
    "ARM: alvo de $300, take-profit em $290, preço atual $240.77.",
    "WOLF: máxima de 52 semanas em $80.82; preço $26.57.",
    "SKHY em $159.68 com EMA bullish.",
])
def test_valor_de_nivel_nao_e_confundido_com_cotacao(texto):
    assert "PRECO_CITADO_ERRADO" not in _cods_1154(texto)


# ── o lado do nível, agora também em inglês ───────────────────────────────

@pytest.mark.parametrize("texto", [
    "BABA: Preço $121.08, ABOVE stop-loss de $126 mas -5.0% em 1D.",
    "O papel a $380, below the stop de $370.",
    "Preço $121.08, above the support de $126.",
])
def test_lado_invertido_em_ingles_tambem_cai(texto):
    """O MESMO erro do BABA voltou na segunda geração do dia, agora escrito
    em inglês no meio da prosa em português."""
    assert "NIVEL_LADO_INVERTIDO" in _cods_1154(texto)


@pytest.mark.parametrize("texto", [
    "BABA: Preço $131.08, ABOVE stop-loss de $126.",
    "BABA: Preço $121.08, below stop-loss de $126.",
])
def test_lado_coerente_em_ingles_passa(texto):
    assert "NIVEL_LADO_INVERTIDO" not in _cods_1154(texto)


# ── a data de earnings que o texto ATRIBUI ─────────────────────────────────
#
# Incidente real (Veredito de 26/08/2026 13:30), publicado com a caixa vazia.
# O parágrafo do INTC dizia:
#
#   "...com plano de saída recomendando monitoramento até 22/out; ... e
#    carregar por 57 dias adicionais sem catalisador visível (próximos
#    earnings em 24/11) é risco de oportunidade perdida"
#
# O painel diz INTC 22/10. Duas cegueiras somadas deixaram passar:
#
#   1. `_DATE_PT` só casava `dd/mês` -- "24/11" era invisível.
#   2. A checagem usava `.search()`, a PRIMEIRA data do trecho. "22/out"
#      casava com o painel, dava por conferido, e "24/11" nunca era olhado.
#
# A segunda é a pior: transforma o acerto num álibi para o erro ao lado.

_SNAP_INTC = {
    "as_of": "2026-08-26",
    "quotes": {"INTC": {"price": 86.94, "as_of": "2026-08-26"}},
    "technicals": {},
    "earnings": {"INTC": "2026-10-22"},
}


def _cods_intc(texto):
    return [i.code for i in lint_veredito(texto, _SNAP_INTC).issues]


def test_o_incidente_da_data_numerica_ao_lado_da_certa():
    achados = _cods_intc(
        "INTC: plano de saída recomendando monitoramento até 22/out; e "
        "carregar sem catalisador (próximos earnings em 24/11) é risco.")
    assert "EARNINGS_DATE_MISMATCH" in achados


@pytest.mark.parametrize("frase,cai", [
    # A data que o texto pendura em earnings, nas duas grafias e nas duas ordens.
    ("INTC: earnings em 24/11 sem catalisador antes.", True),
    ("INTC: earnings em 24/nov sem catalisador antes.", True),
    ("INTC: earnings em 24/11/2026 sem catalisador antes.", True),
    ("INTC: 24/11, data do próximo balanço.", True),
    # A data CERTA, nas mesmas grafias.
    ("INTC: earnings em 22/10 é o próximo catalisador.", False),
    ("INTC: earnings em 22/out é o próximo catalisador.", False),
    ("INTC: 22/out, data do próximo balanço.", False),
    # Data de OUTRA coisa, separada por quebra dura de oração. Sem esta
    # guarda a janela pulava o ponto-e-vírgula e pendurava em earnings uma
    # data que o texto atribuiu ao plano de saída.
    ("INTC: monitoramento até 24/11; o balanço muda a tese.", False),
    # Fração perto de preço em formato americano -- `4/5` não é data, e o
    # ponto de "$507.66" não pode cortar a janela nem criar uma.
    ("INTC: earnings distante; o preço $507.66 fica 4/5 do caminho.", False),
])
def test_data_de_earnings_par_a_par(frase, cai):
    assert ("EARNINGS_DATE_MISMATCH" in _cods_intc(frase)) is cai


def test_uma_data_certa_nao_absolve_a_errada():
    """O ponto do incidente: as DUAS são olhadas."""
    achados = [i for i in lint_veredito(
        "INTC: earnings em 22/out confirmado. INTC: earnings em 24/11 também.",
        _SNAP_INTC).issues if i.code == "EARNINGS_DATE_MISMATCH"]
    assert len(achados) == 1, "só a errada aponta, e ela aponta"
    assert "24/11" in achados[0].message


# ── o bloco contra o Plano de Saída ────────────────────────────────────────
#
# Mesmo veredito de 26/08/2026. O bloco saiu com ARM e INTC em MANTER
# enquanto o painel Plano de Saída dizia, para os dois, "Vender imediatamente
# -- stop-loss acionado", vencido havia 6 dias. Nenhum declarou
# PLANO_DE_SAIDA. E SKHY, sem item no plano, declarou.
#
# A checagem NÃO proíbe contrariar o plano -- o mercado muda. Proíbe
# contrariar em SILÊNCIO: quem lê a tabela vê MANTER e não fica sabendo que
# há uma ordem de venda vencida três painéis abaixo, na mesma tela.

_SNAP_PLANO = {
    "as_of": "2026-08-26",
    "quotes": {"ARM": {}, "INTC": {}, "SKHY": {}, "NVDA": {}},
    "technicals": {}, "earnings": {},
    "plano_de_saida": {
        "ARM": [{"acao": "Vender imediatamente — stop-loss acionado",
                 "data_alvo": "2026-08-20"}],
        "INTC": [{"acao": "Vender imediatamente — stop-loss acionado",
                  "data_alvo": "2026-08-20"}],
        "NVDA": [{"acao": "HOLD até earnings 26/ago — reavalie pós-abertura",
                  "data_alvo": "2026-08-26"}],
    },
}


def _cods_plano(itens):
    return [(i.code, i.ticker) for i in
            validar_bloco_estruturado({"tickers": itens}, _SNAP_PLANO).issues]


def _item(tk, acao, codes):
    return {"ticker": tk, "action": acao, "confidence": 0.5, "reason_codes": codes}


def test_o_incidente_do_bloco_contra_o_plano():
    achados = _cods_plano([
        _item("ARM", "MANTER", ["TENDENCIA_BAIXA"]),
        _item("INTC", "MANTER", ["TENDENCIA_BAIXA"]),
        _item("SKHY", "MANTER", ["PLANO_DE_SAIDA"]),
        _item("NVDA", "AGUARDAR", ["EARNINGS_PROXIMO"]),
    ])
    assert ("BLOCO_CONTRA_PLANO", "ARM") in achados
    assert ("BLOCO_CONTRA_PLANO", "INTC") in achados
    assert ("BLOCO_PLANO_SEM_ITEM", "SKHY") in achados
    assert not [a for a in achados if a[1] == "NVDA"], \
        "HOLD até earnings é acompanhamento, não ordem de venda"


@pytest.mark.parametrize("acao,codes,cai", [
    # Declarar torna a divergência consciente -- mesma isenção que
    # RISCO_CORRELACAO dá à compra do par correlacionado.
    ("MANTER", ["PLANO_DE_SAIDA", "TENDENCIA_BAIXA"], False),
    # Obedecer também passa, claro.
    ("VENDER", ["TENDENCIA_BAIXA"], False),
    # REDUZIR cumpre um plano de saída: vender parte não é contrariar.
    ("REDUZIR", ["TENDENCIA_BAIXA"], False),
    # Contrariar calado é o caso.
    ("MANTER", ["TENDENCIA_BAIXA"], True),
    ("AGUARDAR", ["VOLUME_FRACO"], True),
    ("COMPRAR", ["MACRO_FAVORAVEL"], True),
])
def test_plano_de_saida_par_a_par(acao, codes, cai):
    achados = _cods_plano([_item("ARM", acao, codes)])
    assert (("BLOCO_CONTRA_PLANO", "ARM") in achados) is cai


def test_item_de_acompanhamento_nao_e_ordem_de_venda():
    """"Monitorar", "aguardar earnings" e "reavaliar" são acompanhamento. Se
    qualquer item do plano contasse, todo ticker com plano viraria apontamento
    e a checagem morreria de ruído no primeiro dia."""
    snap = {**_SNAP_PLANO, "plano_de_saida": {
        "ARM": [{"acao": "Monitorar suporte em $250", "data_alvo": "2026-09-01"}]}}
    achados = [i.code for i in validar_bloco_estruturado(
        {"tickers": [_item("ARM", "MANTER", ["TENDENCIA_BAIXA"])]}, snap).issues]
    assert "BLOCO_CONTRA_PLANO" not in achados


def test_sem_plano_no_snapshot_nao_ha_checagem():
    """Ausência é ausência -- mesma regra do capex e do fôlego. Sem o painel,
    apontar seria acusar a partir de dado que não foi lido."""
    snap = {k: v for k, v in _SNAP_PLANO.items() if k != "plano_de_saida"}
    achados = [i.code for i in validar_bloco_estruturado(
        {"tickers": [_item("ARM", "MANTER", ["TENDENCIA_BAIXA"]),
                     _item("SKHY", "MANTER", ["PLANO_DE_SAIDA"])]}, snap).issues]
    assert "BLOCO_CONTRA_PLANO" not in achados
    assert "BLOCO_PLANO_SEM_ITEM" not in achados


def test_item_ja_vendido_nao_e_ordem_em_aberto():
    """O snapshot só carrega `pending` (ver `_plano_de_saida_do_snapshot`),
    mas a checagem não pode depender disso: lista vazia é o mesmo que nenhum
    item."""
    snap = {**_SNAP_PLANO, "plano_de_saida": {"ARM": []}}
    achados = [i.code for i in validar_bloco_estruturado(
        {"tickers": [_item("ARM", "MANTER", ["TENDENCIA_BAIXA"])]}, snap).issues]
    assert "BLOCO_CONTRA_PLANO" not in achados


# ── o texto que nega o dado do ticker ───────────────────────────────────────
#
# Visto em produção DUAS vezes (25 e 26/08/2026), sempre no WOLF. Na segunda:
#
#   "WOLF: Dados técnicos limitados no painel. RVOL histórico reflete baixa
#    liquidez. A posição está marcada como 'monitorar'; sem mudança
#    estrutural visível."
#
# enquanto o painel Técnicos da MESMA tela mostrava "WOLF RSI 44 · Subindo
# 5.7% hoje" -- o único ticker do dia com sinal destacado.
#
# Mesmo defeito que `ANALISE_NEGA_DADO_PRESENTE` pega na análise rápida:
# ninguém conferia as afirmações do texto sobre a DISPONIBILIDADE do dado, só
# sobre o valor dele. Negar dado presente é pior que omitir -- quem lê "dados
# limitados" para de procurar, e desconta a posição inteira por uma escassez
# que não existe.

_SNAP_WOLF = {
    "as_of": "2026-08-26",
    "quotes": {"WOLF": {"price": 26.57, "as_of": "2026-08-26",
                        "change_percent": 5.7}},
    "technicals": {"WOLF": {"rsi": 44.2, "rsi_date": "2026-08-26",
                            "pct_above_sma50": -21.4}},
    "earnings": {},
}


def _cods_wolf(texto, snap=None):
    return [i.code for i in lint_veredito(texto, snap or _SNAP_WOLF).issues]


def test_o_incidente_do_wolf():
    achados = [i for i in lint_veredito(
        "WOLF: Dados técnicos limitados no painel. A posição segue em "
        "monitoramento.", _SNAP_WOLF).issues
        if i.code == "DADO_DO_TICKER_NEGADO"]
    assert len(achados) == 1
    assert achados[0].ticker == "WOLF"
    assert "RSI" in achados[0].message, "a mensagem tem que nomear o que veio"


@pytest.mark.parametrize("frase", [
    "WOLF: Dados técnicos limitados no painel.",
    "WOLF: indicadores indisponíveis nesta leitura.",
    "WOLF: sem dados no painel para este ticker.",
    "WOLF: métricas ausentes, leitura prejudicada.",
    "WOLF: informações não disponíveis para o papel.",
])
def test_negar_dado_presente_cai(frase):
    assert "DADO_DO_TICKER_NEGADO" in _cods_wolf(frase)


# ── o que esta checagem NÃO aponta, de propósito ────────────────────────────
#
# As duas ocorrências do WOLF traziam junto uma frase que PARECE contradita
# pelo painel e não é. Apontar as duas seria trocar um achado real por dois
# palpites -- e o custo do palpite é o leitor aprender a ignorar a caixa.

@pytest.mark.parametrize("frase", [
    # "Estrutura" é tendência e níveis. Um pregão de +5,7% não muda estrutura,
    # e um analista cuidadoso defende essa frase.
    "WOLF: sem mudança estrutural visível; a posição segue em monitoramento.",
    # Urgência não é magnitude -- a ocorrência de 25/08.
    "WOLF: sem movimento urgente hoje.",
    # Liquidez, não disponibilidade: o dado existe e está dizendo que é fino.
    "WOLF: o volume está limitado, refletindo baixa liquidez.",
    # Afirmar o dado, obviamente, passa.
    "WOLF: RSI 44,2 e -21,4% abaixo da SMA50.",
])
def test_frase_defensavel_passa(frase):
    assert "DADO_DO_TICKER_NEGADO" not in _cods_wolf(frase)


def test_sem_technicals_a_frase_e_verdade():
    """Quando o snapshot REALMENTE não tem o ticker, dizer que falta dado é
    correto -- e é o que o prompt manda fazer."""
    snap = {**_SNAP_WOLF, "technicals": {}}
    assert "DADO_DO_TICKER_NEGADO" not in _cods_wolf(
        "WOLF: Dados técnicos limitados no painel.", snap)


def test_a_negacao_de_um_ticker_nao_acusa_o_outro():
    """O trecho de cada ticker vai da menção dele até a do próximo (ver
    `_segmentos_por_ticker`). Sem isso, uma frase sobre WOLF derrubaria BABA
    por vizinhança."""
    snap = {**_SNAP_WOLF,
            "quotes": {**_SNAP_WOLF["quotes"], "BABA": {"price": 121.0, "as_of": "2026-08-26"}},
            "technicals": {**_SNAP_WOLF["technicals"],
                           "BABA": {"rsi": 48.9, "rsi_date": "2026-08-26"}}}
    achados = [i.ticker for i in lint_veredito(
        "BABA: RSI 48,9, dentro da faixa. WOLF: dados técnicos limitados.",
        snap).issues if i.code == "DADO_DO_TICKER_NEGADO"]
    assert achados == ["WOLF"]


# ── os achados do Veredito chegam à TELA ───────────────────────────────────
#
# Descoberto lendo um veredito real de ARM (26/08/2026 15:29), publicado com
# a caixa vazia e dois problemas dentro. `run_veredito` devolvia só o texto:
# os achados iam para o stderr e para o retry, e paravam aí.
#
#   um AVISO nunca disparava retry, então nunca chegava a lugar nenhum
#   um ERRO que sobrevivesse ao retry era publicado sem marca
#
# A tela de Análise Rápida já mostrava os apontamentos dela. O Veredito não
# mostrava nenhum -- e foi por isso que TODA geração lida naquele dia apareceu
# "limpa" enquanto tinha erro dentro. As checagens existiam e ninguém as via.

def test_achados_viram_markdown_para_a_tela():
    rep = ValidationReport()
    rep.add("ERROR", "BLOCO_EARNINGS_NAO_ESTA_PROXIMO",
            "declara EARNINGS_PROXIMO, mas o balanço é em 70 dia(s).", ticker="ARM")
    rep.add("WARN", "BLOCO_REASON_DESCONHECIDO",
            'reason_code "RSI_NEUTRO" fora do vocabulário.', ticker="ARM")
    bloco = rep.bloco_para_a_tela()
    assert "2 problema(s)" in bloco
    assert "BLOCO_EARNINGS_NAO_ESTA_PROXIMO" in bloco
    assert "RSI_NEUTRO" in bloco, "o AVISO também aparece -- era ele que sumia"
    assert "**[ERRO]**" in bloco and "**[AVISO]**" in bloco
    assert bloco.lstrip().startswith("---"), "separado do veredito por uma régua"


def test_erro_vem_antes_de_aviso():
    """Quem lê para de ler; o que exige ação vai primeiro."""
    rep = ValidationReport()
    rep.add("WARN", "A", "aviso")
    rep.add("ERROR", "B", "erro")
    bloco = rep.bloco_para_a_tela()
    assert bloco.index("`B`") < bloco.index("`A`")


def test_veredito_limpo_nao_ganha_bloco_nenhum():
    """Uma régua e um cabeçalho dizendo "0 problemas" seriam ruído em toda
    geração boa."""
    assert ValidationReport().bloco_para_a_tela() == ""


def test_sinais_nao_sao_apontamentos():
    """`signals` alimenta o PROMPT (fatos para o modelo não recalcular), não a
    tela. Misturar os dois transformaria dado auxiliar em acusação."""
    rep = ValidationReport()
    rep.add("WARN", "FADE", "o papel abriu em alta e devolveu", signal=True)
    assert rep.bloco_para_a_tela() == ""


# ── EARNINGS_PROXIMO declarado com o balanço longe ─────────────────────────
#
# Mesmo veredito de ARM. O bloco declarou EARNINGS_PROXIMO enquanto a PRÓPRIA
# PROSA dizia "Earnings em 70 dias (04/11/2026) -- fora da zona imediata" e o
# painel dizia "em 70d".
#
# `EARNINGS_PROXIMO_DIAS` só EXIGIA o código numa compra às vésperas; nada
# impedia declará-lo a dois meses. E a razão inflada é pior que a ausente:
# empresta urgência a uma decisão que não tem nenhuma.

def _snap_earnings(dias):
    d = (date(2026, 8, 26) + timedelta(days=dias)).isoformat()
    return {"as_of": "2026-08-26", "quotes": {"ARM": {}}, "technicals": {},
            "earnings": {"ARM": d}}


def _cods_earn(dias, codes, acao="AGUARDAR"):
    item = {"ticker": "ARM", "action": acao, "confidence": 0.55,
            "reason_codes": codes}
    return [i.code for i in validar_bloco_estruturado(
        {"tickers": [item]}, _snap_earnings(dias)).issues]


def test_o_incidente_do_arm():
    assert "BLOCO_EARNINGS_NAO_ESTA_PROXIMO" in _cods_earn(
        70, ["TENDENCIA_BAIXA", "VOLUME_FRACO", "EARNINGS_PROXIMO"])


@pytest.mark.parametrize("dias,cai", [
    (0, False), (1, False), (7, False), (14, False),
    (30, False),   # a folga é generosa: "próximo" é julgamento
    (31, True), (70, True), (200, True),
])
def test_a_folga_do_proximo(dias, cai):
    assert ("BLOCO_EARNINGS_NAO_ESTA_PROXIMO" in
            _cods_earn(dias, ["EARNINGS_PROXIMO"])) is cai


def test_nao_declarar_a_70_dias_e_o_certo():
    assert "BLOCO_EARNINGS_NAO_ESTA_PROXIMO" not in _cods_earn(
        70, ["TENDENCIA_BAIXA"])


def test_sem_data_de_earnings_nao_ha_o_que_conferir():
    snap = {"as_of": "2026-08-26", "quotes": {"ARM": {}}, "technicals": {},
            "earnings": {}}
    item = {"ticker": "ARM", "action": "AGUARDAR", "confidence": 0.5,
            "reason_codes": ["EARNINGS_PROXIMO"]}
    achados = [i.code for i in validar_bloco_estruturado(
        {"tickers": [item]}, snap).issues]
    assert "BLOCO_EARNINGS_NAO_ESTA_PROXIMO" not in achados


def test_o_veto_de_compra_continua_valendo():
    """A checagem nova não pode ter afrouxado a antiga: comprar às vésperas
    SEM declarar continua sendo erro."""
    assert "BLOCO_COMPRA_SEM_VETO_DECLARADO" in _cods_earn(
        1, ["TENDENCIA_ALTA"], acao="COMPRAR")
