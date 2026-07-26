import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const navigateSpy = vi.fn();
vi.mock("wouter", () => ({
  useLocation: () => ["/analistas", navigateSpy],
}));

import AnalystsPage from "../pages/analysts";

// Alvo baixo abaixo do preço, alvo alto acima -- cobre os dois sentidos da
// condição (acima/abaixo) calculada dinamicamente contra a cotação atual
// (ver comentário em analysts.tsx: alvo baixo às vezes já está acima do
// preço, e vice-versa, então não dá pra fixar a condição por alvo).
const FUNDAMENTALS_RESPONSE = {
  items: [
    {
      ticker: "NVDA",
      price: 208.26,
      analyst: {
        consensus: "compra forte",
        numAnalysts: 42,
        targetMean: 230.5,
        targetHigh: 260,
        targetLow: 180,
        upsidePct: 10.7,
      },
    },
  ],
};

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <AnalystsPage />
    </QueryClientProvider>,
  );
}

describe("AnalystsPage — alerta nos alvos de analista", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(FUNDAMENTALS_RESPONSE),
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    navigateSpy.mockClear();
    cleanup();
  });

  it("mostra os três alvos com a condição certa (baixo < preço = below, alto > preço = above)", async () => {
    renderPage();
    fireEvent.click(await screen.findByTitle("Criar alerta num alvo de analista"));

    // "Alvo Baixo/Médio/Alto" também são os cabeçalhos da tabela -- usa role
    // "button" pra pegar só as opções do menu, não as colunas.
    expect(await screen.findByRole("button", { name: /Alvo Baixo/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Alvo Médio/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Alvo Alto/ })).toBeInTheDocument();
  });

  it("navega pra /alerts com symbol/price/condition=above ao clicar no Alvo Alto (260 > preço 208.26)", async () => {
    renderPage();
    fireEvent.click(await screen.findByTitle("Criar alerta num alvo de analista"));
    fireEvent.click(await screen.findByRole("button", { name: /Alvo Alto/ }));

    expect(navigateSpy).toHaveBeenCalledWith("/alerts?symbol=NVDA&price=260.00&condition=above");
  });

  it("navega com condition=below ao clicar no Alvo Baixo (180 < preço 208.26)", async () => {
    renderPage();
    fireEvent.click(await screen.findByTitle("Criar alerta num alvo de analista"));
    fireEvent.click(await screen.findByRole("button", { name: /Alvo Baixo/ }));

    expect(navigateSpy).toHaveBeenCalledWith("/alerts?symbol=NVDA&price=180.00&condition=below");
  });

  it("fecha o menu ao apertar Escape, sem navegar", async () => {
    renderPage();
    fireEvent.click(await screen.findByTitle("Criar alerta num alvo de analista"));
    await screen.findByRole("button", { name: /Alvo Alto/ });

    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("button", { name: /Alvo Alto/ })).not.toBeInTheDocument());
    expect(navigateSpy).not.toHaveBeenCalled();
  });

  it("clampa o menu pra não nascer fora da tela quando o botão fica perto da borda direita", async () => {
    Object.defineProperty(window, "innerWidth", { value: 1000, configurable: true });
    renderPage();
    const button = await screen.findByTitle("Criar alerta num alvo de analista");
    vi.spyOn(button, "getBoundingClientRect").mockReturnValue({
      left: 980, right: 1010, bottom: 40, top: 20, width: 30, height: 20, x: 980, y: 20, toJSON: () => {},
    } as DOMRect);
    fireEvent.click(button);

    const menuHeader = await screen.findByText("NVDA · alertar no alvo");
    const menu = menuHeader.parentElement as HTMLElement;
    // left aplicado deve ser <= innerWidth - 210 (largura mínima do menu +
    // margem), nunca o rect.left bruto do botão (980), senão nasce cortado.
    expect(parseFloat(menu.style.left)).toBeLessThanOrEqual(1000 - 210);
  });
});
