import { describe, it, expect } from "vitest";
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { GRUPO_LABEL, rotuloGrupo } from "@/pages/radar";

// Este teste lê o Python de propósito. O dado dos grupos mora em
// agent/radar_ia_2026.py e o rótulo mora aqui, no frontend — nada liga os dois
// em tempo de compilação. Foi assim que `rede` virou `networking` no dado e o
// rótulo ficou para trás: ANET e CSCO passaram a aparecer como "networking"
// cru numa coluna de nomes capitalizados, e ninguém notou até o relatório sair
// por e-mail. Comparar as duas fontes aqui é o único ponto que pega a deriva.
const RELATIVO = "artifacts/api-server/src/agent/radar_ia_2026.py";

// Sobe a partir do cwd procurando a raiz do monorepo, em vez de contar saltos
// de `..`: o cwd do vitest muda conforme o teste roda pelo pacote ou pela raiz,
// e um caminho relativo fixo passaria num caso e falharia no outro.
function acharRadar(): string | null {
  let dir = process.cwd();
  for (let i = 0; i < 8; i++) {
    const alvo = resolve(dir, RELATIVO);
    if (existsSync(alvo)) return alvo;
    const pai = dirname(dir);
    if (pai === dir) break;
    dir = pai;
  }
  return null;
}

function gruposNoRadar(): string[] {
  const caminho = acharRadar();
  if (!caminho) throw new Error(`Não achei ${RELATIVO} subindo a partir de ${process.cwd()}`);
  const fonte = readFileSync(caminho, "utf8");
  const achados = [...fonte.matchAll(/"grupo":\s*"([a-z_]+)"/g)].map((m) => m[1]);
  return [...new Set(achados)].sort();
}

describe("rótulos de grupo do Radar", () => {
  it("encontra o arquivo do radar", () => {
    // Falha explícita em vez de skip: se o arquivo mudar de lugar, o teste tem
    // que gritar, senão ele para de guardar em silêncio e o bug volta.
    expect(acharRadar(), `não achei ${RELATIVO} a partir de ${process.cwd()}`).not.toBeNull();
  });

  it("lê grupos de verdade do Python", () => {
    const grupos = gruposNoRadar();
    expect(grupos.length).toBeGreaterThan(5);
    expect(grupos).toContain("memoria");
    expect(grupos).toContain("chips");
  });

  it("todo grupo do radar tem rótulo próprio", () => {
    const semRotulo = gruposNoRadar().filter((g) => !(g in GRUPO_LABEL));
    expect(semRotulo, `grupos sem rótulo em GRUPO_LABEL: ${semRotulo.join(", ")}`).toEqual([]);
  });

  it("não há rótulo órfão apontando para grupo que não existe mais", () => {
    const grupos = new Set(gruposNoRadar());
    const orfaos = Object.keys(GRUPO_LABEL).filter((k) => !grupos.has(k));
    expect(orfaos, `rótulos sem grupo correspondente no radar: ${orfaos.join(", ")}`).toEqual([]);
  });

  it("cobre os três grupos que apareciam crus", () => {
    expect(GRUPO_LABEL.foundry).toBe("Foundry");
    expect(GRUPO_LABEL.neocloud).toBe("Neoclouds");
    expect(GRUPO_LABEL.networking).toBe("Rede/Interconexão");
  });

  it("não guarda mais a chave morta `rede`", () => {
    expect(GRUPO_LABEL).not.toHaveProperty("rede");
  });
});

describe("rotuloGrupo", () => {
  it("usa o rótulo do mapa quando existe", () => {
    expect(rotuloGrupo("memoria")).toBe("Memória");
    expect(rotuloGrupo("hyperscaler")).toBe("Hyperscalers");
  });

  it("capitaliza um grupo novo em vez de mostrar a chave crua", () => {
    // Se entrar um grupo no radar antes de ganhar rótulo, ele não pode
    // aparecer em minúscula no meio de uma coluna de nomes capitalizados.
    expect(rotuloGrupo("fotonica")).toBe("Fotonica");
  });

  it("devolve travessão para ausência, não string vazia", () => {
    expect(rotuloGrupo(null)).toBe("—");
    expect(rotuloGrupo(undefined)).toBe("—");
    expect(rotuloGrupo("")).toBe("—");
  });
});
