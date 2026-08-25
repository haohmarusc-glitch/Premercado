/**
 * A interface é em português — inclusive o que só o leitor de tela lê.
 *
 * O que motivou: o Estudo de Entrada/Saída apareceu com as manchetes em
 * inglês (25/08/2026). A causa principal era de backend (ver
 * agent/traducao.py), mas o sweep que veio junto encontrou uma segunda
 * família de sobras — rótulos em inglês no próprio código da tela, quase
 * todos vindos dos componentes vendorizados do shadcn/ui, que chegam com
 * "Close", "Previous", "Toggle Sidebar" e nunca foram tocados porque não
 * aparecem em texto normal: moram em `sr-only` e `aria-label`, invisíveis
 * para quem só olha a tela e bem audíveis para quem usa leitor de tela.
 *
 * A lista é fechada de propósito. Um teste que reprovasse qualquer palavra
 * inglesa acusaria "Sharpe", "beta", "drawdown", "call" e "put" — termos que
 * o mercado brasileiro usa em inglês mesmo, e que traduzir pioraria.
 */
import { describe, it, expect } from "vitest";
import { readFileSync, readdirSync, statSync } from "fs";
import { join } from "path";

const RAIZ = join(__dirname, "..");

// Rótulos que JÁ vazaram para a tela alguma vez. Cada um esteve em produção.
const PROIBIDOS = [
  "Close", "Previous", "Next", "More pages", "More",
  "Toggle Sidebar", "Loading", "Previous slide", "Next slide",
  "Win Rate", "Trades", "Max DD", "Runs", "Shares Short",
  "Go to previous page", "Go to next page", "Agent Command Center",
  "Sharpe Ratio", "Max Drawdown", "Profit Factor", "Expectancy",
];

function arquivosTsx(dir: string): string[] {
  const saida: string[] = [];
  for (const nome of readdirSync(dir)) {
    if (nome === "node_modules" || nome === "__tests__") continue;
    const caminho = join(dir, nome);
    if (statSync(caminho).isDirectory()) saida.push(...arquivosTsx(caminho));
    else if (nome.endsWith(".tsx")) saida.push(caminho);
  }
  return saida;
}

describe("interface em português", () => {
  it("não tem rótulo em inglês visível nem em sr-only/aria-label", () => {
    const achados: string[] = [];
    for (const caminho of arquivosTsx(RAIZ)) {
      const fonte = readFileSync(caminho, "utf-8");
      for (const termo of PROIBIDOS) {
        // Só nas duas formas que chegam ao usuário: texto de elemento
        // (>Termo<) e atributo (="Termo"). Assim o termo pode aparecer à
        // vontade em nome de variável, comentário ou chave de API.
        if (fonte.includes(`>${termo}<`) || fonte.includes(`"${termo}"`)) {
          achados.push(`${caminho.replace(RAIZ, "src")}: ${termo}`);
        }
      }
    }
    expect(achados).toEqual([]);
  });

  it("varre de fato os componentes vendorizados, onde estavam as sobras", () => {
    const arquivos = arquivosTsx(RAIZ);
    expect(arquivos.some((a) => a.includes(join("ui", "dialog.tsx")))).toBe(true);
    expect(arquivos.length).toBeGreaterThan(50);
  });
});
