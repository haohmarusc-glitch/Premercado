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
    const html = markdownParaHtml("| A | B |\n| --- | --- |\n| 1 | 2 |");
    expect(html).toContain("<table");
    expect(html).toContain("<th");
    expect(html).toContain(">A</th>");
    expect(html).toContain(">1</td>");
    expect(html).not.toContain("| A | B |");
  });

  it("não quebra com string vazia", () => {
    expect(markdownParaHtml("")).toBe("");
  });
});

describe("markdownParaHtml — tabelas", () => {
  const tab = "| Ticker | YTD |\n| --- | --- |\n| NVDA | +12% |\n| AMD | -3% |";

  it("estiliza inline, nunca por classe", () => {
    // O Gmail no celular ignorou o font-family do bloco <style> e a primeira
    // versão deste e-mail chegou com as tabelas como linhas de `|` cruas.
    // Estilo inline é o único que nenhum cliente descarta.
    const html = markdownParaHtml(tab);
    expect(html).toContain('<table style="');
    expect(html).toContain('<th style="');
    expect(html).toContain('<td style="');
    expect(html).not.toContain("class=");
  });

  it("separa cabeçalho de corpo", () => {
    const html = markdownParaHtml(tab);
    expect(html).toContain("<thead>");
    expect(html).toContain("<tbody>");
    expect(html.match(/<tr>/g)).toHaveLength(3); // 1 cabeçalho + 2 linhas
  });

  it("não deixa o separador virar linha de dados", () => {
    expect(markdownParaHtml(tab)).not.toContain("---");
  });

  it("devolve o pipe escapado como conteúdo, não como separador", () => {
    // `tabela()` no frontend escapa `|` nas células; aqui ele volta a ser texto.
    const html = markdownParaHtml("| Sinal |\n| --- |\n| alta \\| volume |");
    expect(html).toContain(">alta | volume</td>");
    expect(html.match(/<td/g)).toHaveLength(1);
  });

  it("preserva o conteúdo escapado dentro da célula", () => {
    const html = markdownParaHtml("| Empresa |\n| --- |\n| AT&T |");
    expect(html).toContain(">AT&amp;T</td>");
  });

  it("converte duas tabelas separadas por texto", () => {
    const html = markdownParaHtml(`${tab}\n\ntexto no meio\n\n${tab}`);
    expect(html.match(/<table/g)).toHaveLength(2);
    expect(html).toContain("texto no meio");
  });

  it("deixa em paz linhas com pipe que não formam tabela", () => {
    // Sem linha separadora não é tabela — é texto que por acaso tem pipes.
    const html = markdownParaHtml("| isto | não é tabela |\n| nem isto |");
    expect(html).not.toContain("<table");
    expect(html).toContain("| isto | não é tabela |");
  });

  it("aceita tabela de uma coluna só", () => {
    const html = markdownParaHtml("| Risco |\n| --- |\n| concentração |");
    expect(html).toContain(">concentração</td>");
  });

  it("aceita tabela sem nenhuma linha de dados", () => {
    const html = markdownParaHtml("| A |\n| --- |");
    expect(html).toContain("<table");
    expect(html).toContain("<tbody></tbody>");
  });

  it("não deixa <br> colado antes ou depois da tabela", () => {
    // <table> já é bloco; o <br> vizinho só adiciona espaço vazio.
    const html = markdownParaHtml(`antes\n${tab}\ndepois`);
    expect(html).not.toContain("<br><table");
    expect(html).not.toContain("</table><br>");
    expect(html).toContain("antes");
    expect(html).toContain("depois");
  });

  it("mantém o texto fora da tabela com <br>", () => {
    const html = markdownParaHtml("linha 1\nlinha 2");
    expect(html).toBe("linha 1<br>linha 2");
  });
});
