import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { ListChecks } from "lucide-react";
import {
  acaoValida, razaoConhecida, rotuloDaRazao, type DecisaoTicker,
} from "@/lib/veredito-bloco";

// ─── A decisão do veredito, como decisão ─────────────────────────────────────
// Este painel mostra o bloco estruturado que o agente escreve no fim do
// veredito. Antes ele chegava cru na tela -- markdown renderiza um fence de
// código como fence de código, e o leitor via o insumo do validador.
//
// A hierarquia certa é esta: o bloco é a DECISÃO, a prosa é o comentário sobre
// ela. Por isso a tabela vem antes do texto.
//
// Duas coisas que a tabela NÃO faz, de propósito:
//
//   1. Não esconde código fora do vocabulário. O validador registra código
//      desconhecido como WARN (o vocabulário evolui), e a tela faz igual: mostra
//      marcado. Esconder transformaria evolução de vocabulário em dado perdido.
//   2. Não substitui o código pelo rótulo legível. `EARNINGS_PROXIMO` é o que a
//      máquina confere; "earnings próximo" é o que a pessoa lê. O código fica no
//      title, a um hover de distância, porque é ele que aparece no log quando
//      uma checagem aponta.

const ESTILO_DA_ACAO: Record<string, string> = {
  COMPRAR: "text-green-400 border-green-400/40 bg-green-400/10",
  AUMENTAR: "text-green-400 border-green-400/40 bg-green-400/10",
  MANTER: "text-foreground border-border bg-secondary/40",
  AGUARDAR: "text-muted-foreground border-border bg-secondary/30",
  REDUZIR: "text-amber-400 border-amber-400/40 bg-amber-400/10",
  VENDER: "text-red-400 border-red-400/40 bg-red-400/10",
};

/** Fora do vocabulário: nem verde nem vermelho -- o leitor não deve adivinhar. */
const ESTILO_DESCONHECIDO = "text-amber-400 border-amber-400/60 bg-amber-400/10 border-dashed";

function Acao({ action }: { action: string }) {
  const ok = acaoValida(action);
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-sm border px-2 py-0.5 font-mono text-[11px] font-bold tracking-wider",
        ok ? ESTILO_DA_ACAO[action] : ESTILO_DESCONHECIDO,
      )}
      title={ok ? undefined : `"${action}" está fora do vocabulário de ações do contrato`}
    >
      {action || "—"}
    </span>
  );
}

/**
 * Confiança como número E barra. O número sozinho vira ruído numa coluna de
 * oito linhas; a barra sozinha esconde a diferença entre 0,92 e 0,95, que é
 * justamente onde mora a distinção entre "decidido" e "quase certo".
 */
function Confianca({ valor }: { valor: number | null }) {
  if (valor === null) {
    return (
      <span
        className="font-mono text-xs text-amber-400"
        title="confidence ausente ou fora de [0, 1] -- o validador aponta isso como erro de schema"
      >
        —
      </span>
    );
  }
  const pct = Math.round(valor * 100);
  return (
    <div className="flex items-center gap-2">
      <span className="font-mono text-xs tabular-nums w-9 text-right">{pct}%</span>
      <div className="h-1.5 w-16 rounded-full bg-secondary overflow-hidden" aria-hidden>
        <div
          className={cn("h-full rounded-full", pct >= 80 ? "bg-primary" : "bg-muted-foreground/60")}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

function Razao({ codigo }: { codigo: string }) {
  const conhecida = razaoConhecida(codigo);
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-sm border px-1.5 py-0.5 font-mono text-[10px]",
        conhecida
          ? "border-border bg-secondary/40 text-muted-foreground"
          : ESTILO_DESCONHECIDO,
      )}
      title={conhecida ? codigo : `${codigo} — fora do vocabulário conhecido de reason_codes`}
    >
      {rotuloDaRazao(codigo)}
    </span>
  );
}

export function VereditoDecisoes({ decisoes }: { decisoes: DecisaoTicker[] }) {
  return (
    <Card className="bg-card border-border shadow-none rounded-sm">
      <CardHeader className="border-b border-border bg-secondary/30 pb-3">
        <CardTitle className="font-mono text-[11px] font-bold uppercase tracking-widest text-muted-foreground flex items-center gap-2">
          <ListChecks className="h-4 w-4" /> Decisão por ticker
        </CardTitle>
      </CardHeader>
      <CardContent className="p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-[10px] font-mono uppercase tracking-widest text-muted-foreground">
                <th className="text-left font-medium px-4 py-2">Ticker</th>
                <th className="text-left font-medium px-4 py-2">Ação</th>
                <th className="text-left font-medium px-4 py-2">Confiança</th>
                <th className="text-left font-medium px-4 py-2">Razões declaradas</th>
              </tr>
            </thead>
            <tbody>
              {decisoes.map((d) => (
                <tr key={d.ticker} className="border-b border-border/50 last:border-0">
                  <td className="px-4 py-2.5 font-mono text-sm font-bold whitespace-nowrap">{d.ticker}</td>
                  <td className="px-4 py-2.5"><Acao action={d.action} /></td>
                  <td className="px-4 py-2.5"><Confianca valor={d.confidence} /></td>
                  <td className="px-4 py-2.5">
                    {d.reasonCodes.length === 0 ? (
                      <span
                        className="font-mono text-[10px] text-amber-400"
                        title="decisão sem razão declarada não é auditável -- o validador aponta como erro"
                      >
                        sem razão declarada
                      </span>
                    ) : (
                      <div className="flex flex-wrap gap-1">
                        {d.reasonCodes.map((c) => <Razao key={c} codigo={c} />)}
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="px-4 py-2.5 text-[10px] font-mono text-muted-foreground border-t border-border">
          A decisão é este quadro; o texto acima é a explicação dela. As razões são
          conferidas contra o dado do dia por checagens determinísticas.
        </p>
      </CardContent>
    </Card>
  );
}
