/**
 * O critério de aceite: "Botão no chat pré-preenche AVGO / preço > 365 / RVOL >
 * 1,2 a partir da resposta de 25/09/2026."
 *
 * O texto da AVGO abaixo é o da resposta real. Os outros casos são os modos de
 * erro que importam num extrator de linguagem natural: inventar um número que
 * ninguém escreveu, pegar o limite errado da faixa, e confundir uma palavra
 * comum com ticker.
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "fs";
import { join } from "path";
import {
  extrairMonitoresDoChat, semBlocoDeMonitor, urlDeAlertaPreenchido,
  lerCondicoesDaUrl,
} from "@/lib/monitorar-do-chat";

const RESPOSTA_AVGO = `
**AVGO** — Ação: Monitorar confirmação acima de **$365-370** com volume **>1.2x**. Por enquanto, segure a posição mas sem adicionar.
`;

describe("o critério de aceite", () => {
  it("extrai AVGO / preço > 365 / RVOL > 1,2 da resposta de 25/09", () => {
    const [m] = extrairMonitoresDoChat(RESPOSTA_AVGO);
    expect(m.ticker).toBe("AVGO");
    expect(m.conditions).toEqual([
      { indicator: "price", op: "above", value: 365 },
      { indicator: "rvol", op: "above", value: 1.2 },
    ]);
    expect(m.origem).toBe("texto");
  });

  it("o pré-preenchimento sobrevive à ida e volta pela URL", () => {
    const [m] = extrairMonitoresDoChat(RESPOSTA_AVGO);
    const url = urlDeAlertaPreenchido(m);
    expect(url).toContain("symbol=AVGO");
    const lido = lerCondicoesDaUrl(url.slice(url.indexOf("?")));
    expect(lido.conditions).toEqual(m.conditions);
  });
});

describe("ênfase markdown no meio do número", () => {
  it("o negrito do agente não esconde o nível", () => {
    // O defeito que só o texto REAL revelou: casos escritos à mão sem asterisco
    // passavam, e `acima de **$365-370**` não encontrava preço nenhum, porque o
    // que vem depois de "de " é `**`. Um extrator testado só com entrada
    // sintética concorda consigo mesmo.
    const formas = [
      "**AVGO** — Ação: Monitorar acima de **$365** com volume **>1.2x**.",
      "**AVGO** — Ação: Monitorar acima de $365 com volume >1.2x.",
      "AVGO — Ação: Monitorar acima de *$365* com volume `>1.2x`.",
    ];
    for (const texto of formas) {
      const [m] = extrairMonitoresDoChat(texto);
      expect(m?.conditions, texto).toEqual([
        { indicator: "price", op: "above", value: 365 },
        { indicator: "rvol", op: "above", value: 1.2 },
      ]);
    }
  });
});

describe("a faixa: qual dos dois números", () => {
  it("'acima de $365-370' usa 365, o limite de ENTRADA", () => {
    // 370 faria o alerta perder exatamente o rompimento que ele existe para
    // pegar: o preço cruza 365 primeiro.
    const [m] = extrairMonitoresDoChat("**NVDA** — Ação: Monitorar acima de $365-370.");
    expect(m.conditions[0]).toEqual({ indicator: "price", op: "above", value: 365 });
  });

  it("'abaixo de $340-350' usa 350, pelo raciocínio invertido", () => {
    const [m] = extrairMonitoresDoChat("**MU** — Ação: Monitorar abaixo de $340-350.");
    expect(m.conditions[0]).toEqual({ indicator: "price", op: "below", value: 350 });
  });

  it("aceita vírgula decimal e 'a' como separador de faixa", () => {
    const [m] = extrairMonitoresDoChat("**ARM** — Ação: Monitorar acima de US$ 152,50 a 158,00 com RVOL > 1,5.");
    expect(m.conditions).toEqual([
      { indicator: "price", op: "above", value: 152.5 },
      { indicator: "rvol", op: "above", value: 1.5 },
    ]);
  });
});

describe("o que ele se recusa a adivinhar", () => {
  it("'Monitorar' sem nível nenhum não vira alerta", () => {
    // Um alerta com nível inventado manda e-mail sobre um preço que o agente
    // nunca recomendou.
    expect(extrairMonitoresDoChat("**AVGO** — Ação: Monitorar de perto.")).toEqual([]);
  });

  it("não inventa condição de volume a partir de um múltiplo qualquer", () => {
    // "2x o normal" fora de contexto de volume. Uma condição de volume a mais
    // deixa o alerta MAIS exigente do que o recomendado, e ele silencia sem
    // avisar -- pior que uma condição a menos.
    const [m] = extrairMonitoresDoChat(
      "**INTC** — Ação: Monitorar acima de $42. O papel já subiu 2x desde a mínima.",
    );
    expect(m.conditions).toEqual([{ indicator: "price", op: "above", value: 42 }]);
  });

  it("palavra comum em maiúscula não é ticker", () => {
    expect(extrairMonitoresDoChat("Ação: Monitorar SE o preço passar de $100.")).toEqual([]);
    expect(extrairMonitoresDoChat("Em ET, Ação: Monitorar acima de $100.")).toEqual([]);
  });

  it("texto sem 'Monitorar' não produz nada", () => {
    expect(extrairMonitoresDoChat("**AVGO** — Ação: Vender 50% acima de $365.")).toEqual([]);
  });
});

describe("vários tickers na mesma resposta", () => {
  it("devolve um por ticker", () => {
    // O relatório de sete tickers de 25/09 recomenda vários de uma vez.
    // Devolver só o primeiro esconderia os outros sem dizer nada.
    const ms = extrairMonitoresDoChat(`
      **AVGO** — Ação: Monitorar confirmação acima de **$365-370** com volume **>1.2x**.
      **NVDA** — Ação: Monitorar acima de $1090 com RVOL > 1.3.
      **MU** — Ação: Segurar.
    `);
    expect(ms.map((m) => m.ticker)).toEqual(["AVGO", "NVDA"]);
  });

  it("o mesmo ticker duas vezes entra uma", () => {
    const ms = extrairMonitoresDoChat(`
      **AVGO** — Ação: Monitorar acima de $365 com volume >1.2x.
      Resumo: **AVGO** — Ação: Monitorar acima de $365 com volume >1.2x.
    `);
    expect(ms).toHaveLength(1);
  });
});

describe("o bloco JSON do agente tem precedência", () => {
  const COM_JSON = `
**AVGO** — Ação: Monitorar confirmação acima de **$365-370** com volume **>1.2x**.

\`\`\`json
{"monitor": {"ticker":"AVGO","conditions":[{"indicator":"price","op":"above","value":366},{"indicator":"rvol","op":"above","value":1.25}],"note":"Chat 25/09 — confirmação de reversão"}}
\`\`\`
`;

  it("usa os números do bloco, não os do texto", () => {
    const ms = extrairMonitoresDoChat(COM_JSON);
    expect(ms).toHaveLength(1);
    expect(ms[0].origem).toBe("json");
    expect(ms[0].conditions[0].value).toBe(366);
    expect(ms[0].note).toBe("Chat 25/09 — confirmação de reversão");
  });

  it("o bloco sai da bolha da conversa", () => {
    const limpo = semBlocoDeMonitor(COM_JSON);
    expect(limpo).not.toContain("monitor");
    expect(limpo).toContain("Ação: Monitorar confirmação");
  });

  it("bloco solto (sem cerca) também é lido e removido", () => {
    const solto = `**MU** — Ação: Monitorar.\n{"monitor":{"ticker":"MU","conditions":[{"indicator":"price","op":"below","value":300}]}}`;
    expect(extrairMonitoresDoChat(solto)[0].conditions[0].value).toBe(300);
    expect(semBlocoDeMonitor(solto)).not.toContain("{");
  });

  it("JSON truncado pelo streaming cai no texto, não derruba nada", () => {
    const truncado = `**AVGO** — Ação: Monitorar acima de $365 com volume >1.2x.\n{"monitor":{"ticker":"AVGO","condi`;
    const ms = extrairMonitoresDoChat(truncado);
    expect(ms).toHaveLength(1);
    expect(ms[0].origem).toBe("texto");
  });

  it("bloco com condição inválida é descartado, e o texto assume", () => {
    // `indicator: "vwap"` não existe no avaliador. Aceitá-lo criaria um alerta
    // que a API recusa com 400 -- ou pior, um que grava e nunca dispara.
    const ruim = `**AVGO** — Ação: Monitorar acima de $365 com volume >1.2x.\n{"monitor":{"ticker":"AVGO","conditions":[{"indicator":"vwap","op":"above","value":1}]}}`;
    expect(extrairMonitoresDoChat(ruim)[0].origem).toBe("texto");
  });
});

describe("lerCondicoesDaUrl", () => {
  it("querystring sem conditions devolve lista vazia", () => {
    expect(lerCondicoesDaUrl("?symbol=AVGO").conditions).toEqual([]);
  });

  it("JSON quebrado na URL devolve formulário vazio, não meio alerta", () => {
    // Metade de um pré-preenchimento é pior que nenhum: o usuário confirma o que
    // a tela mostra.
    expect(lerCondicoesDaUrl("?conditions=%7Bquebrado").conditions).toEqual([]);
  });

  it("descarta condição malformada e mantém as válidas", () => {
    const q = "?conditions=" + encodeURIComponent(JSON.stringify([
      { indicator: "price", op: "above", value: 365 },
      { indicator: "price", op: "above", value: "365" },
      { indicator: "vwap", op: "above", value: 1 },
    ]));
    expect(lerCondicoesDaUrl(q).conditions).toEqual([
      { indicator: "price", op: "above", value: 365 },
    ]);
  });

  it("a nota volta junto", () => {
    expect(lerCondicoesDaUrl("?note=Chat%2025%2F09").note).toBe("Chat 25/09");
  });
});

describe("o exemplo do PROMPT é o que este parser aceita", () => {
  // O guarda que faltaria: o prompt (Python) pede um formato e o parser
  // (TypeScript) espera outro. Ninguém falharia -- o agente emitiria o bloco, a
  // tela o ignoraria, e o botão cairia no caminho por texto sem avisar.
  //
  // Então o exemplo é LIDO do prompt, não copiado para cá.
  it("o bloco de exemplo do llm_runtime.py é extraído corretamente", () => {
    const prompt = readFileSync(
      join(__dirname, "../../../api-server/src/agent/llm_runtime.py"), "utf-8",
    );
    const m = /\{\{"monitor":[\s\S]*?\}\}\}\}/.exec(prompt);
    expect(m, "o exemplo de bloco monitor saiu do prompt").not.toBeNull();

    // O prompt é f-string: `{{` e `}}` no fonte são `{` e `}` no texto que o
    // modelo lê.
    const exemplo = m![0].replace(/\{\{/g, "{").replace(/\}\}/g, "}");
    const [extraido] = extrairMonitoresDoChat(`**AVGO** — Ação: Monitorar.\n${exemplo}`);

    expect(extraido.origem).toBe("json");
    expect(extraido.ticker).toBe("AVGO");
    expect(extraido.conditions).toEqual([
      { indicator: "price", op: "above", value: 365 },
      { indicator: "rvol", op: "above", value: 1.2 },
    ]);
  });

  it("os indicadores que o prompt promete são os que o parser aceita", () => {
    const prompt = readFileSync(
      join(__dirname, "../../../api-server/src/agent/llm_runtime.py"), "utf-8",
    );
    const linha = /Indicadores aceitos: ([^.]+)\./.exec(prompt);
    expect(linha).not.toBeNull();
    const prometidos = linha![1].split(",").map((s) => s.trim());
    for (const ind of prometidos) {
      const texto = `**X** — Ação: Monitorar.\n{"monitor":{"ticker":"XYZ","conditions":[{"indicator":"${ind}","op":"above","value":1}]}}`;
      expect(extrairMonitoresDoChat(texto), `prompt promete ${ind}`).toHaveLength(1);
    }
  });
});
