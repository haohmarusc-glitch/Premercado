import { describe, it, expect } from "vitest";
import { readFileSync } from "fs";
import { join } from "path";

// ─── O plano de saída não nasce com o de outra pessoa ───────────────────────
//
// A tela do Plano de Saída oferecia, para qualquer conta com o plano vazio,
// um botão "Carregar plano de 16/jul/2026". Ele escrevia NOVE itens fixos no
// código -- SKHY, GOOGL, TSLA, SMCI, ARM, AVGO, MRVL, NVDA e um ETF -- com as
// datas de julho/agosto e a justificativa de cada um escrita em primeira
// pessoa: "comprado 1 dia antes do selloff", "não vale segurar earnings de
// algo que já vai sair no trimestre".
//
// Era o plano de UMA pessoa. Em 26/08/2026 uma conta nova abriu a tela e
// recebeu o convite.
//
// Três problemas, em ordem crescente:
//
//   1. o raciocínio de investimento de alguém viajava no bundle, legível por
//      qualquer um que carregasse o app;
//   2. clicar ESCREVIA nove itens na conta de quem clicou -- decisões sobre
//      papéis que a pessoa pode nem ter;
//   3. sete das nove datas já haviam passado. O plano nascia vencido: uma
//      tela cheia de ordens de venda urgentes para ações alheias.
//
// O (3) ficou pior depois que o Veredito passou a LER o plano de saída
// (BLOCO_CONTRA_PLANO): um plano semeado alimentaria a checagem com ruído
// sobre posições que não existem.

const BRUTO = readFileSync(
  join(__dirname, "..", "pages", "exit-plan.tsx"), "utf-8");

/**
 * O arquivo SEM comentários.
 *
 * A primeira versão destes testes acusou o próprio comentário que explica a
 * remoção -- ele cita "Carregar plano de 16/jul/2026" para dizer o que saiu.
 * Alarme falso pelo mesmo mecanismo que este repo passou o dia caçando nos
 * validadores: casar o token em vez da afirmação. Comentário que descreve um
 * defeito removido não é o defeito.
 */
const TELA = BRUTO
  .replace(/\/\*[\s\S]*?\*\//g, "")
  .split("\n").filter((l) => !l.trim().startsWith("//")).join("\n");

describe("a tela não carrega o plano de ninguém", () => {
  it("não há lista de itens fixa no código", () => {
    expect(TELA).not.toContain("SEED_ITEMS");
    expect(TELA).not.toContain("seedPlan");
  });

  it("o botão de carregar sumiu", () => {
    expect(TELA).not.toContain("Carregar plano de");
  });

  it("nenhum ticker do plano antigo sobrou como dado", () => {
    // Casa a FORMA do dado (`ticker: "XXXX"`), não a menção do papel: o
    // comentário que explica a remoção cita os nove nomes, e casar o nome
    // solto acusaria o comentário -- o mesmo alarme falso que este repo
    // passou o dia caçando nos validadores.
    expect(TELA).not.toMatch(/ticker:\s*"[A-Z]+"/);
  });

  it("o campo de justificativa continua existindo -- é do usuário", () => {
    // `rationale` NÃO podia sair: é o campo onde a pessoa escreve o motivo
    // dela. A primeira versão deste teste proibia a string inteira e teria
    // exigido apagar a funcionalidade para "passar" -- o teste mandando no
    // produto em vez de descrevê-lo.
    expect(TELA).toContain("form.rationale");
  });

  it("nenhuma justificativa vem escrita de fábrica", () => {
    // O que não pode voltar é a justificativa LITERAL no código. Uma frase
    // longa em português dentro de uma atribuição é a assinatura disso.
    expect(TELA).not.toMatch(/rationale:\s*"[^"]{40,}"/);
  });

  it("o estado vazio aponta os caminhos que existem", () => {
    // Vazio sem saída é pior que vazio: a pessoa não sabe o que fazer.
    expect(TELA).toContain("Nenhum item no plano de saída ainda.");
    expect(TELA).toContain("Novo item");
    expect(TELA).toContain("Reavaliar plano");
  });
});
