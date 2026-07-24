import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useDraggableOffset } from "../hooks/use-draggable-offset";

const KEY = "test:draggable-offset";

function makeTouchEvent(type: string, clientX: number, clientY: number) {
  const event = new Event(type, { bubbles: true, cancelable: true }) as unknown as TouchEvent;
  Object.defineProperty(event, "touches", { value: [{ clientX, clientY }], configurable: true });
  return event;
}

describe("useDraggableOffset", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });
  afterEach(() => {
    window.localStorage.clear();
  });

  it("começa em {0,0} quando não há nada salvo", () => {
    const { result } = renderHook(() => useDraggableOffset(KEY));
    expect(result.current.offset).toEqual({ x: 0, y: 0 });
    expect(result.current.dragging).toBe(false);
  });

  it("carrega o offset salvo no localStorage", () => {
    window.localStorage.setItem(KEY, JSON.stringify({ x: 40, y: -20 }));
    const { result } = renderHook(() => useDraggableOffset(KEY));
    expect(result.current.offset).toEqual({ x: 40, y: -20 });
  });

  it("ignora valor inválido salvo e usa o padrão", () => {
    window.localStorage.setItem(KEY, "não é json");
    const { result } = renderHook(() => useDraggableOffset(KEY));
    expect(result.current.offset).toEqual({ x: 0, y: 0 });
  });

  it("arrasta e atualiza o offset acompanhando o delta do mouse", () => {
    const { result } = renderHook(() => useDraggableOffset(KEY));

    act(() => {
      result.current.onMouseDown({
        preventDefault: () => {},
        stopPropagation: () => {},
        clientX: 100,
        clientY: 100,
      } as unknown as React.MouseEvent);
    });
    expect(result.current.dragging).toBe(true);

    act(() => {
      document.dispatchEvent(new MouseEvent("mousemove", { clientX: 130, clientY: 80 }));
    });
    expect(result.current.offset).toEqual({ x: 30, y: -20 });

    act(() => {
      document.dispatchEvent(new MouseEvent("mouseup"));
    });
    expect(result.current.dragging).toBe(false);
  });

  it("arrasta por toque (celular) e atualiza o offset acompanhando o delta", () => {
    const { result } = renderHook(() => useDraggableOffset(KEY));

    act(() => {
      result.current.onTouchStart({
        stopPropagation: () => {},
        touches: [{ clientX: 100, clientY: 100 }],
      } as unknown as React.TouchEvent);
    });
    expect(result.current.dragging).toBe(true);

    act(() => {
      document.dispatchEvent(makeTouchEvent("touchmove", 70, 140));
    });
    expect(result.current.offset).toEqual({ x: -30, y: 40 });

    act(() => {
      document.dispatchEvent(new Event("touchend"));
    });
    expect(result.current.dragging).toBe(false);
  });

  it("para de arrastar por toque em touchcancel", () => {
    const { result } = renderHook(() => useDraggableOffset(KEY));
    act(() => {
      result.current.onTouchStart({
        stopPropagation: () => {},
        touches: [{ clientX: 0, clientY: 0 }],
      } as unknown as React.TouchEvent);
    });
    expect(result.current.dragging).toBe(true);
    act(() => {
      document.dispatchEvent(new Event("touchcancel"));
    });
    expect(result.current.dragging).toBe(false);
  });

  it("persiste o offset no localStorage a cada mudança", () => {
    const { result } = renderHook(() => useDraggableOffset(KEY));
    act(() => {
      result.current.onMouseDown({
        preventDefault: () => {},
        stopPropagation: () => {},
        clientX: 0,
        clientY: 0,
      } as unknown as React.MouseEvent);
    });
    act(() => {
      document.dispatchEvent(new MouseEvent("mousemove", { clientX: 10, clientY: 5 }));
    });
    expect(JSON.parse(window.localStorage.getItem(KEY) ?? "null")).toEqual({ x: 10, y: 5 });
  });

  // Grampeamento (containerRef informado): a caixa nunca pode sair do
  // container, seja um offset salvo de antes desse grampeamento existir,
  // seja arrastando demais durante o uso.
  describe("com containerRef (grampeado dentro do container)", () => {
    function makeContainer(width: number, height: number) {
      const el = document.createElement("div");
      Object.defineProperty(el, "clientWidth", { value: width, configurable: true });
      Object.defineProperty(el, "clientHeight", { value: height, configurable: true });
      return el;
    }
    function makeBox(width: number, height: number) {
      const el = document.createElement("div");
      Object.defineProperty(el, "offsetWidth", { value: width, configurable: true });
      Object.defineProperty(el, "offsetHeight", { value: height, configurable: true });
      return el;
    }

    it("autocorrige um offset salvo fora dos limites assim que a caixa aparece, sem precisar arrastar", () => {
      // Container pequeno (200x100) com um offset salvo que arrastaria a
      // caixa (180x60) bem pra fora -- cenário exato do bug: usuário arrastou
      // pra cima/esquerda uma vez e ficou preso lá em toda visita futura.
      window.localStorage.setItem(KEY, JSON.stringify({ x: -500, y: -500 }));
      const containerRef = { current: makeContainer(200, 100) };
      const { result } = renderHook(() => useDraggableOffset(KEY, containerRef));

      act(() => {
        result.current.boxRef(makeBox(180, 60));
      });

      expect(result.current.offset.x).toBeGreaterThanOrEqual(-16); // bw+2*MARGIN-cw = 180+8-200
      expect(result.current.offset.y).toBe(0); // top não pode ficar acima da margem inicial
    });

    it("não deixa arrastar a caixa pra fora do container", () => {
      const containerRef = { current: makeContainer(300, 150) };
      const { result } = renderHook(() => useDraggableOffset(KEY, containerRef));
      act(() => {
        result.current.boxRef(makeBox(100, 50));
      });

      act(() => {
        result.current.onMouseDown({
          preventDefault: () => {},
          stopPropagation: () => {},
          clientX: 0,
          clientY: 0,
        } as unknown as React.MouseEvent);
      });
      // Arrasto enorme pra cima e pra esquerda -- sem grampeamento a caixa
      // sairia muito além do container.
      act(() => {
        document.dispatchEvent(new MouseEvent("mousemove", { clientX: -1000, clientY: -1000 }));
      });

      // right = MARGIN - offset.x deve deixar a borda esquerda (cw - right - bw) >= MARGIN.
      const right = Math.max(4, 4 - result.current.offset.x);
      const left = 300 - right - 100;
      expect(left).toBeGreaterThanOrEqual(4);
      // top = MARGIN + offset.y não pode ficar negativo (acima do container).
      expect(4 + result.current.offset.y).toBeGreaterThanOrEqual(4);
    });

    it("sem containerRef, continua sem grampear (comportamento antigo preservado)", () => {
      const { result } = renderHook(() => useDraggableOffset(KEY));
      act(() => {
        result.current.onMouseDown({
          preventDefault: () => {},
          stopPropagation: () => {},
          clientX: 0,
          clientY: 0,
        } as unknown as React.MouseEvent);
      });
      act(() => {
        document.dispatchEvent(new MouseEvent("mousemove", { clientX: -1000, clientY: -1000 }));
      });
      expect(result.current.offset).toEqual({ x: -1000, y: -1000 });
    });
  });
});
