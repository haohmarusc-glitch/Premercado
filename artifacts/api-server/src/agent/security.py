import os
import re


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


def sanitize_url(url):
    if not url or not isinstance(url, str):
        raise ValueError("URL invalida")
    if not any(url.startswith(p) for p in ("http://", "https://")):
        raise ValueError(f"Protocolo invalido: {url}")
    blocked = [
        r"localhost",
        r"127\.\d+\.\d+\.\d+",
        r"0\.0\.0\.0",
        r"10\.\d+\.\d+\.\d+",
        r"172\.(1[6-9]|2\d|3[01])\.\d+\.\d+",
        r"192\.168\.\d+\.\d+",
        r"169\.254\.\d+\.\d+",
        r"\[::1\]",
        r"::1(?![\da-f])",
        r"file://",
        r"ftp://",
    ]
    for p in blocked:
        if re.search(p, url, re.I):
            raise ValueError(f"URL bloqueada: {url}")
    return url


def mask_sensitive_data(text):
    if not text:
        return text
    text = re.sub(r"sk-ant-[a-zA-Z0-9]{20,}", "sk-ant-***MASKED***", text)
    text = re.sub(r"sk-[a-zA-Z0-9]{20,}", "sk-***MASKED***", text)
    text = re.sub(r"Bearer\s+[a-zA-Z0-9\-_]{20,}", "Bearer ***MASKED***", text)
    text = re.sub(r"://[^:]+:[^@]+@", "://***:***@", text)
    return text
