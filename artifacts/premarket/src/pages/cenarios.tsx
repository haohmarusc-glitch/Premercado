import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Skeleton } from "@/components/ui/skeleton";
import { AGENDA } from "@/lib/eventos";

/* ============================================================
Painel de Cenários — Premercado

Modela o valor da carteira numa data-alvo (padrão: saque em 07/10/2026)
e simula venda parcial das posições. Responde a: dado que o dinheiro
será sacado numa data fixa, qual a distribuição de resultados
possíveis e quanto já está garantido em caixa?

Sem dependências externas -- SVG puro, CSS inline (nenhuma classe
Tailwind), mesma abordagem self-contained do componente original.
============================================================ */

/* ---------- tokens ---------- */
const C = {
  ink: "#0E191D",
  panel: "#14242A",
  raised: "#1A2F36",
  rule: "#254048",
  ruleSoft: "#1E353C",
  text: "#DDE9E7",
  dim: "#84A0A0",
  faint: "#5A7679",
  starboard: "#39BE9C", // garantido em caixa
  port: "#E0574A", // perda / abaixo do break-even
  lamp: "#E3A63C", // em risco
  channel: "#5AA9D6", // referências / break-even
};
const MONO = 'ui-monospace, "SF Mono", "Roboto Mono", "DejaVu Sans Mono", monospace';
const SANS = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';

const RHO = 0.75; // correlação média entre as posições (todas do mesmo setor)
const DEFAULT_DATA_ALVO = "2026-10-07";

/* ---------- tipos ---------- */
interface Posicao {
  t: string; // ticker
  nome: string;
  value: number; // valor de mercado atual em US$
  cost: number; // total investido em US$
  vol: number; // volatilidade anualizada estimada (0–1)
  beta: number; // sensibilidade ao movimento do setor (SOX)
  evento: string; // data do próximo balanço, dd/mm ou "—"
}

interface Metricas {
  T: number;
  custoTotal: number;
  caixa: number;
  risco: number;
  valorTotalHoje: number;
  drift: number;
  sigma: number;
  sd: number;
  pEmpate: number;
  pQueda: number;
  gatilhoQueda: number;
  p05: number;
  p50: number;
  p95: number;
  central: number;
}

/* ---------- matemática ----------
NÃO alterar sem revisar (ver TASK-painel-cenarios.md):
1. Caixa vs risco: posições marcadas como vendidas somam em caixa e
   saem da matriz de covariância.
2. Cenário central: cada posição ativa move beta × movimento_do_setor,
   com piso em zero.
3. Sigma da carteira: √(ΣΣ wᵢwⱼσᵢσⱼρᵢⱼ), ρ=1 na diagonal e 0.75 fora,
   escalado por √T e pelo multiplicador do slider.
4. Probabilidade de empatar: lognormal, com os casos degenerados
   (precisa≤0, risco≤0, sd≤0) tratados à parte.
5. Quantis: q(z) = caixa + risco·exp(drift + z·sd).

Limitações conhecidas (não silenciar):
- vol é estimativa histórica, não volatilidade implícita de opções.
- beta constante trata balanço como movimento difusivo; balanço é
  salto -- as caudas estão subestimadas nas datas de resultado.
- SKHY tem um fator que beta não captura (compressão do prêmio do ADR
  sobre a ação coreana) -- componente mecânico, não de mercado.
------------------------------------------ */
function erf(x: number): number {
  const s = x < 0 ? -1 : 1;
  x = Math.abs(x);
  const t = 1 / (1 + 0.3275911 * x);
  const y =
    1 -
    ((((1.061405429 * t - 1.453152027) * t + 1.421413741) * t - 0.284496736) * t +
      0.254829592) *
      t *
      Math.exp(-x * x);
  return s * y;
}
function Phi(z: number): number {
  return 0.5 * (1 + erf(z / Math.SQRT2));
}
function usd(v: number): string {
  return "US$ " + v.toLocaleString("pt-BR", { minimumFractionDigits: 0, maximumFractionDigits: 0 });
}
function pct(v: number, d = 1): string {
  return (v >= 0 ? "+" : "") + v.toFixed(d) + "%";
}

