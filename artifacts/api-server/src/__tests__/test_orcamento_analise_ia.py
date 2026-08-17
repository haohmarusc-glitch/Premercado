"""
Invariante de orçamento de tempo da Análise Rápida com IA.

Playbook §3: nenhuma camada interna pode ter orçamento MAIOR que o timeout
externo. Se tiver, o Node só descobre o problema matando o subprocesso, e o
usuário recebe um 500 genérico em vez de um erro legível -- foi exatamente o
que aconteceu em 17/08/2026.

Reconstrução do incidente:

  routes/analysis.ts .................... 90s de teto
  API_TIMEOUT_SECONDS (default) ......... 60s por chamada
  AGENT_MAX_RETRIES (SDK, default 1) .... 2 tentativas
  AGENT_TRANSIENT_RETRIES (default 1) ... 2 tentativas
  -> pior caso por PROVEDOR: 2 × (2 × 60s) + backoff ≈ 245s

Uma análise passou em 57,5s -- já encostando no timeout de 60s da própria
API, o que era o sintoma. As duas seguintes bateram 90s cravados:
"Failed: /analise-rapida/ia", 500 na tela, e o log dizia só "timeout" porque
o stderr (com as linhas [provider]) era descartado.

Este teste lê os DOIS lados -- o TypeScript e o Python -- e falha se alguém
mexer num sem mexer no outro. É o tipo de acoplamento que nenhum typecheck
pega, porque atravessa linguagens.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_orcamento_analise_ia.py -v
"""
import pathlib
import re

import pytest

_SRC = pathlib.Path(__file__).resolve().parent.parent
_ROTA = (_SRC / "routes" / "analysis.ts").read_text(encoding="utf-8")


def _timeout_da_rota_s() -> float:
    """O setTimeout da runAnaliseRapidaIA, em segundos."""
    trecho = _ROTA.split("function runAnaliseRapidaIA", 1)[1]
    m = re.search(r"setTimeout\((?:.|\n)*?\}?,\s*([\d_]+)\s*\)", trecho)
    assert m, "não achei o setTimeout de runAnaliseRapidaIA"
    return int(m.group(1).replace("_", "")) / 1000


def _modulo():
    from agent import analise_rapida_ia as mod
    return mod


def test_orcamento_do_python_cabe_no_timeout_do_node():
    """A invariante. Se quebrar, o processo volta a ser morto no meio em vez
    de devolver erro legível."""
    mod = _modulo()
    assert mod._ORCAMENTO_TOTAL_S < _timeout_da_rota_s(), (
        f"orçamento interno ({mod._ORCAMENTO_TOTAL_S}s) >= timeout do Node "
        f"({_timeout_da_rota_s()}s) — ver playbook §3"
    )


def test_duas_tentativas_de_provedor_cabem_no_orcamento():
    """A cadeia de fallback precisa conseguir trocar de provedor ao menos uma
    vez. Com uma tentativa só, um provedor lento derruba a análise inteira em
    vez de cair para o próximo."""
    mod = _modulo()
    coleta_fundamental_s = 20  # yfinance.info + FMP + notícias, com folga
    assert 2 * mod._LLM_TIMEOUT_S + coleta_fundamental_s <= mod._ORCAMENTO_TOTAL_S


def test_retries_desligados_para_esta_rota():
    """Retry do SDK e do fallback multiplicam o tempo sem coordenação (2×2=4
    tentativas de 60s = 240s). Numa rota interativa o certo é trocar de
    provedor, não insistir no mesmo."""
    import os

    _modulo()  # o import é que aplica as variáveis
    assert os.environ["AGENT_MAX_RETRIES"] == "0"
    assert os.environ["AGENT_TRANSIENT_RETRIES"] == "0"
    assert float(os.environ["API_TIMEOUT_SECONDS"]) == pytest.approx(_modulo()._LLM_TIMEOUT_S)


def test_a_rota_registra_o_stderr_no_timeout():
    """Sem isso o log dizia só "timeout" e não dava para saber qual provedor
    consumiu o tempo — foi o que impediu o diagnóstico na primeira ocorrência."""
    trecho = _ROTA.split("function runAnaliseRapidaIA", 1)[1]
    assert "logger.error" in trecho
    assert "stderr" in trecho
