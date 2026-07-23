// Cores/pré-pós-mercado pra colorir o traço do gráfico de preço por sessão
// (pré-mercado / pregão regular / pós-mercado), diferente do candle-shape.tsx
// (que colore CADA candle individual por alta/baixa) -- aqui é a LINHA
// inteira de um gráfico de linha/área, então usamos um <linearGradient> com
// "hardstops" (dois <stop> no mesmo offset) pra fingir múltiplas cores numa
// única <Line>/<Area> do recharts.
export const SESSION_COLORS = {
  pre: "#a78bfa", // violeta — pré-mercado
  post: "#fbbf24", // âmbar — pós-mercado
} as const;

export interface SessionStop {
  offset: string;
  color: string;
}

interface SessionCandle {
  session?: string | null;
}

// O eixo X do recharts nesses gráficos é categórico (sem `type` explícito
// -> pontos igual-espaçados por índice, não pela distância real em tempo),
// então offset = índice / (n-1) bate exatamente com a posição real de cada
// ponto renderizado, mesmo com gaps de horário (ex.: pulo overnight no 5D).
export function sessionGradientStops(candles: SessionCandle[], regularColor: string): SessionStop[] {
  const n = candles.length;
  if (n === 0) return [{ offset: "0%", color: regularColor }, { offset: "100%", color: regularColor }];
  const colorFor = (s?: string | null) =>
    s === "pre" ? SESSION_COLORS.pre : s === "post" ? SESSION_COLORS.post : regularColor;

  const stops: SessionStop[] = [];
  let prevColor = colorFor(candles[0].session);
  stops.push({ offset: "0%", color: prevColor });
  for (let i = 1; i < n; i++) {
    const c = colorFor(candles[i].session);
    if (c !== prevColor) {
      const pct = `${(i / (n - 1)) * 100}%`;
      stops.push({ offset: pct, color: prevColor });
      stops.push({ offset: pct, color: c });
      prevColor = c;
    }
  }
  // Só fecha em 100% se a última transição não já tiver caído exatamente
  // ali (ex.: mudança de sessão no último candle) -- evita stop duplicado.
  if (stops[stops.length - 1].offset !== "100%") {
    stops.push({ offset: "100%", color: prevColor });
  }
  return stops;
}

export function hasExtendedSession(candles: SessionCandle[]): boolean {
  return candles.some((c) => c.session === "pre" || c.session === "post");
}
