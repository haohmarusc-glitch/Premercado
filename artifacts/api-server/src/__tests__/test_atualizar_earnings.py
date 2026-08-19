"""
O calendário de earnings não pode piorar quando a coleta falha.

O EARNINGS do radar era digitado à mão e virou overlay automático. A troca só
vale se a falha degradar para o dado embutido em vez de apagá-lo: uma tela sem
calendário é pior que um calendário velho e rotulado, e um calendário que
"funcionou" a partir de um aviso de cota da API é pior que os dois.

Estes testes rodam SEM rede -- a resposta HTTP é dublada. O que eles fixam é o
contrato entre coleta, overlay e módulo, não a data de nenhum papel.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_atualizar_earnings.py -v
"""
import json

import pytest

from agent import atualizar_earnings as ae


CABECALHO = "symbol,name,reportDate,fiscalDateEnding,estimate,currency,timeOfTheDay\r\n"

REDE_BLOQUEADA = "rede bloqueada nos testes -- duble a resposta HTTP"


@pytest.fixture(autouse=True)
def sem_rede(monkeypatch):
    """Bloqueia a PRIMITIVA, não o nome da função deste módulo.

    Mesmo motivo documentado em test_macro_risk_snapshot.py: este ambiente não
    alcança a Alpha Vantage e o CI alcança. Bloquear por nome de função deixa
    passar teste que só verde aqui.
    """
    def _b(*_a, **_k):
        raise RuntimeError(f"SESSION.get: {REDE_BLOQUEADA}")

    monkeypatch.setattr(ae.SESSION, "get", _b)


class _Resposta:
    def __init__(self, texto: str, status: int = 200):
        self.text = texto
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _responde(monkeypatch, texto: str, status: int = 200) -> None:
    monkeypatch.setattr(ae.SESSION, "get", lambda *a, **k: _Resposta(texto, status))
    monkeypatch.setattr(ae, "_api_key", lambda: "chave-de-teste")
    monkeypatch.setattr(ae.provider_health, "consumir_orcamento_diario",
                        lambda *a, **k: True)


# ── leitura do CSV ──────────────────────────────────────────────────────────

def test_mapeia_a_janela_do_pregao():
    """pre-market/post-market são a convenção BO/AC do radar."""
    csv = (CABECALHO
           + "WMT,WALMART,2026-08-20,2026-07-31,0.73,USD,pre-market\r\n"
           + "CRWD,CROWDSTRIKE,2026-08-26,2026-07-31,0.05,USD,post-market\r\n")
    ev = ae.eventos_do_csv(csv)
    assert ev["WMT"]["quando"] == "BO"
    assert ev["CRWD"]["quando"] == "AC"


def test_janela_vazia_vira_none_e_nao_um_chute():
    """A API deixa timeOfTheDay em branco com frequência.

    Chutar "BO" faria o consumidor tratar ausência de dado como informação --
    e a diferença entre reportar antes ou depois do pregão muda a decisão de
    quem carrega a posição para o dia seguinte.
    """
    csv = CABECALHO + "ORCL,ORACLE,2026-09-08,2026-08-31,1.39,USD,\r\n"
    assert ae.eventos_do_csv(csv)["ORCL"]["quando"] is None


def test_lixo_no_campo_quando_tambem_vira_none():
    csv = CABECALHO + "ORCL,ORACLE,2026-09-08,2026-08-31,1.39,USD,durante-o-almoco\r\n"
    assert ae.eventos_do_csv(csv)["ORCL"]["quando"] is None


def test_fica_com_o_evento_mais_proximo():
    """A API pode listar trimestres à frente, em qualquer ordem.

    Pegar o errado adiantaria o calendário em meses sem nada na tela
    denunciando -- o radar só fala do PRÓXIMO earnings.
    """
    csv = (CABECALHO
           + "MU,MICRON,2026-12-20,2026-11-30,1.0,USD,post-market\r\n"
           + "MU,MICRON,2026-09-22,2026-08-31,1.0,USD,post-market\r\n")
    assert ae.eventos_do_csv(csv)["MU"]["data"] == "2026-09-22"


def test_ordem_inversa_da_o_mesmo_resultado():
    csv = (CABECALHO
           + "MU,MICRON,2026-09-22,2026-08-31,1.0,USD,post-market\r\n"
           + "MU,MICRON,2026-12-20,2026-11-30,1.0,USD,post-market\r\n")
    assert ae.eventos_do_csv(csv)["MU"]["data"] == "2026-09-22"


def test_ticker_fora_do_universo_e_descartado():
    """O CSV traz o mercado inteiro; o radar acompanha ~45 papéis."""
    csv = CABECALHO + "ZZZZ,NAO ACOMPANHADA,2026-09-01,2026-08-31,1.0,USD,pre-market\r\n"
    assert ae.eventos_do_csv(csv) == {}


