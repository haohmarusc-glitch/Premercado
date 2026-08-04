/**
 * Checklist executada ANTES de mandar o relatório por e-mail.
 *
 * Motivação: revisando 7 e-mails seguidos do agente, os defeitos que
 * chegaram ao usuário não eram sutis -- eram coisas que uma checagem
 * mecânica sobre o próprio texto teria pego. Índice com +17,91% num pregão,
 * "SKHY desceu -3,54% hoje" escrito num sábado, rótulo 🟡 no cabeçalho e 🔴
 * três linhas abaixo, quatro fluxos diferentes chegando com o mesmo assunto.
 *
 * O validador de rótulo (report_validator.py) roda DENTRO da execução e só
 * enxerga o Grupo A do relatório diário. Esta checklist é a última porta:
 * roda para TODO modo, sobre o artefato exato que vai ser enviado, e tem
 * acesso ao banco -- que é o que permite comparar com o relatório anterior e
 * detectar segundo envio no mesmo dia.
 *
 * Postura: só DUAS checagens bloqueiam o envio (relatório vazio e e-mail
 * duplicado), porque nas duas mandar é pior que não mandar. As outras oito
 * viram aviso no topo do e-mail: um falso positivo que engole o relatório do
 * dia seria pior que o defeito que ele denuncia.
 */
import { and, desc, eq, ne, sql } from "drizzle-orm";
import { db, reportsTable } from "@workspace/db";
import { logger } from "./logger";

export interface Achado {
  code: string;
  severity: "BLOCK" | "WARN";
  message: string;
}

export interface PreflightResult {
  achados: Achado[];
  bloqueado: boolean;
}

// Um relatório real do agente passa de 1500 chars com folga; abaixo disso é
// mensagem de falha ("Análise incompleta...") ou continuação truncada.
const MIN_CHARS = 800;

// Movimento diário de índice amplo acima disso é implausível (circuit breaker
// interrompe antes). O KOSPI apareceu com +17,91% em DOIS dias diferentes --
// 31/07, quando foi usado como fato, e 02/08, quando o guardrail da tool o
// marcou como suspeito. Valor idêntico nos dois: erro persistente da fonte.
const INDICE_MAX_PCT = 8.0;

const INDICES = [
  "KOSPI", "Nikkei", "Hang Seng", "DAX", "FTSE", "CAC",
  "S&P 500", "Nasdaq 100", "IBOV", "SMH", "SOXX", "QQQ", "SPY",
];

const ROTULOS = ["🟢", "🟡", "🔴"];

function achado(code: string, severity: Achado["severity"], message: string): Achado {
  return { code, severity, message };
}

/** Preços em dólar citados no texto, na ordem em que aparecem. */
function precosCitados(texto: string): string[] {
  return [...texto.matchAll(/\$\s?(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2}))/g)].map((m) => m[1]!);
}