async function fetchScenarioPositions(): Promise<Posicao[]> {
  const r = await fetch("/api/scenarios/positions", { credentials: "include" });
  if (!r.ok) throw new Error("Failed to load scenario positions");
  return r.json();
}

export default function PainelCenarios() {
  const { data: posicoes, isLoading, isError } = useQuery({
    queryKey: ["scenario-positions"],
    queryFn: fetchScenarioPositions,
    staleTime: 5 * 60_000, // ferramenta de cenário, não precisa ser tão ao vivo quanto cotação
  });

  const [dataAlvoStr, setDataAlvoStr] = useState(DEFAULT_DATA_ALVO);
  const [vendidas, setVendidas] = useState<Record<string, boolean>>({});
  const [setor, setSetor] = useState(0); // movimento do setor até a data-alvo, %
  const [volMult, setVolMult] = useState(1); // multiplicador da volatilidade
  const [valores, setValores] = useState<Record<string, number>>({});

  // Inicializa o override manual de cada posição com o valor de mercado
  // vindo do servidor -- só preenche tickers AINDA NÃO presentes em
  // `valores`, pra nunca sobrescrever uma edição manual do usuário quando o
  // fetch atualizar (cache velho do endpoint, refetch, etc.).
  useEffect(() => {
    if (!posicoes) return;
    setValores((prev) => {
      let changed = false;
      const next = { ...prev };
      for (const p of posicoes) {
        if (!(p.t in next)) { next[p.t] = p.value; changed = true; }
      }
      return changed ? next : prev;
    });
  }, [posicoes]);

  const dataAlvo = useMemo(() => new Date(dataAlvoStr + "T00:00:00"), [dataAlvoStr]);
  const dias = Math.max(1, Math.round((dataAlvo.getTime() - Date.now()) / 86400000));

  const lista = posicoes ?? [];

  const m: Metricas = useMemo(() => {
    const T = dias / 365;
    const custoTotal = lista.reduce((a, p) => a + p.cost, 0);

    const ativas = lista.filter((p) => !vendidas[p.t]);
    const caixa = lista.filter((p) => vendidas[p.t]).reduce((a, p) => a + (valores[p.t] ?? p.value), 0);
    const risco = ativas.reduce((a, p) => a + (valores[p.t] ?? p.value), 0);
    const valorTotalHoje = lista.reduce((a, p) => a + (valores[p.t] ?? p.value), 0);

    // cenário central: cada posição move beta × movimento do setor, piso em zero
    const riscoCentral = ativas.reduce(
      (a, p) => a + (valores[p.t] ?? p.value) * Math.max(0, 1 + (p.beta * setor) / 100),
      0,
    );
    const drift = risco > 0 ? Math.log(Math.max(riscoCentral, 1e-6) / risco) : 0;

    // sigma da carteira de risco (matriz de covariância com rho constante)
    let sigma = 0;
    if (risco > 0) {
      let varSum = 0;
      ativas.forEach((a) => {
        ativas.forEach((b) => {
          const wa = (valores[a.t] ?? a.value) / risco;
          const wb = (valores[b.t] ?? b.value) / risco;
          const r = a.t === b.t ? 1 : RHO;
          varSum += wa * wb * a.vol * b.vol * r;
        });
      });
      sigma = Math.sqrt(varSum);
    }
    const sd = sigma * Math.sqrt(T) * volMult;

    // probabilidade de o total atingir o custo total (empatar)
    const precisa = custoTotal - caixa;
    let pEmpate: number;
    if (precisa <= 0) pEmpate = 1;
    else if (risco <= 0 || sd <= 0) pEmpate = 0;
    else pEmpate = 1 - Phi((Math.log(precisa / risco) - drift) / sd);

    // probabilidade de perder mais 20% do valor de risco de hoje
    const gatilhoQueda = caixa + risco * 0.8;
    let pQueda: number;
    if (risco <= 0 || sd <= 0) pQueda = 0;
    else pQueda = Phi((Math.log(0.8) - drift) / sd);

    const q = (z: number) => caixa + risco * Math.exp(drift + z * sd);

    return {
      T, custoTotal, caixa, risco, valorTotalHoje, drift, sigma, sd,
      pEmpate, pQueda, gatilhoQueda,
      p05: q(-1.645), p50: q(0), p95: q(1.645),
      central: caixa + riscoCentral,
    };
  }, [lista, vendidas, setor, volMult, valores, dias]);

  const toggle = (t: string) => setVendidas((v) => ({ ...v, [t]: !v[t] }));
  const perdaHoje = m.valorTotalHoje - m.custoTotal;

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (isError) {
    return (
      <div style={{ background: C.ink, color: C.text, fontFamily: SANS, padding: 14, borderRadius: 3 }}>
        Falha ao carregar as posições da carteira para o painel de cenários.
      </div>
    );
  }

  if (lista.length === 0) {
    return (
      <div style={{ background: C.ink, color: C.text, fontFamily: SANS, padding: 14, minHeight: "100%" }}>
        <p style={{ margin: 0, fontSize: 13, color: C.dim }}>
          Nenhuma posição ativa na carteira. Adicione posições em Carteira para usar o Painel de Cenários.
        </p>
      </div>
    );
  }

  return (
    <div style={{ background: C.ink, color: C.text, fontFamily: SANS, padding: 14, minHeight: "100%" }}>
      <style>{`
        .pc-card{background:${C.panel};border:1px solid ${C.ruleSoft};border-radius:3px;padding:14px;margin-bottom:10px}
        .pc-eyebrow{font-size:10px;letter-spacing:.16em;text-transform:uppercase;color:${C.faint};font-family:${SANS};margin:0 0 10px}
        .pc-num{font-family:${MONO};font-variant-numeric:tabular-nums;letter-spacing:-.02em}
        .pc-row{display:flex;align-items:center;gap:10px;min-height:46px;border-bottom:1px solid ${C.ruleSoft};padding:6px 0}
        .pc-row:last-child{border-bottom:none}
        .pc-chk{width:26px;height:26px;flex:0 0 26px;border-radius:2px;border:1px solid ${C.rule};background:${C.raised};color:${C.starboard};font-family:${MONO};font-size:14px;cursor:pointer;display:flex;align-items:center;justify-content:center;padding:0}
        .pc-chk[data-on="1"]{border-color:${C.starboard};color:${C.starboard}}
        .pc-inp{width:74px;background:${C.raised};border:1px solid ${C.rule};color:${C.text};font-family:${MONO};font-variant-numeric:tabular-nums;font-size:13px;padding:6px 7px;border-radius:2px;text-align:right}
        .pc-date{background:${C.raised};border:1px solid ${C.rule};color:${C.text};font-family:${MONO};font-size:13px;padding:6px 8px;border-radius:2px}
        .pc-rng{width:100%;accent-color:${C.channel};height:30px}
        button:focus-visible,input:focus-visible{outline:2px solid ${C.channel};outline-offset:2px}
        @media (prefers-reduced-motion:no-preference){.pc-anim{transition:width .25s ease,fill .25s ease}}
      `}</style>

      {/* ---- cabeçalho ---- */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", marginBottom: 12, flexWrap: "wrap", gap: 10 }}>
        <div>
          <p className="pc-eyebrow" style={{ margin: 0 }}>Premercado · cenários</p>
          <h1 style={{ margin: "2px 0 0", fontSize: 19, fontWeight: 650, letterSpacing: "-.01em" }}>
            Carteira na data-alvo
          </h1>
        </div>
        <div style={{ textAlign: "right" }}>
          <div className="pc-num" style={{ fontSize: 26, lineHeight: 1, color: C.lamp }}>{dias}</div>
          <div className="pc-eyebrow" style={{ margin: 0 }}>dias</div>
        </div>
      </div>

      <div className="pc-card">
        <p className="pc-eyebrow">Data-alvo (saque)</p>
        <input
          className="pc-date"
          type="date"
          value={dataAlvoStr}
          onChange={(e) => setDataAlvoStr(e.target.value)}
          aria-label="Data-alvo do saque"
        />
      </div>

      {/* ---- leituras principais ---- */}
      <div className="pc-card" style={{ display: "flex", padding: 0 }}>
        {[
          { l: "Garantido", v: usd(m.caixa), c: C.starboard },
          { l: "Em risco", v: usd(m.risco), c: C.lamp },
          { l: "Break-even", v: usd(m.custoTotal), c: C.channel },
        ].map((k, i) => (
          <div key={k.l} style={{ flex: 1, padding: "13px 10px", borderLeft: i ? `1px solid ${C.ruleSoft}` : "none" }}>
            <div className="pc-eyebrow" style={{ margin: "0 0 5px" }}>{k.l}</div>
            <div className="pc-num" style={{ fontSize: 16, color: k.c }}>{k.v}</div>
          </div>
        ))}
      </div>

      {/* ---- barra de carga ---- */}
      <div className="pc-card">
        <p className="pc-eyebrow">Composição · escala até o break-even</p>
        <BarraCarga caixa={m.caixa} risco={m.risco} breakEven={m.custoTotal} />
        <div style={{ display: "flex", justifyContent: "space-between", marginTop: 9, fontSize: 11, color: C.dim }}>
          <span>Hoje <span className="pc-num" style={{ color: C.text }}>{usd(m.valorTotalHoje)}</span></span>
          <span className="pc-num" style={{ color: perdaHoje < 0 ? C.port : C.starboard }}>
            {perdaHoje < 0 ? "−" : "+"}{usd(Math.abs(perdaHoje)).replace("US$ ", "")} ({pct(m.custoTotal > 0 ? (perdaHoje / m.custoTotal) * 100 : 0)})
          </span>
        </div>
      </div>

      {/* ---- distribuição ---- */}
      <div className="pc-card">
        <p className="pc-eyebrow">Faixa de resultados na data-alvo</p>
        <Distribuicao m={m} />
        <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
          <Selo rot="Empatar ou mais" val={(m.pEmpate * 100).toFixed(0) + "%"} cor={C.starboard} />
          <Selo rot="Cair mais 20%" val={(m.pQueda * 100).toFixed(0) + "%"} cor={C.port} />
        </div>
        <div style={{ marginTop: 12, display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8 }}>
          {([
            ["Pessimista · 5%", m.p05, C.port],
            ["Central", m.central, C.text],
            ["Otimista · 95%", m.p95, C.starboard],
          ] as const).map(([l, v, c]) => (
            <div key={l} style={{ background: C.raised, borderRadius: 2, padding: "9px 10px" }}>
              <div className="pc-eyebrow" style={{ margin: "0 0 4px", fontSize: 9 }}>{l}</div>
              <div className="pc-num" style={{ fontSize: 13, color: c }}>{usd(v)}</div>
            </div>
          ))}
        </div>
      </div>

      {/* ---- premissas ---- */}
      <div className="pc-card">
        <p className="pc-eyebrow">Premissas</p>
        <Slider
          rot="Movimento do setor até a data-alvo"
          val={pct(setor, 0)}
          min={-40} max={40} step={1} value={setor}
          onChange={setSetor}
          nota="Cada posição move β × este valor"
        />
        <Slider
          rot="Volatilidade"
          val={"σ " + (m.sigma * volMult * 100).toFixed(0) + "% a.a."}
          min={0.5} max={1.8} step={0.05} value={volMult}
          onChange={setVolMult}
          nota={`Desvio no horizonte: ±${(m.sd * 100).toFixed(0)}% · correlação ${RHO}`}
        />
      </div>

      {/* ---- posições ---- */}
      <div className="pc-card">
        <p className="pc-eyebrow">Posições · marque para vender agora</p>
        {lista.map((p) => {
          const v = valores[p.t] ?? p.value;
          const res = p.cost > 0 ? ((v - p.cost) / p.cost) * 100 : 0;
          const paraEmpatar = v > 0 ? (p.cost / v - 1) * 100 : 0;
          const on = !!vendidas[p.t];
          return (
            <div className="pc-row" key={p.t}>
              <button
                className="pc-chk" data-on={on ? 1 : 0}
                onClick={() => toggle(p.t)}
                aria-pressed={on}
                aria-label={`Vender ${p.t} agora`}
              >{on ? "✓" : ""}</button>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="pc-num" style={{ fontSize: 13, fontWeight: 600, color: on ? C.faint : C.text }}>
                  {p.t}
                  <span style={{ fontFamily: SANS, fontSize: 10, color: C.faint, marginLeft: 7, letterSpacing: ".04em" }}>
                    β{p.beta.toFixed(2)} · {p.evento}
                  </span>
                </div>
                <div className="pc-num" style={{ fontSize: 11, color: res < 0 ? C.port : C.starboard, marginTop: 2 }}>
                  {pct(res)}
                  <span style={{ color: C.faint }}> · precisa {pct(paraEmpatar, 0)}</span>
                </div>
              </div>
              <input
                className="pc-inp" type="number" inputMode="decimal" value={v}
                aria-label={`Valor atual de ${p.t}`}
                onChange={(e) =>
                  setValores((s) => ({ ...s, [p.t]: Math.max(0, Number(e.target.value) || 0) }))
                }
              />
            </div>
          );
        })}
      </div>

      {/* ---- agenda ---- */}
      <div className="pc-card" style={{ marginBottom: 0 }}>
        <p className="pc-eyebrow">Agenda até o saque</p>
        {AGENDA.map((e) => (
          <div key={e.d} style={{ display: "flex", alignItems: "center", gap: 10, padding: "6px 0" }}>
            <span
              style={{
                width: e.peso * 3 + 2, height: e.peso * 3 + 2, borderRadius: "50%", flex: "0 0 auto",
                background: e.peso >= 3 ? C.lamp : e.peso === 2 ? C.channel : C.rule,
              }}
            />
            <span className="pc-num" style={{ fontSize: 12, color: C.dim, width: 46 }}>{e.d}</span>
            <span style={{ fontSize: 12, color: e.peso >= 2 ? C.text : C.dim }}>{e.txt}</span>
          </div>
        ))}
      </div>

      <p style={{ fontSize: 10.5, color: C.faint, lineHeight: 1.55, marginTop: 12 }}>
        Modelo lognormal com β sobre o setor e correlação fixa. Volatilidades são estimativas, não
        implícitas de mercado. Ferramenta de dimensionamento de risco — não é recomendação de compra
        ou venda.
      </p>
    </div>
  );
}

/* ---------- barra de carga ---------- */
function BarraCarga({ caixa, risco, breakEven }: { caixa: number; risco: number; breakEven: number }) {
  const total = caixa + risco;
  const escala = Math.max(breakEven, total, 1) * 1.02;
  const w = (v: number) => (v / escala) * 100;
  return (
    <div>
      <div style={{ position: "relative", height: 26, background: C.raised, borderRadius: 2, overflow: "hidden" }}>
        <div className="pc-anim" style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: w(caixa) + "%", background: C.starboard }} />
        <div className="pc-anim" style={{ position: "absolute", left: w(caixa) + "%", top: 0, bottom: 0, width: w(risco) + "%", background: C.lamp, opacity: 0.85 }} />
      </div>
      <div style={{ position: "relative", height: 16, marginTop: -1 }}>
        <div style={{ position: "absolute", left: w(breakEven) + "%", top: 0, width: 1, height: 8, background: C.channel }} />
        <div
          className="pc-num"
          style={{
            position: "absolute", left: w(breakEven) + "%", top: 7, fontSize: 9.5, color: C.channel,
            transform: "translateX(-100%)", paddingRight: 4, whiteSpace: "nowrap",
          }}
        >break-even</div>
      </div>
    </div>
  );
}

