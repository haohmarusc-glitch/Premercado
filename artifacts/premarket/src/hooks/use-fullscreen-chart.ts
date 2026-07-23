import { useEffect, useState } from "react";

// Fecha o gráfico em tela cheia ao apertar Esc e trava o scroll do body atrás
// do overlay (fixed inset-0) enquanto ele estiver aberto.
export function useFullscreenEscape(expanded: boolean, onCollapse: () => void) {
  useEffect(() => {
    if (!expanded) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCollapse();
    };
    document.addEventListener("keydown", onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [expanded, onCollapse]);
}

// Altura real (em px) que o gráfico deve ocupar em tela cheia, calculada a
// partir da altura da viewport -- não dá pra usar strings tipo calc() aqui
// porque o CandleChart em SVG usa a altura em cálculos de geometria (viewBox),
// não só em CSS. `reserve` é o espaço estimado ocupado pelo cabeçalho/legendas
// acima do gráfico dentro do overlay.
export function useFullscreenChartHeight(expanded: boolean, reserve: number, collapsedHeight: number): number {
  const [vh, setVh] = useState(() => (typeof window !== "undefined" ? window.innerHeight : 900));
  useEffect(() => {
    if (!expanded) return;
    const onResize = () => setVh(window.innerHeight);
    onResize();
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [expanded]);
  return expanded ? Math.max(240, vh - reserve) : collapsedHeight;
}
