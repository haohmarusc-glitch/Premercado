import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import LoginPage from "../pages/login";

const mockFetch = vi.fn();
global.fetch = mockFetch;

describe("LoginPage", () => {
  it("renderiza o formulário de login", () => {
    render(<LoginPage onSuccess={() => {}} />);
    expect(screen.getByText("PRÉ-MERCADO")).toBeInTheDocument();
    expect(screen.getByText("Senha de acesso")).toBeInTheDocument();
  });

  it("botão submit desabilitado quando senha está vazia", () => {
    render(<LoginPage onSuccess={() => {}} />);
    const button = screen.getByRole("button", { name: /entrar/i });
    expect(button).toBeDisabled();
  });

  it("botão submit habilitado quando senha é digitada", () => {
    render(<LoginPage onSuccess={() => {}} />);
    const input = screen.getByPlaceholderText("••••••••••••••••");
    fireEvent.change(input, { target: { value: "minhasenha" } });
    const button = screen.getByRole("button", { name: /entrar/i });
    expect(button).not.toBeDisabled();
  });

  it("exibe erro de senha incorreta quando API retorna 401", async () => {
    mockFetch.mockResolvedValueOnce({ ok: false });
    render(<LoginPage onSuccess={() => {}} />);
    const input = screen.getByPlaceholderText("••••••••••••••••");
    fireEvent.change(input, { target: { value: "errada" } });
    fireEvent.submit(screen.getByRole("button", { name: /entrar/i }));
    await waitFor(() => {
      expect(screen.getByText("Senha incorreta.")).toBeInTheDocument();
    });
  });

  it("chama onSuccess quando login é bem-sucedido", async () => {
    const onSuccess = vi.fn();
    mockFetch.mockResolvedValueOnce({ ok: true });
    render(<LoginPage onSuccess={onSuccess} />);
    const input = screen.getByPlaceholderText("••••••••••••••••");
    fireEvent.change(input, { target: { value: "correta" } });
    fireEvent.submit(screen.getByRole("button", { name: /entrar/i }));
    await waitFor(() => {
      expect(onSuccess).toHaveBeenCalledOnce();
    });
  });
});
