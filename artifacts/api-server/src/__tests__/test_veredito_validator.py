"""
Testes de agent/veredito_validator.py -- regressão dos erros encontrados na
análise manual do Veredito do Dia de 31/07/2026 (AVGO com sinal trocado,
SKHY com snapshot de datas misturadas, RSI do ARM defasado, dia da semana
errado, earnings fantasma da SMCI).

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_veredito_validator.py -v
(conftest.py no mesmo diretório já cuida do sys.path)
"""

from agent.veredito_validator import lint_veredito, validate_snapshot

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
