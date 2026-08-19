/**
 * Modo earnings do Painel de Cenários — os selos, com os números da auditoria.
 *
 * Todo valor aqui é medido, não inventado:
 *   - "realizado" e "centro" vieram da auditoria de 17/08/2026 sobre os 5
 *     tickers do Radar IA (earnings_reaction_analysis.py, 8 eventos por papel);
 *   - "implícita" vem de dados/radar_overrides.json, coleta manual do
 *     OptionSlam em 14/08/2026 (campo move_impl_sem).
 *
 * Fixture com número real importa aqui mais que o normal: o selo é uma
 * classificação com corte numérico, e um corte calibrado contra dado
 * sintético passa a testar a si mesmo.
 */
import { describe, it, expect } from "vitest";
import {
  classificarPremio, distribuicaoBimodal, volModeloSemanalPct,
  PREMIO_RATIO_BARATA, PREMIO_RATIO_CARA,
  type ReacaoEarnings,
} from "@workspace/scenario-math";

// ── casos reais ─────────────────────────────────────────────────────────────

// PDD, balanço de 24/08/2026. O caso que motivou a tarefa: o modelo dava
// ±4,7%/sem contra 10,3% realizados, e errava o centro em 8 p.p.
const PDD = {
  volAnual: 0.339,        // ~±4,7%/sem, a vol de difusão que o painel usava
  realizadaPct: 10.3,     // |média| das reações
  centroPct: -8.2,        // viés — o modelo assume 0
  implicitaPct: 7.38,     // OptionSlam, move_impl_sem, coletado em 14/08
};

// XPEV. O modelo ACERTA a magnitude aqui (±7,7% vs 6,7%) — e mesmo assim há
// sinal, porque a implícita está muito acima do que o papel costuma andar.
const XPEV = {
  volAnual: 0.556,        // ~±7,7%/sem
  realizadaPct: 6.7,
  implicitaPct: 10.61,    // OptionSlam, move_impl_sem
};

describe("selo de prêmio — casos medidos na auditoria", () => {
  it("PDD: implícita 7,38% contra realizada 10,3% → vol barata", () => {
    expect(classificarPremio(PDD.implicitaPct, PDD.realizadaPct)).toBe("vol_barata");
    // O que o selo está dizendo: as opções pedem ~72% do que o papel
    // historicamente anda no balanço.
    expect(PDD.implicitaPct / PDD.realizadaPct).toBeLessThan(PREMIO_RATIO_BARATA);
  });

  it("XPEV: implícita 10,61% contra realizada 6,7% → prêmio caro", () => {
    expect(classificarPremio(XPEV.implicitaPct, XPEV.realizadaPct)).toBe("premio_caro");
    expect(XPEV.implicitaPct / XPEV.realizadaPct).toBeGreaterThan(PREMIO_RATIO_CARA);
  });

  it("o modelo sozinho não distingue os dois casos — por isso as três vols", () => {
    // PDD: modelo muito abaixo do realizado. XPEV: modelo em linha.
    expect(volModeloSemanalPct(PDD.volAnual)).toBeCloseTo(4.7, 1);
    expect(volModeloSemanalPct(XPEV.volAnual)).toBeCloseTo(7.7, 1);
    // E ainda assim o XPEV é o que tem desalinhamento a explorar. Olhar só o
    // modelo contra o realizado teria apontado o ticker errado.
  });
});

// ── a banda ─────────────────────────────────────────────────────────────────

describe("selo de prêmio — bordas da banda", () => {
  it("igual à realizada é alinhada", () => {
    expect(classificarPremio(10, 10)).toBe("alinhadas");
  });

  it("as bordas exatas contam como desalinhamento, não como alinhada", () => {
    expect(classificarPremio(8, 10)).toBe("vol_barata");     // razão 0,80
    expect(classificarPremio(12.5, 10)).toBe("premio_caro"); // razão 1,25
  });

  it("dentro da banda é alinhada dos dois lados", () => {
    expect(classificarPremio(8.5, 10)).toBe("alinhadas");
    expect(classificarPremio(12, 10)).toBe("alinhadas");
  });

  it("sem um dos dois lados NÃO inventa selo", () => {
    // Um selo comparando contra número ausente parece leitura e não é.
    expect(classificarPremio(null, 10)).toBeNull();
    expect(classificarPremio(10, null)).toBeNull();
    expect(classificarPremio(undefined, undefined)).toBeNull();
  });

  it("valor não-positivo não vira selo", () => {
    // Realizada 0 dividiria por zero; implícita 0 é cadeia sem preço, não
    // "opção de graça".
    expect(classificarPremio(10, 0)).toBeNull();
    expect(classificarPremio(0, 10)).toBeNull();
    expect(classificarPremio(-1, 10)).toBeNull();
  });
});

// ── bimodalidade ────────────────────────────────────────────────────────────

function reacao(over: Partial<ReacaoEarnings>): ReacaoEarnings {
  return {
    n_events: 8, close_pct_mean: 0, close_pct_abs_mean: 10,
    close_pct_std: 5, suggested_threshold_pct: 15, ...over,
  };
}

describe("aviso de distribuição bimodal", () => {
  it("MRVL: desvio 13,4pp acima da magnitude média → avisa", () => {
    // O caso da auditoria: ±19-23% ou quase nada, sem "movimento típico".
    expect(distribuicaoBimodal(reacao({ close_pct_abs_mean: 13.4, close_pct_std: 15.2 }))).toBe(true);
  });

  it("PDD: reações concentradas em torno da média → não avisa", () => {
    expect(distribuicaoBimodal(reacao({ close_pct_abs_mean: 10.3, close_pct_std: 6.1 }))).toBe(false);
  });

  it("desvio ausente não vira aviso", () => {
    // 1 evento só (ticker recém-listado): std não existe. Falta de dado não é
    // evidência de bimodalidade.
    expect(distribuicaoBimodal(reacao({ n_events: 1, close_pct_std: null }))).toBe(false);
  });

  it("com 2 eventos NÃO avisa mesmo com desvio alto", () => {
    // Com dois pontos o desvio-padrão é só a distância entre eles; o teste
    // dispararia por construção e o aviso viraria ruído em todo ticker novo.
    expect(distribuicaoBimodal(reacao({ n_events: 2, close_pct_abs_mean: 5, close_pct_std: 20 }))).toBe(false);
  });

  it("sem reação nenhuma não avisa", () => {
    expect(distribuicaoBimodal(null)).toBe(false);
    expect(distribuicaoBimodal(undefined)).toBe(false);
  });
});

// ── escala semanal ──────────────────────────────────────────────────────────

describe("volModeloSemanalPct", () => {
  it("usa dias corridos, a mesma convenção do resto do painel", () => {
    // √(7/365), não √(5/252): T = dias/365 em computeScenarioMetrics. Se as
    // duas convenções se misturassem, a coluna "modelo" discordaria da
    // distribuição desenhada logo acima dela na mesma tela.
    expect(volModeloSemanalPct(1)).toBeCloseTo(Math.sqrt(7 / 365) * 100, 6);
    expect(volModeloSemanalPct(0.5)).toBeCloseTo(6.92, 2);
  });

  it("vol zero não vira NaN", () => {
    expect(volModeloSemanalPct(0)).toBe(0);
  });
});
