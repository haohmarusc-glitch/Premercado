import { FileSearch } from "lucide-react";

// ─── O que não veio, e quem deveria ter buscado ──────────────────────────────
// A camada fundamental é opcional por desenho: fonte fora do ar não derruba a
// análise técnica. Mas o texto que sai disso ("Informações fundamentais e de
// valuation não estavam disponíveis para análise neste momento") é um beco sem
// saída -- não diz QUAL fonte falhou, nem POR QUÊ, nem onde olhar.
//
// Até aqui a única pista era a OMISSÃO na linha de fontes: notar que
// "valuation/DCF (FMP)" sumiu exige saber de cor que a lista tem três itens.
// O motivo real só existia no stderr do processo Python -- e a tela é onde o
// operador estava olhando.

/** Cada ausência vem do `COLETORES` do `analise_rapida_ia.py`. */
export interface AusenciaDeColeta {
  bloco: string;
  funcao: string;
  arquivo: string;
  motivo: string;
}

// O link aponta para o ARQUIVO, não para uma linha: número de linha envelhece
// a cada commit e mandaria o leitor para o meio de outra função. O nome da
// função vai no texto do link, que é o que se procura dentro do arquivo.
const REPO = "https://github.com/haohmarusc-glitch/Premercado/blob/main";

export function CamadaAusente({ ausencias }: { ausencias: AusenciaDeColeta[] }) {
  if (ausencias.length === 0) return null;
  return (
    <div className="font-mono text-[11px] px-3 py-2 rounded border border-border bg-secondary/30 text-muted-foreground space-y-1">
      <p className="flex items-center gap-1.5 font-semibold text-foreground/80">
        <FileSearch className="h-3.5 w-3.5" />
        {ausencias.length === 1
          ? "1 bloco da camada fundamental não veio:"
          : `${ausencias.length} blocos da camada fundamental não vieram:`}
      </p>
      {ausencias.map((a) => (
        <p key={a.bloco} className="pl-5">
          <span className="text-foreground/80">{a.bloco}</span> — {a.motivo} · busca em{" "}
          <a
            href={`${REPO}/${a.arquivo}`}
            target="_blank"
            rel="noopener noreferrer"
            className="underline decoration-dotted underline-offset-2 hover:text-foreground"
            title={a.arquivo}
          >
            {a.funcao}()
          </a>
        </p>
      ))}
    </div>
  );
}
