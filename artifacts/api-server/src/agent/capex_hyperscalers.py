"""
Capex dos hiperescaladores -- a tese de IA/data center virando FATO datado.

O usuário observou, com razão, que "a tese está ganhando tração" é
informação real que a estatística de preço não captura. Ela não precisa
ficar como opinião: o capex de quem constrói os data centers é publicado,
trimestre a trimestre, e mede o buildout diretamente. A Microsoft saiu de
US$ 64,6 bi (ano fiscal 2025) para US$ 115,9 bi (2026) -- não é vago, é
número auditável.

O que este módulo NÃO faz: virar sinal de compra, voto ou multiplicador de
sizing. Isso é o RegimeStage que foi arquivado em 20/08/2026 (modulador
sobre base sem edge), e aqui teria um agravante estatístico próprio: capex
dá ~4 pontos por ano, e testar "capex acelerando -> retorno maior" com uma
ou duas dúzias de observações não sustenta conclusão nenhuma. O lugar dele
é CONTEXTO medido -- entra no snapshot do Veredito como fato com data,
sujeito ao validador como qualquer outro número.

Duas armadilhas tratadas explicitamente:

1. DATA DE DISPONIBILIDADE. O capex do trimestre encerrado em 30/06 só
   passa a existir para o mundo quando a empresa reporta, semanas depois.
   Usar o número a partir do fim do trimestre é look-ahead -- o mesmo vício
   que o backtest tinha até 20/08. Cada linha carrega `disponivelEm`, e
   quem for condicionar qualquer coisa a este dado usa ESSA data.

2. TRIMESTRE INCOMPLETO. Somar o grupo quando só três das cinco empresas
   reportaram produz um total menor que o anterior -- uma "queda de capex"
   que é só calendário. O agregado marca `completo: false` e o consumidor
   mostra o aviso em vez do número.

Fonte: yfinance (grátis, sem cota) com Alpha Vantage como fallback -- a cota
da AV é de 15 chamadas/dia e já é disputada pelo calendário de earnings e
pelas notícias, então gastar cinco delas aqui seria trocar um fato novo por
um fato existente. A fonte usada é declarada em cada linha.

Rodar (na VPS, dentro do container):
    docker compose exec -T -w /app/artifacts/api-server/src app \
      /app/.venv/bin/python -m agent.capex_hyperscalers < /dev/null
"""
import json
import os
import sys
from datetime import date, datetime, timedelta

try:
    import json_seguro
    from brt import today_brt
except ImportError:
    from agent import json_seguro
    from agent.brt import today_brt

OVERLAY_PATH_DEFAULT = "/var/cache/premercado/capex_hyperscalers.json"

# Quem constrói o data center. ORCL entra porque virou comprador relevante
# de capacidade; os "neoclouds" ficam de fora enquanto não tiverem série
# trimestral longa o bastante para variação a/a fazer sentido.
HYPERSCALERS = [
    ("MSFT", "Microsoft"),
    ("GOOGL", "Alphabet"),
    ("AMZN", "Amazon"),
    ("META", "Meta"),
    ("ORCL", "Oracle"),
]

# Defasagem conservadora entre o fim do trimestre fiscal e a divulgação,
# usada quando a data real de reporte não vem na fonte. 45 dias cobre o
# calendário típico das big techs (reportam entre 3 e 6 semanas depois) sem
# fingir precisão que não temos.
DIAS_ATE_DIVULGAR = 45
TRIMESTRES_GUARDADOS = 12

# Profundidade mínima por empresa. Descoberto na PRIMEIRA rodada real
# (25/08/2026): o yfinance devolve só ~4-5 trimestres de fluxo de caixa, e
# com isso (a) a variação a/a fica indisponível -- precisa de 5 trimestres --
# e (b) exigir que as CINCO empresas tenham o mesmo trimestre deixou apenas
# TRÊS trimestres completos, todos "acelerando": o experimento de regime não
# tinha lado de contraste para medir. A Alpha Vantage devolve 81 trimestres.
# Por isso a cascata deixou de ser "yfinance OU AV" e virou "yfinance,
# COMPLEMENTADO pela AV quando o histórico é raso": a fonte rápida e sem cota
# continua servindo o trimestre recente, e a cota só é gasta pela
# profundidade que ela é a única a ter.
PROFUNDIDADE_MINIMA = 10


# ── conta pura ───────────────────────────────────────────────────────────────

