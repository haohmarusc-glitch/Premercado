import {
  useGetScenarioAlertSettings, getGetScenarioAlertSettingsQueryKey,
  useGetScenarioProgress, getGetScenarioProgressQueryKey,
} from "@workspace/api-client-react";
import { pctConfirmacao } from "@workspace/scenario-math";

// Mesma paleta de scenario-alert-settings.tsx (classes .pc-card/.pc-eyebrow
// etc. são globais na página, ver comentário lá).
const COLOR_STARBOARD = "#39BE9C";
const COLOR_PORT = "#E0574A";
const COLOR_LAMP = "#E3A63C";
const COLOR_DIM = "#84A0A0";
const COLOR_FAINT = "#5A7679";
const COLOR_RULE = "#254048";

function usd(v: number): string {
  return v.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
}

function fmtData(iso: string): string {
  return iso.split("-").reverse().join("/");
}

function corPct(pct: number): string {
  if (pct >= 66) return COLOR_STARBOARD;
  if (pct >= 33) return COLOR_LAMP;
  return COLOR_PORT;
}

// Termômetro de confirmação: acompanha, dia a dia (via os snapshots do
// checker em background), se a chance de empatar do Painel de Cenários se
// manteve acima do limiar configurado no alerta -- e fecha com um selo
// final ✓/✗ quando a data-alvo do ciclo vigente já foi resolvida.
export function ScenarioThermometer() {
  const { data: settings } = useGetScenarioAlertSettings({
    query: { queryKey: getGetScenarioAlertSettingsQueryKey() },
  });
  const { data: progress } = useGetScenarioProgress({
    query: { queryKey: getGetScenarioProgressQueryKey(), enabled: !!settings?.configured },
  });

  if (!settings?.configured) {
    return (
      <div className="pc-card">
        <p className="pc-eyebrow">Termômetro de confirmação</p>
        <p style={{ fontSize: 11, color: COLOR_FAINT, margin: 0 }}>
          Configure o alerta acima pra começar a acompanhar diariamente.
        </p>
      </div>
    );
  }

  const resolucaoAtual = progress?.resolutions.find((r) => r.dataAlvo === settings.dataAlvo);
  const snapshots = progress?.snapshots ?? [];

  if (resolucaoAtual) {
    return (
      <div className="pc-card">
        <p className="pc-eyebrow">Termômetro de confirmação</p>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{ fontSize: 22 }}>{resolucaoAtual.bateu ? "✓" : "✗"}</span>
          <div>
            <div className="pc-num" style={{ fontSize: 14, color: resolucaoAtual.bateu ? COLOR_STARBOARD : COLOR_PORT }}>
              {resolucaoAtual.bateu ? "Cenário confirmado" : "Cenário não confirmado"}
            </div>
            <div style={{ fontSize: 11, color: COLOR_DIM, marginTop: 2 }}>
              Data-alvo {fmtData(resolucaoAtual.dataAlvo)} · resultado {usd(resolucaoAtual.valorFinal)} vs. break-even {usd(resolucaoAtual.custoTotal)}
            </div>
          </div>
        </div>
        <p style={{ fontSize: 10, color: COLOR_FAINT, margin: "10px 0 0" }}>
          Ciclo encerrado. Defina uma nova data-alvo acima pra começar um novo acompanhamento.
        </p>
      </div>
    );
  }

  if (!snapshots.length) {
    return (
      <div className="pc-card">
        <p className="pc-eyebrow">Termômetro de confirmação</p>
        <p style={{ fontSize: 11, color: COLOR_FAINT, margin: 0 }}>
          Sem histórico ainda — o primeiro snapshot é gerado no próximo ciclo do checker (a cada hora).
        </p>
      </div>
    );
  }

  const pct = pctConfirmacao(snapshots.map((s) => ({ pEmpate: s.pEmpate })), settings.thresholdPct) ?? 0;
  const dentro = snapshots.filter((s) => s.pEmpate * 100 >= settings.thresholdPct).length;
  const cor = corPct(pct);

  return (
    <div className="pc-card">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <p className="pc-eyebrow" style={{ margin: 0 }}>Termômetro de confirmação</p>
        <span className="pc-num" style={{ fontSize: 15, color: cor }}>{pct.toFixed(0)}%</span>
      </div>

      <div style={{ background: COLOR_RULE, borderRadius: 3, height: 8, marginTop: 8, overflow: "hidden" }}>
        <div style={{ width: `${pct}%`, height: "100%", background: cor, transition: "width .3s" }} />
      </div>

      <p style={{ fontSize: 11, color: COLOR_DIM, margin: "8px 0 0" }}>
        Em {dentro} de {snapshots.length} dia{snapshots.length === 1 ? "" : "s"} acompanhado{snapshots.length === 1 ? "" : "s"},
        a chance de empatar ficou ≥ {settings.thresholdPct.toFixed(0)}%.
      </p>

      {/* histórico diário -- um bloco por dia, verde/vermelho conforme o limiar */}
      <div style={{ display: "flex", gap: 2, marginTop: 10, flexWrap: "wrap" }} role="img" aria-label="Histórico diário de confirmação">
        {snapshots.slice(-30).map((s) => (
          <div
            key={s.snapshotDate}
            title={`${fmtData(s.snapshotDate)}: ${(s.pEmpate * 100).toFixed(0)}% de chance de empatar`}
            style={{
              width: 10, height: 16, borderRadius: 1,
              background: s.pEmpate * 100 >= settings.thresholdPct ? COLOR_STARBOARD : COLOR_PORT,
              opacity: 0.85,
            }}
          />
        ))}
      </div>
    </div>
  );
}
