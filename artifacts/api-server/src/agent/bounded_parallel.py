"""
bounded_parallel.py — busca em paralelo com orçamento de tempo total, pra
scripts CLI de vida curta (spawnados pelo Node com um timeout embutido do
lado de fora, ex.: alert-checker.ts/portfolio-alerts.ts) nunca segurarem o
processo além do que o chamador vai esperar.

Por que isso existe: `with ThreadPoolExecutor(...) as pool: ... for f in
as_completed(futures): ...` (padrão usado em get_quotes.py/
get_intraday_spikes.py/get_bounce_alerts.py/get_squeeze_alerts.py) espera
implicitamente TODAS as tasks terminarem no __exit__ do `with`
(shutdown(wait=True)) -- mesmo que uma chamada de rede individual (yfinance)
trave ou demore mais que o timeout do lado Node, o processo Python continua
vivo até ela realmente terminar (ou o timeout interno de ~30s do próprio
yfinance por request). O Node só descobre isso quando o SEU PRÓPRIO timeout
mata o subprocesso à força -- visto em produção 01/08/2026: os 4 checkers
batendo o teto do timeout inteiro (60-120s), toda vez, porque a rede estava
degradada e nenhuma camada tinha um orçamento mais curto que o timeout
externo.

Duas peças:
- bounded_parallel_map: devolve o que já completou dentro do orçamento,
  sem esperar as tasks pendentes.
- exit_now: imprime o resultado e sai IMEDIATAMENTE via os._exit() -- um
  sys.exit()/fim normal do __main__ ainda esperaria as threads pendentes
  via o atexit hook do concurrent.futures.thread (Python não tem como
  matar uma thread de verdade; testado localmente: sem os._exit, um
  processo com 1 task travada em sleep(120) só termina depois dos 120s
  mesmo já tendo "desistido" de esperar por ela no nível da aplicação).
"""
import os
import sys
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError, as_completed
from typing import Callable, TypeVar

T = TypeVar("T")
R = TypeVar("R")


def bounded_parallel_map(
    fn: Callable[[T], R],
    items: list[T],
    *,
    budget_s: float,
    max_workers: int = 8,
    label: str = "bounded_parallel_map",
) -> list[R]:
    """Roda fn(item) em paralelo pra cada item, devolve os resultados dos
    que completaram dentro de budget_s (ordem de conclusão, não de entrada
    -- mesmo padrão já usado por quem chama isso hoje). Itens que não
    terminam a tempo são omitidos (best-effort parcial, não erro fatal) e
    logados em stderr -- o chamador decide se quer registrar isso de outra
    forma (ex.: marcar o ticker com preço nulo em vez de sumir da lista).

    fn já deve tratar suas próprias exceções internamente (todo fn passado
    aqui hoje já faz isso, ver _spikes_for/_bounce_for/fetch_quote etc.) --
    esta função não tenta recuperar erros de fn, só o timeout do conjunto.
    """
    pool = ThreadPoolExecutor(max_workers=max_workers)
    futures = {pool.submit(fn, item): item for item in items}
    results: list[R] = []
    pending = set(futures)
    try:
        for future in as_completed(futures, timeout=budget_s):
            pending.discard(future)
            results.append(future.result())
    except FutureTimeoutError:
        pass
    if pending:
        stragglers = [futures[f] for f in pending]
        print(f"[{label}] orçamento de {budget_s}s esgotado com {len(stragglers)} "
              f"pendente(s), seguindo sem eles: {stragglers}", file=sys.stderr)
    return results


def exit_now(payload: str, code: int = 0) -> None:
    """Escreve `payload` em stdout e sai IMEDIATAMENTE, sem esperar
    threads de rede ainda presas em segundo plano (ver docstring do
    módulo). Chamar isso no lugar do fim natural do script sempre que o
    script usou bounded_parallel_map com resultado parcial."""
    sys.stdout.write(payload)
    sys.stdout.flush()
    os._exit(code)
