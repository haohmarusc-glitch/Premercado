import type { Candle } from "@workspace/api-client-react";

// Recharts injeta essas props no componente passado via <Bar shape={...} />
// quando o dataKey resolve para um par [low, high] (barra "flutuante") — x/y
// e o topo do range (valor alto), y+height e o fundo (valor baixo).
interface CandleShapeProps {
  x?: number;
  y?: number;
  width?: number;
  height?: number;
  payload?: Candle;
}

const UP_COLOR = "#22c55e";
const DOWN_COLOR = "#ef4444";
const FLAT_COLOR = "#71717a";

// O tipo `shape` do <Bar> do recharts exige retornar um ReactElement (nao
// aceita null) — usa um <g/> vazio como "nada para desenhar" em vez de null.
export function CandleShape({ x, y, width, height, payload }: CandleShapeProps) {
  if (x == null || y == null || width == null || height == null || !payload) return <g />;
  const { o, h, l, c } = payload;

  const centerX = x + width / 2;
  const bodyWidth = Math.max(width * 0.6, 2);

  // h === l: sem variacao no candle (ex.: pre-mercado sem negociacao) — so o pavio.
  if (h === l) {
    return <line x1={x} x2={x + width} y1={y} y2={y} stroke={FLAT_COLOR} strokeWidth={1} />;
  }

  const up = c >= o;
  const color = up ? UP_COLOR : DOWN_COLOR;
  // Interpola o valor -> pixel Y dentro do range [y, y+height] que o recharts
  // ja calculou para o par [l, h] deste candle.
  const valueToY = (v: number) => y + ((h - v) / (h - l)) * height;

  const openY = valueToY(o);
  const closeY = valueToY(c);
  const bodyTop = Math.min(openY, closeY);
  const bodyHeight = Math.max(Math.abs(closeY - openY), 1);

  return (
    <g>
      <line x1={centerX} x2={centerX} y1={y} y2={y + height} stroke={color} strokeWidth={1} />
      <rect x={centerX - bodyWidth / 2} y={bodyTop} width={bodyWidth} height={bodyHeight} fill={color} />
    </g>
  );
}

export type CandleRangeDatum = Candle & { range: [number, number] };

export function toCandleRangeData(candles: Candle[]): CandleRangeDatum[] {
  return candles.map((c) => ({ ...c, range: [c.l, c.h] }));
}

export function candleDomain(candles: Candle[], padPct = 0.02): [number, number] {
  if (!candles.length) return [0, 100];
  const lows = candles.map((c) => c.l);
  const highs = candles.map((c) => c.h);
  const min = Math.min(...lows);
  const max = Math.max(...highs);
  const pad = (max - min) * padPct || 1;
  return [min - pad, max + pad];
}
