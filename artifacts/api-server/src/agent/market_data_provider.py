"""
market_data_provider.py — Fachada única para histórico diário e cotação,
com fallback de fonte, disjuntor por provedor e checagem cruzada.

## O problema que isto resolve

24 módulos chamam `yfinance` direto (README, "Fontes de dado"). Cada um
decide sozinho o que fazer quando o Yahoo bloqueia/erra — a maioria devolve
erro e alguns silenciosamente mostram dado velho do cache. Não existe hoje
NENHUM caminho que troque de fonte quando a primária falha: se o yfinance
sai do ar, o sistema inteiro perde histórico e cotação ao mesmo tempo, apesar
de o preço de fechamento diário existir em pelo menos mais um lugar gratuito
(Alpha Vantage).

Este módulo não troca o yfinance pela fonte externa — ele o mantém como fonte
PRIMÁRIA (é a única com pré-mercado, `fast_info`, opções etc.) e adiciona uma
cadeia de degradação explícita e observável:

    yfinance (com retry curto)
      -> cache em disco dentro do TTL
      -> cache VENCIDO (last-known-good), conferido contra a Alpha Vantage
      -> Alpha Vantage (fallback externo, marcado)
      -> erro explícito (nunca dado inventado)

Cada camada é OPT-IN por módulo consumidor — nenhum dos 24 call-sites
existentes precisa mudar para o repo continuar funcionando como hoje; este
módulo é aditivo.

## Por que isto custa pouco

- Nenhuma chave nova: a da Alpha Vantage já existe no projeto (usada pelo
  feed de notícias) e o endpoint de série diária é do tier gratuito.
- Reaproveita infraestrutura que já existe (`hist_cache.py`, `http_retry.py`,
  `brt.py`) em vez de criar uma segunda forma de cachear/retentar.
- É aditivo: pode ser adotado módulo a módulo (começando pelos mais críticos
  — `market_alerts.py`, `get_quotes.py`) sem exigir uma reescrita coordenada
  dos 24 pontos de uso do yfinance.
"""
from __future__ import annotations

import random
import sys
import time
from dataclasses import dataclass, field

import pandas as pd
import yfinance as yf

try:
    from . import hist_cache
    from . import provider_health
    from . import alpha_vantage_provider
    from .security import friendly_error
except ImportError:  # execução standalone
    import hist_cache
    import provider_health
    import alpha_vantage_provider
    from security import friendly_error

# Diferença de fechamento acima disso entre duas fontes vira warning
# explícito em vez de silêncio — mesmo espírito do incidente da vol coletada à
# mão, que divergia até 3,6x da medida pelo agente e contaminava stop e sizing
# sem ninguém perceber.
CROSS_CHECK_TOLERANCE_PCT = 1.5

# Retry CURTO e local, antes de acionar o disjuntor — não confundir com o
# retry de rede genérico de http_retry.py (que é para GET simples tipo SEC
# EDGAR). yfinance já faz sua própria sessão HTTP internamente; aqui só
# damos uma segunda chance para um erro transitório (rate limit momentâneo)
# antes de declarar falha ao circuit breaker.
_YF_ATTEMPTS = 2
_YF_BACKOFF_BASE_S = 0.6


@dataclass
class HistoryResult:
    df: pd.DataFrame | None
    source: str  # "yfinance" | "yfinance_cache" | "cache_stale" | "alphavantage" | "none"
    is_stale: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.df is not None and not self.df.empty


@dataclass
class QuoteResult:
    quote: dict | None
    source: str  # "yfinance" | "alphavantage_eod" | "none"
    is_delayed: bool = False
    warnings: list[str] = field(default_factory=list)


def _yf_history_with_retry(ticker: str, period: str, auto_adjust: bool) -> pd.DataFrame | None:
    last_exc: Exception | None = None
    for attempt in range(_YF_ATTEMPTS):
        try:
            df = yf.Ticker(ticker).history(period=period, auto_adjust=auto_adjust)
            if df is not None and not df.empty:
                return df
            last_exc = RuntimeError("resposta vazia (possível bloqueio de rate limit)")
        except Exception as ex:  # noqa: BLE001 — fonte externa, qualquer exceção é fail-open
            last_exc = ex
        if attempt < _YF_ATTEMPTS - 1:
            time.sleep(_YF_BACKOFF_BASE_S * (attempt + 1) + random.uniform(0, 0.3))
    if last_exc is not None:
        print(f"[market_data_provider] yfinance history {ticker}: {last_exc}",
              file=sys.stderr)
    return None


