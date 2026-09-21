/**
 * Ver o cabeçalho de lib/erro-de-rede.ts para o incidente que motivou.
 *
 * O teste tem dois lados, e o segundo é o que fecha o buraco de verdade: não
 * basta traduzir a falha de rede se o SERVIDOR continuar respondendo frases
 * que se parecem com ela — foi essa coincidência que deixou impossível saber,
 * olhando a tela, se a Análise Rápida tinha perdido a conexão ou recebido um
 * 500 do cálculo.
 */
import { describe, it, expect } from "vitest";
import { readFileSync, readdirSync, statSync } from "fs";
import { join } from "path";
import { ehFalhaDeRede, ehRespostaNaoJson, mensagemDeFalha } from "../lib/erro-de-rede";

function erroDe(nome: string, mensagem: string): Error {
  const e = new Error(mensagem);
  e.name = nome;
  return e;
}

describe("erro-de-rede", () => {
  it("reconhece a frase de cada navegador para 'nada voltou'", () => {
    // Chrome, Firefox, Safari e React Native dizem a mesma coisa de formas
    // diferentes. Cobrir só o Chrome deixaria o iPhone com a frase crua.
    for (const m of [
      "Failed to fetch",
      "NetworkError when attempting to fetch resource.",
      "Load failed",
      "Network request failed",
    ]) {
      expect(ehFalhaDeRede(erroDe("TypeError", m))).toBe(true);
    }
  });

  it("não confunde erro do app com falha de rede", () => {
    // Estes VOLTARAM do servidor: houve resposta, e a mensagem dela é o
    // diagnóstico. Traduzir por cima apagaria a informação.
    expect(ehFalhaDeRede(new Error("Falha ao calcular /trend"))).toBe(false);
    expect(ehFalhaDeRede(new Error("ticker inválido"))).toBe(false);
    expect(ehFalhaDeRede(erroDe("TypeError", "x.map is not a function"))).toBe(false);
    expect(ehFalhaDeRede("Failed to fetch")).toBe(false);
  });

  it("reconhece corpo que não era JSON (página do proxy)", () => {
    expect(ehRespostaNaoJson(erroDe("SyntaxError", "Unexpected token '<', \"<html>\"... is not valid JSON"))).toBe(true);
    expect(ehRespostaNaoJson(erroDe("SyntaxError", "Unexpected end of JSON input"))).toBe(true);
    expect(ehRespostaNaoJson(new Error("Unexpected token '<'"))).toBe(false);
  });

  it("a frase da falha de rede diz o que aconteceu e o que fazer", () => {
    const m = mensagemDeFalha(erroDe("TypeError", "Failed to fetch"));
    expect(m).toContain("conexão caiu");
    expect(m).toContain("Tente de novo");
    // Nada de jargão do navegador chegando à tela.
    expect(m).not.toContain("TypeError");
    expect(m).not.toContain("Failed to fetch");
  });

  it("deixa passar intacta a mensagem que o backend escreveu", () => {
    expect(mensagemDeFalha(new Error("ticker inválido"))).toBe("ticker inválido");
    expect(mensagemDeFalha("Sem resultado")).toBe("Sem resultado");
    expect(mensagemDeFalha(null)).toBe("Falha desconhecida");
  });
});

// A varredura olha o repositório inteiro, não só as telas tocadas: a frase
// estava em seis rotas diferentes, e consertar as três da Análise Rápida
// deixaria as outras prontas para repetir o incidente.
const RAIZ_SERVIDOR = join(__dirname, "..", "..", "..", "api-server", "src");
const RAIZ_TELA = join(__dirname, "..");

function arquivos(dir: string, exts: string[]): string[] {
  const saida: string[] = [];
  for (const nome of readdirSync(dir)) {
    if (nome === "node_modules" || nome === "__tests__") continue;
    const caminho = join(dir, nome);
    if (statSync(caminho).isDirectory()) saida.push(...arquivos(caminho, exts));
    else if (exts.some((e) => nome.endsWith(e))) saida.push(caminho);
  }
  return saida;
}

describe("nenhuma mensagem nossa imita o erro do navegador", () => {
  it("não há 'Failed to fetch' em texto que chega ao usuário", () => {
    const achados: string[] = [];
    const alvos = [
      ...arquivos(RAIZ_SERVIDOR, [".ts"]),
      ...arquivos(RAIZ_TELA, [".ts", ".tsx"]),
    ];
    for (const caminho of alvos) {
      if (caminho.endsWith("erro-de-rede.ts")) continue; // é quem documenta o caso
      for (const linha of readFileSync(caminho, "utf-8").split("\n")) {
        // `logger.error(...)` fica de fora: log é para nós, não para a tela.
        if (linha.includes("logger.")) continue;
        // Comentário também: a nota que EXPLICA por que a frase não pode
        // voltar precisa poder citá-la.
        if (/^\s*(\/\/|\*|\/\*)/.test(linha)) continue;
        if (/["'`]Failed to fetch/.test(linha)) achados.push(`${caminho}: ${linha.trim()}`);
      }
    }
    expect(achados).toEqual([]);
  });
});
