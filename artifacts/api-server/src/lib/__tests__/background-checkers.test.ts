/**
 * Guard que decide se o processo roda os checkers de fundo.
 *
 * Sem ele, index.ts ligava os quatro incondicionalmente -- então um
 * `pnpm run dev` no mesmo container do app deployado dobrava a carga de
 * subprocessos Python sobre o yfinance, e todo subprocesso do lado deployado
 * estourava seu timeout (visto em produção 02/08).
 */
import { describe, it, expect } from "vitest";
import { shouldRunBackgroundCheckers } from "../background-checkers";

describe("shouldRunBackgroundCheckers", () => {
  it("liga em produção", () => {
    expect(shouldRunBackgroundCheckers({ NODE_ENV: "production" })).toBe(true);
  });

  it("desliga em development -- o caso que causou a contenção", () => {
    expect(shouldRunBackgroundCheckers({ NODE_ENV: "development" })).toBe(false);
  });

  it("liga quando NODE_ENV não está definido (não presume dev)", () => {
    expect(shouldRunBackgroundCheckers({})).toBe(true);
  });

  it("a env força ligado mesmo em development", () => {
    expect(
      shouldRunBackgroundCheckers({ NODE_ENV: "development", RUN_BACKGROUND_CHECKERS: "1" }),
    ).toBe(true);
  });

  it("a env força desligado mesmo em produção", () => {
    expect(
      shouldRunBackgroundCheckers({ NODE_ENV: "production", RUN_BACKGROUND_CHECKERS: "0" }),
    ).toBe(false);
  });

  it('aceita "false" além de "0"', () => {
    expect(
      shouldRunBackgroundCheckers({ NODE_ENV: "production", RUN_BACKGROUND_CHECKERS: "false" }),
    ).toBe(false);
    expect(
      shouldRunBackgroundCheckers({ NODE_ENV: "production", RUN_BACKGROUND_CHECKERS: "FALSE" }),
    ).toBe(false);
  });

  it("env vazia não conta como escolha explícita", () => {
    expect(
      shouldRunBackgroundCheckers({ NODE_ENV: "development", RUN_BACKGROUND_CHECKERS: "" }),
    ).toBe(false);
  });
});