/* ---------- distribuição ---------- */
function Distribuicao({ m }: { m: Metricas }) {
  const W = 320, H = 116, PB = 20;
  if (m.risco <= 0 || m.sd <= 0) {
    return (
      <div style={{ height: H, display: "flex", alignItems: "center", justifyContent: "center",
        background: C.raised, borderRadius: 2, fontSize: 12, color: C.dim, textAlign: "center", padding: 12 }}>
        Tudo em caixa. Resultado travado em <span className="pc-num" style={{ color: C.starboard, marginLeft: 4 }}>{usd(m.caixa)}</span>.
      </div>
    );
  }
  const N = 140, lo = m.drift - 3.2 * m.sd, hi = m.drift + 3.2 * m.sd;
  const pts = Array.from({ length: N + 1 }, (_, i) => {
    const x = lo + ((hi - lo) * i) / N;
    return { x, val: m.caixa + m.risco * Math.exp(x), d: Math.exp(-0.5 * ((x - m.drift) / m.sd) ** 2) };
  });
  const px = (x: number) => ((x - lo) / (hi - lo)) * W;
  const py = (d: number) => H - PB - d * (H - PB - 6);
  const xBE = px(Math.log(Math.max(m.custoTotal - m.caixa, 1e-6) / m.risco));
  const clampBE = Math.min(Math.max(xBE, 0), W);
  const path = pts.map((p, i) => `${i ? "L" : "M"}${px(p.x).toFixed(1)},${py(p.d).toFixed(1)}`).join("");
  const area = `${path}L${W},${H - PB}L0,${H - PB}Z`;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={`Distribuição de resultados. Probabilidade de empatar: ${(m.pEmpate * 100).toFixed(0)}%.`}>
      <defs>
        <clipPath id="pc-esq"><rect x="0" y="0" width={clampBE} height={H} /></clipPath>
        <clipPath id="pc-dir"><rect x={clampBE} y="0" width={W - clampBE} height={H} /></clipPath>
      </defs>
      <path d={area} fill={C.port} opacity="0.30" clipPath="url(#pc-esq)" />
      <path d={area} fill={C.starboard} opacity="0.34" clipPath="url(#pc-dir)" />
      <path d={path} fill="none" stroke={C.text} strokeWidth="1.1" opacity="0.55" />
      <line x1="0" y1={H - PB} x2={W} y2={H - PB} stroke={C.rule} strokeWidth="1" />
      {xBE >= 0 && xBE <= W && (
        <>
          <line x1={xBE} y1="2" x2={xBE} y2={H - PB} stroke={C.channel} strokeWidth="1" strokeDasharray="3 2.5" />
          <text x={Math.min(xBE + 4, W - 62)} y="11" fill={C.channel} fontSize="8.5" fontFamily={MONO}>
            {usd(m.custoTotal)}
          </text>
        </>
      )}
      {[-1.645, 0, 1.645].map((z) => {
        const x = px(m.drift + z * m.sd);
        return (
          <g key={z}>
            <line x1={x} y1={H - PB} x2={x} y2={H - PB + 3} stroke={C.faint} />
            <text x={x} y={H - PB + 13} fill={C.dim} fontSize="8.5" fontFamily={MONO} textAnchor="middle">
              {(m.caixa + m.risco * Math.exp(m.drift + z * m.sd)).toFixed(0)}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

/* ---------- auxiliares ---------- */
function Selo({ rot, val, cor }: { rot: string; val: string; cor: string }) {
  return (
    <div style={{ flex: 1, background: C.raised, borderLeft: `2px solid ${cor}`, borderRadius: 2, padding: "9px 10px" }}>
      <div className="pc-eyebrow" style={{ margin: "0 0 4px", fontSize: 9 }}>{rot}</div>
      <div className="pc-num" style={{ fontSize: 18, color: cor }}>{val}</div>
    </div>
  );
}

function Slider({ rot, val, min, max, step, value, onChange, nota }: {
  rot: string; val: string; min: number; max: number; step: number;
  value: number; onChange: (v: number) => void; nota: string;
}) {
  return (
    <div style={{ marginBottom: 14 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <label style={{ fontSize: 12, color: C.dim }}>{rot}</label>
        <span className="pc-num" style={{ fontSize: 13, color: C.text }}>{val}</span>
      </div>
      <input className="pc-rng" type="range" min={min} max={max} step={step} value={value}
        onChange={(e) => onChange(Number(e.target.value))} aria-label={rot} />
      <div style={{ fontSize: 10, color: C.faint, marginTop: -2 }}>{nota}</div>
    </div>
  );
}
