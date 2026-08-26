"""As duas cópias de `_rvol_signal` não podem divergir.

Três comentários no código citavam ESTE arquivo como a garantia disso:

    tools.py:907            "test_rvol_abertura.py amarra as duas cópias."
    get_technicals.py:38    "test_rvol_abertura.py garante que as duas
                             cópias não divirjam."

Ele não existia. A duplicação era documentada como segura por um teste que
nunca foi escrito — e foi por isso que a CONTA do rvol (que também era cópia,
e essa nem citava teste nenhum) pôde quebrar nos dois arquivos ao mesmo tempo
sem ninguém ver. A conta agora mora em volume_intradiario.py; o `_rvol_signal`
segue duplicado, e este arquivo passa a ser o que os comentários prometem.

## Por que comparar o FONTE e não importar

`get_technicals.py` faz, no nível do módulo:

    _real_stdout_fd = os.dup(1)
    os.dup2(2, 1)
    sys.stdout = open(os.devnull, "w")

Importá-lo dentro do pytest redireciona o fd 1 do processo inteiro para
stderr, pelo resto da sessão de testes. É quase certo que foi esse o obstáculo
que deixou o arquivo por escrever. Comparar o código-fonte não tem efeito
colateral nenhum e responde exatamente à pergunta que interessa: as duas
cópias dizem a mesma coisa?
"""

import ast
import pathlib

AGENTE = pathlib.Path(__file__).resolve().parents[1] / "agent"


def _funcao(arquivo: str, nome: str) -> str:
    """O corpo da função, sem comentário nem docstring.

    `ast.unparse` normaliza espaçamento e apaga comentários; a docstring é
    removida à mão. Sem isso o teste acusaria diferença de formatação, que é
    ruído, em vez de diferença de comportamento, que é o que importa.
    """
    arvore = ast.parse((AGENTE / arquivo).read_text(encoding="utf-8"))
    for no in ast.walk(arvore):
        if isinstance(no, ast.FunctionDef) and no.name == nome:
            corpo = list(no.body)
            if (corpo and isinstance(corpo[0], ast.Expr)
                    and isinstance(corpo[0].value, ast.Constant)
                    and isinstance(corpo[0].value.value, str)):
                corpo = corpo[1:]
            return "\n".join(ast.unparse(x) for x in corpo)
    raise AssertionError(f"{nome} não encontrada em {arquivo}")


def _constante(arquivo: str, nome: str) -> str:
    """A EXPRESSÃO da constante, não o valor.

    `_RVOL_FRACAO_MINIMA = 6 / 78` não é literal, então `literal_eval` estoura.
    E comparar a expressão é mais forte que comparar o número: pega uma cópia
    virar `0.0769`, que dá o mesmo resultado hoje e esconde de onde veio.
    """
    arvore = ast.parse((AGENTE / arquivo).read_text(encoding="utf-8"))
    for no in arvore.body:
        if isinstance(no, ast.Assign) and any(
                isinstance(a, ast.Name) and a.id == nome for a in no.targets):
            return ast.unparse(no.value)
    raise AssertionError(f"{nome} não encontrada em {arquivo}")


def test_rvol_signal_e_identico_nos_dois_arquivos():
    assert (_funcao("tools.py", "_rvol_signal")
            == _funcao("get_technicals.py", "_rvol_signal"))


def test_a_fracao_minima_e_a_mesma():
    """O piso que segura o rvol inflado da abertura — NBIS 17/08/2026 saiu com
    rvol 5,81 "alto" aos sete minutos de pregão."""
    assert (_constante("tools.py", "_RVOL_FRACAO_MINIMA")
            == _constante("get_technicals.py", "_RVOL_FRACAO_MINIMA"))


def test_a_conta_do_rvol_nao_voltou_a_ser_duplicada():
    """A conta saiu dos dois arquivos e foi para volume_intradiario.py. Se
    alguém a reescrever inline, a divergência volta a ser possível — e desta
    vez sem aviso, porque o rvol tem valor plausível mesmo quando está errado
    (8,89 "alto" num dia de volume comum)."""
    for arquivo in ("tools.py", "get_technicals.py"):
        fonte = (AGENTE / arquivo).read_text(encoding="utf-8")
        assert "len(intraday) / 78" not in fonte, (
            f"{arquivo} voltou a derivar o tempo decorrido da CONTAGEM de "
            f"barras — use rvol_da_sessao de volume_intradiario.py")
        assert "rvol_da_sessao" in fonte, (
            f"{arquivo} deixou de usar a conta compartilhada")