def get_daily_history(
    ticker: str, period: str = "6mo", *, auto_adjust: bool = False,
    permitir_externa: bool = True,
) -> HistoryResult:
    """Histórico diário com fallback. Sempre retorna um `HistoryResult` — nunca
    lança exceção; `ok=False` é o sinal de "sem dado utilizável em fonte
    nenhuma", que o chamador já sabe tratar (mesmo contrato de erro explícito
    que o resto do repo usa, ex.: `get_quotes._empty_quote`).

    `permitir_externa=False` corta a cadeia no cache vencido, sem nunca tocar a
    fonte externa. Serve a quem pede série AJUSTADA (`auto_adjust=True`): a
    externa devolve preço "as traded", e um desdobramento dentro da janela
    apareceria como salto de preço — RSI, médias e tendência sairiam com um
    degrau que nunca existiu. O cache continua valendo porque foi gravado a
    partir do yfinance, já ajustado.

    O parâmetro existe para essa regra virar CÓDIGO. Ela já estava escrita no
    plano ("nunca migrar o que usa auto_adjust=True"), e regra que mora só em
    documento é regra que alguém integra sem querer daqui a três meses.
    """
    warnings: list[str] = []

    # 1) yfinance, mas só se o disjuntor não estiver aberto — evita pagar o
    #    timeout de rede inteiro quando já sabemos (últimos 5 min, mesma
    #    cadência do checker mais frequente) que a fonte está bloqueando.
    if not provider_health.is_open("yfinance"):
        df = _yf_history_with_retry(ticker, period, auto_adjust)
        if df is not None and not df.empty:
            provider_health.record_success("yfinance")
            hist_cache.guardar(ticker, period, df, auto_adjust=auto_adjust)
            return HistoryResult(df=df, source="yfinance")
        provider_health.record_failure("yfinance")
    else:
        warnings.append("yfinance em cooldown (falhas recentes) — pulado direto pro fallback")

    # 2) cache em disco dentro do TTL — mais rápido e mais confiável que uma
    #    fonte externa nova, mesmo que os dados tenham alguns minutos.
    cached_df = hist_cache.carregar(ticker, period, auto_adjust=auto_adjust)
    if cached_df is not None and not cached_df.empty:
        warnings.append("yfinance indisponível — servindo cache dentro do TTL")
        return HistoryResult(df=cached_df, source="yfinance_cache", warnings=warnings)

    # 3) cache VENCIDO (last-known-good). Aqui, e só aqui, a checagem cruzada
    #    tem os dois lados para comparar: um número velho que já andou é o
    #    caso mais perigoso da cadeia (alimenta stop e sizing parecendo
    #    normal), e a Alpha Vantage é a única segunda opinião disponível.
    #
    #    O plano original chamava o cross-check no ramo da Alpha Vantage, passando
    #    `cached_df` como referência — mas naquele ponto `cached_df` é
    #    necessariamente None (os dois `return` acima já teriam disparado),
    #    então a comparação nunca rodava. Aqui ela roda de verdade.
    stale_df = _load_stale_cache(ticker, period, auto_adjust)
    if stale_df is not None and not stale_df.empty:
        warnings.append("yfinance indisponível — servindo cache VENCIDO (last-known-good)")
        _conferir_cache_vencido(ticker, stale_df, period, warnings)
        return HistoryResult(df=stale_df, source="cache_stale", is_stale=True, warnings=warnings)

    if not permitir_externa:
        warnings.append(
            "Fonte externa não usada: série ajustada foi pedida, e a externa é "
            "\"as traded\" (um split na janela viraria degrau de preço)"
        )
        return HistoryResult(df=None, source="none", warnings=warnings)

    # 4) Alpha Vantage — fonte externa alternativa. Marcada explicitamente
    #    porque TIME_SERIES_DAILY é "as traded": o ajuste de split/dividendo
    #    pode não bater com auto_adjust=True. Sem referência local para
    #    conferir: se houvesse cache, teríamos servido ele acima.
    externo_df = alpha_vantage_provider.fetch_daily_history(ticker, period)
    if externo_df is not None and not externo_df.empty:
        provider_health.record_success("alphavantage")
        warnings.append(
            "yfinance e cache indisponíveis — servindo Alpha Vantage "
            "(fallback externo, ajuste de split/dividendo não confirmado)"
        )
        return HistoryResult(df=externo_df, source="alphavantage", warnings=warnings)
    provider_health.record_failure("alphavantage")

    warnings.append("Nenhuma fonte de histórico disponível (yfinance, cache e Alpha Vantage falharam)")
    return HistoryResult(df=None, source="none", warnings=warnings)


def _load_stale_cache(ticker: str, period: str, auto_adjust: bool) -> pd.DataFrame | None:
    """Lê o pickle do hist_cache ignorando o TTL — só para o caminho de
    emergência (todas as fontes vivas falharam). Reimplementa a leitura em
    vez de mudar `hist_cache.carregar()` para não afetar o comportamento dos
    ~20 outros consumidores que esperam `None` em cache vencido.
    """
    try:
        caminho = hist_cache._caminho(ticker, period, auto_adjust, "1d")  # noqa: SLF001
        import pickle
        with open(caminho, "rb") as f:
            df = pickle.load(f)
        return df if isinstance(df, pd.DataFrame) and not df.empty else None
    except Exception:
        return None


