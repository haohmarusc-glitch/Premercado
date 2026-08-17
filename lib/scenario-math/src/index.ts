/* ============================================================
Núcleo matemático do Painel de Cenários — compartilhado entre o
frontend (artifacts/premarket/src/pages/cenarios.tsx, renderização
interativa) e o backend (artifacts/api-server/src/lib/scenario-alert-checker.ts,
checagem periódica em background pro alerta por e-mail). Extraído pra
pacote próprio pra existir UMA fonte de verdade da fórmula, em vez de
manter a mesma matemática duplicada em dois lugares (e arriscar os dois
divergirem com o tempo).

NÃO alterar sem revisar:
1. Caixa vs risco: posições marcadas como vendidas somam em caixa e
   saem da matriz de covariância.
2. Cenário central: cada posição ativa move beta × movimento_do_setor,
   com piso em zero.
3. Sigma da carteira: √(ΣΣ wᵢwⱼσᵢσⱼρᵢⱼ), ρ=1 na diagonal e 0.75 fora,
   escalado por √T e pelo multiplicador do slider.
4. Probabilidade de empatar: lognormal, com os casos degenerados
   (precisa≤0, risco≤0, sd≤0) tratados à parte.
5. Quantis: q(z) = caixa + risco·exp(drift + z·sd).

Limitações conhecidas (não silenciar):
- vol é estimativa histórica, não volatilidade implícita de opções.
- beta constante trata balanço como movimento difusivo; o salto de
  earnings (ver volComSalto) cobre isso só pra posições com balanço
  dentro do horizonte até a data-alvo.
- SKHY tem um fator que beta não captura (compressão do prêmio do ADR
  sobre a ação coreana) -- componente mecânico, não de mercado.
============================================================ */

export const RHO = 0.75; // correlação média entre as posições (todas do mesmo setor)

export interface ScenarioPosition {
  t: string; // ticker
  nome: string;
  value: number; // valor de mercado atual em US$
  cost: number; // total investido em US$
  vol: number; // volatilidade anualizada estimada (0–1)
  beta: number; // sensibilidade ao movimento do setor (SOX)
  evento: string; // data do próximo balanço, dd/mm ou "—"
  eventoISO: string | null; // data do próximo balanço, YYYY-MM-DD
  jumpStdPct: number | null; // desvio-padrão (pp) da reação histórica de earnings, ou null se indisponível
}

export interface ScenarioMetrics {
  T: number;
  custoTotal: number;
  caixa: number;
  risco: number;
  valorTotalHoje: number;
  drift: number;
  sigma: number;
  sd: number;
  pEmpate: number;
  pQueda: number;
  gatilhoQueda: number;
  p05: number;
  p50: number;
  p95: number;
  central: number;
}

export function erf(x: number): number {
  const s = x < 0 ? -1 : 1;
  x = Math.abs(x);
  const t = 1 / (1 + 0.3275911 * x);
  const y =
    1 -
    ((((1.061405429 * t - 1.453152027) * t + 1.421413741) * t - 0.284496736) * t +
      0.254829592) *
      t *
      Math.exp(-x * x);
  return s * y;
}

export function Phi(z: number): number {
  return 0.5 * (1 + erf(z / Math.SQRT2));
}

export function diasAteAlvo(dataAlvo: Date, agora: Date = new Date()): number {
  return Math.max(1, Math.round((dataAlvo.getTime() - agora.getTime()) / 86400000));
}

// Soma um "salto" de earnings à volatilidade de difusão quando o próximo
// balanço da posição cai dentro da janela [hoje, data-alvo] -- em vez de
// tratar o dia do resultado como um dia normal de difusão (que subestima a
// cauda), a variância do salto (jumpStdPct, medido sobre a reação histórica
// real do ticker) é somada como um choque independente:
// vol_efetivo² · T = vol² · T + jumpVar.
export function volComSalto(vol: number, jumpStdPct: number | null, eventoISO: string | null, dataAlvo: Date, T: number, agora: Date = new Date()): number {
  if (jumpStdPct == null || !eventoISO || T <= 0) return vol;
  const evento = new Date(eventoISO + "T00:00:00");
  if (evento < agora || evento > dataAlvo) return vol; // balanço fora da janela até a data-alvo
  const jumpVar = (jumpStdPct / 100) ** 2;
  return Math.sqrt(vol * vol + jumpVar / T);
}

export function temSaltoNoHorizonte(p: ScenarioPosition, dataAlvo: Date, agora: Date = new Date()): boolean {
  if (p.jumpStdPct == null || !p.eventoISO) return false;
  const evento = new Date(p.eventoISO + "T00:00:00");
  return evento >= agora && evento <= dataAlvo;
}

