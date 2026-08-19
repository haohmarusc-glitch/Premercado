"""
Cache + retry das datas de balanço (agent/earnings_dates.py).

Motivo: `get_earnings_dates` é a chamada mais instável do yfinance e não passa
pela cadeia de fallback (que trata série de PREÇO). Visto em produção (NBIS,
17/08/2026 11:36 BRT): o painel "Reação a earnings" da Análise Rápida saiu com
"falha na busca das datas" -- sem níveis, sem run-up, sem histórico -- minutos
depois de o MESMO script rodar com sucesso no terminal contra o mesmo ticker.

O que os testes fixam, em ordem de importância:

  1. stale-if-error: com a rede fora e cópia velha em disco, serve a cópia
     MARCADA. É o que transforma "painel vazio" em "painel com aviso", e é o
     ganho real -- retry sozinho não resolve rate limit.
  2. Cache fresco não toca na rede.
  3. Retry só até a primeira resposta boa, com espera crescente.
  4. Sem rede e sem cache, erro explícito (não None silencioso).

Sem rede e sem sleep de verdade: `fetch` e `dormir` são injetados.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_earnings_dates_cache.py -v
"""
import os
import time

import pandas as pd
import pytest

from agent import earnings_dates as ed


@pytest.fixture(autouse=True)
def _cache_isolado(tmp_path, monkeypatch):
    """Cada teste com seu diretório: sem isso um teste enxergaria o pickle do
    anterior e o resultado dependeria da ordem de execução."""
    monkeypatch.setattr(ed, "_DIR", str(tmp_path))


def _df(datas: list[str]) -> pd.DataFrame:
    idx = pd.DatetimeIndex(pd.to_datetime(datas)).tz_localize("America/New_York")
    return pd.DataFrame({"EPS Estimate": [1.0] * len(datas)}, index=idx)


class _Fetch:
    """fetch() programável que conta chamadas."""

    def __init__(self, *respostas):
        self.respostas = list(respostas)
        self.chamadas = 0

    def __call__(self):
        self.chamadas += 1
        r = self.respostas[min(self.chamadas - 1, len(self.respostas) - 1)]
        if isinstance(r, Exception):
            raise r
        return r


def _esperas():
    """Coletor para o `dormir` injetado -- nenhum teste espera de verdade."""
    registro: list[float] = []
    return registro, registro.append


# ── caminho feliz e cache ───────────────────────────────────────────────────

def test_busca_ao_vivo_e_grava_em_disco():
    fetch = _Fetch(_df(["2026-08-12", "2026-05-13"]))
    df, fonte, erro = ed.buscar("NBIS", fetch, limit=14)

    assert fonte == "yfinance"
    assert erro is None
    assert len(df) == 2
    assert os.path.exists(ed._caminho("NBIS", 14))


def test_cache_fresco_nao_toca_na_rede():
    ed.buscar("NBIS", _Fetch(_df(["2026-08-12"])), limit=14)

    segunda = _Fetch(_df(["2026-08-12"]))
    df, fonte, _ = ed.buscar("NBIS", segunda, limit=14)

    assert fonte == "cache"
    assert segunda.chamadas == 0
    assert len(df) == 1


def test_limit_faz_parte_da_chave():
    """Um consumidor pede 8 eventos e outro 14; servir a resposta curta a quem
    pediu a longa truncaria o histórico em silêncio."""
    ed.buscar("NBIS", _Fetch(_df(["2026-08-12"])), limit=8)

    outro = _Fetch(_df(["2026-08-12", "2026-05-13", "2026-02-12"]))
    df, fonte, _ = ed.buscar("NBIS", outro, limit=14)

    assert fonte == "yfinance"  # não reaproveitou o de limit=8
    assert len(df) == 3


# ── retry ───────────────────────────────────────────────────────────────────

def test_retry_ate_a_primeira_resposta_boa():
    fetch = _Fetch(RuntimeError("429 Too Many Requests"), _df(["2026-08-12"]))
    esperas, dormir = _esperas()

    df, fonte, erro = ed.buscar("NBIS", fetch, limit=14, dormir=dormir)

    assert fonte == "yfinance"
    assert erro is None
    assert fetch.chamadas == 2
    assert esperas == [pytest.approx(ed.ESPERA_INICIAL_S)]  # esperou uma vez só


def test_resposta_vazia_conta_como_falha():
    """yfinance devolve DataFrame vazio em vez de erro quando é bloqueado --
    tratar isso como sucesso cacheava o vazio e propagava o problema."""
    fetch = _Fetch(pd.DataFrame(), _df(["2026-08-12"]))
    _, dormir = _esperas()

    df, fonte, _ = ed.buscar("NBIS", fetch, limit=14, dormir=dormir)
    assert fonte == "yfinance"
    assert fetch.chamadas == 2
    assert len(df) == 1


def test_espera_cresce_entre_tentativas():
    fetch = _Fetch(RuntimeError("boom"))  # falha sempre
    esperas, dormir = _esperas()

    ed.buscar("NBIS", fetch, limit=14, dormir=dormir)

    assert fetch.chamadas == ed.TENTATIVAS
    # Uma espera a menos que tentativas (não dorme depois da última), dobrando.
    assert esperas == [
        pytest.approx(ed.ESPERA_INICIAL_S * (2 ** i)) for i in range(ed.TENTATIVAS - 1)
    ]


# ── stale-if-error: o ponto principal ───────────────────────────────────────

def test_serve_cache_vencido_quando_a_rede_cai():
    ed.buscar("NBIS", _Fetch(_df(["2026-08-12", "2026-05-13"])), limit=14)

    # Envelhece o arquivo além do TTL.
    caminho = ed._caminho("NBIS", 14)
    velho = time.time() - (ed.TTL_S + 60)
    os.utime(caminho, (velho, velho))

    fetch = _Fetch(RuntimeError("429 Too Many Requests"))
    _, dormir = _esperas()
    df, fonte, erro = ed.buscar("NBIS", fetch, limit=14, dormir=dormir)

    assert fonte == "cache_vencido"
    assert len(df) == 2                    # o dado ainda serve
    assert "429" in erro                   # e o motivo vem junto
    assert fetch.chamadas == ed.TENTATIVAS  # tentou de verdade antes de desistir


def test_sem_rede_e_sem_cache_devolve_erro_explicito():
    fetch = _Fetch(RuntimeError("sem internet"))
    _, dormir = _esperas()

    df, fonte, erro = ed.buscar("DESCONHECIDO", fetch, limit=14, dormir=dormir)

    assert df is None
    assert fonte == "erro"
    assert "sem internet" in erro


def test_falha_de_disco_nao_derruba_a_busca(monkeypatch):
    """Falha aberta, igual ao hist_cache: sem permissão de escrita o resultado
    ao vivo continua valendo."""
    monkeypatch.setattr(ed, "_DIR", "/proc/nao-da-pra-escrever-aqui")
    df, fonte, erro = ed.buscar("NBIS", _Fetch(_df(["2026-08-12"])), limit=14)

    assert fonte == "yfinance"
    assert erro is None
    assert len(df) == 1
