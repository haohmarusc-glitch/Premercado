"""
Testes de consensus_report.py -- reconciliação de rótulo entre provedores.

Cobre só a lógica PURA (extração de rótulo, votação por maioria, montagem do
relatório final) com textos de relatório sintéticos -- nada aqui chama LLM de
verdade (writer_results é sempre construído à mão nos testes).

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_consensus_report.py -v
(conftest.py no mesmo diretório já cuida do sys.path)
"""
import pytest

from agent import consensus_report as cr


def _relatorio(secoes: dict[str, str]) -> str:
    """Monta um texto de relatório com uma seção '### TICKER\\n\\nROTULO ...'
    por entrada de `secoes` (ticker -> rótulo/emoji)."""
    partes = ["## Contexto Macro\n\nFear&Greed 63, tudo calmo."]
    for ticker, rotulo in secoes.items():
        partes.append(f"### {ticker}\n\n{rotulo} — justificativa qualquer.\nMais texto da seção.")
    return "\n\n".join(partes)


class TestExtractLabels:
    def test_extrai_rotulo_de_cada_ticker(self):
        texto = _relatorio({"NVDA": "🟢", "SMCI": "🟡", "ARM": "🔴"})
        labels = cr._extract_labels(texto, ["NVDA", "SMCI", "ARM"])
        assert labels == {"NVDA": "🟢", "SMCI": "🟡", "ARM": "🔴"}

    def test_ticker_ausente_do_texto_vira_none(self):
        texto = _relatorio({"NVDA": "🟢"})
        labels = cr._extract_labels(texto, ["NVDA", "SMCI"])
        assert labels == {"NVDA": "🟢", "SMCI": None}


class TestPrimaryAmong:
    def test_respeita_ordem_de_prioridade(self):
        resultados = [{"provider": "gemini"}, {"provider": "anthropic"}, {"provider": "deepseek"}]
        assert cr._primary_among(resultados)["provider"] == "anthropic"

    def test_pula_pro_proximo_se_o_primario_nao_respondeu(self):
        resultados = [{"provider": "gemini"}, {"provider": "deepseek"}]
        assert cr._primary_among(resultados)["provider"] == "deepseek"

    def test_fallback_pro_primeiro_se_nenhum_prioritario_presente(self):
        resultados = [{"provider": "outro-provedor-qualquer"}]
        assert cr._primary_among(resultados)["provider"] == "outro-provedor-qualquer"


class TestReconcile:
    TICKERS = ["NVDA", "SMCI"]

    def _writer(self, provider, nvda, smci):
        return {"provider": provider, "text": _relatorio({"NVDA": nvda, "SMCI": smci}), "labels": {"NVDA": nvda, "SMCI": smci}, "error": None}

    def test_unanimidade(self):
        writers = [
            self._writer("anthropic", "🟢", "🟡"),
            self._writer("deepseek", "🟢", "🟡"),
            self._writer("gemini", "🟢", "🟡"),
        ]
        r = cr.reconcile(writers, self.TICKERS)
        assert r["per_ticker"]["NVDA"] == {"label": "🟢", "provider": "anthropic", "unanimidade": True}
        assert r["divergencias"] == []

    def test_maioria_dois_de_tres(self):
        writers = [
            self._writer("anthropic", "🟡", "🟢"),
            self._writer("deepseek", "🟡", "🔴"),
            self._writer("gemini", "🔴", "🟢"),
        ]
        r = cr.reconcile(writers, self.TICKERS)
        # NVDA: 🟡🟡🔴 -> maioria 🟡, publicado pelo primeiro que deu 🟡 (anthropic)
        assert r["per_ticker"]["NVDA"]["label"] == "🟡"
        assert r["per_ticker"]["NVDA"]["provider"] == "anthropic"
        assert r["per_ticker"]["NVDA"]["unanimidade"] is False
        # SMCI: 🟢🔴🟢 -> maioria 🟢, publicado pelo primeiro que deu 🟢 (anthropic)
        assert r["per_ticker"]["SMCI"]["label"] == "🟢"
        assert r["per_ticker"]["SMCI"]["provider"] == "anthropic"
        assert set(r["divergencias"]) == {"NVDA", "SMCI"}

    def test_sem_maioria_tres_rotulos_diferentes_usa_primario(self):
        writers = [
            self._writer("gemini", "🔴", "🟢"),
            self._writer("deepseek", "🟡", "🟢"),
            self._writer("anthropic", "🟢", "🟢"),
        ]
        r = cr.reconcile(writers, self.TICKERS)
        # NVDA: 🔴🟡🟢 -- sem maioria, usa o primário (anthropic = 🟢)
        assert r["per_ticker"]["NVDA"]["label"] == "🟢"
        assert r["per_ticker"]["NVDA"]["provider"] == "anthropic"
        assert "NVDA" in r["divergencias"]
        # SMCI: 🟢🟢🟢 -- unânime, não é divergência
        assert r["per_ticker"]["SMCI"]["unanimidade"] is True
        assert "SMCI" not in r["divergencias"]

    def test_ticker_sem_rotulo_em_nenhum_provedor(self):
        writers = [
            {"provider": "anthropic", "text": "## Contexto Macro\n\nsem seções de ticker", "labels": {"NVDA": None, "SMCI": None}, "error": None},
        ]
        r = cr.reconcile(writers, self.TICKERS)
        assert r["per_ticker"]["NVDA"] == {"label": None, "provider": None, "unanimidade": False}
        assert "NVDA" in r["divergencias"]

    def test_dois_provedores_discordam_sem_empate_de_maioria(self):
        """Com só 2 válidos, qualquer discordância já é 'sem maioria' (1 voto
        cada) -- cai no fallback do primário, não trava nem inventa maioria."""
        writers = [
            self._writer("deepseek", "🟡", "🟢"),
            self._writer("gemini", "🔴", "🟢"),
        ]
        r = cr.reconcile(writers, self.TICKERS)
        # NVDA diverge (🟡 vs 🔴) -- primário entre os presentes é deepseek
        # (anthropic não está na lista, deepseek vem antes de gemini na ordem)
        assert r["per_ticker"]["NVDA"]["provider"] == "deepseek"
        assert "NVDA" in r["divergencias"]
        # SMCI unânime entre os 2 presentes
        assert r["per_ticker"]["SMCI"]["unanimidade"] is True