def trimestre_calendario(data_iso: str) -> str | None:
    """"2026-06-30" -> "2026Q2". As big techs têm anos fiscais diferentes
    (o da Microsoft fecha em junho), mas os TRIMESTRES delas terminam nos
    mesmos meses de calendário -- agregar por trimestre-calendário é o que
    permite somar maçãs com maçãs."""
    try:
        d = datetime.strptime(data_iso[:10], "%Y-%m-%d").date()
    except Exception:
        return None
    return f"{d.year}Q{(d.month - 1) // 3 + 1}"


def disponivel_em(data_iso: str, dias: int = DIAS_ATE_DIVULGAR) -> str | None:
    """A data em que o número passou a existir para quem olha de fora."""
    try:
        d = datetime.strptime(data_iso[:10], "%Y-%m-%d").date()
    except Exception:
        return None
    return (d + timedelta(days=dias)).isoformat()


def _pct(atual: float, anterior: float) -> float | None:
    if anterior in (None, 0) or atual is None:
        return None
    return round((atual - anterior) / abs(anterior) * 100, 1)


def agregar(por_empresa: dict, hoje: str | None = None) -> list:
    """[{trimestre, totalUsd, empresas, completo, variacaoQoQPct,
    variacaoYoYPct, disponivelEm}], do mais antigo para o mais novo.

    `completo` é falso quando nem todas as empresas do grupo reportaram
    aquele trimestre -- somar parcial produziria uma "queda" que é só
    calendário, e essa é a leitura errada mais fácil de fazer aqui."""
    esperado = len(por_empresa)
    trimestres: dict = {}
    for ticker, linhas in por_empresa.items():
        for l in linhas:
            t = l.get("trimestre")
            if not t or l.get("capexUsd") is None:
                continue
            slot = trimestres.setdefault(t, {"capex": {}, "disponivel": []})
            slot["capex"][ticker] = float(l["capexUsd"])
            if l.get("disponivelEm"):
                slot["disponivel"].append(l["disponivelEm"])

    ordenados = sorted(trimestres)
    saida = []
    for t in ordenados:
        slot = trimestres[t]
        total = sum(slot["capex"].values())
        # A informação do trimestre só está completa quando a ÚLTIMA
        # empresa reportou -- é a data que vale para uso posterior.
        disp = max(slot["disponivel"]) if slot["disponivel"] else None
        saida.append({
            "trimestre": t,
            "totalUsd": round(total, 2),
            "empresas": sorted(slot["capex"]),
            "completo": len(slot["capex"]) == esperado,
            "disponivelEm": disp,
            "porEmpresaUsd": {k: round(v, 2) for k, v in sorted(slot["capex"].items())},
        })

    por_trimestre = {r["trimestre"]: r for r in saida}
    for r in saida:
        ano, q = int(r["trimestre"][:4]), int(r["trimestre"][-1])
        ant = por_trimestre.get(f"{ano}Q{q - 1}" if q > 1 else f"{ano - 1}Q4")
        ano_passado = por_trimestre.get(f"{ano - 1}Q{q}")
        # Variação só entre trimestres COMPLETOS: comparar completo com
        # parcial inventa uma queda.
        r["variacaoQoQPct"] = (_pct(r["totalUsd"], ant["totalUsd"])
                               if ant and ant["completo"] and r["completo"] else None)
        r["variacaoYoYPct"] = (_pct(r["totalUsd"], ano_passado["totalUsd"])
                               if ano_passado and ano_passado["completo"] and r["completo"] else None)

    hoje = hoje or today_brt().isoformat()
    return [r for r in saida if not r["disponivelEm"] or r["disponivelEm"] <= hoje][-TRIMESTRES_GUARDADOS:]


def resumo(agregado: list) -> dict:
    """O que o Veredito cita: último trimestre completo e a direção."""
    completos = [r for r in agregado if r["completo"]]
    if not completos:
        return {"disponivel": False,
                "nota": "nenhum trimestre com todas as empresas reportadas ainda"}
    ultimo = completos[-1]
    direcao = "estável"
    if ultimo["variacaoQoQPct"] is not None:
        direcao = ("acelerando" if ultimo["variacaoQoQPct"] > 3
                   else "desacelerando" if ultimo["variacaoQoQPct"] < -3 else "estável")
    return {
        "disponivel": True,
        "trimestre": ultimo["trimestre"],
        "totalUsdBi": round(ultimo["totalUsd"] / 1e9, 1),
        "variacaoQoQPct": ultimo["variacaoQoQPct"],
        "variacaoYoYPct": ultimo["variacaoYoYPct"],
        "direcao": direcao,
        "disponivelEm": ultimo["disponivelEm"],
        "empresas": ultimo["empresas"],
    }