/** Divide o texto em seções por cabeçalho Markdown. */
function secoes(texto: string): string[] {
  return texto.split(/\n(?=#{1,6}\s)/);
}

export async function preflightRelatorio(opts: {
  content: string;
  date: string;
  mode: string;
  tickers: string[];
  agora?: Date;
  /** Id do relatório recém-gravado, para não compará-lo consigo mesmo. */
  reportIdAtual?: number;
}): Promise<PreflightResult> {
  const { content, date, mode, tickers, reportIdAtual } = opts;
  const agora = opts.agora ?? new Date();
  const achados: Achado[] = [];
  const texto = content;
  const baixo = texto.toLowerCase();

  // 1. Relatório vazio ou curto demais para ser um relatório.
  if (texto.trim().length < MIN_CHARS) {
    achados.push(achado("RELATORIO_VAZIO", "BLOCK",
      `Conteúdo tem ${texto.trim().length} chars (mínimo ${MIN_CHARS}) — provável falha da run, não relatório.`));
  }

  // 2. Segundo e-mail do MESMO modo no MESMO dia. Em 31/07 saíram três
  //    e-mails (08:37, 08:50, 14:18), sendo um deles só um fragmento com
  //    GOOGL/TSLA -- a caixa de entrada não deixa distinguir qual vale.
  try {
    const anteriores = await db
      .select({ n: sql<number>`count(*)::int` })
      .from(reportsTable)
      .where(and(eq(reportsTable.date, date), eq(reportsTable.mode, mode)));
    const n = anteriores[0]?.n ?? 0;
    if (n > 1) {
      achados.push(achado("SEGUNDO_EMAIL_HOJE", "BLOCK",
        `Já existem ${n} relatórios do modo "${mode}" em ${date} — este seria um envio duplicado.`));
    }
  } catch (err) {
    logger.warn({ err }, "Preflight: falha ao checar envio duplicado (seguindo sem essa checagem)");
  }

  // 3. Fim de semana não sinalizado. Sábado/domingo não têm pregão nos EUA,
  //    e os relatórios de 01 e 02/08 apresentaram o fechamento de sexta como
  //    leitura do dia sem dizer isso em nenhum momento.
  const diaSemana = agora.getUTCDay(); // 0=dom, 6=sáb
  const fimDeSemana = diaSemana === 0 || diaSemana === 6;
  if (fimDeSemana) {
    const sinaliza = /sem pregão|não há pregão|nao ha pregao|mercado fechado|último pregão|ultimo pregao|fim de semana/i.test(texto);
    if (!sinaliza) {
      achados.push(achado("FIM_DE_SEMANA_NAO_SINALIZADO", "WARN",
        "Hoje não tem pregão e o texto não diz isso — os números são do último fechamento."));
    }
  }

  // 4. Percentual implausível atribuído a índice amplo.
  for (const idx of INDICES) {
    const re = new RegExp(`${idx.replace(/[.*+?^${}()|[\\]\\\\]/g, "\\$&")}[^.\\n]{0,40}?([+-]?\\d{1,3}[.,]\\d{1,2})\\s*%`, "gi");
    for (const m of texto.matchAll(re)) {
      const pct = Math.abs(parseFloat(m[1]!.replace(",", ".")));
      if (pct > INDICE_MAX_PCT) {
        achados.push(achado("INDICE_IMPLAUSIVEL", "WARN",
          `${idx} citado com ${m[1]}% num pregão — acima do máximo plausível de ${INDICE_MAX_PCT}% para índice amplo.`));
      }
    }
  }

  // 5. Preços idênticos ao relatório anterior num dia útil. Em fim de semana
  //    isso é o correto (mercado fechado) e a checagem 3 já cobre.
  //
  //    `reportIdAtual` NÃO é opcional por preguiça: runner.ts grava o relatório
  //    ANTES de chamar o preflight, então "o mais recente do mesmo modo" é o
  //    próprio relatório sendo checado. Sem excluí-lo, a comparação é do texto
  //    contra ele mesmo e o alerta dispara sempre.
  //
  //    Produção 04/08: "Todos os 25 preços citados são idênticos aos do
  //    relatório anterior" numa run em que SMCI foi de 29,72 pra 30,05, ARM de
  //    250,50 pra 270,00 e HCC de 76,84 pra 82,16. Um alerta que grita em toda
  //    run ensina a ignorar alertas -- custa mais que não existir.
  //
  //    A checagem 2 (SEGUNDO_EMAIL_HOJE) usa `n > 1` justamente porque conta
  //    com o recém-inserido; por isso a exclusão é só aqui.
  if (!fimDeSemana) {
    try {
      const [anterior] = await db
        .select({ content: reportsTable.content })
        .from(reportsTable)
        .where(
          reportIdAtual != null
            ? and(eq(reportsTable.mode, mode), ne(reportsTable.id, reportIdAtual))
            : eq(reportsTable.mode, mode),
        )
        .orderBy(desc(reportsTable.id))
        .limit(1);
      if (anterior?.content) {
        const atuais = precosCitados(texto);
        const antigos = new Set(precosCitados(anterior.content));
        const repetidos = atuais.filter((p) => antigos.has(p));
        if (atuais.length >= 4 && repetidos.length === atuais.length) {
          achados.push(achado("PRECOS_CONGELADOS", "WARN",
            `Todos os ${atuais.length} preços citados são idênticos aos do relatório anterior num dia útil — cotação possivelmente travada.`));
        }
      }
    } catch (err) {
      logger.warn({ err }, "Preflight: falha ao comparar com relatório anterior");
    }
  }

  // 6. Earnings iminente (≤5 dias) sem seção própria do ativo. É o gate mais
  //    forte da rubrica; se o ativo merece 🔴 por evento, ele merece seção.
  for (const m of texto.matchAll(/\b([A-Z]{2,5})\b[^.\n]{0,120}?earnings\s+em\s+(\d{1,2})\s*(?:dias?|d)\b/gi)) {
    const tk = m[1]!.toUpperCase();
    const dias = parseInt(m[2]!, 10);
    if (dias > 5) continue;
    const temSecao = secoes(texto).some((s) => /^#{1,6}\s/.test(s) && new RegExp(`(?<![A-Z0-9])${tk}(?![A-Z0-9])`).test(s.split("\n")[0] ?? ""));
    if (!temSecao) {
      achados.push(achado("EARNINGS_IMINENTE_SEM_SECAO", "WARN",
        `${tk} tem earnings em ${dias} dias mas não tem seção própria no relatório.`));
    }
  }

  // 7. Dois rótulos diferentes na mesma seção. Em 02/08 o HCC saiu com 🟡 no
  //    cabeçalho e uma "correção" para 🔴 três linhas abaixo — o leitor não
  //    tem como saber qual valeu.
  for (const sec of secoes(texto)) {
    const presentes = ROTULOS.filter((r) => sec.includes(r));
    if (presentes.length > 1) {
      const titulo = (sec.split("\n")[0] ?? "").replace(/^#+\s*/, "").slice(0, 60);
      achados.push(achado("ROTULO_CONTRADITORIO", "WARN",
        `Seção "${titulo}" tem ${presentes.join(" e ")} ao mesmo tempo.`));
    }
  }

  // 8. Verbo de direção contra o sinal do percentual ("subiu ... -3,5%").
  for (const m of texto.matchAll(/\b(subiu|avançou|avancou|disparou|caiu|recuou|despencou)\b[^.\n]{0,40}?([+-])\s?(\d{1,3}[.,]\d{1,2})\s*%/gi)) {
    const verbo = m[1]!.toLowerCase();
    const sinal = m[2]!;
    const alta = /^(subiu|avan|disparou)/.test(verbo);
    if ((alta && sinal === "-") || (!alta && sinal === "+")) {
      achados.push(achado("DIRECAO_INCOERENTE", "WARN",
        `"${verbo}" seguido de ${sinal}${m[3]}% — verbo e sinal se contradizem.`));
    }
  }

  // 9. Ticker da carteira ausente do relatório.
  const ausentes = tickers.filter(
    (tk) => !new RegExp(`(?<![A-Z0-9])${tk}(?![A-Z0-9])`, "i").test(texto),
  );
  if (ausentes.length) {
    achados.push(achado("TICKER_AUSENTE", "WARN",
      `Ativo(s) sem menção no relatório: ${ausentes.join(", ")}.`));
  }

  // 10. Marcador de execução truncada. O loop já anexa esses avisos quando a
  //     run acaba cedo; sair por e-mail sem destaque faz passar despercebido.
  if (/análise incompleta|analise incompleta|limite de turnos atingido|encerrada antes do previsto|observações esperadas foram salvas/i.test(baixo)) {
    achados.push(achado("EXECUCAO_TRUNCADA", "WARN",
      "Texto contém marcador de execução incompleta — relatório pode estar parcial."));
  }

  return { achados, bloqueado: achados.some((a) => a.severity === "BLOCK") };
}

/** Bloco de avisos para o topo do e-mail. Vazio quando não há achado WARN. */
export function bannerDeAvisos(achados: Achado[]): string {
  const warns = achados.filter((a) => a.severity === "WARN");
  if (!warns.length) return "";
  const linhas = warns.map((a) => `- **${a.code}**: ${a.message}`);
  return `> ⚠️ **Verificação automática antes do envio**\n>\n${linhas.map((l) => `> ${l}`).join("\n")}\n\n---\n\n`;
}
