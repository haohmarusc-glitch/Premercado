"""Sessão HTTP compartilhada com retry/backoff para chamadas de rede a fontes
externas (SEC EDGAR, FRED, Fear & Greed, congresso/dark pool, etc.) -- sem
isso, um 429/503 passageiro de qualquer uma dessas fontes derruba a
ferramenta inteira (e, se for dentro de um turno do agente, o turno inteiro
com ela).

Só GET é retentado (allowed_methods=["GET"]) -- POST/PATCH/DELETE (nossas
próprias chamadas internas que criam/alteram dado, ex.: create_alert,
save_observation, create_exit_plan_item) NUNCA devem ser retentados
automaticamente, sob risco de duplicar o efeito colateral quando a primeira
tentativa na verdade só demorou a responder, não falhou de verdade.

Self-contido (sem imports do resto do pacote `agent`) de propósito -- alguns
consumidores rodam como script standalone (`from http_retry import SESSION`,
sys.path[0] = diretório do próprio script) e outros como parte do pacote
(`from .http_retry import SESSION`), e os dois precisam resolver pro mesmo
arquivo sem risco de import circular.
"""
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

_retry = Retry(
    total=3,
    backoff_factor=1,  # espera 0s, 1s, 2s entre tentativas
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"],
    raise_on_status=False,
)
_adapter = HTTPAdapter(max_retries=_retry)

SESSION = requests.Session()
SESSION.mount("https://", _adapter)
SESSION.mount("http://", _adapter)