# ── coleta (rede) ────────────────────────────────────────────────────────────

def _do_yfinance(ticker: str) -> list:
    import yfinance as yf
    df = yf.Ticker(ticker).quarterly_cashflow
    if df is None or df.empty:
        return []
    linha = None
    for nome in ("Capital Expenditure", "CapitalExpenditures", "Capital Expenditures"):
        if nome in df.index:
            linha = df.loc[nome]
            break
    if linha is None:
        return []
    saida = []
    for col, valor in linha.items():
        if valor is None or valor != valor:  # NaN
            continue
        data = str(col)[:10]
        t = trimestre_calendario(data)
        if not t:
            continue
        # yfinance devolve capex NEGATIVO (saída de caixa); o módulo publica
        # a magnitude do investimento, que é como se lê "capex de 35 bi".
        saida.append({"trimestre": t, "fimFiscal": data, "capexUsd": abs(float(valor)),
                      "disponivelEm": disponivel_em(data), "fonte": "yfinance"})
    return saida


def _do_alpha_vantage(ticker: str) -> list:
    """Fallback. Gasta cota (15/dia, compartilhada com earnings e notícias),
    por isso só roda quando o yfinance não devolveu nada para o ticker."""
    try:
        from alpha_vantage_provider import _api_key  # type: ignore
    except ImportError:
        from agent.alpha_vantage_provider import _api_key  # type: ignore
    try:
        from http_retry import SESSION
    except ImportError:
        from agent.http_retry import SESSION
    chave = _api_key()
    if not chave:
        return []
    # A cota é de 15/dia e compartilhada com o calendário de earnings e as
    # notícias. Debitar é o ponto: orçamento que alguém não debita é
    # orçamento que não protege ninguém (mesma regra de
    # atualizar_earnings.py). Cinco chamadas por SEMANA cabem com folga --
    # mas só se estiverem contadas.
    try:
        from provider_health import consumir_orcamento_diario
    except ImportError:
        from agent.provider_health import consumir_orcamento_diario
    orcamento = int(os.environ.get("AGENT_ALPHAVANTAGE_MAX_DIA", "15"))
    if not consumir_orcamento_diario("alphavantage", orcamento):
        print(f"[capex] cota diária da Alpha Vantage ({orcamento}) esgotada — "
              f"{ticker} fica com o histórico raso do yfinance", file=sys.stderr)
        return []
    r = SESSION.get("https://www.alphavantage.co/query",
                    params={"function": "CASH_FLOW", "symbol": ticker, "apikey": chave},
                    timeout=20)
    r.raise_for_status()
    dados = r.json()
    saida = []
    for rel in (dados.get("quarterlyReports") or []):
        bruto = rel.get("capitalExpenditures")
        if bruto in (None, "None", ""):
            continue
        data = str(rel.get("fiscalDateEnding", ""))[:10]
        t = trimestre_calendario(data)
        if not t:
            continue
        saida.append({"trimestre": t, "fimFiscal": data, "capexUsd": abs(float(bruto)),
                      "disponivelEm": disponivel_em(data), "fonte": "alpha_vantage"})
    return saida


def combinar(principal: list, complemento: list) -> list:
    """Une as duas fontes por trimestre, com a PRINCIPAL vencendo o empate.

    O yfinance é a fonte primária (rápida, sem cota) e cobre o trimestre
    recente; a Alpha Vantage entra pela profundidade. Onde as duas têm o
    mesmo trimestre, fica o valor do yfinance -- trocar a fonte de um
    trimestre para o outro no meio da série criaria degrau artificial na
    variação t/t, que é justamente o número que se lê aqui."""
    por_trimestre = {l["trimestre"]: l for l in complemento if l.get("trimestre")}
    por_trimestre.update({l["trimestre"]: l for l in principal if l.get("trimestre")})
    return sorted(por_trimestre.values(), key=lambda l: l["trimestre"])


