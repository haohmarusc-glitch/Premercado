import { describe, it, expect, vi, afterEach } from "vitest";
import { render, fireEvent } from "@testing-library/react";
import { CandleChart, type Candle } from "../components/candle-chart";

// getBoundingClientRect real (jsdom não faz layout) -- fixamos top:0/height:H
// pra que clientY já caia direto em unidades do viewBox (mesma escala que o
// componente usa internamente pra desenhar).
const H = 200;

const candles: Candle[] = [
  { t: 0, o: 100, h: 110, l: 90, c: 105, v: 0 },
  { t: 1, o: 105, h: 115, l: 95, c: 110, v: 0 },
];
// lo = min(90,95) - 5%*(115-90) = 90 - 1.25 = 88.75
// hi = max(110,115) + 5%*(115-90) = 115 + 1.25 = 116.25
// plotH = H - PAD_T(8) - PAD_B(22) = 170

describe("CandleChart onPriceContextMenu", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  function renderChart(onPriceContextMenu: (price: number, x: number, y: number) => void) {
    const { container } = render(
      <CandleChart candles={candles} height={H} labelFor={() => ""} onPriceContextMenu={onPriceContextMenu} />,
    );
    const svg = container.querySelector("svg")!;
    vi.spyOn(svg, "getBoundingClientRect").mockReturnValue({
      top: 0, left: 0, height: H, width: 1000, bottom: H, right: 1000, x: 0, y: 0, toJSON: () => {},
    } as DOMRect);
    return svg;
  }

  it("converte o topo da área de plotagem no preço mais alto (hi)", () => {
    const spy = vi.fn();
    const svg = renderChart(spy);
    fireEvent.contextMenu(svg, { clientX: 50, clientY: 8 });
    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy.mock.calls[0][0]).toBeCloseTo(116.25, 2);
  });

  it("converte o fundo da área de plotagem no preço mais baixo (lo)", () => {
    const spy = vi.fn();
    const svg = renderChart(spy);
    fireEvent.contextMenu(svg, { clientX: 50, clientY: 178 });
    expect(spy.mock.calls[0][0]).toBeCloseTo(88.75, 2);
  });

  it("converte o meio da área de plotagem na média entre hi e lo", () => {
    const spy = vi.fn();
    const svg = renderChart(spy);
    fireEvent.contextMenu(svg, { clientX: 50, clientY: 93 });
    expect(spy.mock.calls[0][0]).toBeCloseTo(102.5, 2);
  });

  it("passa as coordenadas de tela do clique junto com o preço", () => {
    const spy = vi.fn();
    const svg = renderChart(spy);
    fireEvent.contextMenu(svg, { clientX: 321, clientY: 93 });
    expect(spy).toHaveBeenCalledWith(expect.any(Number), 321, 93);
  });

  it("não quebra quando onPriceContextMenu não é passado", () => {
    const { container } = render(<CandleChart candles={candles} height={H} labelFor={() => ""} />);
    const svg = container.querySelector("svg")!;
    expect(() => fireEvent.contextMenu(svg, { clientX: 50, clientY: 93 })).not.toThrow();
  });
});
