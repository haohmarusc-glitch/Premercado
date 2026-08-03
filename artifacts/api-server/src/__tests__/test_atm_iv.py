"""
IV ATM: descartar contrato sem cotação em vez de tratá-lo como vol zero.

Visto em produção 03/08. A run gravou os sete ativos do dia com `atm_iv_pct`
entre 0,78 e 2,61 -- NVDA saiu com 2,08 quando a IV real fica na casa de
40-50%. A causa é que o yfinance devolve `impliedVolatility` = 0 para contrato
sem cotação, e a média direta dos 3 strikes mais próximos tratava esse zero
como observação real de volatilidade zero.

O efeito de segunda ordem é o que torna isso grave: o gate de IV do relatório
compara `atm_iv_pct >= 32 x atr_pct`. Com NVDA em 2,08 contra um limiar de
121,9, o gate ficou MORTO -- sem nunca falhar, sem log, sem teste vermelho.
Só parou de discriminar.

Rodar: pytest artifacts/api-server/src/__tests__/test_atm_iv.py -v
"""

import pandas as pd
import pytest

from agent.tools import (
    IV_ATM_CONTRATOS,
    IV_ATM_MAX_PCT,
    IV_ATM_MIN_PCT,
    _atm_iv_pct,
)


def cadeia(strikes, ivs) -> pd.DataFrame:
    return pd.DataFrame({"strike": strikes, "impliedVolatility": ivs})


class TestAtmIvPct:
    def test_cadeia_saudavel_devolve_media_dos_vizinhos(self):
        # spot 165 -> vizinhos 160, 165, 170 -> media 0,40 -> 40%
        c = cadeia([155, 160, 165, 170, 175], [0.44, 0.41, 0.40, 0.39, 0.45])
        assert _atm_iv_pct(c, 165.0) == 40.0

    def test_contrato_sem_iv_nao_entra_na_media(self):
        """O 170 está zerado; sem o filtro ele entraria como vizinho imediato
        e a média de 0,41/0,40/0,0 daria 27% em vez dos ~42% reais -- é essa
        diluição, repetida, que produziu o 2,08 do NVDA."""
        c = cadeia([155, 160, 165, 170, 175], [0.44, 0.41, 0.40, 0.0, 0.45])
        # Válidos: 155/160/165/175. Vizinhos de 165 -> 165 (0), 160 (5) e,
        # no empate de distância 10, o 155 vem primeiro: 0,40/0,41/0,44.
        assert _atm_iv_pct(c, 165.0) == pytest.approx(41.67)
        # A diluição que o filtro evita:
        media_com_zero = (0.41 + 0.40 + 0.0) / 3 * 100
        assert media_com_zero < 30.0

    def test_cadeia_quase_toda_zerada_devolve_none(self):
        """Cenário real de 03/08: sem contrato válido suficiente, o honesto é
        dizer 'não sei'. None faz o gate ser PULADO; um número pequeno faria o
        gate rodar contra lixo e nunca disparar."""
        c = cadeia([155, 160, 165, 170, 175], [0.0, 0.0, 0.0, 0.0, 0.42])
        assert _atm_iv_pct(c, 165.0) is None

    def test_exige_minimo_de_contratos_validos(self):
        ivs = [0.0] * 5
        for i in range(IV_ATM_CONTRATOS - 1):
            ivs[i] = 0.40
        c = cadeia([155, 160, 165, 170, 175], ivs)
        assert _atm_iv_pct(c, 165.0) is None

    @pytest.mark.parametrize("iv", [0.0001, 0.02, 0.049])
    def test_resultado_abaixo_da_faixa_vira_none(self, iv):
        """2% de IV ATM não é observação de mercado, é dado quebrado."""
        c = cadeia([160, 165, 170], [iv, iv, iv])
        assert _atm_iv_pct(c, 165.0) is None

    def test_resultado_acima_da_faixa_vira_none(self):
        c = cadeia([160, 165, 170], [6.0, 6.0, 6.0])  # 600%
        assert _atm_iv_pct(c, 165.0) is None

    def test_limites_da_faixa_sao_aceitos(self):
        baixo = cadeia([160, 165, 170], [IV_ATM_MIN_PCT / 100] * 3)
        alto = cadeia([160, 165, 170], [IV_ATM_MAX_PCT / 100] * 3)
        assert _atm_iv_pct(baixo, 165.0) == IV_ATM_MIN_PCT
        assert _atm_iv_pct(alto, 165.0) == IV_ATM_MAX_PCT

    def test_escolhe_os_vizinhos_certos_depois_do_filtro(self):
        """Depois de descartar zeros o índice fica com buracos -- a seleção
        precisa continuar sendo por POSIÇÃO, não por rótulo."""
        c = cadeia(
            [100, 105, 110, 160, 165, 170, 220],
            [0.0, 0.0, 0.0, 0.30, 0.31, 0.32, 0.90],
        )
        # vizinhos de 165 entre os válidos: 160, 165, 170 -> 31%
        assert _atm_iv_pct(c, 165.0) == pytest.approx(31.0)

    def test_spot_ausente_devolve_none(self):
        c = cadeia([160, 165, 170], [0.4, 0.4, 0.4])
        assert _atm_iv_pct(c, None) is None

    def test_cadeia_vazia_devolve_none(self):
        assert _atm_iv_pct(pd.DataFrame({"strike": [], "impliedVolatility": []}), 165.0) is None

    def test_cadeia_sem_as_colunas_devolve_none(self):
        assert _atm_iv_pct(pd.DataFrame({"strike": [160, 165, 170]}), 165.0) is None

    def test_nan_nao_vira_numero(self):
        c = cadeia([155, 160, 165, 170, 175], [float("nan"), 0.41, 0.40, 0.39, 0.45])
        r = _atm_iv_pct(c, 165.0)
        assert r == pytest.approx(40.0)