def coletar(tickers=None, *, yf_fn=_do_yfinance, av_fn=_do_alpha_vantage,
            profundidade_minima: int = PROFUNDIDADE_MINIMA) -> dict:
    """{ticker: [linhas]} + relatório de falhas. Funções injetáveis para a
    suíte exercitar a cascata sem rede.

    A AV é chamada quando o yfinance vem VAZIO ou RASO -- ver
    PROFUNDIDADE_MINIMA para o incidente que motivou o "raso"."""
    alvo = tickers or [t for t, _ in HYPERSCALERS]
    por_empresa, falhas, rasos = {}, [], []
    for t in alvo:
        linhas = []
        try:
            linhas = yf_fn(t)
        except Exception as e:
            print(f"[capex] yfinance falhou em {t}: {type(e).__name__}: {e}", file=sys.stderr)
        if len(linhas) < profundidade_minima:
            motivo = ("sem capex no yfinance" if not linhas
                      else f"só {len(linhas)} trimestres no yfinance "
                           f"(mínimo {profundidade_minima} para variação a/a e regime)")
            print(f"[capex] {t}: {motivo}, complementando com Alpha Vantage", file=sys.stderr)
            try:
                linhas = combinar(linhas, av_fn(t))
            except Exception as e:
                print(f"[capex] alpha vantage falhou em {t}: {type(e).__name__}: {e}",
                      file=sys.stderr)
            if len(linhas) < profundidade_minima:
                rasos.append(t)
        if linhas:
            por_empresa[t] = linhas
        else:
            falhas.append(t)
            print(f"[capex] {t}: SEM DADO nas duas fontes", file=sys.stderr)
    if rasos:
        print(f"[capex] histórico ainda raso em {', '.join(rasos)} -- variação a/a e "
              f"experimento de regime ficam limitados", file=sys.stderr)
    return {"porEmpresa": por_empresa, "falhas": falhas, "rasos": rasos}


def montar(tickers=None, **kw) -> dict:
    col = coletar(tickers, **kw)
    agregado = agregar(col["porEmpresa"])
    fontes = sorted({l["fonte"] for linhas in col["porEmpresa"].values() for l in linhas})
    return {
        "coletadoEm": today_brt().isoformat(),
        "empresasPedidas": len(tickers or HYPERSCALERS),
        "empresasComDado": len(col["porEmpresa"]),
        "falhas": col["falhas"],
        "historicoRaso": col.get("rasos", []),
        "fontes": fontes,
        "trimestres": agregado,
        "resumo": resumo(agregado),
    }


def gravar_overlay(dados: dict, caminho: str = OVERLAY_PATH_DEFAULT) -> bool:
    try:
        os.makedirs(os.path.dirname(caminho) or ".", exist_ok=True)
        tmp = caminho + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json_seguro.dumps(dados))
        os.replace(tmp, caminho)
        return True
    except Exception as e:
        print(f"[capex] não consegui gravar o overlay ({caminho}): {e}", file=sys.stderr)
        return False


def ler_overlay(caminho: str = OVERLAY_PATH_DEFAULT) -> dict | None:
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except Exception as e:
        print(f"[capex] overlay ilegível ({caminho}): {e}", file=sys.stderr)
        return None


if __name__ == "__main__":
    # --json: saída para o checker de fundo consumir (mesmo contrato de
    # atualizar_earnings.py). Sem a flag, imprime o resumo legível.
    modo_json = "--json" in sys.argv
    caminho = os.environ.get("CAPEX_OVERLAY_PATH", OVERLAY_PATH_DEFAULT)
    try:
        dados = montar()
    except Exception as e:
        if modo_json:
            print(json_seguro.dumps({"ok": False, "erro": f"{type(e).__name__}: {e}"}))
            sys.exit(0)
        raise
    if modo_json:
        gravou = gravar_overlay(dados, caminho)
        r = dados["resumo"]
        print(json_seguro.dumps({
            "ok": bool(gravou and r.get("disponivel")),
            "trimestre": r.get("trimestre"),
            "totalUsdBi": r.get("totalUsdBi"),
            "direcao": r.get("direcao"),
            "variacaoQoQPct": r.get("variacaoQoQPct"),
            "empresasComDado": dados["empresasComDado"],
            "falhas": dados["falhas"],
            "fontes": dados["fontes"],
            "overlay": caminho if gravou else None,
        }))
        sys.exit(0)
    r = dados["resumo"]
    if r.get("disponivel"):
        yoy = f"{r['variacaoYoYPct']}%" if r.get("variacaoYoYPct") is not None else "indisponível"
        print(f"{r['trimestre']}: US$ {r['totalUsdBi']} bi de capex somado "
              f"({len(r['empresas'])} empresas) — {r['direcao']} "
              f"(t/t {r['variacaoQoQPct']}%, a/a {yoy})")
    else:
        print(f"sem trimestre completo: {r.get('nota')}")
    if dados["falhas"]:
        print(f"sem dado: {', '.join(dados['falhas'])}")
    if gravar_overlay(dados, caminho):
        print(f"overlay gravado em {caminho}")
