import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  useGetScenarioAlertSettings, getGetScenarioAlertSettingsQueryKey,
  useUpdateScenarioAlertSettings,
} from "@workspace/api-client-react";

// Paleta e classes CSS (.pc-card, .pc-eyebrow, .pc-num, .pc-date etc.) são as
// mesmas de pages/cenarios.tsx -- o <style> lá é global na página (não CSS
// modules), então reaproveita sem duplicar os tokens de cor.
const COLOR_STARBOARD = "#39BE9C";
const COLOR_PORT = "#E0574A";
const COLOR_DIM = "#84A0A0";
const COLOR_FAINT = "#5A7679";
const COLOR_TEXT = "#DDE9E7";
const COLOR_RAISED = "#1A2F36";
const COLOR_RULE = "#254048";

export function ScenarioAlertSettings({ dataAlvoAtual }: { dataAlvoAtual: string }) {
  const queryClient = useQueryClient();
  const { data: settings } = useGetScenarioAlertSettings({
    query: { queryKey: getGetScenarioAlertSettingsQueryKey() },
  });
  const update = useUpdateScenarioAlertSettings();

  const [open, setOpen] = useState(false);
  const [enabled, setEnabled] = useState(false);
  const [thresholdPct, setThresholdPct] = useState(50);
  const [notifyEmail, setNotifyEmail] = useState("");
  const [savedJustNow, setSavedJustNow] = useState(false);

  // Sincroniza o form com o que veio do servidor só na primeira carga (ou
  // quando o usuário abre o painel) -- não sobrescreve edições em andamento
  // a cada refetch.
  useEffect(() => {
    if (!settings) return;
    setEnabled(settings.enabled);
    setThresholdPct(settings.thresholdPct);
    setNotifyEmail(settings.notifyEmail ?? "");
  }, [settings]);

  function save() {
    update.mutate(
      { data: { dataAlvo: dataAlvoAtual, thresholdPct, enabled, notifyEmail: notifyEmail.trim() || null } },
      {
        onSuccess: () => {
          queryClient.invalidateQueries({ queryKey: getGetScenarioAlertSettingsQueryKey() });
          setSavedJustNow(true);
          setTimeout(() => setSavedJustNow(false), 2500);
        },
      },
    );
  }

  const dataAlvoDessincronizada = settings?.configured && settings.dataAlvo !== dataAlvoAtual;

  return (
    <div className="pc-card">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        style={{
          display: "flex", alignItems: "center", justifyContent: "space-between", width: "100%",
          background: "none", border: "none", cursor: "pointer", padding: 0, color: COLOR_TEXT,
        }}
      >
        <p className="pc-eyebrow" style={{ margin: 0 }}>Alertar por e-mail</p>
        <span className="pc-num" style={{ fontSize: 11, color: settings?.configured && settings.enabled ? COLOR_STARBOARD : COLOR_FAINT }}>
          {settings?.configured && settings.enabled ? "ativo" : "inativo"} {open ? "▲" : "▼"}
        </span>
      </button>

      {open && (
        <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 10 }}>
          <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: COLOR_DIM, cursor: "pointer" }}>
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
              style={{ width: 18, height: 18 }}
              aria-label="Habilitar alerta por e-mail"
            />
            Avisar quando a chance de empatar cair abaixo do limiar
          </label>

          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <label style={{ fontSize: 12, color: COLOR_DIM, flex: 1 }}>Limiar (%)</label>
            <input
              className="pc-inp"
              type="number" min={1} max={99} step={1}
              value={thresholdPct}
              onChange={(e) => setThresholdPct(Math.min(99, Math.max(1, Number(e.target.value) || 50)))}
              aria-label="Limiar de probabilidade de empatar"
            />
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <label style={{ fontSize: 12, color: COLOR_DIM, flex: 1 }}>E-mail</label>
            <input
              style={{ background: COLOR_RAISED, border: `1px solid ${COLOR_RULE}`, color: COLOR_TEXT, fontSize: 12, padding: "6px 8px", borderRadius: 2, flex: 2, minWidth: 0 }}
              type="email"
              value={notifyEmail}
              onChange={(e) => setNotifyEmail(e.target.value)}
              placeholder="seu@email.com"
              aria-label="E-mail de notificação do alerta"
            />
          </div>

          {dataAlvoDessincronizada && (
            <p style={{ fontSize: 10, color: COLOR_FAINT, margin: 0 }}>
              Alerta salvo usa a data-alvo {settings!.dataAlvo.split("-").reverse().join("/")} — salve de novo pra
              atualizar com a data selecionada acima ({dataAlvoAtual.split("-").reverse().join("/")}).
            </p>
          )}

          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <button
              type="button"
              onClick={save}
              disabled={update.isPending}
              style={{
                background: COLOR_STARBOARD, color: "#0E191D", border: "none", borderRadius: 2,
                padding: "7px 14px", fontSize: 12, fontWeight: 700, cursor: "pointer", opacity: update.isPending ? 0.6 : 1,
              }}
            >
              {update.isPending ? "Salvando..." : "Salvar"}
            </button>
            {savedJustNow && <span style={{ fontSize: 11, color: COLOR_STARBOARD }}>✓ Salvo</span>}
            {update.isError && <span style={{ fontSize: 11, color: COLOR_PORT }}>Falha ao salvar</span>}
          </div>

          <p style={{ fontSize: 10, color: COLOR_FAINT, margin: 0, lineHeight: 1.5 }}>
            Checado a cada hora, cenário neutro (sem venda manual, setor parado, volatilidade base). No máximo
            1 e-mail por dia enquanto a condição persistir.
          </p>
        </div>
      )}
    </div>
  );
}
