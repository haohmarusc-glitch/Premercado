export function clamp(val: unknown, min: number, max: number, def: number): number {
  const n = parseFloat(String(val ?? def));
  if (isNaN(n)) return def;
  return Math.min(Math.max(n, min), max);
}

// SL/TP são opcionais -- undefined desliga o mecanismo inteiro (o Python só
// checa o nível quando o valor não é None). 0 ou negativo não fazem sentido
// como distância de stop/alvo, então caem no mesmo "desligado" que ausente.
export function optionalPct(val: unknown): number | undefined {
  if (val === undefined || val === null || val === "") return undefined;
  const n = parseFloat(String(val));
  if (isNaN(n) || n <= 0) return undefined;
  return Math.min(n, 0.95);
}
