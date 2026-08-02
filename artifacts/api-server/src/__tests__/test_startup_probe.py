"""
Testes de startup_probe.py — instrumentação do tempo de subida do subprocesso.

Contexto: mesmo depois das PRs #198 e #199, todos os subprocessos Python do
container deployado seguiram estourando seus timeouts. A hipótese é que o
orçamento interno nunca chega a ser consultado porque o processo ainda está
importando quando o Node o mata -- mas o único indício era indireto (o agente
levou ~3min entre o POST /api/agent/run e o primeiro "STEP:").

Este módulo troca o indício por medida, separando o tempo do interpretador
subindo do tempo dos imports pesados.

O que os testes travam:
- as marcas vão pra STDERR, nunca pra stdout (stdout é o canal de resultado,
  e o Node faz JSON.parse nele -- uma linha extra ali quebraria o consumidor);
- o início do processo vem de /proc/self/stat, não do import do módulo (senão
  o tempo de o container entregar CPU ficaria invisível, que é o suspeito);
- o módulo não derruba nada onde /proc não existir.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_startup_probe.py -v
(conftest.py no mesmo diretório já cuida do sys.path)
"""

import subprocess
import sys
import time

from agent import startup_probe


def test_inicio_do_processo_e_anterior_ao_import_do_modulo():
    """A marca de boot precisa cobrir o interpretador subindo, não só o tempo
    a partir do import deste módulo -- senão mede justamente o que não
    interessa."""
    assert startup_probe._INICIO_PROCESSO is not None, "/proc/self/stat indisponível"
    assert startup_probe._INICIO_PROCESSO <= startup_probe._IMPORTADO_EM


def test_inicio_do_processo_e_plausivel():
    """Nem no futuro, nem antigo demais: o processo do pytest começou há pouco."""
    idade = time.time() - startup_probe._INICIO_PROCESSO
    assert 0 <= idade < 3600


def test_marcas_vao_para_stderr_e_nao_para_stdout():
    """Uma linha de probe no stdout quebraria o JSON.parse do lado Node."""
    proc = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, %r);"
         "from agent import startup_probe as sp; sp.boot(); sp.imports_prontos();"
         "print('RESULTADO')" % str(_src_dir())],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "RESULTADO", "probe vazou pro stdout"
    assert "[probe] boot" in proc.stderr
    assert "[probe] imports" in proc.stderr
    assert "[probe] total_ate_imports" in proc.stderr


def test_marca_nao_quebra_sem_proc(monkeypatch, capsys):
    """Sem /proc (outro SO), a medida some mas nada levanta."""
    monkeypatch.setattr(startup_probe, "_INICIO_PROCESSO", None)
    startup_probe.boot()
    assert capsys.readouterr().err == ""


def _src_dir() -> str:
    import pathlib
    return str(pathlib.Path(startup_probe.__file__).resolve().parent.parent)
