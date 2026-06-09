import { describe, it, expect } from "vitest";
import { cn } from "../lib/utils";

describe("cn", () => {
  it("combina classes simples", () => {
    expect(cn("foo", "bar")).toBe("foo bar");
  });

  it("ignora valores falsy", () => {
    expect(cn("foo", undefined, null, false, "bar")).toBe("foo bar");
  });

  it("resolve conflitos do Tailwind (último vence)", () => {
    expect(cn("p-2", "p-4")).toBe("p-4");
  });

  it("combina classes condicionais", () => {
    const active = true;
    expect(cn("base", active && "active")).toBe("base active");
  });
});
