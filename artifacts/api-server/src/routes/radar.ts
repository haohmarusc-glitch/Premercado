import { Router, type IRouter } from "express";
import path from "path";
import { getPythonBin, agentDir } from "../lib/runner";
import { spawnPython } from "../lib/python-spawn";
import { comVagaPython } from "../lib/vaga-python";

const router: IRouter = Router();

// Dados 100% estáticos (snapshot embutido em radar_ia_2026.py, sem rede) --
// o subprocess só serializa constantes, então uma execução por vida do
// processo basta; o cache morre junto com o deploy que trocar o snapshot.
let cache: unknown | null = null;

function runRadarJson(timeoutMs = 15_000): Promise<unknown> {
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
    // Sem chamada de rede no script -- 15s cobre com folga até o pior boot
    // frio de interpretador.
    const t = setTimeout(() => { py.kill("SIGTERM"); reject(new Error("radar_ia_2026.py timed out")); }, timeoutMs);
    py.on("close", (code) => {
      clearTimeout(t);
      if (code !== 0) { reject(new Error(err || "radar_ia_2026.py failed")); return; }
      try { resolve(JSON.parse(out)); } catch { reject(new Error("Failed to parse radar_ia_2026.py output")); }
    });
  }));
}

// GET /radar -- blob completo do Radar IA 2026 (snapshot estático de
// 14/08/2026): calendário de earnings, correlações medidas, tema IA
// (YTD/vol/beta), riscos por ticker e screening de mínima de 52 semanas.
// Sem tabela no Postgres de propósito: o snapshot mora no módulo Python
// (fonte única de verdade) e o dado não muda entre deploys -- mesmo padrão
// de /confluence e /backtest, que também servem cálculo de subprocess sem
// persistir nada.
router.get("/radar", async (_req, res, next): Promise<void> => {
  try {
    if (cache == null) cache = await runRadarJson();
    res.json(cache);
  } catch (e) {
    next(e);
  }
});

export default router;