class TestIntroAtePrimeiroTicker:
    def test_extrai_texto_antes_do_primeiro_ticker(self):
        texto = _relatorio({"NVDA": "🟢", "SMCI": "🟡"})
        intro = cr._intro_ate_primeiro_ticker(texto, ["NVDA", "SMCI"])
        assert "Contexto Macro" in intro
        assert "NVDA" not in intro

    def test_vazio_se_nenhum_ticker_encontrado(self):
        texto = "## Contexto Macro\n\nsó isso, sem nenhuma seção de ticker."
        intro = cr._intro_ate_primeiro_ticker(texto, ["NVDA", "SMCI"])
        assert intro == ""


def _dado_ticker(
    ticker: str,
    change_pct: float = 1.0,
    days_until_earnings: int | None = None,
    atm_iv_pct: float | None = None,
    atr_pct: float = 2.0,
    pct_above_sma200: float = 5.0,
    rsi_date: str = "2026-08-11",
    as_of: str = "2026-08-11",
    short_pct_of_float: float = 3.0,
    headlines: list[str] | None = None,
) -> dict:
    return {
        "quote": {"ticker": ticker, "change_pct": change_pct, "as_of": as_of},
        "technicals": {
            "ticker": ticker,
            "rsi_date": rsi_date,
            "atr_pct": atr_pct,
            "pct_above_sma200": pct_above_sma200,
        },
        "options": {"ticker": ticker, "atm_iv_pct": atm_iv_pct, "as_of": as_of},
        "short_interest": {"ticker": ticker, "short_pct_of_float": short_pct_of_float, "squeeze_risk": "baixo"},
        "candles": {},
        "analyst_ratings": {},
        "news": [{"title": t} for t in (headlines or [])],
    }


def _dado_portfolio(tickers_data: dict[str, dict], earnings: list[dict] | None = None) -> dict:
    return {
        "macro": {"earnings_calendar": earnings or []},
        "tickers": tickers_data,
    }


class TestBuildSnapshot:
    def test_monta_snapshot_a_partir_do_dado_coletado(self):
        data = _dado_portfolio(
            {"NVDA": _dado_ticker("NVDA", change_pct=-1.5, headlines=["NVDA processo antitruste nos EUA"])},
            earnings=[{"ticker": "NVDA", "days_until_earnings": 3}],
        )
        snap = cr._build_snapshot(data, ["NVDA"])
        assert snap["quotes"]["NVDA"]["change_pct"] == -1.5
        assert snap["earnings"]["NVDA"] == 3
        assert "NVDA processo antitruste nos EUA" in snap["headlines"]["NVDA"]


