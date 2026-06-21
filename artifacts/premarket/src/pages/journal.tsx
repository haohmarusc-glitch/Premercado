import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { BookOpen, Plus, Trash2, X } from "lucide-react";

interface TradeJournalEntry {
  id: number;
  ticker: string;
  entryDate: string;
  entryPrice: number | null;
  stopLoss: number | null;
  targetPrice: number | null;
  thesis: string | null;
  emotionalState: string;
  exitDate: string | null;
  exitPrice: number | null;
  result: string | null;
  notes: string | null;
  createdAt: string;
  updatedAt: string;
}

async function fetchJournal(): Promise<TradeJournalEntry[]> {
  const r = await fetch("/api/journal", { credentials: "include" });
  if (!r.ok) throw new Error("Failed to fetch");
  return r.json();
}

const EMOTIONAL_STATES = [
  { value: "neutral", label: "😐 Neutro" },
  { value: "confident", label: "😎 Confiante" },
  { value: "anxious", label: "😰 Ansioso" },
];

const RESULT_OPTIONS = [
  { value: "", label: "ABERTO" },
  { value: "win", label: "WIN" },
  { value: "loss", label: "LOSS" },
];

function ResultBadge({ result }: { result: string | null }) {
  if (!result || result === "") return <Badge variant="outline" className="text-primary border-primary/40 font-mono text-[10px]">ABERTO</Badge>;
  if (result === "win") return <Badge variant="outline" className="text-green-500 border-green-500/40 bg-green-500/10 font-mono text-[10px]">WIN</Badge>;
  return <Badge variant="outline" className="text-red-500 border-red-500/40 bg-red-500/10 font-mono text-[10px]">LOSS</Badge>;
}

function EmotionBadge({ state }: { state: string }) {
  const map: Record<string, string> = { neutral: "😐 Neutro", confident: "😎 Confiante", anxious: "😰 Ansioso" };
  return <span className="font-mono text-xs text-muted-foreground">{map[state] ?? state}</span>;
}

const EMPTY_FORM = {
  ticker: "", entryDate: "", entryPrice: "", stopLoss: "", targetPrice: "",
  thesis: "", emotionalState: "neutral", exitDate: "", exitPrice: "", result: "", notes: "",
};

