import { describe, it, expect } from "vitest";
import { markdownParaHtml } from "../markdown-email";

describe("markdownParaHtml", () => {
  it("converte título de nível 1 e 2 em h2", () => {
    expect(markdownParaHtml("# Radar")).toContain("<h2>Radar</h2>");
    expect(markdownParaHtml("## Resultado")).toContain("<h2>Resultado</h2>");
  });

  it("converte título de nível 3+ em h3", () => {
    expect(markdownParaHtml("### Operações")).toContain("<h3>Operações</h3>");
  });

  it("converte negrito e itálico", () => {
    expect(markdownParaHtml("**Preço:** $10")).toContain("<strong>Preço:</strong>");
    expect(markdownParaHtml("*nota*")).toContain("<em>nota</em>");
  });

  it("escapa & < > antes de qualquer conversão", () => {
    // Manchete e nome de empresa chegam sem sanitização; sem o escape o
    // cliente de e-mail come pedaço do relatório achando que é tag.
    const html = markdownParaHtml("AT&T <script>alert(1)</script>");
    expect(html).toContain("AT&amp;T");
    expect(html).toContain("&lt;script&gt;");
    expect(html).not.toContain("<script>");
  });

  it("não deixa markdown injetar tag via escape", () => {
    const html = markdownParaHtml("**<b>x</b>**");
    expect(html).toContain("<strong>&lt;b&gt;x&lt;/b&gt;</strong>");
  });

  it("troca quebra de linha por <br>", () => {
    expect(markdownParaHtml("a\nb")).toBe("a<br>b");
  });

  it("deixa a tabela como texto, alinhada pelo escape do pipe", () => {
    // Tabela não vira <table> hoje; o corpo do e-mail é monoespaçado, então
    // as linhas continuam alinhadas. O que não pode é sumir conteúdo.
    const html = markdownParaHtml("| A | B |\n| --- | --- |\n| 1 | 2 |");
    expect(html).toContain("| A | B |");
    expect(html).toContain("| 1 | 2 |");
  });

  it("não quebra com string vazia", () => {
    expect(markdownParaHtml("")).toBe("");
  });
});
