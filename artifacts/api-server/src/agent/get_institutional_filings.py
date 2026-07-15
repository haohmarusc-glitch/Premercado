"""Rastreador de filings 13F (holdings institucionais) — standalone subprocess.

Acompanha uma lista curada de gestores institucionais ("smart money") e
reporta os Form 13F-HR mais recentes que cada um arquivou na SEC — inclusive
se o mais recente é novo desde o trimestre anterior. NÃO faz o parsing
holding-a-holding da tabela de posições (o "information table" de cada
filing) porque o nome do arquivo XML dentro de cada accession não é
padronizado entre gestores; em vez disso devolve o link direto pro filing no
EDGAR pra leitura manual. Mesmo padrão de acesso à SEC já usado (e testado em
produção) por market_alerts.py._fetch_edgar_recent — free, sem API key.

Lista padrão configurável via env INSTITUTIONAL_CIKS (formato
"cik:Rótulo,cik:Rótulo"); se omitida, usa DEFAULT_FILERS abaixo.

Input (stdin JSON):  {}  (sem parâmetros -- lista vem de env/default)
Output (stdout JSON): {"filers": [{cik, label, name, latestFiling, previousFiling, isNew}, ...]}
"""
import sys, json, os, urllib.request
from security import friendly_error

SEC_USER_AGENT = "Jefferson Investor jefferson@example.com"

# CIKs bem conhecidos e estáveis de gestores "smart money" acompanhados por
# padrão. Confirme/adicione outros via INSTITUTIONAL_CIKS -- busque o CIK de
# um gestor em https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany
DEFAULT_FILERS = [
    ("0001067983", "Berkshire Hathaway"),
    ("0001037389", "Renaissance Technologies"),
]

def _parse_env_filers(raw: str) -> list[tuple[str, str]]:
    out = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        cik, _, label = chunk.partition(":")
        cik = cik.strip().zfill(10)
        if cik.isdigit():
            out.append((cik, label.strip() or cik))
    return out

def resolve_filers() -> list[tuple[str, str]]:
    raw = os.environ.get("INSTITUTIONAL_CIKS", "").strip()
    if raw:
        parsed = _parse_env_filers(raw)
        if parsed:
            return parsed
    return DEFAULT_FILERS

def fetch_filer(cik: str, label: str) -> dict:
    cik = cik.zfill(10)
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": SEC_USER_AGENT})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[get_institutional_filings] {cik} ({label}): {e}", file=sys.stderr)
        return {"cik": cik, "label": label, "error": friendly_error(e)}

    name = data.get("name") or label
    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accs = recent.get("accessionNumber", [])

    filings_13f = []
    for i in range(len(forms)):
        if str(forms[i]).startswith("13F-HR"):
            acc = accs[i] if i < len(accs) else ""
            filings_13f.append({
                "filingDate": dates[i] if i < len(dates) else "",
                "accessionNumber": acc,
                "url": (
                    f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
                    f"&CIK={int(cik)}&type=13F-HR&dateb=&owner=include&count=10"
                    if not acc else
                    f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/{acc}-index.htm"
                ),
            })
        if len(filings_13f) >= 2:
            break

    if not filings_13f:
        return {"cik": cik, "label": label, "name": name, "error": "Nenhum 13F-HR encontrado"}

    return {
        "cik": cik,
        "label": label,
        "name": name,
        "latestFiling": filings_13f[0],
        "previousFiling": filings_13f[1] if len(filings_13f) > 1 else None,
    }

if __name__ == "__main__":
    try:
        json.loads(sys.stdin.read() or "{}")
    except Exception:
        pass
    filers = resolve_filers()
    results = [fetch_filer(cik, label) for cik, label in filers]
    print(json.dumps({"filers": results}, ensure_ascii=False))
