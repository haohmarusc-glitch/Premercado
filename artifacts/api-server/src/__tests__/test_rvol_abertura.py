"""Nem a conta nem o SINAL do rvol podem voltar a existir em duas cópias.

Três comentários no código citavam ESTE arquivo como a garantia de que as duas
cópias de `_rvol_signal` não divergiam:

    tools.py:907            "test_rvol_abertura.py amarra as duas cópias."
    get_technicals.py:38    "test_rvol_abertura.py garante que as duas
                             cópias não divirjam."

Ele não existia. A duplicação era documentada como segura por um teste que
nunca foi escrito — e foi por isso que a CONTA do rvol (que também era cópia,
e essa nem citava teste nenhum) pôde quebrar nos dois arquivos ao mesmo tempo
sem ninguém ver.

Hoje as duas coisas moram em `volume_intradiario.py` e as cópias foram
apagadas. Este arquivo guarda a ausência: um teste de igualdade entre cópias
ainda permitia editar as duas juntas e errar nas duas juntas.

## Por que ler o FONTE e não importar

`get_technicals.py` faz, no nível do módulo:

    _real_stdout_fd = os.dup(1)
    os.dup2(2, 1)
    sys.stdout = open(os.devnull, "w")

Importá-lo dentro do pytest redireciona o fd 1 do processo inteiro para
stderr, pelo resto da sessão de testes. É quase certo que foi esse o obstáculo
que deixou o arquivo por escrever. Ler o código-fonte não tem efeito colateral
nenhum e responde exatamente à pergunta que interessa.
"""

import os
import pathlib
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

AGENTE = pathlib.Path(__file__).resolve().parents[1] / "agent"


def test_rvol_signal_nao_existe_mais_como_copia():
    """A duplicação foi APAGADA, não amarrada.

    Amarrar duas cópias com um teste de igualdade é o segundo melhor: continua
    sendo possível editar as duas juntas e errar nas duas juntas, que é
    exatamente o que aconteceu com a conta do rvol. O sinal agora mora em
    `volume_intradiario.situacao_do_rvol` e este teste guarda a ausência.
    """
    for arquivo in ("tools.py", "get_technicals.py"):
        fonte = (AGENTE / arquivo).read_text(encoding="utf-8")
        assert "def _rvol_signal" not in fonte, (
            f"{arquivo} recriou _rvol_signal -- use situacao_do_rvol de "
            f"volume_intradiario.py")
        assert "_RVOL_FRACAO_MINIMA" not in fonte, (
            f"{arquivo} recriou o piso de abertura. Ele agora e' "
            f"MINUTOS_MINIMOS_CONCLUSIVOS, em MINUTOS -- a forma em fracao "
            f"(6/78) vale 16 minutos num pregao de 210, nao 30.")


def test_o_piso_de_abertura_e_medido_em_minutos():
    """`6/78` em fração contra 30 em minutos não é a mesma regra.

    Num pregão curto (210 minutos) a fração 6/78 corresponde a 16 minutos, e o
    guarda que existe por causa do NBIS (rvol 5,81 aos SETE minutos) ficaria
    afrouxado justo nos dias em que o denominador já é mais frágil.
    """
    from agent.volume_intradiario import (
        MINUTOS_MINIMOS_CONCLUSIVOS, MINUTOS_DO_PREGAO_CURTO, situacao_do_rvol,
    )
    assert MINUTOS_MINIMOS_CONCLUSIVOS == 30
    equivalente_em_minutos = (6 / 78) * MINUTOS_DO_PREGAO_CURTO
    assert equivalente_em_minutos < MINUTOS_MINIMOS_CONCLUSIVOS
    # 16 minutos de pregão não bastam, mesmo num dia curto.
    assert situacao_do_rvol(5.81, equivalente_em_minutos) == "indefinido_abertura"


def test_a_conta_do_rvol_nao_voltou_a_ser_duplicada():
    """A conta saiu dos dois arquivos e foi para volume_intradiario.py. Se
    alguém a reescrever inline, a divergência volta a ser possível — e desta
    vez sem aviso, porque o rvol tem valor plausível mesmo quando está errado
    (8,89 "alto" num dia de volume comum)."""
    for arquivo in ("tools.py", "get_technicals.py"):
        fonte = (AGENTE / arquivo).read_text(encoding="utf-8")
        assert "len(intraday) / 78" not in fonte, (
            f"{arquivo} voltou a derivar o tempo decorrido da CONTAGEM de "
            f"barras — use medida_do_rvol de volume_intradiario.py")
        assert "medida_do_rvol" in fonte, (
            f"{arquivo} deixou de usar a conta compartilhada")