def _conferir_cache_vencido(
    ticker: str, stale_df: pd.DataFrame, period: str, warnings: list[str]
) -> None:
    """Pede à Alpha Vantage uma segunda opinião sobre o último fechamento do cache
    vencido que estamos prestes a servir.

    Custa uma chamada de cota num caminho raro (yfinance fora E cache fora do
    TTL). Nunca bloqueia a entrega: falha da Alpha Vantage — inclusive cota do
    dia esgotada — só significa "sem segunda opinião", e o cache é servido do
    mesmo jeito; o que muda é o relatório carregar ou não o aviso de
    divergência.
    """
    try:
        referencia = alpha_vantage_provider.fetch_daily_history(ticker, period)
    except Exception:
        return
    if referencia is None or referencia.empty:
        return
    _cross_check_last_close(ticker, referencia, stale_df, warnings)


def _cross_check_last_close(
    ticker: str,
    reference_df: pd.DataFrame | None,
    candidate_df: pd.DataFrame,
    warnings: list[str],
) -> None:
    """Compara o último fechamento de duas fontes, quando ambas existem, e
    registra divergência — não decide sozinho qual está certa. No incidente
    da vol coletada à mão, a resposta certa foi remedir, não escolher uma das
    duas por default."""
    if reference_df is None or reference_df.empty:
        return
    try:
        ref_close = float(reference_df["Close"].iloc[-1])
        cand_close = float(candidate_df["Close"].iloc[-1])
        if ref_close == 0:
            return
        diff_pct = abs(cand_close - ref_close) / ref_close * 100
        if diff_pct > CROSS_CHECK_TOLERANCE_PCT:
            warnings.append(
                f"Divergência de {diff_pct:.1f}% no último fechamento de {ticker} "
                f"({cand_close} servido vs {ref_close} na outra fonte) — checar "
                "manualmente antes de usar em stop/sizing"
            )
    except Exception:
        pass  # cross-check é bônus, nunca motivo de falha


@dataclass
class BatchClosesResult:
    """Fechamentos de VÁRIOS tickers numa matriz larga (colunas = tickers).

    `fontes` diz de onde veio a série de CADA ticker — num lote de fallback é
    normal metade vir do cache e metade não vir de lugar nenhum, e o chamador
    precisa saber quem é quem para marcar degradação com precisão.
    """
    closes: pd.DataFrame | None
    fontes: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.closes is not None and not self.closes.empty

    @property
    def degradadas(self) -> dict[str, str]:
        """Só os tickers cuja série não veio do caminho normal."""
        return {t: s for t, s in self.fontes.items()
                if s not in ("yfinance", "yfinance_cache")}


def _extrair_ohlcv_por_ticker(data: pd.DataFrame, ticker: str) -> pd.DataFrame | None:
    """Recorta o OHLCV completo de UM ticker do resultado do yf.download em
    lote (colunas MultiIndex campo×ticker). Devolve None se o recorte não
    tiver as colunas todas — melhor não gravar do que gravar um frame parcial:
    o hist_cache é compartilhado com get_technicals/get_trend, que esperam a
    forma completa, e um cache com metade das colunas é corrupção silenciosa
    (mesma classe da armadilha do auto_adjust na chave)."""
    try:
        if isinstance(data.columns, pd.MultiIndex):
            df = data.xs(ticker, axis=1, level=1)
        else:
            df = data  # lote de 1 ticker: colunas já são planas
        colunas = ["Open", "High", "Low", "Close", "Volume"]
        if not all(c in df.columns for c in colunas):
            return None
        df = df[colunas].dropna(how="all")
        return df if not df.empty else None
    except Exception:
        return None


