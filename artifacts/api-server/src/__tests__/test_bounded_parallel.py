"""
Testes de agent/bounded_parallel.py -- garante que um item travado (rede
presa) nunca segura o resultado além do orçamento configurado, e que
exit_now() de fato sai do processo sem esperar threads pendentes (senão a
correção do incidente de 01/08/2026 -- checkers batendo o teto do timeout
do lado Node porque o Python continuava vivo -- não teria efeito nenhum).

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_bounded_parallel.py -v
(conftest.py no mesmo diretório já cuida do sys.path)
"""

import subprocess
import sys
import time

from agent.bounded_parallel import bounded_parallel_map


def test_returns_fast_items_within_budget():
    def fn(x):
        time.sleep(0.05)
        return x * 2

    t0 = time.time()
    results = bounded_parallel_map(fn, [1, 2, 3, 4], budget_s=5)
    elapsed = time.time() - t0

    assert sorted(results) == [2, 4, 6, 8]
    assert elapsed < 2  # bem abaixo do orçamento -- não deveria esperar o teto


def test_stuck_item_does_not_block_past_budget(capsys):
    def fn(x):
        if x == "stuck":
            time.sleep(30)
            return "never"
        time.sleep(0.05)
        return f"ok-{x}"

    t0 = time.time()
    results = bounded_parallel_map(fn, ["a", "b", "stuck", "c"], budget_s=1, label="test")
    elapsed = time.time() - t0

    assert elapsed < 2  # respeita o orçamento (1s), não os 30s do item travado
    assert sorted(results) == ["ok-a", "ok-b", "ok-c"]

    err = capsys.readouterr().err
    assert "test" in err
    assert "stuck" in err


def test_all_items_stuck_returns_empty_within_budget():
    def fn(_x):
        time.sleep(30)
        return "never"

    t0 = time.time()
    results = bounded_parallel_map(fn, ["a", "b"], budget_s=1)
    elapsed = time.time() - t0

    assert results == []
    assert elapsed < 2


def test_exit_now_does_not_wait_for_leftover_threads():
    """Reproduz o bug real: sem os._exit(), o processo ficaria vivo até a
    thread travada terminar de verdade (aqui, 30s), mesmo já tendo
    "desistido" dela no nível da aplicação -- o teste falha (timeout do
    subprocess) se a correção regredir pra sys.exit()/fim normal."""
    script = """
import sys
sys.path.insert(0, {src_dir!r})
import time
from agent.bounded_parallel import bounded_parallel_map, exit_now

def fn(x):
    if x == "stuck":
        time.sleep(30)
        return "never"
    return f"ok-{{x}}"

results = bounded_parallel_map(fn, ["a", "stuck"], budget_s=1)
exit_now("done:" + ",".join(sorted(results)) + "\\n")
"""
    import os
    src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = script.format(src_dir=src_dir)

    t0 = time.time()
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, timeout=10,  # falha o teste se travar
    )
    elapsed = time.time() - t0

    assert proc.returncode == 0
    assert proc.stdout.strip() == "done:ok-a"
    assert elapsed < 5  # bem abaixo dos 30s da thread travada
