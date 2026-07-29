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