def get_daily_closes_batch(
    tickers: list[str], period: str = "1y", *,
    auto_adjust: bool = False, permitir_externa: bool = True,
) -> BatchClosesResult:
    """Fechamentos diários de vários tickers, com fallback POR TICKER.

    Caminho feliz: UM yf.download em lote (é o que o chamador já fazia — não
    virar N chamadas quando a rede está saudável). No sucesso, cada recorte
    por ticker é gravado no hist_cache com a chave normal, então o fallback
    de amanhã se alimenta do lote de hoje.

    Quando o lote falha (ou o disjuntor já está aberto), cada ticker desce a
    cadeia individual de get_daily_history — que serve cache no TTL, cache
    vencido e, se permitido, a fonte externa. O registro no disjuntor fica a
    cargo dessas chamadas individuais: as primeiras falhas abrem o breaker e
    as seguintes vão direto ao cache, então o custo de rede é autolimitado.

    O resultado nunca mistura silenciosamente: `fontes` nomeia a origem de
    cada coluna, e um ticker sem fonte nenhuma simplesmente não vira coluna.
    """
    limpos = [t for t in dict.fromkeys(tickers) if t]
    if not limpos:
        return BatchClosesResult(closes=None, warnings=["Lote vazio"])

    warnings: list[str] = []

    if not provider_health.is_open("yfinance"):
        try:
            data = yf.download(limpos, period=period, interval="1d",
                               auto_adjust=auto_adjust, progress=False)
        except Exception as ex:  # noqa: BLE001 — fonte externa, fail-open
            print(f"[market_data_provider] yf.download lote: {ex}", file=sys.stderr)
            data = None
        if data is not None and not data.empty:
            provider_health.record_success("yfinance")
            closes = data["Close"] if "Close" in data else data
            if not hasattr(closes, "columns"):
                closes = closes.to_frame(name=limpos[0])
            fontes = {t: "yfinance" for t in limpos if t in closes.columns}
            for t in fontes:
                recorte = _extrair_ohlcv_por_ticker(data, t)
                if recorte is not None:
                    hist_cache.guardar(t, period, recorte, auto_adjust=auto_adjust)
            return BatchClosesResult(closes=closes, fontes=fontes)
        provider_health.record_failure("yfinance")
        warnings.append("yf.download em lote falhou — caindo para a cadeia por ticker")
    else:
        warnings.append("yfinance em cooldown (falhas recentes) — lote pulado, cadeia por ticker")

    colunas: dict[str, pd.Series] = {}
    fontes: dict[str, str] = {}
    for t in limpos:
        r = get_daily_history(t, period, auto_adjust=auto_adjust,
                              permitir_externa=permitir_externa)
        if r.ok and "Close" in r.df.columns:
            serie = r.df["Close"].dropna()
            if serie.index.tz is not None:
                serie.index = serie.index.tz_localize(None)
            colunas[t] = serie
            fontes[t] = r.source
    if not colunas:
        warnings.append("Nenhuma fonte de histórico disponível para o lote inteiro")
        return BatchClosesResult(closes=None, fontes=fontes, warnings=warnings)

    return BatchClosesResult(closes=pd.DataFrame(colunas), fontes=fontes, warnings=warnings)


def get_quote(ticker: str) -> QuoteResult:
    """Cotação com fallback. Diferente do histórico, aqui NÃO há como
    disfarçar a degradação: yfinance é a única fonte com pré-mercado/
    intradiário do repo, então qualquer fallback é necessariamente um
    fechamento diário do dia anterior — por isso `is_delayed=True` sempre
    que a fonte não é yfinance, para a UI poder mostrar isso explicitamente
    em vez de um preço "ao vivo" que na verdade é de ontem.
    """
    warnings: list[str] = []

    if not provider_health.is_open("yfinance"):
        try:
            fi = yf.Ticker(ticker).fast_info
            price = getattr(fi, "last_price", None)
            if price is not None:
                provider_health.record_success("yfinance")
                return QuoteResult(
                    quote={"price": round(float(price), 4), "currency": getattr(fi, "currency", None)},
                    source="yfinance",
                )
            provider_health.record_failure("yfinance")
        except Exception as ex:
            print(f"[market_data_provider] yfinance quote {ticker}: {friendly_error(ex)}",
                  file=sys.stderr)
            provider_health.record_failure("yfinance")
    else:
        # Mesmo aviso do histórico: quem consome precisa saber que a fonte
        # primária nem foi tentada, senão "atrasado" parece falha da fonte
        # externa quando o Yahoo é que nem chegou a ser consultado.
        warnings.append("yfinance em cooldown (falhas recentes) — pulado direto pro fallback")

    fallback = alpha_vantage_provider.fetch_last_close(ticker)
    if fallback is not None:
        provider_health.record_success("alphavantage")
        warnings.append(
            f"Cotação ao vivo indisponível — mostrando fechamento de "
            f"{fallback['asOf']} (Alpha Vantage, atrasado)"
        )
        return QuoteResult(quote=fallback, source="alphavantage_eod", is_delayed=True, warnings=warnings)

    provider_health.record_failure("alphavantage")
    warnings.append("Nenhuma fonte de cotação disponível")
    return QuoteResult(quote=None, source="none", warnings=warnings)


if __name__ == "__main__":
    import json

    symbols = sys.argv[1:] or ["NVDA"]
    out = {}
    for s in symbols:
        r = get_quote(s)
        out[s] = {"source": r.source, "delayed": r.is_delayed, "quote": r.quote, "warnings": r.warnings}
    print(json.dumps(out, indent=2, ensure_ascii=False))
