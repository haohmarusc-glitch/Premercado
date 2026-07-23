import { describe, it, expect, vi, afterEach } from "vitest";
import { renderHook } from "@testing-library/react";
import { useFullscreenEscape, useFullscreenChartHeight } from "../hooks/use-fullscreen-chart";

describe("useFullscreenChartHeight", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("retorna a altura recolhida quando não está expandido", () => {
    const { result } = renderHook(() => useFullscreenChartHeight(false, 190, 200));
    expect(result.current).toBe(200);
  });

  it("calcula a altura a partir da viewport quando expandido", () => {
    vi.stubGlobal("innerHeight", 900);
    const { result } = renderHook(() => useFullscreenChartHeight(true, 190, 200));
    expect(result.current).toBe(900 - 190);
  });

  it("nunca retorna menos que o mínimo de 240px mesmo em viewports pequenas", () => {
    vi.stubGlobal("innerHeight", 300);
    const { result } = renderHook(() => useFullscreenChartHeight(true, 190, 200));
    expect(result.current).toBe(240);
  });
});

describe("useFullscreenEscape", () => {
  afterEach(() => {
    document.body.style.overflow = "";
  });

  it("não mexe no overflow do body quando não está expandido", () => {
    document.body.style.overflow = "auto";
    renderHook(() => useFullscreenEscape(false, () => {}));
    expect(document.body.style.overflow).toBe("auto");
  });

  it("trava o scroll do body enquanto expandido e restaura ao desmontar", () => {
    document.body.style.overflow = "auto";
    const { unmount } = renderHook(() => useFullscreenEscape(true, () => {}));
    expect(document.body.style.overflow).toBe("hidden");
    unmount();
    expect(document.body.style.overflow).toBe("auto");
  });

  it("chama onCollapse ao apertar Esc, e não em outras teclas", () => {
    const onCollapse = vi.fn();
    renderHook(() => useFullscreenEscape(true, onCollapse));

    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter" }));
    expect(onCollapse).not.toHaveBeenCalled();

    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    expect(onCollapse).toHaveBeenCalledTimes(1);
  });
});
