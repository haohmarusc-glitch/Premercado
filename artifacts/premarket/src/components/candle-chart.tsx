import { useMemo, useState } from "react";

// ─── CandleChart ─────────────────────────────────────────────────────────────
// Candlestick em SVG puro (sem dependências externas).
// Verde = fechamento >= abertura; vermelho = fechamento < abertura.

export interface Candle {
  t: number; // timestamp ms
  o: number;
  h: number;
  l: number;
  c: number;
  v: number;
}

export interface NewsMarker {
  ts?: number | null; // timestamp de publicação (ms)
  tone: string; // "positivo" | "negativo"
  title: string;
}

interface CandleChartProps {
  candles: Candle[];
  height?: number;
  labelFor: (ts: number) => string;
  markers?: NewsMarker[];
  // "Criar alerta neste preço" (botão direito) -- recebe o preço já convertido
  // e as coordenadas de tela do clique, pro chamador posicionar o menu.
  onPriceContextMenu?: (price: number, clientX: number, clientY: number) => void;
}

const UP = "#22c55e";
const DOWN = "#ef4444";

function fmtPrice(n: number) {
  return n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function CandleChart({ candles, height = 200, labelFor, markers, onPriceContextMenu }: CandleChartProps) {
  const [hover, setHover] = useState<number | null>(null);
  const [openMarker, setOpenMarker] = useState<number | null>(null);

  const W = 1000; // viewBox width — escala junto com o container
  const H = height;
  const PAD_L = 8;
  const PAD_R = 64; // espaço pro eixo Y à direita
  const PAD_T = 8;
  const PAD_B = 22; // espaço pros labels do eixo X

  const geom = useMemo(() => {
    if (!candles.length) return null;
    const lows = candles.map((c) => c.l);
    const highs = candles.map((c) => c.h);
    const minP = Math.min(...lows);
    const maxP = Math.max(...highs);
    const pad = (maxP - minP) * 0.05 || 1;
    const lo = minP - pad;
    const hi = maxP + pad;

    const plotW = W - PAD_L - PAD_R;
    const plotH = H - PAD_T - PAD_B;
    const step = plotW / candles.length;
    const bodyW = Math.max(1.5, Math.min(14, step * 0.65));

    const y = (p: number) => PAD_T + plotH * (1 - (p - lo) / (hi - lo));
    const x = (i: number) => PAD_L + step * i + step / 2;

    // ~4 ticks no eixo Y
    const ticks: number[] = [];
    for (let k = 0; k <= 3; k++) ticks.push(lo + ((hi - lo) * k) / 3);

    // ~5 labels no eixo X
    const xIdx: number[] = [];
    const n = Math.min(5, candles.length);
    for (let k = 0; k < n; k++) xIdx.push(Math.round((candles.length - 1) * (k / Math.max(1, n - 1))));

    return { lo, hi, y, x, step, bodyW, ticks, xIdx, plotH };
  }, [candles, H]);

  if (!geom || !candles.length) return null;

  const { y, x, bodyW, ticks, xIdx, lo, hi, plotH } = geom;
  const hovered = hover != null ? candles[hover] : null;

  // Converte a posição Y do clique (pixels reais da tela) pra unidades do
  // viewBox (1000xH) e depois pra preço, invertendo a mesma escala que `y()`
  // usa pra desenhar -- dá o preço exato sob o cursor, contínuo (não preso
  // à vela mais próxima), igual o botão direito de plataformas de gráfico.
  const handleContextMenu = (e: React.MouseEvent<SVGSVGElement>) => {
    if (!onPriceContextMenu) return;
    e.preventDefault();
    const rect = e.currentTarget.getBoundingClientRect();
    const viewBoxY = ((e.clientY - rect.top) / rect.height) * H;
    const price = hi - ((viewBoxY - PAD_T) / plotH) * (hi - lo);
    onPriceContextMenu(price, e.clientX, e.clientY);
  };

  // ── Marcadores de notícia: posiciona sobre a vela mais próxima da data ─────
  // Renderizados como divs HTML (não SVG): o preserveAspectRatio="none" do SVG
  // distorceria círculos em elipses; divs por % ficam redondas e "tocáveis".
  const markerDots = (markers ?? [])
    .filter((m) => m.ts != null && m.ts >= candles[0].t - 86_400_000)
    .map((m) => {
      let best = 0;
      let bestDist = Infinity;
      for (let i = 0; i < candles.length; i++) {
        const d = Math.abs(candles[i].t - (m.ts as number));
        if (d < bestDist) {
          bestDist = d;
          best = i;
        }
      }
      return { ...m, idx: best, leftPct: (x(best) / W) * 100, topPct: (y(candles[best].h) / H) * 100 };
    });

  return (
    <div className="relative w-full select-none">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full"
        style={{ height }}
        preserveAspectRatio="none"
        onMouseLeave={() => setHover(null)}
        onContextMenu={handleContextMenu}
      >
        {/* linhas de grade + eixo Y */}
        {ticks.map((p, i) => (
          <g key={i}>
            <line x1={PAD_L} x2={W - PAD_R} y1={y(p)} y2={y(p)} stroke="#27272a" strokeWidth={0.5} />
            <text
              x={W - PAD_R + 6}
              y={y(p) + 3}
              fontSize={10}
              fontFamily="monospace"
              fill="#6b7280"
            >
              ${fmtPrice(p)}
            </text>
          </g>
        ))}

        {/* velas */}
        {candles.map((c, i) => {
          const up = c.c >= c.o;
          const color = up ? UP : DOWN;
          const cx = x(i);
          const top = y(Math.max(c.o, c.c));
          const bot = y(Math.min(c.o, c.c));
          const bodyH = Math.max(1, bot - top);
          return (
            <g key={c.t} opacity={hover == null || hover === i ? 1 : 0.45}>
              {/* pavio */}
              <line x1={cx} x2={cx} y1={y(c.h)} y2={y(c.l)} stroke={color} strokeWidth={1} />
              {/* corpo */}
              <rect x={cx - bodyW / 2} y={top} width={bodyW} height={bodyH} fill={color} rx={0.5} />
              {/* área de hover invisível */}
              <rect
                x={cx - geom.step / 2}
                y={PAD_T}
                width={geom.step}
                height={geom.plotH}
                fill="transparent"
                onMouseEnter={() => setHover(i)}
                onTouchStart={() => setHover(i)}
              />
            </g>
          );
        })}

        {/* labels eixo X */}
        {xIdx.map((i) => (
          <text
            key={i}
            x={x(i)}
            y={H - 6}
            fontSize={10}
            fontFamily="monospace"
            fill="#6b7280"
            textAnchor="middle"
          >
            {labelFor(candles[i].t)}
          </text>
        ))}
      </svg>

      {/* Marcadores de notícia — bolinhas grandes, tocáveis */}
      {markerDots.map((m, i) => (
        <button
          key={i}
          onClick={() => setOpenMarker(openMarker === i ? null : i)}
          className="absolute z-10 rounded-full border-2 shadow-md"
          style={{
            left: `${m.leftPct}%`,
            top: `${m.topPct}%`,
            transform: "translate(-50%, -130%)",
            width: 18,
            height: 18,
            background: m.tone === "positivo" ? UP : DOWN,
            borderColor: "#0a0a0a",
          }}
          aria-label={`Notícia: ${m.title}`}
        />
      ))}

      {/* Headline do marcador tocado */}
      {openMarker != null && markerDots[openMarker] && (
        <div
          className="absolute bottom-7 left-1 right-1 z-20 rounded-md border px-3 py-2.5 font-mono text-sm leading-snug"
          style={{ background: "hsl(var(--card))", borderColor: "hsl(var(--border))" }}
          onClick={() => setOpenMarker(null)}
        >
          <span style={{ color: markerDots[openMarker].tone === "positivo" ? UP : DOWN }}>
            {markerDots[openMarker].tone === "positivo" ? "▲ " : "▼ "}
          </span>
          {markerDots[openMarker].title}
        </div>
      )}

      {/* tooltip */}
      {hovered && (
        <div
          className="absolute top-1 left-1 rounded-md border px-2 py-1 font-mono text-[11px] leading-4 pointer-events-none"
          style={{ background: "hsl(var(--card))", borderColor: "hsl(var(--border))" }}
        >
          <div className="text-muted-foreground">{labelFor(hovered.t)}</div>
          <div>A <span style={{ color: hovered.c >= hovered.o ? UP : DOWN }}>${fmtPrice(hovered.o)}</span></div>
          <div>M ${fmtPrice(hovered.h)} · m ${fmtPrice(hovered.l)}</div>
          <div>F <span style={{ color: hovered.c >= hovered.o ? UP : DOWN }}>${fmtPrice(hovered.c)}</span></div>
          <div className="text-muted-foreground">Vol {hovered.v.toLocaleString("en-US")}</div>
        </div>
      )}
    </div>
  );
}
