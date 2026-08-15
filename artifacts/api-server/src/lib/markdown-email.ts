/**
 * Markdown → HTML do corpo dos e-mails.
 *
 * Módulo próprio, e não uma função dentro de mailer.ts, porque mailer.ts
 * importa `@workspace/db` no topo (pra resolver o e-mail de notificação) e
 * portanto exige DATABASE_URL só pra ser carregado — o que impede testar esta
 * conversão, que é pura, sem subir banco.
 *
 * Subconjunto deliberado: título, negrito, itálico e quebra de linha. O escape
 * de `& < >` vem PRIMEIRO — o conteúdo carrega manchete de notícia e nome de
 * empresa com `&` ou `<`, e sem isso o cliente de e-mail come pedaço do
 * relatório achando que é tag.
 *
 * Tabela de markdown não é convertida: fica como texto monoespaçado, que num
 * corpo `Courier New` continua alinhado. Trocar por `<table>` é a próxima
 * melhoria óbvia se o relatório de tela passar a ter tabela larga demais.
 */
export function markdownParaHtml(md: string): string {
  return md
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/^#{1,2} (.+)$/gm, "<h2>$1</h2>")
    .replace(/^#{3,} (.+)$/gm, "<h3>$1</h3>")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\*(.+?)\*/g, "<em>$1</em>")
    .replace(/\n/g, "<br>");
}
