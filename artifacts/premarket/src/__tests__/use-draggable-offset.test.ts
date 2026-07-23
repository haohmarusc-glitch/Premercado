import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useDraggableOffset } from "../hooks/use-draggable-offset";

const KEY = "test:draggable-offset";

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
});
