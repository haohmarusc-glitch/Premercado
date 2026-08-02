import ipaddress
import os
import re
import socket
from urllib.parse import urlparse


def validate_api_key(key, expected_prefix="sk-ant-"):
    if not key or not isinstance(key, str):
        return False
    if len(key) < 20:
        return False
    if expected_prefix and not key.startswith(expected_prefix):
        return False
    return True


def sanitize_for_llm(text):
    if not text or not isinstance(text, str):
        return ""
    patterns = [
        r"(?i)(system\s*prompt|ignore\s*previous|you\s*are\s*now|new\s*instruction)",
        r"(?i)(forget\s*everything|disregard\s*all|override\s*instructions)",
        r"(?i)(act\s*as\s*if|pretend\s*to\s*be|now\s*you\s*are)",
    ]
    for p in patterns:
        text = re.sub(p, "[REDACTED]", text)
    text = re.sub(r"[!?]{3,}", "!!", text)
    text = re.sub(r"[#$%^&*]{5,}", "***", text)
    if len(text) > 50000:
        text = text[:50000] + "\n\n[TRUNCADO]"
    return text


def sanitize_ticker(ticker):
    if not ticker or not isinstance(ticker, str):
        raise ValueError("Ticker invalido")
    cleaned = re.sub(r"[^A-Za-z0-9.\-]", "", ticker).upper()
    # Base (ex: NVDA, RADL3) + sufixo opcional de bolsa/classe (ex: .SA, -B)
    if not re.fullmatch(r"[A-Z0-9]{1,8}(?:[.\-][A-Z0-9]{1,4})?", cleaned):
        raise ValueError(f"Ticker invalido: {ticker}")
    return cleaned


def _host_is_internal(host):
    """True se o host aponta pra rede interna/loopback/metadata de cloud.

    Normaliza o host como IP antes de decidir, em vez de casar o texto cru
    contra uma lista de regex. A versão anterior comparava padrões tipo
    `127\\.\\d+\\.\\d+\\.\\d+` e deixava passar TODA forma alternativa de
    escrever o mesmo endereço -- `127.1`, `127.0.1`, `2130706433` (decimal)
    e `0x7f000001` (hex) chegam todas em 127.0.0.1 no resolver do SO, mas
    nenhuma casa aquele regex. `socket.inet_aton` aceita exatamente esse
    conjunto de formas legadas, então converter primeiro e só depois
    classificar com `ipaddress` fecha as quatro de uma vez, sem precisar
    prever cada grafia.
    """
    if not host:
        return True  # sem host não dá pra afirmar que é externo
    h = host.strip().strip(".").lower()
    if h == "localhost" or h.endswith(".localhost"):
        return True

    # IPv6 vem entre colchetes na URL (ex.: "[::1]")
    candidate = h[1:-1] if h.startswith("[") and h.endswith("]") else h

    ip = None
    try:
        ip = ipaddress.ip_address(candidate)
    except ValueError:
        # Formas legadas de IPv4 (curta/decimal/hex) que ip_address recusa
        try:
            ip = ipaddress.ip_address(socket.inet_aton(candidate))
        except (OSError, ValueError):
            ip = None

    if ip is None:
        return False  # nome DNS comum -- ver limitação no docstring de sanitize_url

    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return (
        ip.is_loopback or ip.is_private or ip.is_link_local
        or ip.is_reserved or ip.is_multicast or ip.is_unspecified
    )


def sanitize_url(url):
    """Valida uma URL de fonte externa antes de buscá-la (guarda de SSRF).

    Só o HOST é inspecionado, não a URL inteira: a versão anterior rodava os
    padrões de bloqueio sobre a string toda, então uma URL legítima com
    "localhost" ou um IP privado em qualquer lugar do path/query
    (ex.: https://data.sec.gov/search?q=localhost) era rejeitada sem motivo.

    Limitação conhecida: um nome DNS que RESOLVE pra um IP interno passa --
    checar isso exigiria resolução de DNS aqui dentro (I/O de rede numa
    função pura) e ainda assim não fecharia DNS rebinding, já que o endereço
    pode mudar entre a checagem e a requisição de verdade. Esta função é a
    primeira barreira, não a única: as chamadas de rede daqui vão só pra
    domínios fixos conhecidos (SEC, FRED, Yahoo, etc.).
    """
    if not url or not isinstance(url, str):
        raise ValueError("URL invalida")
    try:
        parsed = urlparse(url)
    except ValueError:
        raise ValueError(f"URL invalida: {url}")
    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError(f"Protocolo invalido: {url}")
    try:
        host = parsed.hostname
    except ValueError:
        raise ValueError(f"URL invalida: {url}")
    if _host_is_internal(host):
        raise ValueError(f"URL bloqueada: {url}")
    return url


def friendly_error(exc):
    """Mensagem curta e sem detalhes técnicos pra devolver ao frontend quando uma
    chamada de dados externa (yfinance, SEC EDGAR, etc.) falha -- nunca a exceção
    Python crua (tipo "Failed to perform, curl: (56) CONNECT tunnel failed,
    response 403" ou "'exchangeTimezoneName'"), que vaza detalhes de infra e é
    ilegível pra quem não é dev. Quem chamar isso deve logar a exceção original
    em stderr antes -- essa função só decide o texto exibido, não descarta o erro.
    """
    text = str(exc).lower()
    network_markers = (
        "connect", "tunnel", "timeout", "timed out", "403", "429",
        "connection", "proxy", "refused", "reset", "unreachable", "dns",
        "urlopen",
    )
    if any(m in text for m in network_markers):
        return "Dados indisponíveis (falha de conexão com a fonte externa)"
    return "Dados indisponíveis no momento"


def mask_sensitive_data(text):
    if not text:
        return text
    text = re.sub(r"sk-ant-[a-zA-Z0-9]{20,}", "sk-ant-***MASKED***", text)
    text = re.sub(r"sk-[a-zA-Z0-9]{20,}", "sk-***MASKED***", text)
    text = re.sub(r"Bearer\s+[a-zA-Z0-9\-_]{20,}", "Bearer ***MASKED***", text)
    text = re.sub(r"://[^:]+:[^@]+@", "://***:***@", text)
    # Segredo em QUERY STRING. Vazou de verdade em produção (02/08): a FMP
    # respondeu 403, o requests põe a URL inteira na mensagem da exceção, e o
    # print do erro em news_sources.py mandou a chave crua pro log do servidor:
    #   [news_sources] fmp/GOOGL: 403 Client Error: Forbidden for url:
    #   https://financialmodelingprep.com/api/v3/stock_news?...&apikey=<chave>
    # Os provedores que este repo usa passam credencial assim: FMP (apikey),
    # Finnhub (token), FRED (api_key), ROIC. As regras acima não pegavam nenhum
    # deles -- só cobriam chave da Anthropic/OpenAI, Bearer e credencial de URL.
    text = re.sub(
        r"(?i)([?&](?:apikey|api_key|token|access_token|auth|key)=)[^&\s\"']+",
        r"\1***MASKED***",
        text,
    )
    return text
