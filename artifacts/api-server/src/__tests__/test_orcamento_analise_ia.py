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
    vez de cair para o próximo.

    Este teste JÁ EXISTIA e passava -- usando 20s de coleta fundamental como
    suposição que nada no código garantia. Em 18/08/2026 a suposição falhou em
    produção: a coleta mais a primeira chamada consumiram 124s dos 135s, e a
    troca de provedor ficou inalcançável exatamente no caso para o qual foi
    escrita. Aritmética certa no papel, livre na prática.

    Agora o teto da coleta é constante do módulo (_TETO_FUNDAMENTO_S) e a
    conta amarra código, não estimativa."""
    mod = _modulo()
    assert 2 * mod._LLM_TIMEOUT_S + mod._TETO_FUNDAMENTO_S <= mod._ORCAMENTO_TOTAL_S


def test_a_camada_opcional_tem_teto_proprio():
    """A camada fundamental é fail-open por projeto, mas "opcional" sem teto
    de TEMPO não é opcional: o que ela consome sai do LLM, que é obrigatório.
    yfinance.info sozinho já levou dezenas de segundos em produção."""
    mod = _modulo()
    assert mod._TETO_FUNDAMENTO_S > 0
    assert mod._TETO_FUNDAMENTO_S < mod._ORCAMENTO_TOTAL_S


def test_a_rota_registra_o_stderr_quando_a_analise_tropeca_e_sai(monkeypatch):
    """O caso CARO é o intermediário: um provedor trunca (e a tentativa perdida
    É cobrada -- tokens de raciocínio contam como saída), o seguinte entrega o
    texto, e o desfecho é sucesso. Registrar só no erro deixava exatamente esse
    caminho sem rastro.

    Produção 18/08/2026: análise a US$ 0,0608 contra os ~US$ 0,015 esperados, e
    `docker compose logs | grep analise_rapida_ia` vazio -- não por falha do
    log, mas porque a execução tinha dado certo."""
    trecho = _ROTA.split("function runAnaliseRapidaIA", 1)[1]
    assert "MARCAS_DE_TROPECO" in trecho
    # As três marcas que o Python imprime quando a cadeia anda.
    for marca in ("pulando", "truncou", "toco"):
        assert marca in trecho, f"'{marca}' não é reconhecida como tropeço"


def test_a_regra_de_unidade_de_tempo_esta_no_system():
    """Número certo com unidade errada é pior que número errado: o valor
    confere com o JSON, então o leitor não tem como desconfiar. Visto em
    produção -- `momentumAnnualPct` (taxa anualizada) descrito como
    'momentum de 106,56% em 90 dias', que seria ~38%."""
    mod = _modulo()
    assert "momentumAnnualPct" in mod.SYSTEM
    assert "ANUALIZADA" in mod.SYSTEM


def test_a_rota_registra_o_stderr_quando_o_script_devolve_erro():
    """O script sai com código 0 E {"error": ...} quando nenhum provedor
    produz texto -- é o que a Tarefa 0 passou a fazer. Sem registrar o stderr
    NESSE caminho, todo o diagnóstico morre na variável `err`: em 18/08/2026 a
    tela mostrou "0 chars" e o log do container não tinha uma linha sobre a
    causa.

    Trocar um 500 mudo por um erro elegante não pode significar trocar um erro
    legível por um erro bonito e inauditável."""
    trecho = _ROTA.split("function runAnaliseRapidaIA", 1)[1]
    assert "registrarDiagnostico" in trecho, "o caminho de sucesso-com-erro não registra o stderr"
    # E precisa valer nos DOIS parses (bloco inteiro e última linha), senão
    # stdout poluído volta a engolir o diagnóstico.
    assert trecho.count("registrarDiagnostico(parsed)") == 2


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
