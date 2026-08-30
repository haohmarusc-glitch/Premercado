"""Todo script que o servidor spawna tem de IMPORTAR no contexto do spawn.

Incidente real (27/08/2026): o /trend caiu em produção com

    ImportError: attempted relative import with no known parent package

A #410 fez o get_trend importar `fala_do_papel` de news_sources -- e
news_sources puxa `config` e `cache` por import RELATIVO, que só resolve
dentro do pacote. O get_trend rodava por CAMINHO (sys.path[0] = src/agent,
sem pacote); o braço de imports planos, que era o que realmente executava
lá, morreu no import.

A causa de fundo nunca foi o import da #410: era o repo ter DUAS formas de
rodar o mesmo script, exigindo formas OPOSTAS de import. Enquanto as duas
conviviam, cada dependência nova precisava ser plana até o fundo, e bastava
um módulo intermediário usar import relativo para derrubar a rota.

Desde a unificação do spawn (`spawnAgente`, lib/runner.ts), existe uma forma
só: `python -m agent.<script>` com cwd no diretório que contém o pacote.
Este arquivo vigia as duas metades disso:

  1. nenhum .ts volta a spawnar por caminho de script;
  2. todo script spawnado importa como módulo do pacote.

A lista de scripts é DERIVADA dos .ts, não copiada à mão: uma lista copiada
envelhece calada, e foi assim que o folego_de_caixa passou meses quebrado
sem ninguém ver.
"""

import os
import pathlib
import re
import subprocess
import sys

_SRC = pathlib.Path(__file__).resolve().parent.parent
_AGENT_DIR = _SRC / "agent"

# QUALQUER literal `"x.py"` no .ts, e não só o que aparece colado no
# `spawnAgente(` -- metade dos scripts chega lá como ARGUMENTO de um helper
# (`runScript("folego_de_caixa.py")`, `makeTickerRoute("/trend",
# "get_trend.py")`), e um regex preso ao call site do spawn perde todos eles.
#
# Custou uma versão errada desta guarda: presa ao `spawnAgente(`, ela derivou
# 21 scripts em vez de 26 -- sem o get_trend, que é o do incidente que este
# arquivo existe para vigiar. Guarda que encolhe em silêncio é pior que
# guarda nenhuma, porque ainda passa verde.
_POR_HELPER = re.compile(r'"([A-Za-z0-9_]+)\.py"')
_POR_MODULO = re.compile(r'"-m",\s*"agent\.([A-Za-z0-9_]+)"')

# Spawn por CAMINHO -- a forma que a unificação eliminou.
_POR_CAMINHO = re.compile(r'path\.join\(\s*agentDir\s*,\s*"agent"\s*,')


def _ts_do_servidor() -> list[pathlib.Path]:
    return [p for p in _SRC.rglob("*.ts") if "__tests__" not in str(p)]


def _scripts_spawnados() -> list[str]:
    achados: set[str] = set()
    for p in _ts_do_servidor():
        texto = p.read_text(encoding="utf-8")
        achados |= set(_POR_HELPER.findall(texto))
        achados |= set(_POR_MODULO.findall(texto))
    return sorted(achados)


def test_nenhum_ts_spawna_por_caminho_de_script():
    """A invariante que faz a perna de import plano ser desnecessária.

    Um único `path.join(agentDir, "agent", ...)` que volte traz de novo o
    contexto sem pacote -- e com ele a exigência de que toda dependência
    nova seja plana até o fundo, que é o que ninguém lembra de fazer."""
    reincidentes = [
        str(p.relative_to(_SRC)) for p in _ts_do_servidor()
        if _POR_CAMINHO.search(p.read_text(encoding="utf-8"))
    ]
    assert not reincidentes, (
        "spawn por caminho de script de volta em: "
        f"{reincidentes}. Use spawnAgente() -- ver lib/runner.ts")


def test_a_lista_derivada_nao_esta_vazia():
    """Se os regexes acima pararem de casar (alguém renomeou o helper), o
    teste seguinte passaria trivialmente sem checar nada."""
    scripts = _scripts_spawnados()
    assert len(scripts) >= 20, (
        f"só {len(scripts)} scripts achados nos .ts -- o padrão de spawn "
        "mudou e esta guarda parou de enxergar o que devia vigiar")


def test_todo_script_spawnado_importa_como_modulo():
    """Um subprocesso só, importando todos em sequência: pandas/yfinance
    carregam uma vez e o teste fica em segundos. O nome do primeiro que
    falhar sai no stderr. get_technicals redireciona o stdout do processo
    (os.dup2) no import -- por isso subprocess, nunca import direto."""
    scripts = _scripts_spawnados()

    faltando = [s for s in scripts if not (_AGENT_DIR / f"{s}.py").exists()]
    assert not faltando, (
        f"scripts citados nos .ts que não existem: {faltando} -- ou o .ts "
        f"aponta pro nada, ou renomearam o arquivo sem atualizar o .ts")

    codigo = "\n".join(
        f"import agent.{s}\nprint('ok {s}', flush=True)" for s in scripts)
    r = subprocess.run(
        [sys.executable, "-c", codigo],
        # Mesmo contexto do spawnAgente(): cwd no diretório que CONTÉM o
        # pacote, e o pacote importado pelo nome.
        cwd=str(_SRC),
        env={**os.environ, "PYTHONPATH": str(_SRC)},
        capture_output=True, text=True, timeout=180,
    )
    assert r.returncode == 0, (
        "import quebrado no contexto de módulo -- último ok foi "
        f"'{(r.stdout.strip().splitlines() or ['nenhum'])[-1]}'.\n"
        f"stderr:\n{r.stderr[-1500:]}"
    )