// Probabilidade de UMA posição isolada (sem correlação com o resto da
// carteira) empatar com o próprio custo até a data-alvo -- mesma lógica
// lognormal de ScenarioMetrics.pEmpate, mas em escala individual.
export function probEmpateIndividual(p: ScenarioPosition, currentValue: number, dataAlvo: Date, T: number, setor: number, volMult: number): number | null {
  if (currentValue <= 0 || T <= 0) return null;
  if (p.cost <= 0) return 1;
  const volEff = volComSalto(p.vol, p.jumpStdPct, p.eventoISO, dataAlvo, T);
  const sd = volEff * Math.sqrt(T) * volMult;
  if (sd <= 0) return null;
  const centralMult = Math.max(0, 1 + (p.beta * setor) / 100);
  const drift = Math.log(Math.max(centralMult, 1e-6));
  return 1 - Phi((Math.log(p.cost / currentValue) - drift) / sd);
}

// Calcula as métricas agregadas da carteira pra distribuição de resultados
// na data-alvo. `valores` é um override manual por ticker (ex.: edição do
// usuário na UI); tickers ausentes usam `p.value` (preço de mercado vindo
// do backend). `vendidas` marca quais posições já viraram caixa.
export function computeScenarioMetrics(
  lista: ScenarioPosition[],
  vendidas: Record<string, boolean>,
  valores: Record<string, number>,
  setor: number,
  volMult: number,
  dataAlvo: Date,
): ScenarioMetrics {
  const dias = diasAteAlvo(dataAlvo);
  const T = dias / 365;
  const custoTotal = lista.reduce((a, p) => a + p.cost, 0);

  const ativas = lista.filter((p) => !vendidas[p.t]);
  const caixa = lista.filter((p) => vendidas[p.t]).reduce((a, p) => a + (valores[p.t] ?? p.value), 0);
  const risco = ativas.reduce((a, p) => a + (valores[p.t] ?? p.value), 0);
  const valorTotalHoje = lista.reduce((a, p) => a + (valores[p.t] ?? p.value), 0);

  // cenário central: cada posição move beta × movimento do setor, piso em zero
  const riscoCentral = ativas.reduce(
    (a, p) => a + (valores[p.t] ?? p.value) * Math.max(0, 1 + (p.beta * setor) / 100),
    0,
  );
  const drift = risco > 0 ? Math.log(Math.max(riscoCentral, 1e-6) / risco) : 0;

  // sigma da carteira de risco (matriz de covariância com rho constante) --
  // usa vol_efetivo (com salto de earnings quando aplicável) no lugar do
  // vol de difusão puro, ver volComSalto().
  let sigma = 0;
  if (risco > 0) {
    let varSum = 0;
    ativas.forEach((a) => {
      ativas.forEach((b) => {
        const wa = (valores[a.t] ?? a.value) / risco;
        const wb = (valores[b.t] ?? b.value) / risco;
        const r = a.t === b.t ? 1 : RHO;
        const volA = volComSalto(a.vol, a.jumpStdPct, a.eventoISO, dataAlvo, T);
        const volB = volComSalto(b.vol, b.jumpStdPct, b.eventoISO, dataAlvo, T);
        varSum += wa * wb * volA * volB * r;
      });
    });
    sigma = Math.sqrt(varSum);
  }
  const sd = sigma * Math.sqrt(T) * volMult;

  // probabilidade de o total atingir o custo total (empatar)
  const precisa = custoTotal - caixa;
  let pEmpate: number;
  if (precisa <= 0) pEmpate = 1;
  else if (risco <= 0 || sd <= 0) pEmpate = 0;
  else pEmpate = 1 - Phi((Math.log(precisa / risco) - drift) / sd);

  // probabilidade de perder mais 20% do valor de risco de hoje
  const gatilhoQueda = caixa + risco * 0.8;
  let pQueda: number;
  if (risco <= 0 || sd <= 0) pQueda = 0;
  else pQueda = Phi((Math.log(0.8) - drift) / sd);

  const q = (z: number) => caixa + risco * Math.exp(drift + z * sd);

  return {
    T, custoTotal, caixa, risco, valorTotalHoje, drift, sigma, sd,
    pEmpate, pQueda, gatilhoQueda,
    p05: q(-1.645), p50: q(0), p95: q(1.645),
    central: caixa + riscoCentral,
  };
}

// Termômetro de confirmação: fração dos dias de acompanhamento em que a
// pEmpate do snapshot diário ficou acima do limiar configurado -- "em X% dos
// dias desde que você começou a acompanhar, o modelo dava pelo menos Y% de
// chance de empatar". Retorna null sem histórico (nada a mostrar ainda).
export function pctConfirmacao(snapshots: { pEmpate: number }[], thresholdPct: number): number | null {
  if (!snapshots.length) return null;
  const dentro = snapshots.filter((s) => s.pEmpate * 100 >= thresholdPct).length;
  return (dentro / snapshots.length) * 100;
}

