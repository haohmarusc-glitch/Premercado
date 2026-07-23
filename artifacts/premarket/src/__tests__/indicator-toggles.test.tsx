import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { IndicatorToggles } from "../components/indicator-toggles";
import type { IndicatorKey } from "../lib/indicators";

describe("IndicatorToggles", () => {
  it("mostra todas as opções por padrão e nenhuma marcada", () => {
    render(<IndicatorToggles enabled={new Set()} onToggle={() => {}} />);
    fireEvent.click(screen.getByTitle("Indicadores técnicos"));
    expect(screen.getByText("Média Móvel 21")).toBeInTheDocument();
    expect(screen.getByText("MACD")).toBeInTheDocument();
    expect(screen.getByText("IFR (RSI)")).toBeInTheDocument();
    for (const checkbox of screen.getAllByRole("checkbox")) {
      expect(checkbox).not.toBeChecked();
    }
  });

  it("restringe as opções mostradas via `available`", () => {
    render(<IndicatorToggles enabled={new Set()} onToggle={() => {}} available={["volume", "rsi"]} />);
    fireEvent.click(screen.getByTitle("Indicadores técnicos"));
    expect(screen.getByText("Volume")).toBeInTheDocument();
    expect(screen.getByText("IFR (RSI)")).toBeInTheDocument();
    expect(screen.queryByText("MACD")).not.toBeInTheDocument();
    expect(screen.queryByText("Média Móvel 21")).not.toBeInTheDocument();
  });

  it("marca os checkboxes já habilitados em `enabled`", () => {
    render(<IndicatorToggles enabled={new Set<IndicatorKey>(["sma21", "volume"])} onToggle={() => {}} />);
    fireEvent.click(screen.getByTitle("Indicadores técnicos"));
    expect(screen.getByText("Média Móvel 21").closest("label")?.querySelector("input")).toBeChecked();
    expect(screen.getByText("Volume").closest("label")?.querySelector("input")).toBeChecked();
    expect(screen.getByText("MACD").closest("label")?.querySelector("input")).not.toBeChecked();
  });

  it("chama onToggle com a chave certa ao clicar num checkbox", () => {
    const onToggle = vi.fn();
    render(<IndicatorToggles enabled={new Set()} onToggle={onToggle} />);
    fireEvent.click(screen.getByTitle("Indicadores técnicos"));
    fireEvent.click(screen.getByText("MACD"));
    expect(onToggle).toHaveBeenCalledWith("macd");
  });

  it("mostra o contador de indicadores ativos no botão", () => {
    render(<IndicatorToggles enabled={new Set<IndicatorKey>(["sma21", "rsi"])} onToggle={() => {}} />);
    expect(screen.getByText("2")).toBeInTheDocument();
  });

  it("fecha o menu ao clicar fora", () => {
    render(
      <div>
        <IndicatorToggles enabled={new Set()} onToggle={() => {}} />
        <div data-testid="outside">fora</div>
      </div>,
    );
    fireEvent.click(screen.getByTitle("Indicadores técnicos"));
    expect(screen.getByText("MACD")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("outside"));
    expect(screen.queryByText("MACD")).not.toBeInTheDocument();
  });
});