class TestApplyGateBackstop:
    TICKERS = ["NVDA", "SMCI"]

    def _reconciled(self, nvda_label, smci_label):
        return {
            "per_ticker": {
                "NVDA": {"label": nvda_label, "provider": "anthropic", "unanimidade": True},
                "SMCI": {"label": smci_label, "provider": "anthropic", "unanimidade": True},
            },
            "divergencias": [],
        }

    def test_corrige_verde_inflado_por_maioria(self):
        """Dois provedores mais fracos concordam em 🟢, mas earnings em 3 dias
        (crítico) proíbe 🟢 -- o gate tem que vencer a maioria."""
        data = _dado_portfolio(
            {"NVDA": _dado_ticker("NVDA"), "SMCI": _dado_ticker("SMCI")},
            earnings=[{"ticker": "NVDA", "days_until_earnings": 3}],
        )
        snap = cr._build_snapshot(data, self.TICKERS)
        reconciled = self._reconciled("🟢", "🟢")
        corrigidos = cr._apply_gate_backstop(reconciled, snap, self.TICKERS)
        assert corrigidos == ["NVDA"]
        assert reconciled["per_ticker"]["NVDA"]["label"] == "🟡"
        assert reconciled["per_ticker"]["NVDA"]["label_original"] == "🟢"
        assert "SMCI" not in corrigidos

    def test_corrige_vermelho_inflado(self):
        """Maioria vota 🔴 mas só há um gate ativo (não sustenta 🔴) --
        rebaixa pro teto real em vez de aceitar o receio da maioria."""
        data = _dado_portfolio({"NVDA": _dado_ticker("NVDA", change_pct=-0.5)})
        snap = cr._build_snapshot(data, ["NVDA"])
        reconciled = self._reconciled("🔴", "🟢")
        corrigidos = cr._apply_gate_backstop(reconciled, snap, ["NVDA"])
        assert corrigidos == ["NVDA"]
        assert reconciled["per_ticker"]["NVDA"]["label"] == "🟡"

    def test_nao_mexe_em_rotulo_ja_correto(self):
        data = _dado_portfolio(
            {"NVDA": _dado_ticker("NVDA", change_pct=1.0), "SMCI": _dado_ticker("SMCI")},
        )
        snap = cr._build_snapshot(data, self.TICKERS)
        reconciled = self._reconciled("🟢", "🟢")
        corrigidos = cr._apply_gate_backstop(reconciled, snap, self.TICKERS)
        assert corrigidos == []
        assert reconciled["per_ticker"]["NVDA"]["label"] == "🟢"

    def test_ticker_sem_rotulo_nao_quebra(self):
        data = _dado_portfolio({"NVDA": _dado_ticker("NVDA")})
        snap = cr._build_snapshot(data, ["NVDA"])
        reconciled = {"per_ticker": {"NVDA": {"label": None, "provider": None, "unanimidade": False}}, "divergencias": ["NVDA"]}
        corrigidos = cr._apply_gate_backstop(reconciled, snap, ["NVDA"])
        assert corrigidos == []


class TestRotularSecao:
    def test_troca_primeiro_rotulo_encontrado(self):
        secao = "### NVDA\n\n🟢 — setup favorável.\nMais texto."
        assert cr._rotular_secao(secao, "🟡").startswith("### NVDA\n\n🟡 —")

    def test_sem_rotulo_no_texto_devolve_inalterado(self):
        secao = "### NVDA\n\nsem rótulo nenhum aqui."
        assert cr._rotular_secao(secao, "🟡") == secao


class TestAssembleFinalReport:
    def _writer(self, provider, nvda, smci):
        return {"provider": provider, "text": _relatorio({"NVDA": nvda, "SMCI": smci}), "labels": {"NVDA": nvda, "SMCI": smci}, "error": None}

    def test_relatorio_unanime_nao_lista_divergencias(self):
        writers = [
            self._writer("anthropic", "🟢", "🟡"),
            self._writer("deepseek", "🟢", "🟡"),
        ]
        reconciled = cr.reconcile(writers, ["NVDA", "SMCI"])
        texto = cr._assemble_final_report("2026-08-11", writers, reconciled, ["NVDA", "SMCI"])
        assert "Divergência entre provedores" not in texto
        assert "### NVDA" in texto and "### SMCI" in texto
        assert "🟢" in texto and "🟡" in texto

    def test_relatorio_com_divergencia_lista_apendice(self):
        writers = [
            self._writer("anthropic", "🟢", "🟡"),
            self._writer("deepseek", "🔴", "🟡"),
        ]
        reconciled = cr.reconcile(writers, ["NVDA", "SMCI"])
        texto = cr._assemble_final_report("2026-08-11", writers, reconciled, ["NVDA", "SMCI"])
        assert "Divergência entre provedores" in texto
        assert "NVDA" in texto.split("Divergência entre provedores")[1]

    def test_rotulo_corrigido_aparece_no_texto_e_no_apendice(self):
        writers = [
            self._writer("anthropic", "🟢", "🟡"),
            self._writer("deepseek", "🟢", "🟡"),
        ]
        reconciled = cr.reconcile(writers, ["NVDA", "SMCI"])
        reconciled["per_ticker"]["NVDA"]["label"] = "🟡"
        reconciled["per_ticker"]["NVDA"]["label_original"] = "🟢"
        reconciled["per_ticker"]["NVDA"]["gate_detalhe"] = "[critico] earnings em 3 dias"
        texto = cr._assemble_final_report("2026-08-11", writers, reconciled, ["NVDA", "SMCI"], corrigidos=["NVDA"])
        assert "Rótulos corrigidos por gate determinístico" in texto
        assert "🟢 → 🟡" in texto
        secao_nvda = texto.split("### NVDA")[1].split("### SMCI")[0]
        assert "🟡" in secao_nvda

    def test_ticker_sem_secao_valida_nao_quebra_montagem(self):
        writers = [
            {"provider": "anthropic", "text": "## Contexto Macro\n\nnada de ticker aqui", "labels": {"NVDA": None, "SMCI": None}, "error": None},
        ]
        reconciled = cr.reconcile(writers, ["NVDA", "SMCI"])
        texto = cr._assemble_final_report("2026-08-11", writers, reconciled, ["NVDA", "SMCI"])
        assert "Nenhum provedor produziu uma seção válida" in texto