// Resolução de um ciclo: bateu = a carteira realmente empatou (ou superou) o
// custo total até a data-alvo -- o mesmo evento que pEmpate estimava a
// probabilidade de acontecer.
export function cicloBateu(valorFinal: number, custoTotal: number): boolean {
  return valorFinal >= custoTotal;
}

/* ============================================================
Modo earnings — as três volatilidades

Mora aqui, e não na tela, porque a leitura tem que ser a MESMA nos dois
lados: o card de /cenarios e o resumo servido pela rota
(/api/scenarios/earnings-window) precisam concordar sobre o que é
"prêmio caro". Duas cópias da regra divergiriam na primeira calibragem.

O ponto do modo: quando a janela do cenário contém um balanço, a vol de
difusão do painel não é uma aproximação -- é o número errado. Ela é
simétrica (não tem viés), não tem drift e dilui o dia do evento entre 251
dias comuns. Medido na auditoria de 17/08/2026:

  PDD   modelo ±4,7%/sem vs realizado 10,3% com centro -8,2%
  XPEV  modelo ±7,7% vs realizado 6,7% (em linha) -- mas implícita 10,6%
  MRVL  modelo ±11% vs realizado 13,4%, e distribuição bimodal

O modo não substitui a lognormal: põe as três leituras lado a lado e
deixa o desalinhamento visível, que é onde está a informação.
============================================================ */

export type SeloPremio = "vol_barata" | "premio_caro" | "alinhadas";

// Fonte da vol implícita, em ordem de preferência. "manual" carrega
// carimbo de coleta -- ver dados/radar_overrides.json.
export type FonteImplicito = "straddle_atm" | "manual";

export interface MoveImplicito {
  pct: number;
  fonte: FonteImplicito;
  vencimento?: string | null;
  fonteNome?: string | null;
  coletadoEm?: string | null;
  idadeDias?: number | null;
}

// Subconjunto do `summary` de earnings_reaction_analysis.py que este
// módulo usa. Nomes em snake_case porque vêm do Python sem tradução --
// renomear no meio do caminho só criaria um segundo vocabulário.
export interface ReacaoEarnings {
  n_events: number;
  close_pct_mean: number;
  close_pct_abs_mean: number;
  close_pct_std: number | null;
  suggested_threshold_pct: number;
  current_price?: number;
  r1_price?: number;
  r2_price?: number;
  s1_price?: number;
  s2_price?: number;
}

// Banda de "alinhadas": ±25% em torno da realizada, simétrica em log
// (1/0,8 = 1,25). Não é arbitrária -- com ~8 eventos o erro-padrão da
// própria média realizada já fica na casa de 30%, então uma banda mais
// estreita estaria rotulando ruído amostral como sinal.
export const PREMIO_RATIO_BARATA = 0.8;
export const PREMIO_RATIO_CARA = 1.25;

// Vol de difusão anual -> desvio de uma semana, em pontos percentuais.
// √(7/365) e não √(5/252): o painel inteiro conta o horizonte em dias
// CORRIDOS (ver diasAteAlvo e T = dias/365), e misturar as duas
// convenções aqui faria a coluna "modelo" discordar do resto da tela.
export function volModeloSemanalPct(volAnual: number): number {
  return volAnual * Math.sqrt(7 / 365) * 100;
}

// A comparação que dá o selo: o que as opções cobram HOJE contra o que o
// papel realmente fez nos últimos balanços.
//
// null quando falta um dos dois lados. Deliberado: um selo comparando
// contra um número ausente é pior que nenhum selo -- ele parece uma
// leitura e não é.
export function classificarPremio(
  implicitaPct: number | null | undefined,
  realizadaPct: number | null | undefined,
): SeloPremio | null {
  if (implicitaPct == null || realizadaPct == null) return null;
  if (!(realizadaPct > 0) || !(implicitaPct > 0)) return null;
  const razao = implicitaPct / realizadaPct;
  if (razao <= PREMIO_RATIO_BARATA) return "vol_barata";
  if (razao >= PREMIO_RATIO_CARA) return "premio_caro";
  return "alinhadas";
}

// Indício de bimodalidade tipo MRVL: quando o desvio-padrão das reações
// supera a magnitude média delas, a série não tem um "movimento típico"
// -- ela alterna entre saltos grandes e quase nada. A lognormal concentra
// massa justamente no centro que esse papel não frequenta, e o efeito é
// subestimar as duas caudas ao mesmo tempo.
//
// Precisa de >= 3 eventos: com dois, o desvio-padrão é a distância entre
// eles e o teste dispara por construção.
export function distribuicaoBimodal(r: ReacaoEarnings | null | undefined): boolean {
  if (!r || r.close_pct_std == null || r.n_events < 3) return false;
  return r.close_pct_std > r.close_pct_abs_mean;
}
