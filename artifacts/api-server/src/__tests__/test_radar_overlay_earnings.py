"""
O overlay de earnings só pode MELHORAR o calendário embutido.

radar_ia_2026.py aplica o JSON de atualizar_earnings.py por cima do EARNINGS
digitado à mão, no import. A regra que estes testes fixam: overlay ausente,
corrompido ou parcial deixa o embutido valendo -- calendário velho e rotulado
é melhor que tela sem calendário.

O módulo é recarregado a cada caso porque o overlay é aplicado UMA vez, no
import, e ele MUTA o dicionário EARNINGS. Sem o reload, o primeiro caso a
gravar deixaria a alteração visível para todos os seguintes -- e um teste
veria o efeito de outro.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_radar_overlay_earnings.py -v
"""
import importlib
import json

import pytest


def _radar(monkeypatch, blob=None, tmp_path=None, bruto=None):
    """Recarrega radar_ia_2026 com o overlay apontado (ou sem nenhum)."""
    if blob is not None or bruto is not None:
        destino = tmp_path / "earnings.json"
        destino.write_text(bruto if bruto is not None
                           else json.dumps(blob, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setenv("RADAR_EARNINGS_OVERLAY", str(destino))
    else:
        monkeypatch.setenv("RADAR_EARNINGS_OVERLAY", str(tmp_path / "nao-existe.json"))
    # O overlay de correlações também roda no import e busca o caminho de
    # produção; apontá-lo para o vazio mantém este teste sobre uma coisa só.
    monkeypatch.setenv("RADAR_CORR_OVERLAY", str(tmp_path / "nao-existe-corr.json"))
    import agent.radar_ia_2026 as r
    return importlib.reload(r)


@pytest.fixture
def embutido(monkeypatch, tmp_path):
    """O calendário como ele é sem overlay nenhum."""
    return _radar(monkeypatch, tmp_path=tmp_path)


# ── o caminho feliz ─────────────────────────────────────────────────────────

def test_aplica_data_janela_e_procedencia(monkeypatch, tmp_path):
    r = _radar(monkeypatch, tmp_path=tmp_path, blob={
        "atualizado_em": "2026-08-19",
        "earnings": {"MU": {"data": "2026-10-01", "quando": "AC"}},
    })
    assert r.EARNINGS["MU"]["data"] == "2026-10-01"
    assert r.EARNINGS["MU"]["quando"] == "AC"
    # `fonte` é o que permite distinguir data confirmada de data digitada.
    assert r.EARNINGS["MU"]["fonte"] == "alphavantage"
    assert r.EARNINGS_ATUALIZADO_EM == "2026-08-19"
    assert r.EARNINGS_APLICADOS == 1


def test_setor_e_preservado(monkeypatch, tmp_path):
    """O setor é classificação NOSSA -- a API não devolve isso.

    Se o overlay substituísse a entrada em vez de atualizar campos, a linha
    perderia o setor e sumiria do agrupamento da tela.
    """
    r = _radar(monkeypatch, tmp_path=tmp_path, blob={
        "atualizado_em": "2026-08-19",
        "earnings": {"MU": {"data": "2026-10-01", "quando": "AC"}},
    })
    assert r.EARNINGS["MU"]["setor"] == "semis"


def test_ticker_desconhecido_nao_e_criado(monkeypatch, tmp_path):
    """Criar entrada aqui produziria linha sem setor na tela."""
    r = _radar(monkeypatch, tmp_path=tmp_path, blob={
        "atualizado_em": "2026-08-19",
        "earnings": {"ZZZZ": {"data": "2026-10-01", "quando": "BO"}},
    })
    assert "ZZZZ" not in r.EARNINGS
    assert r.EARNINGS_APLICADOS == 0


def test_quem_nao_veio_no_overlay_fica_intacto(monkeypatch, tmp_path, embutido):
    original = dict(embutido.EARNINGS["NVDA"])
    r = _radar(monkeypatch, tmp_path=tmp_path, blob={
        "atualizado_em": "2026-08-19",
        "earnings": {"MU": {"data": "2026-10-01", "quando": "AC"}},
    })
    assert r.EARNINGS["NVDA"] == original


def test_nota_de_divergencia_some_quando_a_fonte_responde(monkeypatch, tmp_path, embutido):
    """Toda `nota` do EARNINGS é especulação sobre a data.

    Manter "fontes divergem: pode ser 01/09" embaixo da data que a Alpha
    Vantage confirmou seria pior que inútil: a tela contradiria o próprio dado.
    """
    assert "nota" in embutido.EARNINGS["CRWD"]  # o embutido de fato tem a nota
    r = _radar(monkeypatch, tmp_path=tmp_path, blob={
        "atualizado_em": "2026-08-19",
        "earnings": {"CRWD": {"data": "2026-08-26", "quando": "AC"}},
    })
    assert "nota" not in r.EARNINGS["CRWD"]


def test_janela_invalida_vira_none_em_vez_de_passar_adiante(monkeypatch, tmp_path):
    r = _radar(monkeypatch, tmp_path=tmp_path, blob={
        "atualizado_em": "2026-08-19",
        "earnings": {"MU": {"data": "2026-10-01", "quando": "meio-do-dia"}},
    })
    assert r.EARNINGS["MU"]["quando"] is None


# ── degradação: o embutido tem que sobreviver ───────────────────────────────

def test_sem_arquivo_mantem_o_embutido(monkeypatch, tmp_path, embutido):
    r = _radar(monkeypatch, tmp_path=tmp_path)
    assert r.EARNINGS["MU"]["data"] == embutido.EARNINGS["MU"]["data"]
    assert r.EARNINGS_ATUALIZADO_EM is None


def test_json_corrompido_mantem_o_embutido(monkeypatch, tmp_path, embutido):
    r = _radar(monkeypatch, tmp_path=tmp_path, bruto="{isto nao e json")
    assert r.EARNINGS["MU"]["data"] == embutido.EARNINGS["MU"]["data"]
    assert r.EARNINGS_ATUALIZADO_EM is None


def test_shape_errado_mantem_o_embutido(monkeypatch, tmp_path, embutido):
    r = _radar(monkeypatch, tmp_path=tmp_path,
               blob={"atualizado_em": "2026-08-19", "earnings": ["MU", "NVDA"]})
    assert r.EARNINGS["MU"]["data"] == embutido.EARNINGS["MU"]["data"]


def test_overlay_vazio_nao_apaga_o_calendario(monkeypatch, tmp_path, embutido):
    r = _radar(monkeypatch, tmp_path=tmp_path,
               blob={"atualizado_em": "2026-08-19", "earnings": {}})
    assert len(r.EARNINGS) == len(embutido.EARNINGS)


def test_data_malformada_e_ignorada_por_ticker(monkeypatch, tmp_path, embutido):
    """Entrada ruim para um papel não pode contaminar os outros."""
    r = _radar(monkeypatch, tmp_path=tmp_path, blob={
        "atualizado_em": "2026-08-19",
        "earnings": {"MU": {"data": "01/10/2026", "quando": "AC"},
                     "NVDA": {"data": "2026-11-11", "quando": "AC"}},
    })
    assert r.EARNINGS["MU"]["data"] == embutido.EARNINGS["MU"]["data"]
    assert r.EARNINGS["NVDA"]["data"] == "2026-11-11"
