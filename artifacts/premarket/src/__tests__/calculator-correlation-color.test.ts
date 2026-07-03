import { describe, it, expect } from "vitest";
import { correlationColor } from "../pages/calculator";

describe("correlationColor", () => {
  it("returns muted for missing values", () => {
    expect(correlationColor(null)).toBe("text-muted-foreground");
  });

  it("flags strong positive correlation as high risk (red)", () => {
    expect(correlationColor(0.95)).toBe("text-red-400");
    expect(correlationColor(0.8)).toBe("text-red-400");
  });

  it("flags strong negative correlation distinctly (blue, not red)", () => {
    expect(correlationColor(-0.95)).toBe("text-blue-400");
    expect(correlationColor(-0.8)).toBe("text-blue-400");
  });

  it("flags moderate correlation (yellow/cyan)", () => {
    expect(correlationColor(0.6)).toBe("text-yellow-400");
    expect(correlationColor(-0.6)).toBe("text-cyan-400");
  });

  it("treats weak correlation as unremarkable", () => {
    expect(correlationColor(0.2)).toBe("text-muted-foreground");
    expect(correlationColor(-0.1)).toBe("text-muted-foreground");
    expect(correlationColor(1)).toBe("text-red-400"); // self-correlation on the diagonal
  });
});
