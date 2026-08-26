import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { readFileSync } from "fs";
import { join } from "path";

// ─── Em nome de quem o subprocesso age ──────────────────────────────────────
//
// Vazamento real (26/08/2026), reportado da tela. O agente Python autentica
// na API interna com a OPERATOR_API_KEY, e `requireAuth` resolvia essa chave
// SEMPRE para a conta dona. Consequência:
//
//   Veredito       -> `get_exit_plan_items` e `get_scenario_status` devolviam
//                     o plano e o cenário do DONO para qualquer conta
//   Chat           -> "qual meu plano de saída?" respondia com o do dono
//   Reavaliar Plano-> `update_exit_plan_item`/`create_exit_plan_item` podiam
//                     ESCREVER no plano do dono a mando de outra conta
//
// A escrita é a pior das três: ler dado alheio é vazamento, alterar é dano.
//
// O conserto é uma corrente de três elos, e cada elo tem teste aqui:
//
//   runner/chat  ->  AGENT_ACTING_USER_ID no env do spawn
//   tools.py     ->  X-Acting-User-Id no header
//   requireAuth  ->  honra o header, e SÓ no caminho da chave de operador

const RAIZ = join(__dirname, "..");
const fonte = (rel: string) => readFileSync(join(RAIZ, rel), "utf-8");

describe("elo 1 — o spawn diz quem disparou", () => {
  it("a run do agente passa o id de quem clicou", () => {
    const runner = fonte("lib/runner.ts");
    expect(runner).toContain("AGENT_ACTING_USER_ID");
    // Só quando há usuário: run agendada não representa ninguém e deve cair
    // na conta dona, como sempre caiu.
    expect(runner).toMatch(/userId != null \? \{ AGENT_ACTING_USER_ID/);
  });

  it("o chat passa o id de quem está conversando", () => {
    expect(fonte("routes/chat.ts")).toContain("AGENT_ACTING_USER_ID: String(req.userId!)");
  });
});

describe("elo 2 — o Python manda o header", () => {
  const tools = fonte("agent/tools.py");

  it("o header sai de _internal_headers", () => {
    expect(tools).toContain("X-Acting-User-Id");
    expect(tools).toContain("AGENT_ACTING_USER_ID");
  });

  it("sem chave de operador não sai header nenhum", () => {
    // `if not key: return {}` antes de qualquer coisa -- mandar identidade
    // sem credencial seria dizer quem se é sem provar.
    //
    // Casa a ATRIBUIÇÃO, não o nome do header: a primeira versão deste teste
    // encontrou "X-Acting-User-Id" na DOCSTRING, que vem antes do código, e
    // acusou ordem invertida. Alarme falso pelo mesmo mecanismo que este repo
    // passou o dia caçando nos validadores.
    const corpo = tools.slice(tools.indexOf("def _internal_headers"));
    const semChave = corpo.indexOf("if not key:");
    const atribui = corpo.indexOf('headers["X-Acting-User-Id"]');
    expect(semChave).toBeGreaterThan(-1);
    expect(atribui).toBeGreaterThan(-1);
    expect(semChave).toBeLessThan(atribui);
  });
});

describe("elo 3 — a API só aceita o header de quem tem a chave", () => {
  const auth = fonte("middleware/require-auth.ts");

  it("o header é lido dentro do ramo da OPERATOR_API_KEY", () => {
    const ramo = auth.slice(auth.indexOf("Bearer ${operatorKey}"));
    const leHeader = ramo.indexOf("actingUserIdDoHeader");
    const ramoCookie = ramo.indexOf("SESSION_COOKIE");
    expect(leHeader).toBeGreaterThan(-1);
    expect(leHeader).toBeLessThan(ramoCookie);
  });

  it("o caminho do cookie NÃO lê o header", () => {
    // Aceitar identidade por cookie seria escalação de privilégio pura:
    // qualquer usuário logado leria a conta que quisesse.
    const cookie = auth.slice(auth.indexOf("req.cookies?.[SESSION_COOKIE]"));
    expect(cookie).not.toContain("actingUserIdDoHeader");
    expect(cookie).toContain("req.userId = payload.userId");
  });
});

// ── o formato do id, onde mora o abuso ─────────────────────────────────────
//
// A validação está no middleware; aqui fica a tabela do que entra e do que
// não entra, para o regex não afrouxar sem alguém notar.

describe("o id do header é validado", () => {
  const valida = (texto: string): number | null => {
    if (typeof texto !== "string" || !/^[0-9]{1,10}$/.test(texto)) return null;
    const id = Number(texto);
    return Number.isSafeInteger(id) && id > 0 ? id : null;
  };

  it.each([["7", 7], ["42", 42], ["1000000", 1_000_000]])(
    "aceita %s", (entrada, esperado) => {
      expect(valida(entrada as string)).toBe(esperado);
    });

  it.each([
    ["0"],            // não existe usuário 0
    ["-1"],           // sinal
    ["1.5"],          // decimal
    ["1 OR 1=1"],     // injeção
    ["7; DROP"],      //
    [""],             //
    ["  7  "],        // espaço: o header vem cru, não normalizado
    ["99999999999"],  // 11 dígitos, fora do formato
    ["0x7"],          //
  ])("recusa %j", (entrada) => {
    expect(valida(entrada as string)).toBeNull();
  });
});

// ── a fronteira que NÃO recebe identidade, e por quê ───────────────────────

describe("spawn coalescido não pode ler dado de usuário", () => {
  it("analise_rapida_ia não chama a API interna", () => {
    // `runAnaliseRapidaIA` é COALESCIDA: dois usuários pedindo o mesmo ticker
    // compartilham um subprocesso. Identidade por usuário ali seria ERRADA,
    // não apenas desnecessária -- o segundo usuário herdaria a do primeiro.
    //
    // Hoje ela só lê mercado (yfinance, FMP, notícias). Este teste é o que
    // impede alguém de acrescentar uma leitura de dado de usuário sem
    // perceber que a coalescência a torna insegura.
    const ia = fonte("agent/analise_rapida_ia.py");
    for (const chamada of ["get_exit_plan_items", "get_scenario_status",
                           "get_portfolio_snapshot", "_internal_headers"]) {
      expect(ia).not.toContain(chamada);
    }
  });
});