export default function JournalPage() {
  const qc = useQueryClient();
  const [resultFilter, setResultFilter] = useState<string>("all");
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ ...EMPTY_FORM });

  const { data, isLoading } = useQuery({ queryKey: ["journal"], queryFn: fetchJournal });

  const addEntry = useMutation({
    mutationFn: async () => {
      const body: Record<string, unknown> = {
        ticker: form.ticker.toUpperCase(),
        entryDate: form.entryDate,
        emotionalState: form.emotionalState,
      };
      if (form.entryPrice) body.entryPrice = parseFloat(form.entryPrice);
      if (form.stopLoss) body.stopLoss = parseFloat(form.stopLoss);
      if (form.targetPrice) body.targetPrice = parseFloat(form.targetPrice);
      if (form.thesis) body.thesis = form.thesis;
      if (form.exitDate) body.exitDate = form.exitDate;
      if (form.exitPrice) body.exitPrice = parseFloat(form.exitPrice);
      if (form.result) body.result = form.result;
      if (form.notes) body.notes = form.notes;
      const r = await fetch("/api/journal", {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["journal"] });
      setShowForm(false);
      setForm({ ...EMPTY_FORM });
    },
  });

  const remove = useMutation({
    mutationFn: async (id: number) => {
      await fetch(`/api/journal/${id}`, { method: "DELETE", credentials: "include" });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["journal"] }),
  });

  const filtered = (data ?? []).filter((e) => {
    if (resultFilter === "all") return true;
    if (resultFilter === "open") return !e.result || e.result === "";
    return e.result === resultFilter;
  });

  function rrRatio(e: TradeJournalEntry): string {
    if (!e.entryPrice || !e.stopLoss || !e.targetPrice) return "—";
    const risk = Math.abs(e.entryPrice - e.stopLoss);
    const reward = Math.abs(e.targetPrice - e.entryPrice);
    if (risk === 0) return "—";
    return (reward / risk).toFixed(2) + "R";
  }

  return (
    <div className="space-y-6 animate-in fade-in duration-500">
      <div className="border-b border-border pb-4 flex items-end justify-between">
        <div>
          <h1 className="text-3xl font-bold font-mono text-foreground tracking-tight flex items-center gap-2">
            <BookOpen className="h-7 w-7 text-primary" /> DIÁRIO DE TRADES
          </h1>
          <p className="text-muted-foreground font-mono text-sm mt-2">Registro e análise de operações</p>
        </div>
        <button
          onClick={() => setShowForm((v) => !v)}
          className="flex items-center gap-1.5 px-4 py-2 bg-primary text-primary-foreground rounded font-mono text-sm font-bold"
        >
          <Plus className="h-4 w-4" /> Novo Trade
        </button>
      </div>

      {/* Form dialog */}
      {showForm && (
        <div className="border border-border rounded-lg bg-card p-5 space-y-4">
          <div className="flex items-center justify-between mb-2">
            <p className="text-xs font-mono text-muted-foreground uppercase tracking-widest">Novo Trade</p>
            <button onClick={() => setShowForm(false)}><X className="h-4 w-4 text-muted-foreground hover:text-foreground" /></button>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {[
              { label: "Ticker *", key: "ticker", type: "text" },
              { label: "Data Entrada *", key: "entryDate", type: "date" },
              { label: "Preço Entrada", key: "entryPrice", type: "number" },
              { label: "Stop Loss", key: "stopLoss", type: "number" },
              { label: "Alvo", key: "targetPrice", type: "number" },
              { label: "Data Saída", key: "exitDate", type: "date" },
              { label: "Preço Saída", key: "exitPrice", type: "number" },
            ].map(({ label, key, type }) => (
              <div key={key} className="flex flex-col gap-1">
                <label className="text-[10px] font-mono text-muted-foreground uppercase">{label}</label>
                <input
                  type={type}
                  value={form[key as keyof typeof form]}
                  onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))}
                  className="bg-background border border-border rounded px-2 py-1.5 font-mono text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
                />
              </div>
            ))}
            <div className="flex flex-col gap-1">
              <label className="text-[10px] font-mono text-muted-foreground uppercase">Estado Emocional</label>
              <select
                value={form.emotionalState}
                onChange={(e) => setForm((f) => ({ ...f, emotionalState: e.target.value }))}
                className="bg-background border border-border rounded px-2 py-1.5 font-mono text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
              >
                {EMOTIONAL_STATES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
              </select>
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-[10px] font-mono text-muted-foreground uppercase">Resultado</label>
              <select
                value={form.result}
                onChange={(e) => setForm((f) => ({ ...f, result: e.target.value }))}
                className="bg-background border border-border rounded px-2 py-1.5 font-mono text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
              >
                {RESULT_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </div>
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[10px] font-mono text-muted-foreground uppercase">Tese</label>
            <textarea
              value={form.thesis}
              onChange={(e) => setForm((f) => ({ ...f, thesis: e.target.value }))}
              rows={2}
              className="bg-background border border-border rounded px-2 py-1.5 font-mono text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary resize-none"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[10px] font-mono text-muted-foreground uppercase">Notas</label>
            <textarea
              value={form.notes}
              onChange={(e) => setForm((f) => ({ ...f, notes: e.target.value }))}
              rows={2}
              className="bg-background border border-border rounded px-2 py-1.5 font-mono text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary resize-none"
            />
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => form.ticker && form.entryDate && addEntry.mutate()}
              disabled={!form.ticker || !form.entryDate || addEntry.isPending}
              className="px-4 py-2 bg-primary text-primary-foreground rounded font-mono text-sm font-bold disabled:opacity-50"
            >
              {addEntry.isPending ? "Salvando..." : "Salvar Trade"}
            </button>
            <button onClick={() => setShowForm(false)} className="px-4 py-2 border border-border rounded font-mono text-sm text-muted-foreground hover:text-foreground">
              Cancelar
            </button>
          </div>
        </div>
      )}

      {/* Filter */}
      <div className="flex gap-2 flex-wrap">
        {[["all", "Todos"], ["open", "Abertos"], ["win", "Win"], ["loss", "Loss"]].map(([v, l]) => (
          <button
            key={v}
            onClick={() => setResultFilter(v)}
            className={`px-3 py-1.5 rounded font-mono text-xs font-bold border transition-colors ${
              resultFilter === v ? "bg-primary text-primary-foreground border-primary" : "bg-secondary border-border text-muted-foreground hover:text-foreground"
            }`}
          >
            {l}
          </button>
        ))}
      </div>

      {/* Table */}
      {isLoading ? (
        <div className="space-y-2">{Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-14 w-full" />)}</div>
      ) : !filtered.length ? (
        <div className="p-12 text-center border border-dashed border-border rounded font-mono text-muted-foreground text-sm">
          Nenhum trade encontrado.
        </div>
      ) : (
        <div className="border border-border rounded-lg overflow-hidden overflow-x-auto">
          <table className="w-full font-mono text-sm min-w-[800px]">
            <thead className="bg-secondary/40 border-b border-border">
              <tr>
                {["Ticker", "Data", "Entrada", "Stop", "Alvo", "R/R", "Emoção", "Resultado", "Tese", ""].map((h) => (
                  <th key={h} className="text-left px-3 py-2.5 text-[10px] text-muted-foreground uppercase tracking-widest whitespace-nowrap">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map((e, idx) => (
                <tr key={e.id} className={idx % 2 === 0 ? "bg-card" : "bg-secondary/10"}>
                  <td className="px-3 py-2.5">
                    <Badge variant="outline" className="font-mono bg-secondary/50 border-border text-primary font-bold">{e.ticker}</Badge>
                  </td>
                  <td className="px-3 py-2.5 text-muted-foreground text-xs">{e.entryDate}</td>
                  <td className="px-3 py-2.5 text-foreground">{e.entryPrice != null ? `$${e.entryPrice.toFixed(2)}` : "—"}</td>
                  <td className="px-3 py-2.5 text-red-400">{e.stopLoss != null ? `$${e.stopLoss.toFixed(2)}` : "—"}</td>
                  <td className="px-3 py-2.5 text-green-400">{e.targetPrice != null ? `$${e.targetPrice.toFixed(2)}` : "—"}</td>
                  <td className="px-3 py-2.5 text-primary font-bold">{rrRatio(e)}</td>
                  <td className="px-3 py-2.5"><EmotionBadge state={e.emotionalState} /></td>
                  <td className="px-3 py-2.5"><ResultBadge result={e.result} /></td>
                  <td className="px-3 py-2.5 text-muted-foreground text-xs max-w-[200px] truncate" title={e.thesis ?? ""}>{e.thesis ?? "—"}</td>
                  <td className="px-3 py-2.5 text-right">
                    <button onClick={() => remove.mutate(e.id)} className="text-muted-foreground hover:text-red-400 transition-colors">
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
