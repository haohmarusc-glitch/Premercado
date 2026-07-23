import { useEffect, useRef, useState } from "react";
import { SlidersHorizontal } from "lucide-react";
import { cn } from "@/lib/utils";
import { INDICATOR_LABELS, type IndicatorKey } from "@/lib/indicators";

const ALL_KEYS: IndicatorKey[] = ["sma21", "sma50", "bollinger", "volume", "macd", "rsi"];

interface IndicatorTogglesProps {
  enabled: Set<IndicatorKey>;
  onToggle: (key: IndicatorKey) => void;
  // Restringe as opções mostradas -- ex.: overlay (SMA/Bollinger) não dá pra
  // desenhar em cima do candle em SVG puro do Dashboard, só nos painéis
  // auxiliares (RSI/MACD/Volume) embaixo.
  available?: IndicatorKey[];
}

export function IndicatorToggles({ enabled, onToggle, available = ALL_KEYS }: IndicatorTogglesProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("click", onClick);
    return () => document.removeEventListener("click", onClick);
  }, [open]);

  const activeCount = available.filter((k) => enabled.has(k)).length;

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        title="Indicadores técnicos"
        className={cn(
          "p-1 rounded border border-border text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors flex items-center gap-1",
          activeCount > 0 && "text-primary border-primary/50",
        )}
      >
        <SlidersHorizontal className="h-3 w-3" />
        {activeCount > 0 && <span className="text-[9px] font-mono">{activeCount}</span>}
      </button>
      {open && (
        <div className="absolute right-0 top-full mt-1 z-50 min-w-[190px] rounded-md border border-border bg-card shadow-lg py-1 font-mono text-xs">
          {available.map((key) => (
            <label
              key={key}
              className="flex items-center gap-2 px-3 py-1.5 hover:bg-secondary transition-colors cursor-pointer"
            >
              <input
                type="checkbox"
                checked={enabled.has(key)}
                onChange={() => onToggle(key)}
                className="h-3 w-3"
              />
              {INDICATOR_LABELS[key]}
            </label>
          ))}
        </div>
      )}
    </div>
  );
}
