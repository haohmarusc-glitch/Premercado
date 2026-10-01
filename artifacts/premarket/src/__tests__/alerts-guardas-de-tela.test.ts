/**
 * Guardas da tela de alertas que não dá para exercitar sem DOM.
 *
 * As duas nasceram da auditoria de 30/09/2026, e as duas são da mesma família:
 * o código estava presente e certo, e a TELA não o usava. Nenhum teste falhava,
 * porque nada estava quebrado -- estava desligado.
 *
 * Lê o fonte, como test_rvol_abertura.py e contrato-de-alerta.test.ts: o que se
 * garante é um CALL SITE, e uma verificação no texto é honesta para isso.
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "fs";
import { join } from "path";

const FONTE = readFileSync(join(__dirname, "../pages/alerts.tsx"), "utf-8");

describe("a tela avalia com o MESMO guarda que o checker", () => {
  it("passa a data da bolsa para avaliarCondicoes", () => {
    // `avaliarCondicoes(condicoes, retrato)` — sem o terceiro argumento — usa a
    // mesma função do checker e DESLIGA o guarda de pregão. A tela mostrava ✅
    // numa condição de RVOL cujo dado era de ontem, enquanto o checker a
    // recusava: a divergência que o pacote compartilhado existe para impedir,
    // reintroduzida por um argumento a menos.
    // O terceiro argumento de `avaliarCondicoes` vem depois do retrato. O
    // intervalo é generoso porque há um comentário longo entre os dois -- o
    // que se verifica é a ORDEM, não a formatação.
    const chamada = /retratoDe\(alert\.symbol\),[\s\S]{0,1500}?dataDaBolsa\(\)/.exec(FONTE);
    expect(chamada, "avaliarCondicoes na lista não recebe a data da bolsa")
      .not.toBeNull();
  });

  it("importa dataDaBolsa do pacote compartilhado, não de uma cópia local", () => {
    expect(FONTE).toMatch(/import \{[^}]*dataDaBolsa[^}]*\} from "@workspace\/alertas"/s);
  });
});

describe("o histórico mostra com que números o alerta disparou", () => {
  it("renderiza as condições gravadas em alert_firings", () => {
    // A coluna `conditions` de alert_firings foi criada com a justificativa de
    // que essa é "a única pergunta que se faz a um histórico de alerta" -- e
    // ficou gravada sem ninguém mostrar por três dias.
    expect(FONTE).toContain("f.conditions?.length ? descreverCondicoes(f.conditions)");
  });

  it("a tabela tem a coluna", () => {
    expect(FONTE).toContain(">Condições</th>");
  });
});

describe("a confirmação atrasada é identificável na tela", () => {
  it("o histórico mostra a sessão quando ela difere do dia do disparo", () => {
    // Um disparo de segunda sobre o fechamento de sexta parece uma leitura de
    // segunda sem esta marca, e a decisão seria sobre o pregão errado. É o
    // mesmo motivo de a nota ir no assunto do e-mail, e a mesma armadilha de
    // guardar `conditions` sem mostrar.
    expect(FONTE).toContain("f.sessionDate && !f.firedAt.startsWith(f.sessionDate)");
    expect(FONTE).toContain("sessão de ");
  });
});
