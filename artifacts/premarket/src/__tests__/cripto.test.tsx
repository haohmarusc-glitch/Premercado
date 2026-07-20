import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import CryptoPage from "../pages/cripto";

// TradingViewChart injeta um <script src="https://s3.tradingview.com/..."> real
// -- em jsdom isso nunca dispara onload (nem deveria: não é o que este teste
// verifica). Mockamos pra só checar que a página passa o símbolo certo.
vi.mock("@/components/tradingview-chart", () => ({
  TradingViewChart: ({ symbol }: { symbol: string }) => (
    <div data-testid="tv-chart">{symbol}</div>
  ),
}));

describe("CryptoPage", () => {
  it("renders all 10 tiles with BTC selected by default", () => {
    render(<CryptoPage />);

    for (const ticker of ["BTC", "ETH", "BNB", "SOL", "XRP", "DOGE", "ADA", "TRX", "AVAX", "LINK"]) {
      expect(screen.getByTestId(`crypto-tile-${ticker}`)).toBeInTheDocument();
    }
    expect(screen.getByTestId("tv-chart")).toHaveTextContent("BTCUSD");
  });

  it("switches the chart symbol when another tile is clicked", () => {
    render(<CryptoPage />);

    fireEvent.click(screen.getByTestId("crypto-tile-ETH"));

    expect(screen.getByTestId("tv-chart")).toHaveTextContent("ETHUSD");
  });
});
