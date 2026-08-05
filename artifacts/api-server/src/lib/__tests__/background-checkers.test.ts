/**
 * Guard que decide se o processo roda os checkers de fundo POR TIMER.
 *
 * Histórico: começou como "ligado em produção, desligado em dev" (um `pnpm run
 * dev` no mesmo container dobrava a carga de Python e todo subprocesso do lado
 * deployado estourava timeout, visto 02/08). Em 05/08 virou "desligado em todo
 * lugar": no Autoscale a CPU só existe durante um request, então os ciclos
 * migraram pra POST /api/checkers/run disparado por Scheduled Deployment, e o
 * timer interno passou a ser opt-in explícito via env.
 */
import { describe, it, expect } from "vitest";
import { shouldRunBackgroundCheckers } from "../background-checkers";

describe("shouldRunBackgroundCheckers", () => {
  it("desligado em produção -- os ciclos rodam via request agendado", () => {
    expect(shouldRunBackgroundCheckers({ NODE_ENV: "production" })).toBe(false);
  });

  it("desligado em development", () => {
    expect(shouldRunBackgroundCheckers({ NODE_ENV: "development" })).toBe(false);
  });

  it("desligado quando NODE_ENV não está definido", () => {
    expect(shouldRunBackgroundCheckers({})).toBe(false);
  });

  it("a env força ligado (worker dedicado / teste local)", () => {
    expect(
      shouldRunBackgroundCheckers({ NODE_ENV: "development", RUN_BACKGROUND_CHECKERS: "1" }),
    ).toBe(true);
    expect(
      shouldRunBackgroundCheckers({ NODE_ENV: "production", RUN_BACKGROUND_CHECKERS: "1" }),
    ).toBe(true);
  });

  it('a env com "0"/"false" mantém desligado', () => {
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
    ).toBe(false);
  });
});
