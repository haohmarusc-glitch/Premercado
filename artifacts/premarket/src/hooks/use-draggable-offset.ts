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
  const dragStartRef = useRef<{ mouseX: number; mouseY: number; offsetX: number; offsetY: number } | null>(null);

  const onMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragStartRef.current = { mouseX: e.clientX, mouseY: e.clientY, offsetX: offset.x, offsetY: offset.y };
    setDragging(true);
  }, [offset]);

  useEffect(() => {
    if (!dragging) return;
    const onMove = (e: MouseEvent) => {
      const start = dragStartRef.current;
      if (!start) return;
      setOffset({ x: start.offsetX + (e.clientX - start.mouseX), y: start.offsetY + (e.clientY - start.mouseY) });
    };
    const onUp = () => setDragging(false);
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
    return () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    };
  }, [dragging]);

  useEffect(() => {
    try {
      window.localStorage.setItem(storageKey, JSON.stringify(offset));
    } catch {
      // ignora falha ao persistir -- não é crítico, só perde a posição na próxima visita.
    }
  }, [storageKey, offset]);

  return { offset, dragging, onMouseDown };
}
