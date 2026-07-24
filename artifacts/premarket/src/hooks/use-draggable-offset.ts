import { useCallback, useEffect, useRef, useState } from "react";

interface Offset {
  x: number;
  y: number;
}

// Deslocamento (x,y) arrastável a partir de uma posição-padrão -- usado pra
// deixar o usuário mover a caixa de dados do gráfico pra onde achar melhor,
// já que ela por padrão fica em cima de parte das linhas em algumas telas.
// Persiste no localStorage (mesma posição em todos os gráficos, é uma
// preferência do usuário, não por ticker) pra não precisar arrastar de novo
// toda vez que reabre a página.
//
// Suporta mouse E toque -- boa parte do uso real desse app é em celular
// (touchscreen), onde mousedown/mousemove nunca disparam.
export function useDraggableOffset(storageKey: string) {
  const [offset, setOffset] = useState<Offset>(() => {
    if (typeof window === "undefined") return { x: 0, y: 0 };
    try {
      const raw = window.localStorage.getItem(storageKey);
      if (raw) {
        const parsed = JSON.parse(raw);
        if (typeof parsed?.x === "number" && typeof parsed?.y === "number") return parsed;
      }
    } catch {
      // localStorage indisponível ou valor inválido -- usa o padrão.
    }
    return { x: 0, y: 0 };
  });
  const [dragging, setDragging] = useState(false);
  const dragStartRef = useRef<{ startX: number; startY: number; offsetX: number; offsetY: number } | null>(null);

  const startDrag = useCallback((clientX: number, clientY: number) => {
    dragStartRef.current = { startX: clientX, startY: clientY, offsetX: offset.x, offsetY: offset.y };
    setDragging(true);
  }, [offset]);

  const onMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    startDrag(e.clientX, e.clientY);
  }, [startDrag]);

  const onTouchStart = useCallback((e: React.TouchEvent) => {
    const t = e.touches[0];
    if (!t) return;
    e.stopPropagation();
    startDrag(t.clientX, t.clientY);
  }, [startDrag]);

  useEffect(() => {
    if (!dragging) return;
    const move = (clientX: number, clientY: number) => {
      const start = dragStartRef.current;
      if (!start) return;
      setOffset({ x: start.offsetX + (clientX - start.startX), y: start.offsetY + (clientY - start.startY) });
    };
    const onMouseMove = (e: MouseEvent) => move(e.clientX, e.clientY);
    const onTouchMove = (e: TouchEvent) => {
      const t = e.touches[0];
      if (!t) return;
      e.preventDefault(); // evita rolar a página enquanto arrasta a caixa
      move(t.clientX, t.clientY);
    };
    const stop = () => setDragging(false);
    document.addEventListener("mousemove", onMouseMove);
    document.addEventListener("mouseup", stop);
    document.addEventListener("touchmove", onTouchMove, { passive: false });
    document.addEventListener("touchend", stop);
    document.addEventListener("touchcancel", stop);
    return () => {
      document.removeEventListener("mousemove", onMouseMove);
      document.removeEventListener("mouseup", stop);
      document.removeEventListener("touchmove", onTouchMove);
      document.removeEventListener("touchend", stop);
      document.removeEventListener("touchcancel", stop);
    };
  }, [dragging]);

  useEffect(() => {
    try {
      window.localStorage.setItem(storageKey, JSON.stringify(offset));
    } catch {
      // ignora falha ao persistir -- não é crítico, só perde a posição na próxima visita.
    }
  }, [storageKey, offset]);

  return { offset, dragging, onMouseDown, onTouchStart };
}
