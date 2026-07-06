import { useEffect, useRef } from "react";

// ─── TradingViewChart ────────────────────────────────────────────────────────
// Embed oficial e gratuito do widget "Advanced Real-Time Chart" da TradingView
// (script s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js)
// -- não precisa de chave de API nem passa pelo nosso backend, os dados e o
// gráfico rodam num iframe deles no domínio da própria TradingView.
//
// Sem prefixo de bolsa (ex.: "NASDAQ:"), o widget resolve o símbolo pela
// listagem mais líquida -- funciona bem pros tickers US da cesta. Se algum
// ticker resolver errado no futuro, dá pra mapear pra "BOLSA:TICKER" aqui.

interface TradingViewChartProps {
  symbol: string;
  height?: number;
  interval?: string;
  hideSideToolbar?: boolean;
}

export function TradingViewChart({ symbol, height = 400, interval = "D", hideSideToolbar = true }: TradingViewChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    container.innerHTML = "";

    const widgetDiv = document.createElement("div");
    widgetDiv.className = "tradingview-widget-container__widget";
    widgetDiv.style.height = "100%";
    widgetDiv.style.width = "100%";
    container.appendChild(widgetDiv);

    const script = document.createElement("script");
    script.type = "text/javascript";
    script.src = "https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js";
    script.async = true;
    // O widget lê essa config do próprio texto da tag <script> (não é eval
    // nosso) -- é assim que o embed oficial da TradingView funciona.
    script.text = JSON.stringify({
      autosize: true,
      symbol,
      interval,
      timezone: "America/Sao_Paulo",
      theme: "dark",
      style: "1",
      locale: "br",
      allow_symbol_change: false,
      hide_side_toolbar: hideSideToolbar,
      calendar: false,
      support_host: "https://www.tradingview.com",
    });
    container.appendChild(script);

    return () => {
      container.innerHTML = "";
    };
  }, [symbol, interval, hideSideToolbar]);

  // O script injeta autosize:true, que faz a própria TradingView sobrescrever
  // a altura do elemento com a classe "tradingview-widget-container" pra
  // 100% (ela assume que é ela quem controla o próprio container, esperando
  // que UM PAI tenha a altura fixa). Por isso a altura pedida vai num wrapper
  // por fora, não no elemento que o script vai mexer -- senão a altura pedida
  // é sobrescrita e o gráfico colapsa pra um valor mínimo.
  return (
    <div style={{ height, width: "100%" }}>
      <div className="tradingview-widget-container" ref={containerRef} style={{ height: "100%", width: "100%" }} />
    </div>
  );
}