class TestRegistrarIv:
    """Segunda barreira, no caminho de GRAVAÇÃO (agent.py::_registrar_iv).

    Vale a redundância com _atm_iv_pct porque o custo do erro aqui é diferente:
    linha ruim em iv_history não volta atrás e não dá pra distinguir de uma boa
    depois -- ela contamina o IV Rank de todo dia futuro que olhar pra trás.
    Foi exatamente o que aconteceu em 03/08, antes de existir checagem.
    """

    def _registrar(self, snapshot):
        from agent import agent as a

        a._registrar_iv(snapshot)
        return a.get_last_iv_snapshot()

    def test_grava_iv_plausivel_com_atr(self):
        snap = {
            "options": {"NVDA": {"atm_iv_pct": 45.2}},
            "technicals": {"NVDA": {"atr_pct": 3.81}},
        }
        assert self._registrar(snap) == {"NVDA": {"atm_iv_pct": 45.2, "atr_pct": 3.81}}

    def test_descarta_iv_implausivel(self):
        """Os números exatos da run de 03/08."""
        snap = {
            "options": {
                "NVDA": {"atm_iv_pct": 2.08},
                "SMCI": {"atm_iv_pct": 2.61},
                "SKHY": {"atm_iv_pct": 0.78},
            },
            "technicals": {"NVDA": {"atr_pct": 3.81}},
        }
        assert self._registrar(snap) == {}

    def test_descarta_iv_alta_demais(self):
        snap = {"options": {"X": {"atm_iv_pct": 900.0}}, "technicals": {}}
        assert self._registrar(snap) == {}

    def test_iv_ausente_nao_grava(self):
        snap = {"options": {"X": {"atm_iv_pct": None}}, "technicals": {}}
        assert self._registrar(snap) == {}

    def test_atr_ausente_nao_impede_gravacao(self):
        """A IV é o dado que importa pro rank; o atr_pct é só o proxy de
        transição. SKHY veio sem atr_pct em produção e ainda assim vale
        guardar a IV -- desde que ela seja plausível."""
        snap = {"options": {"SKHY": {"atm_iv_pct": 62.0}}, "technicals": {}}
        assert self._registrar(snap) == {"SKHY": {"atm_iv_pct": 62.0, "atr_pct": None}}

    def test_bool_nao_passa_por_numero(self):
        snap = {"options": {"X": {"atm_iv_pct": True}}, "technicals": {}}
        assert self._registrar(snap) == {}

    def test_run_seguinte_nao_herda_a_anterior(self):
        self._registrar({"options": {"A": {"atm_iv_pct": 40.0}}, "technicals": {}})
        assert self._registrar({"options": {}, "technicals": {}}) == {}
