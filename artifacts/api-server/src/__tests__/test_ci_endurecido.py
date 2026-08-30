"""
O CI protege contra as classes de defeito que já custaram caro neste repo.

Este arquivo não testa código de produção -- testa a CONFIGURAÇÃO, porque ela
envelhece igual a código e ninguém percebe. Um passo removido num refactor de
YAML não quebra teste nenhum, e a proteção some em silêncio.

Contexto (auditoria de 29/08/2026, sobre os 440 PRs): o repositório passou
três meses com CI sem varredura de segredo, tendo vazado credencial QUATRO
vezes -- #257 (senha em log), #377 (Alpha Vantage), #409 (FMP na tela, no .md
e no e-mail) e um campo de diagnóstico novo em 28/08.
"""
import pathlib
import re

import pytest

_RAIZ = pathlib.Path(__file__).resolve().parents[4]
_CI = _RAIZ / ".github" / "workflows" / "ci.yml"


@pytest.fixture(scope="module")
def ci() -> str:
    assert _CI.exists(), f"CI não encontrado em {_CI}"
    return _CI.read_text(encoding="utf-8")


def test_o_ci_varre_segredo(ci):
    """A defesa que faltava. `mask_sensitive_data` é máscara de RUNTIME: roda
    depois que o segredo já está no processo, e todo caminho novo nasce sem
    máscara -- foi o que aconteceu nas quatro vezes."""
    assert "gitleaks" in ci.lower()


def test_a_varredura_de_segredo_e_bloqueante(ci):
    """Diferente do preflight de fontes, que é informativo de propósito.
    Aquele olha o mundo externo, que pode cair sem culpa nossa; este olha o
    nosso diff, e credencial no diff é sempre defeito nosso.

    Um `continue-on-error` no passo do gitleaks o transformaria em decoração.
    """
    bloco = ci[ci.index("gitleaks"):]
    ate_o_proximo_job = bloco.split("\n  typecheck", 1)[0]
    assert "continue-on-error" not in ate_o_proximo_job


def test_a_varredura_enxerga_o_historico_do_pr(ci):
    """Com `fetch-depth: 1` não há com o que comparar, e o gitleaks passa a
    olhar só a árvore -- perdendo justamente o que os commits do PR
    introduzem."""
    bloco = ci[ci.index("segredos:"):ci.index("typecheck-and-js-tests:")]
    assert "fetch-depth: 0" in bloco


def test_o_ci_roda_lint(ci):
    assert "ruff check" in ci


def test_a_config_do_ruff_existe_e_escolhe_as_regras(_=None):
    """Ruleset explícito, não o padrão. Rodar o padrão neste repo devolve 189
    achados, dos quais 7 são defeito -- o resto briga com decisão deliberada
    (import tardio para não pagar yfinance, `l` de linha). Checagem que
    reprova código correto ensina a ser ignorada."""
    cfg = _RAIZ / "ruff.toml"
    assert cfg.exists()
    texto = cfg.read_text(encoding="utf-8")
    # `F` (pyflakes) é o conjunto que pega código que não faz o que parece.
    assert re.search(r'select\s*=\s*\[[^\]]*"F"', texto), "F tem que estar no select"
    # E722: bare except engole SIGTERM, e este repo depende de morrer nele.
    assert "E722" in texto


def test_o_arquivo_do_gitleaks_cobre_os_provedores_que_ja_vazaram(_=None):
    """Os quatro incidentes foram com provedor de dado de mercado. A regra
    própria existe porque o ruleset padrão do gitleaks não conhece o formato
    de chave da FMP nem da Alpha Vantage -- são strings alfanuméricas sem
    prefixo reconhecível."""
    cfg = _RAIZ / ".gitleaks.toml"
    assert cfg.exists()
    texto = cfg.read_text(encoding="utf-8")
    for parametro in ("apikey", "api_key", "access_token"):
        assert parametro in texto, f"{parametro} fora da regra"


def test_a_regra_casa_com_a_forma_real_do_vazamento():
    """A URL do incidente do #409, verbatim na forma que ela tinha. Se o regex
    do .gitleaks.toml deixar de casar com isto, a regra virou enfeite."""
    cfg = (_RAIZ / ".gitleaks.toml").read_text(encoding="utf-8")
    m = re.search(r"regex = '''(.+?)'''", cfg, re.S)
    assert m, "regra sem regex"
    padrao = re.compile(m.group(1))
    vazamento = ("402 Client Error for url: https://financialmodelingprep.com/"
                 "stable/discounted-cash-flow?symbol=WOLF&apikey=EXEMPLOAb3xK9mQ2pLw7Zt4")
    assert padrao.search(vazamento), "o regex não pega o vazamento do #409"


