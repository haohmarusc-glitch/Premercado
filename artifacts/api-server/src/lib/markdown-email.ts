/**
 * Markdown → HTML do corpo dos e-mails.
 *
 * Módulo próprio, e não uma função dentro de mailer.ts, porque mailer.ts
 * importa `@workspace/db` no topo (pra resolver o e-mail de notificação) e
 * portanto exige DATABASE_URL só pra ser carregado — o que impede testar esta
 * conversão, que é pura, sem subir banco.
 *
 * Subconjunto deliberado: título, negrito, itálico, tabela e quebra de linha.
 * O escape de `& < >` vem PRIMEIRO — o conteúdo carrega manchete de notícia e
 * nome de empresa com `&` ou `<`, e sem isso o cliente de e-mail come pedaço
 * do relatório achando que é tag.
 */

// Estilo INLINE em toda célula, nunca classe nem <style> no head. O Gmail no
// celular ignorou o `font-family` do bloco <style> na primeira versão deste
// e-mail e as tabelas chegaram como linhas de `|` cruas, quebrando de linha —
// ilegíveis. Estilo inline é a única coisa que nenhum cliente descarta.
const _TABELA = "border-collapse:collapse;width:100%;margin:12px 0;font-size:13px";
// Sem `white-space:nowrap` no cabeçalho de propósito: a tabela de earnings do
// Radar tem 6 colunas, e forçar cada título numa linha só a empurraria para
// além da largura da tela do celular. Título quebrado em duas linhas é melhor
// que tabela que sai da tela.
const _TH = "border:1px solid #333;padding:6px 8px;text-align:left;background:#1a1a1a;color:#ffaa44;font-weight:bold";
const _TD = "border:1px solid #333;padding:6px 8px;text-align:left;color:#e0e0e0";

/** Uma linha `| a | b |` vira as células ["a","b"], respeitando `\|` escapado. */
function _celulas(linha: string): string[] {
  const bruto = linha.trim().replace(/^\|/, "").replace(/\|$/, "");
  const saida: string[] = [];
  let atual = "";
  for (let i = 0; i < bruto.length; i++) {
    if (bruto[i] === "\\" && bruto[i + 1] === "|") {
      // Pipe escapado por `tabela()` no frontend: é conteúdo, não separador.
      atual += "|";
      i++;
      continue;
    }
    if (bruto[i] === "|") {
      saida.push(atual.trim());
      atual = "";
      continue;
    }
    atual += bruto[i];
  }
  saida.push(atual.trim());
  return saida;
}

function _ehLinhaDeTabela(linha: string): boolean {
  const t = linha.trim();
  return t.startsWith("|") && t.endsWith("|") && t.length > 1;
}

/** `| --- | --- |` — a linha que separa cabeçalho de corpo. */
function _ehSeparador(linha: string): boolean {
  return _ehLinhaDeTabela(linha) && _celulas(linha).every((c) => /^:?-{3,}:?$/.test(c));
}

/**
 * Converte blocos de tabela markdown em `<table>`. As demais linhas passam
 * intactas para o resto do pipeline.
 *
 * Uma tabela precisa de cabeçalho + separador; um bloco de linhas com `|` sem
 * separador não é tabela (é texto que por acaso tem pipes) e fica como está.
 */
function _tabelasParaHtml(texto: string): string {
  const linhas = texto.split("\n");
  const saida: string[] = [];
  let i = 0;

  while (i < linhas.length) {
    const ehInicio = _ehLinhaDeTabela(linhas[i])
      && i + 1 < linhas.length
      && _ehSeparador(linhas[i + 1]);

    if (!ehInicio) {
      saida.push(linhas[i]);
      i++;
      continue;
    }

    const cabecalho = _celulas(linhas[i]);
    i += 2; // pula cabeçalho e separador
    const corpo: string[][] = [];
    while (i < linhas.length && _ehLinhaDeTabela(linhas[i]) && !_ehSeparador(linhas[i])) {
      corpo.push(_celulas(linhas[i]));
      i++;
    }

    const th = cabecalho.map((c) => `<th style="${_TH}">${c}</th>`).join("");
    const tr = corpo
      .map((l) => `<tr>${l.map((c) => `<td style="${_TD}">${c}</td>`).join("")}</tr>`)
      .join("");
    saida.push(`<table style="${_TABELA}"><thead><tr>${th}</tr></thead><tbody>${tr}</tbody></table>`);
  }

  return saida.join("\n");
}

export function markdownParaHtml(md: string): string {
  const escapado = md
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/^#{1,2} (.+)$/gm, "<h2>$1</h2>")
    .replace(/^#{3,} (.+)$/gm, "<h3>$1</h3>")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\*(.+?)\*/g, "<em>$1</em>");

  // A tabela vira HTML ANTES do `\n → <br>`: depois disso não há mais linhas
  // para reconhecer, e cada <br> solto dentro de um <table> vira espaço em
  // branco no topo da tabela em vários clientes.
  return _tabelasParaHtml(escapado)
    .replace(/\n/g, "<br>")
    // O bloco <table> já é elemento de bloco; o <br> que sobrou colado nele só
    // adiciona espaço vazio antes e depois.
    .replace(/<br>(<table)/g, "$1")
    .replace(/(<\/table>)<br>/g, "$1");
}
