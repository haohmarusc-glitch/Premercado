import ReactMarkdown from 'react-markdown';
import { extrairBlocoDoVeredito } from '@/lib/veredito-bloco';
import { VereditoDecisoes } from '@/components/veredito-decisoes';

/**
 * Markdown de relatório.
 *
 * O bloco estruturado da decisão sai da prosa e vira tabela AQUI, e não na
 * tela -- a primeira versão ligou isso só em `veredito.tsx`, e o mesmo
 * relatório continuou aparecendo com o fence cru no Histórico e no Dashboard.
 * Sete telas renderizam relatório; consertar uma a uma é exatamente como a
 * MM50 e a tabela do Fear & Greed acabaram com duas cópias divergentes.
 *
 * Conteúdo sem bloco legível passa intacto -- `extrairBlocoDoVeredito`
 * devolve o texto original quando não há bloco, quando o JSON não parseia, ou
 * quando a forma não bate. As telas que nunca têm bloco (Análise Rápida,
 * Reação a Earnings, chat) não mudam em nada.
 */
export function MarkdownContent({ content }: { content: string }) {
  const { prosa, decisoes } = extrairBlocoDoVeredito(content);
  return (
    <div className="space-y-4">
      <div className="prose prose-sm dark:prose-invert prose-p:text-muted-foreground prose-headings:text-foreground prose-a:text-primary max-w-none">
        <ReactMarkdown>{prosa}</ReactMarkdown>
      </div>
      {decisoes && <VereditoDecisoes decisoes={decisoes} />}
    </div>
  );
}
