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
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError, as_completed
from typing import Callable, TypeVar

T = TypeVar("T")
R = TypeVar("R")

# Epoch em MILISSEGUNDOS do momento em que o chamador (Node) vai desistir.
# Quem spawna o processo escreve isto no env; ver alert-checker.ts.
DEADLINE_ENV = "AGENT_DEADLINE_TS"

# Folga entre o fim do map e o timeout do Node: serializar o JSON, escrever em
# stdout e sair. Sobra pro Node ler o pipe antes de matar o processo.
DEFAULT_RESERVE_S = 3.0

# Piso do orçamento. Se o startup comeu tudo, ainda tentamos uma janela curta em
# vez de devolver lista vazia na hora -- alguns tickers costumam responder nela.
MIN_BUDGET_S = 5.0


def budget_from_deadline(
    default_budget_s: float,
    *,
    reserve_s: float = DEFAULT_RESERVE_S,
    label: str = "bounded_parallel",
) -> float:
    """Orçamento em segundos calculado a partir do deadline REAL do chamador.

    Por que não basta uma constante no script: um `BUDGET_S` fixo é contado a
    partir do início do map, então a diferença entre ele e o timeout do Node
    precisa cobrir TODO o resto do processo -- e o resto do processo é
    dominado pelo import (pandas + numpy + yfinance = ~8s numa máquina
    ociosa). Com 45s de budget contra 60s de timeout, metade da folga já ia
    embora antes da primeira chamada de rede, e sob contenção de CPU (vários
    checkers subindo juntos) o processo estourava o timeout externo mesmo com
    o map respeitando o budget dele.

    Visto em produção (02/08/2026): intraday spike e bounce estourando 60s com
    1ms de diferença entre eles -- spawnados juntos, nenhum dos dois entregou.

    Como esta função roda DEPOIS dos imports, `time.time()` aqui já embute o
    custo de startup, e o orçamento passa a ser o tempo que de fato resta.
    Sem a variável de ambiente (rodada manual do script, ou chamador que não
    define deadline) cai no `default_budget_s` de antes.
    """
    raw = os.environ.get(DEADLINE_ENV)
    if not raw:
        return default_budget_s
    try:
        deadline_s = float(raw) / 1000.0
    except ValueError:
        print(f"[{label}] {DEADLINE_ENV} inválido ({raw!r}), usando budget fixo de "
              f"{default_budget_s}s", file=sys.stderr)
        return default_budget_s

    restante = deadline_s - time.time() - reserve_s
    if restante < MIN_BUDGET_S:
        # Sinal explícito de que o startup consumiu o orçamento -- era
        # justamente isso que não aparecia em lugar nenhum antes.
        print(f"[{label}] só restam {restante:.1f}s do deadline do chamador "
              f"(startup consumiu a folga); usando o piso de {MIN_BUDGET_S}s",
              file=sys.stderr)
        return MIN_BUDGET_S
    return restante


def deadline_exceeded(*, reserve_s: float = DEFAULT_RESERVE_S) -> bool:
    """True quando não sobra tempo útil até o deadline do chamador.

    Serve os scripts que percorrem tickers em série (get_performance.py,
    get_earnings.py, get_scenario_params.py) e por isso não têm onde encaixar
    o bounded_parallel_map. Sem isso eles rodam até o Node matar o processo,
    e o resultado do trabalho JÁ FEITO é jogado fora junto -- o chamador
    recebe só um timeout.

    Com o guard, o laço para sozinho e o script ainda imprime o que conseguiu:
    resultado parcial é bem melhor que nenhum, e todos os consumidores destes
    scripts já toleram ticker faltando/com erro.

    Sem AGENT_DEADLINE_TS no env devolve sempre False -- execução manual do
    script continua sem limite.
    """
    raw = os.environ.get(DEADLINE_ENV)
    if not raw:
        return False
    try:
        deadline_s = float(raw) / 1000.0
    except ValueError:
        return False
    return time.time() >= deadline_s - reserve_s


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