def test_data_ilegivel_nao_derruba_o_lote():
    """Uma linha ruim é dado ausente para aquele ticker, não falha do resto."""
    csv = (CABECALHO
           + "MU,MICRON,nao-e-data,2026-08-31,1.0,USD,post-market\r\n"
           + "WMT,WALMART,2026-08-20,2026-07-31,0.73,USD,pre-market\r\n")
    ev = ae.eventos_do_csv(csv)
    assert "MU" not in ev
    assert ev["WMT"]["data"] == "2026-08-20"


# ── 200 OK que não é dado ───────────────────────────────────────────────────

def test_aviso_json_com_200_vira_erro(monkeypatch):
    """A armadilha central da Alpha Vantage.

    Cota estourada, chave inválida e endpoint premium respondem 200 com JSON.
    Sem esta checagem o csv.DictReader leria o JSON como linha esquisita e
    devolveria zero eventos -- indistinguível de "ninguém reporta nos próximos
    6 meses", e o overlay gravaria o apagamento do calendário.
    """
    _responde(monkeypatch, json.dumps({"Information": "premium endpoint"}))
    saida = ae.atualizar_e_gravar()
    assert saida["ok"] is False
    assert "premium" in saida["erro"]


def test_csv_sem_a_coluna_esperada_vira_erro(monkeypatch):
    _responde(monkeypatch, "isto,nao,e,o,calendario\r\n1,2,3,4,5\r\n")
    saida = ae.atualizar_e_gravar()
    assert saida["ok"] is False
    assert "reportDate" in saida["erro"]


def test_csv_valido_sem_nenhum_ticker_nosso_nao_grava(monkeypatch, tmp_path):
    """Zero eventos com CSV bom é filtro que não casou, não mercado parado.

    Gravar isso apagaria o calendário inteiro no próximo import.
    """
    destino = tmp_path / "earnings.json"
    monkeypatch.setenv("RADAR_EARNINGS_OVERLAY", str(destino))
    _responde(monkeypatch, CABECALHO
              + "ZZZZ,NAO ACOMPANHADA,2026-09-01,2026-08-31,1.0,USD,pre-market\r\n")
    saida = ae.atualizar_e_gravar()
    assert saida["ok"] is False
    assert not destino.exists()


def test_sem_chave_nao_chama_a_rede(monkeypatch):
    monkeypatch.setattr(ae, "_api_key", lambda: "")
    saida = ae.atualizar_e_gravar()
    assert saida["ok"] is False
    assert "ALPHAVANTAGE_API_KEY" in saida["erro"]


def test_cota_esgotada_falha_em_vez_de_chamar(monkeypatch):
    """A cota é compartilhada com o feed de notícias.

    Estourar aqui derrubaria as notícias junto -- trocar uma falha parcial por
    duas é exatamente o que o orçamento existe para impedir.
    """
    monkeypatch.setattr(ae, "_api_key", lambda: "chave-de-teste")
    monkeypatch.setattr(ae.provider_health, "consumir_orcamento_diario",
                        lambda *a, **k: False)
    saida = ae.atualizar_e_gravar()
    assert saida["ok"] is False
    assert "cota" in saida["erro"]


def test_http_500_nao_levanta(monkeypatch):
    """Quem chama é um checker de background: exceção derruba o ciclo."""
    _responde(monkeypatch, "erro do servidor", status=500)
    saida = ae.atualizar_e_gravar()
    assert saida["ok"] is False


# ── gravação e diferenças ───────────────────────────────────────────────────

def test_grava_e_relata_o_que_mudou(monkeypatch, tmp_path):
    destino = tmp_path / "earnings.json"
    monkeypatch.setenv("RADAR_EARNINGS_OVERLAY", str(destino))
    # CRWD com a data que o embutido já tem, MU deslocada.
    atual_crwd = ae.EARNINGS["CRWD"]["data"]
    _responde(monkeypatch, CABECALHO
              + f"CRWD,CROWDSTRIKE,{atual_crwd},2026-07-31,0.05,USD,post-market\r\n"
              + "MU,MICRON,2026-10-01,2026-08-31,1.0,USD,post-market\r\n")
    saida = ae.atualizar_e_gravar()

    assert saida["ok"] is True
    assert saida["confirmados"] == 1
    assert [m["ticker"] for m in saida["mudaram"]] == ["MU"]
    assert saida["mudaram"][0]["para"] == "2026-10-01"
    # Quem não veio na resposta mantém a data embutida -- e aparece no log,
    # porque é o conjunto que continua envelhecendo à mão.
    assert "NVDA" in saida["ausentes"]

    blob = json.loads(destino.read_text(encoding="utf-8"))
    assert blob["fonte"] == "alphavantage_earnings_calendar"
    assert blob["earnings"]["MU"] == {"data": "2026-10-01", "quando": "AC"}
