/**
 * Guard que decide se o processo roda os checkers de fundo POR TIMER.
 *
 * Histórico, porque o padrão já mudou duas vezes e cada mudança tinha razão:
 *
 * 1. "ligado em produção, desligado em dev" -- um `pnpm run dev` no mesmo
 *    container dobrava a carga de Python e todo subprocesso do lado deployado
 *    estourava timeout (02/08).
 * 2. "desligado em todo lugar" -- no Autoscale as instâncias antigas seguiam
 *    vivas nas trocas de versão, cada uma com timer e fila próprios, e a que
 *    não recebia tráfego falhava tudo (04/08). Os ciclos migraram para
 *    POST /api/checkers/run.
 * 3. De volta a "ligado fora de development" -- Reserved VM: um processo só,
 *    sempre de pé, CPU dedicada. Sem instância fantasma, timer é o modelo
 *    certo, e um padrão `false` sem gatilho externo faria NENHUM checker rodar
 *    em silêncio.
 */
import { describe, it, expect } from "vitest";
import { shouldRunBackgroundCheckers } from "../background-checkers";

describe("shouldRunBackgroundCheckers", () => {
  it("ligado em produção -- um processo só, sempre de pé", () => {
    expect(shouldRunBackgroundCheckers({ NODE_ENV: "production" })).toBe(true);
  });

  it("desligado em development -- não competir com o deployado", () => {
    expect(shouldRunBackgroundCheckers({ NODE_ENV: "development" })).toBe(false);
  });

  it("ligado quando NODE_ENV não está definido", () => {
    // Só development desliga. Qualquer outro ambiente é tratado como "roda" --
    // o modo de falha de rodar de menos (nenhum alerta, em silêncio) é pior
    // que o de rodar demais (um ciclo concorrendo, visível no log).
    expect(shouldRunBackgroundCheckers({})).toBe(true);
  });

  it("a env força ligado (teste local em dev)", () => {
    expect(
      shouldRunBackgroundCheckers({ NODE_ENV: "development", RUN_BACKGROUND_CHECKERS: "1" }),
    ).toBe(true);
  });

  it('a env com "0"/"false" força desligado (instância só de HTTP)', () => {
    expect(
      shouldRunBackgroundCheckers({ NODE_ENV: "production", RUN_BACKGROUND_CHECKERS: "0" }),
    ).toBe(false);
    expect(
      shouldRunBackgroundCheckers({ NODE_ENV: "production", RUN_BACKGROUND_CHECKERS: "false" }),
    ).toBe(false);
    expect(
      shouldRunBackgroundCheckers({ NODE_ENV: "production", RUN_BACKGROUND_CHECKERS: "FALSE" }),
    ).toBe(false);
  });

  it("env vazia não conta como escolha explícita", () => {
    expect(
      shouldRunBackgroundCheckers({ NODE_ENV: "production", RUN_BACKGROUND_CHECKERS: "" }),
    ).toBe(true);
    expect(
      shouldRunBackgroundCheckers({ NODE_ENV: "development", RUN_BACKGROUND_CHECKERS: "" }),
    ).toBe(false);
  });
});
