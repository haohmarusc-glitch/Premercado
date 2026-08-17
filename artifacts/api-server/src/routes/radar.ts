import { Router, type IRouter } from "express";
import path from "path";
import { getPythonBin, agentDir } from "../lib/runner";
import { spawnPython } from "../lib/python-spawn";
import { comVagaPython } from "../lib/vaga-python";

const router: IRouter = Router();

// O radar deixou de ser 100% estático: preço e faixa de 52 semanas agora vêm
// vivos do market_data_provider (auditoria 17/08/2026 -- o snapshot manual
// servia PDD com preço ABAIXO da própria mínima de 52 semanas).
//
// Por isso o cache ganhou TTL. Antes era "uma execução por vida do processo",
// o que fazia sentido enquanto o blob era constante e morria no deploy; com
// preço vivo, isso congelaria a cotação da primeira requisição pós-boot para
// sempre -- tornando a busca viva inútil, do jeito mais silencioso possível.
//
// 30min: mesma convenção do cache de tendência (get_trend.py). O radar é
// leitura de candle diário, não de tick.
const CACHE_TTL_MS = 30 * 60_000;
let cache: { blob: unknown; em: number } | null = null;

function runRadarJson(timeoutMs = 45_000): Promise<unknown> {
  // comVagaPython -- teto de Python simultâneo vindo de rota HTTP, mesmo
  // padrão de earnings-reaction.ts (aqui só protege o burst do primeiro
  // acesso pós-boot; depois disso o cache responde sem subprocess).
  return comVagaPython("radar_ia", () => new Promise((resolve, reject) => {
    const scriptPath = path.join(agentDir, "agent", "radar_ia_2026.py");
    const py = spawnPython(getPythonBin(), [scriptPath, "--json"]);
    // Sem payload: modo --json ignora stdin. Fechar logo evita o script
    // ficar esperando EOF caso um dia leia stdin (visto em produção com
    // earnings_reaction_analysis + docker exec -T).
    py.stdin.end();
    let out = "";
    let err = "";
    py.stdout.on("data", (d: Buffer) => { out += d.toString(); });
    py.stderr.on("data", (d: Buffer) => { err += d.toString(); });
    // 45s: o script agora BUSCA preço e 52 semanas dos tickers do screening.
    // O caminho normal é uma chamada em lote (aquece o hist_cache) e leituras
    // de cache, mas o primeiro acesso pós-deploy paga a rede inteira. 15s
    // cobria só o boot do interpretador e passou a ser curto demais.
    const t = setTimeout(() => { py.kill("SIGTERM"); reject(new Error("radar_ia_2026.py timed out")); }, timeoutMs);
    py.on("close", (code) => {
      clearTimeout(t);
      if (code !== 0) { reject(new Error(err || "radar_ia_2026.py failed")); return; }
      try { resolve(JSON.parse(out)); } catch { reject(new Error("Failed to parse radar_ia_2026.py output")); }
    });
  }));
}

// GET /radar -- blob completo do Radar IA 2026: calendário de earnings,
// correlações medidas, tema IA (YTD/vol/beta), riscos por ticker e screening
// de mínima de 52 semanas.
//
// Procedência MISTA, e o blob diz qual é qual: preço/52 semanas são vivos;
// EVR e move implícito são coleta manual do OptionSlam (overridesColetadoEm /
// overridesIdadeDias); correlações e YTD seguem embutidos no módulo.
// `fontesDegradadas` aparece quando algum ticker não atualizou.
//
// Sem tabela no Postgres de propósito: o módulo Python é a fonte única --
// mesmo padrão de /confluence e /backtest.
router.get("/radar", async (_req, res, next): Promise<void> => {
  try {
    if (cache == null || Date.now() - cache.em > CACHE_TTL_MS) {
      cache = { blob: await runRadarJson(), em: Date.now() };
    }
    res.json(cache.blob);
  } catch (e) {
    next(e);
  }
});

export default router;
