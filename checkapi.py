import os
import urllib.request

url = os.environ.get("CHECKAPI_URL", "http://localhost:5000/api/portfolio")
token = os.environ.get("OPERATOR_API_KEY")
if not token:
    raise SystemExit(
        "Defina a variável de ambiente OPERATOR_API_KEY antes de rodar este script "
        "(ex: OPERATOR_API_KEY=xxxxx python checkapi.py). "
        "Nunca hardcode o token no código."
    )
req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})

try:
    with urllib.request.urlopen(req) as response:
        print("STATUS:", response.status)
        print("BODY:", response.read().decode()[:800])
except urllib.error.HTTPError as e:
    print("HTTP ERROR:", e.code)
    print("BODY:", e.read().decode()[:800])
except Exception as e:
    print("ERRO:", e)
