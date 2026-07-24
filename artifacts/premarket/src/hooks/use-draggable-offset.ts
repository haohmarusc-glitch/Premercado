import { useCallback, useEffect, useRef, useState, type RefObject } from "react";

interface Offset {
  x: number;
  y: number;
}

// Margem mantida entre a caixa e a borda do container (mesma usada no
// `top`/`right` iniciais nos componentes que consomem este hook).
const MARGIN = 4;

// Restringe o offset pra caixa nunca sair do container (arrastada demais pra
// cima cobre o cabeçalho acima do gráfico; arrastada demais pra esquerda
// vaza pra fora da coluna) -- vira no-op se container/caixa ainda não
// estiverem montados (mede via clientWidth/offsetWidth, então não depende
// de layout calculado por transform).
function clampOffset(offset: Offset, container: HTMLElement | null, box: HTMLElement | null): Offset {
  if (!container || !box) return offset;
  const cw = container.clientWidth;
  const ch = container.clientHeight;
  const bw = box.offsetWidth;
  const bh = box.offsetHeight;

  // top = MARGIN + offset.y precisa ficar em [MARGIN, ch - MARGIN - bh].
  const maxY = Math.max(0, ch - bh - 2 * MARGIN);
  // right = MARGIN - offset.x (quando offset.x <= 0, já que positivo é
  // grampeado a MARGIN no próprio estilo) precisa deixar a borda esquerda da
  // caixa dentro do container: offset.x >= bw + 2*MARGIN - cw.
  const minX = Math.min(0, bw + 2 * MARGIN - cw);

  return {
    x: Math.min(0, Math.max(minX, offset.x)),
    y: Math.min(maxY, Math.max(0, offset.y)),
  };
}

// Deslocamento (x,y) arrastável a partir de uma posição-padrão -- usado pra
// deixar o usuário mover a caixa de dados do gráfico pra onde achar melhor,
// já que ela por padrão fica em cima de parte das linhas em algumas telas.
// Persiste no localStorage (mesma posição em todos os gráficos, é uma
// preferência do usuário, não por ticker) pra não precisar arrastar de novo
// toda vez que reabre a página.
//
// `containerRef` (opcional) é o elemento relative ao qual a caixa fica
// ancorada -- quando informado, o offset é sempre grampeado pra caixa ficar
// inteira dentro dele (tanto durante o arraste quanto assim que a caixa
// aparece, então um offset salvo antigo/fora dos limites -- de uma tela
// maior, ou de antes desse grampeamento existir -- se autocorrige sem o
// usuário precisar arrastar de novo).
//
// Suporta mouse E toque -- boa parte do uso real desse app é em celular
// (touchscreen), onde mousedown/mousemove nunca disparam.
export function useDraggableOffset(storageKey: string, containerRef?: RefObject<HTMLElement | null>) {
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
  const boxNodeRef = useRef<HTMLElement | null>(null);

  // Ref-callback (em vez de useRef simples) pra saber exatamente quando a
  // caixa é montada -- ela só existe no DOM enquanto há um ponto sob hover,
  // então é nesse instante que grampeamos um offset salvo que já esteja fora
  // dos limites atuais.
  const boxRef = useCallback((node: HTMLElement | null) => {
    boxNodeRef.current = node;
    if (node) setOffset((prev) => clampOffset(prev, containerRef?.current ?? null, node));
  }, [containerRef]);

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
      const next = { x: start.offsetX + (clientX - start.startX), y: start.offsetY + (clientY - start.startY) };
      setOffset(clampOffset(next, containerRef?.current ?? null, boxNodeRef.current));
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
  }, [dragging, containerRef]);

  // Reajusta se o container mudar de tamanho (orientação do celular, resize
  // da janela) e o offset guardado não couber mais.
  useEffect(() => {
    const onResize = () => setOffset((prev) => clampOffset(prev, containerRef?.current ?? null, boxNodeRef.current));
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [containerRef]);

  useEffect(() => {
    try {
      window.localStorage.setItem(storageKey, JSON.stringify(offset));
    } catch {
      // ignora falha ao persistir -- não é crítico, só perde a posição na próxima visita.
    }
  }, [storageKey, offset]);

  return { offset, dragging, onMouseDown, onTouchStart, boxRef };
}