def test_a_regra_nao_pega_placeholder_de_documentacao():
    """Falso positivo em varredura de segredo é caro de um jeito próprio: ele
    treina quem revisa a passar `--no-verify`, e aí a defesa inteira cai."""
    cfg = (_RAIZ / ".gitleaks.toml").read_text(encoding="utf-8")
    assert "sua[-_]?chave" in cfg or "your[-_]?key" in cfg


def test_a_allowlist_reconhece_a_fixture_marcada():
    """As fixtures de teste precisam ter a FORMA de uma chave real -- é isso
    que faz o teste de mascaramento valer alguma coisa. O marcador `EXEMPLO`
    dentro da própria string é o que as separa de um vazamento.

    A allowlist é por MARCADOR, não por caminho: uma chave de verdade colada
    num arquivo de teste continua sendo pega."""
    cfg = (_RAIZ / ".gitleaks.toml").read_text(encoding="utf-8")
    assert "EXEMPLO" in cfg, "sem isto, toda fixture de segredo reprova o CI"


def test_a_chave_do_incidente_nao_esta_mais_na_arvore():
    """Ela estava em test_security.py como "o caso real" até 29/08/2026, e foi
    a varredura de segredo que a encontrou. Trocar a fixture NÃO a remove do
    histórico do git -- só a rotação da chave resolve aquilo. Este teste
    impede que ela volte."""
    vazada = "aaIKPZy3" + "lwwVgKyfLeovcRcWwDoqGEiY"  # partida para não recolar
    for arq in (_RAIZ / "artifacts").rglob("*.py"):
        assert vazada not in arq.read_text(encoding="utf-8", errors="ignore"), arq


def _regex_da_regra() -> str:
    cfg = (_RAIZ / ".gitleaks.toml").read_text(encoding="utf-8")
    m = re.search(r"regex = '''(.+?)'''", cfg, re.S)
    assert m, "regra sem regex"
    return m.group(1)


def test_o_regex_nao_atravessa_quebra_de_linha():
    """O defeito da primeira versão desta regra, achado rodando o gitleaks de
    verdade antes de commitar.

    `\\s` inclui `\\n`. Com `\\s*` dos dois lados do `=`, o padrão casava uma
    atribuição VAZIA com o NOME da variável da linha seguinte -- e nomes como
    `DEEPSEEK_API_KEY` têm 16 caracteres, que é exatamente o piso do regex.
    Resultado: as cinco chaves vazias do .env.example viravam cinco vazamentos
    e o CI reprovava todo commit do repositório, para sempre.

    Uma varredura que reprova o .env.example é pior que varredura nenhuma:
    ela some em uma semana, desabilitada por quem precisa commitar."""
    padrao = re.compile(_regex_da_regra())
    env_example = "ANTHROPIC_API_KEY=\nDEEPSEEK_API_KEY=\nGEMINI_API_KEY=\n"
    assert not padrao.search(env_example), (
        "o regex atravessou a linha e leu o nome da variável seguinte como valor"
    )
    # E o arquivo real, que é quem paga a conta.
    assert not padrao.search((_RAIZ / ".env.example").read_text(encoding="utf-8"))


def test_o_grupo_capturado_e_a_chave_e_nao_o_rotulo():
    """O outro defeito da primeira versão. O gitleaks reporta o grupo 1 como
    sendo o segredo; com `(apikey|api_key|...)` em grupo 1, TODO achado saía
    como `Secret: apikey`.

    Não é cosmético: a allowlist por marcador é testada contra o segredo, e
    testar `EXEMPLO` contra a string `apikey` nunca casa. Ou seja, o rótulo em
    grupo 1 desligava a allowlist inteira -- as fixtures marcadas voltavam a
    reprovar o build, que é o mesmo fim do teste acima."""
    cfg = (_RAIZ / ".gitleaks.toml").read_text(encoding="utf-8")
    assert "secretGroup" in cfg, "sem secretGroup o gitleaks escolhe sozinho"

    m = re.search(r"apikey=([A-Za-z0-9_\-]+)", "x?apikey=EXEMPLOAb3xK9mQ2pLw7Zt4")
    assert m  # sanidade do próprio teste
    achado = re.search(_regex_da_regra(), "url?apikey=EXEMPLOAb3xK9mQ2pLw7Zt4")
    assert achado, "o regex parou de casar com a forma do #409"
    assert achado.group(1) == "EXEMPLOAb3xK9mQ2pLw7Zt4", (
        "o grupo 1 tem que ser o VALOR -- é ele que a allowlist por marcador lê"
    )
