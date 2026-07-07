import urllib.request

url = "http://localhost:5000/api/portfolio"
req = urllib.request.Request(url, headers={"Authorization": "Bearer Jefferson"})

try:
    with urllib.request.urlopen(req) as response:
        print("STATUS:", response.status)
        print("BODY:", response.read().decode()[:800])
except urllib.error.HTTPError as e:
    print("HTTP ERROR:", e.code)
    print("BODY:", e.read().decode()[:800])
except Exception as e:
    print("ERRO:", e)
