import type { NewsItem } from "@workspace/api-client-react";

// `published` vem do yfinance em dois formatos possíveis (get_news_feed.py):
// string ISO 8601 (pubDate) ou epoch em segundos (providerPublishTime,
// fallback do formato antigo). Heurística: valores < 10^12 são segundos.
export function parseNewsPublished(value: string | number | null | undefined): number | null {
  if (value == null || value === "") return null;
  if (typeof value === "number") {
    return value < 1e12 ? value * 1000 : value;
  }
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? null : parsed;
}

// Casa cada notícia com o candle mais próximo no tempo (não "mesmo dia" —
// funciona igual para candles intradiários de 1D/5D e diários/semanais de
// períodos maiores). Descarta notícias longe demais de qualquer candle
// (> 1.5x o espaçamento médio) para não colar uma notícia antiga na borda
// de uma janela curta.
export function attachNewsMarkers<T extends { t: number }>(
  rows: T[],
  news: NewsItem[],
): (T & { newsItems: NewsItem[] })[] {
  const withNews = rows.map((r) => ({ ...r, newsItems: [] as NewsItem[] }));
  if (!withNews.length) return withNews;

  const spacings: number[] = [];
  for (let i = 1; i < rows.length; i++) spacings.push(rows[i].t - rows[i - 1].t);
  const avgSpacing = spacings.length ? spacings.reduce((a, b) => a + b, 0) / spacings.length : Infinity;
  const maxDistance = avgSpacing * 1.5;

  for (const item of news) {
    const ts = parseNewsPublished(item.published);
    if (ts == null) continue;

    let bestIdx = -1;
    let bestDist = Infinity;
    for (let i = 0; i < withNews.length; i++) {
      const dist = Math.abs(withNews[i].t - ts);
      if (dist < bestDist) {
        bestDist = dist;
        bestIdx = i;
      }
    }
    if (bestIdx >= 0 && bestDist <= maxDistance) {
      withNews[bestIdx].newsItems.push(item);
    }
  }

  return withNews;
}

interface NewsMarkerShapeProps {
  x?: number;
  y?: number;
  width?: number;
  payload?: { newsItems?: NewsItem[] };
}

// Bar shape que só desenha algo quando o candle tem notícia associada — as
// demais posições ficam com um <g/> vazio (o Bar de marcadores usa o MESMO
// dataKey/array de dados do gráfico principal, então a posição X já vem
// perfeitamente alinhada com o candle correspondente).
export function NewsMarkerShape({ x, y, width, payload }: NewsMarkerShapeProps) {
  const items = payload?.newsItems;
  if (x == null || y == null || width == null || !items || !items.length) return <g />;

  const cx = x + width / 2;
  const title = items
    .slice(0, 3)
    .map((n) => n.title + (n.summary ? ` — ${n.summary}` : ""))
    .join("\n\n");

  return (
    <g>
      <circle cx={cx} cy={y} r={4} fill="#facc15" stroke="#78350f" strokeWidth={1} />
      <title>{title}</title>
    </g>
  );
}
