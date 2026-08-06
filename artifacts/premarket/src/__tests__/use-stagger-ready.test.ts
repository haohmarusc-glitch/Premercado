import { describe, it, expect, vi, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useStaggerReady } from "@/hooks/use-stagger-ready";

afterEach(() => {
  vi.useRealTimers();
});

describe("useStaggerReady", () => {
  it("fica pronto na hora quando o atraso é 0", () => {
    const { result } = renderHook(() => useStaggerReady(0));
    expect(result.current).toBe(true);
  });

  it("começa falso e vira true só depois do atraso", () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useStaggerReady(250));
    expect(result.current).toBe(false);

    act(() => {
      vi.advanceTimersByTime(249);
    });
    expect(result.current).toBe(false);

    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(result.current).toBe(true);
  });

  it("não estoura se desmontar antes do atraso vencer", () => {
    vi.useFakeTimers();
    const { unmount } = renderHook(() => useStaggerReady(500));
    unmount();
    // Se o timeout não fosse limpo, isto chamaria setState num componente
    // desmontado -- o teste passa por não lançar/avisar, não por retorno.
    expect(() => vi.advanceTimersByTime(1000)).not.toThrow();
  });
});
