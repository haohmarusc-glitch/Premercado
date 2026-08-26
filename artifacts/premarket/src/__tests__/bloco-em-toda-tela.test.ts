import { describe, it, expect } from "vitest";
import { readFileSync, readdirSync } from "fs";
import { join } from "path";

// ─── O bloco não pode voltar a ser tratado tela a tela ──────────────────────
//
// A primeira versão da tabela de decisão foi ligada só em `veredito.tsx`. O
// MESMO relatório continuou saindo com o fence ```json cru no Histórico e no
// Dashboard, que renderizam o mesmo conteúdo por outro caminho -- reportado
// da tela: "esse código ainda continua aqui".
//
// Sete telas chamam `MarkdownContent`. Consertar uma a uma é exatamente como
// a MM50 e a tabela do Fear & Greed acabaram com cópias divergentes. Agora a
// extração mora dentro do `MarkdownContent`, e este teste é o que impede a
// próxima tela de esquecer.

const SRC = join(__dirname, "..");

function arquivosTsx(dir: string): string[] {
  const saida: string[] = [];
  for (const entrada of readdirSync(dir, { withFileTypes: true })) {
    const caminho = join(dir, entrada.name);
    if (entrada.isDirectory()) {
      if (entrada.name === "__tests__" || entrada.name === "ui") continue;
      saida.push(...arquivosTsx(caminho));
    } else if (entrada.name.endsWith(".tsx")) {
      saida.push(caminho);
    }
  }
  return saida;
}

describe("a extração do bloco mora num lugar só", () => {
  it("MarkdownContent é quem chama extrairBlocoDoVeredito", () => {
    const md = readFileSync(join(SRC, "components/markdown.tsx"), "utf-8");
    expect(md).toContain("extrairBlocoDoVeredito");
    expect(md).toContain("VereditoDecisoes");
  });

  it("nenhuma TELA extrai o bloco por conta própria", () => {
    // Uma tela que extraia sozinha ou renderiza duas vezes (a tabela do
    // MarkdownContent mais a dela) ou passa prosa já limpa e a tabela some.
    const culpadas = arquivosTsx(join(SRC, "pages"))
      .filter((f) => readFileSync(f, "utf-8").includes("extrairBlocoDoVeredito"));
    expect(culpadas).toEqual([]);
  });

  it("toda tela que renderiza relatório usa MarkdownContent", () => {
    // A porta de entrada é uma só. Uma tela que chamasse ReactMarkdown direto
    // pularia a extração e voltaria a mostrar o fence cru.
    const direto = arquivosTsx(join(SRC, "pages"))
      .filter((f) => readFileSync(f, "utf-8").includes("<ReactMarkdown"));
    expect(direto).toEqual([]);
  });
});
