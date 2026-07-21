import { useState, useRef, useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { MarkdownContent } from "@/components/markdown";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { useViewMode } from "@/lib/view-mode";
import { cn } from "@/lib/utils";
import {
  useListChatSessions,
  getListChatSessionsQueryKey,
  useDeleteChatSession,
} from "@workspace/api-client-react";
import type { ChatSession } from "@workspace/api-client-react";
import { Send, Trash2, MessageSquare, Plus, Clock, X } from "lucide-react";

interface LocalMsg {
  localId: number;
  role: "user" | "assistant";
  content: string;
}

let _lid = 0;
function lid() { return ++_lid; }

const SUGGESTIONS = [
  "Como está NVDA hoje?",
  "Qual o Fear & Greed index?",
  "Análise técnica de MU",
  "Sentimento do setor de memória?",
  "Short interest de SMCI",
  "Ratings de analistas para TSM",
];

function fmtDate(iso: string) {
  return new Date(iso).toLocaleDateString("pt-BR", {
    day: "2-digit", month: "2-digit", year: "2-digit",
    hour: "2-digit", minute: "2-digit",
  });
}

export default function Chat() {
  const queryClient = useQueryClient();
  const { viewMode } = useViewMode();
  const isMobile = viewMode === "mobile";
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [messages, setMessages] = useState<LocalMsg[]>([]);
  const [sessionId, setSessionId] = useState<number | null>(null);
  const sessionIdRef = useRef<number | null>(null);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [step, setStep] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const { data: sessions, isLoading: loadingSessions } = useListChatSessions({
    query: { queryKey: getListChatSessionsQueryKey(), refetchInterval: 0 },
  });

  const deleteSession = useDeleteChatSession();

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  async function loadSession(id: number) {
    const res = await fetch(`/api/chat/sessions/${id}/messages`, { credentials: "include" });
    if (!res.ok) return;
    const data = (await res.json()) as Array<{ id: number; role: string; content: string }>;
    setMessages(data.map((m) => ({ localId: lid(), role: m.role as "user" | "assistant", content: m.content })));
    setSessionId(id);
    sessionIdRef.current = id;
    setStep(null);
    setLoading(false);
    setSidebarOpen(false);
  }

  function newConversation() {
    setMessages([]);
    setSessionId(null);
    sessionIdRef.current = null;
    setStep(null);
    setLoading(false);
    setSidebarOpen(false);
    textareaRef.current?.focus();
  }

  function handleDeleteSession(e: React.MouseEvent, id: number) {
    e.stopPropagation();
    deleteSession.mutate({ id }, {
      onSuccess: () => {
        queryClient.invalidateQueries({ queryKey: getListChatSessionsQueryKey() });
        if (sessionId === id) newConversation();
      },
    });
  }

  async function send() {
    const text = input.trim();
    if (!text || loading) return;

    const userMsg: LocalMsg = { localId: lid(), role: "user", content: text };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setLoading(true);
    setStep("Iniciando...");

    const historyPayload = messages.map((m) => ({ role: m.role, content: m.content }));

    try {
      const res = await fetch("/api/chat/message", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, history: historyPayload, sessionId }),
        credentials: "include",
      });

      if (!res.body) throw new Error("Resposta sem corpo");
      if (!res.ok) {
        const msg =
          res.status === 401 ? "Não autorizado — verifique a chave da API." :
          res.status === 429 ? "Limite de requisições atingido. Aguarde um momento." :
          res.status >= 500 ? "Erro interno do servidor. Tente novamente." :
          `Erro ${res.status}`;
        throw new Error(msg);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const parts = buf.split("\n\n");
        buf = parts.pop() ?? "";

        for (const part of parts) {
          let eventType = "";
          let rawData = "";
          for (const line of part.split("\n")) {
            if (line.startsWith("event: ")) eventType = line.slice(7).trim();
            else if (line.startsWith("data: ")) rawData = line.slice(6);
          }
          if (!eventType || !rawData) continue;

          let payload: unknown;
          try { payload = JSON.parse(rawData); } catch { payload = rawData; }

          if (eventType === "session") {
            const s = payload as { sessionId: number };
            setSessionId(s.sessionId);
            sessionIdRef.current = s.sessionId;
            queryClient.invalidateQueries({ queryKey: getListChatSessionsQueryKey() });
          } else if (eventType === "step") {
            setStep(payload as string);
          } else if (eventType === "done") {
            setMessages((prev) => [...prev, { localId: lid(), role: "assistant", content: payload as string }]);
            setStep(null);
            setLoading(false);
            queryClient.invalidateQueries({ queryKey: getListChatSessionsQueryKey() });
          } else if (eventType === "title") {
            const newTitle = payload as string;
            const sid = sessionIdRef.current;
            if (sid !== null) {
              queryClient.setQueryData(
                getListChatSessionsQueryKey(),
                (old: ChatSession[] | undefined) =>
                  old?.map((s) => (s.id === sid ? { ...s, title: newTitle } : s)),
              );
            }
          } else if (eventType === "error") {
            setMessages((prev) => [...prev, { localId: lid(), role: "assistant", content: `⚠️ ${payload as string}` }]);
            setStep(null);
            setLoading(false);
          }
        }
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Erro de conexão com o agente.";
      setMessages((prev) => [
        ...prev,
        { localId: lid(), role: "assistant", content: `⚠️ ${msg}` },
      ]);
      setStep(null);
      setLoading(false);
    }
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); void send(); }
  }

  function onInputChange(e: React.ChangeEvent<HTMLTextAreaElement>) {
    setInput(e.target.value);
    // auto-resize
    const el = e.target;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }

  const empty = messages.length === 0 && !loading;

  return (
    <div className="space-y-4 animate-in fade-in duration-500">
      {/* Header */}
      <div className="border-b border-border pb-4">
        <h1 className="text-3xl font-bold font-mono text-foreground tracking-tight">CHAT</h1>
        <p className="text-muted-foreground font-mono text-sm mt-2">
          Consulte o agente sobre os ativos monitorados
        </p>
      </div>

      {/* 2-column layout (a barra de sessões vira gaveta sobreposta no
          celular -- em largura fixa ela espremia a caixa de digitação até
          sobrar só ~1 caractere de largura, ver bug reportado 20/07) */}
      <div className="relative flex gap-0 border border-border rounded-lg overflow-hidden" style={{ height: "calc(100vh - 220px)", minHeight: "500px" }}>

        {isMobile && sidebarOpen && (
          <div
            className="fixed inset-0 z-40 bg-black/60"
            onClick={() => setSidebarOpen(false)}
            aria-hidden="true"
          />
        )}

        {/* ── Sessions sidebar ── */}
        <div
          className={cn(
            "w-52 flex-shrink-0 border-r border-border bg-secondary/10 flex flex-col",
            isMobile && "fixed inset-y-0 left-0 z-50 transition-transform duration-200",
            isMobile && (sidebarOpen ? "translate-x-0" : "-translate-x-full"),
          )}
        >
          <div className="p-3 border-b border-border flex items-center gap-2">
            <button
              type="button"
              onClick={newConversation}
              className="flex-1 flex items-center gap-2 px-3 py-2 rounded-md bg-primary/10 border border-primary/30 text-primary font-mono text-xs font-bold hover:bg-primary/20 transition-colors"
            >
              <Plus className="h-3.5 w-3.5" />
              Nova conversa
            </button>
            {isMobile && (
              <button
                type="button"
                onClick={() => setSidebarOpen(false)}
                className="p-2 rounded-md text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors"
                aria-label="Fechar histórico"
              >
                <X className="h-4 w-4" />
              </button>
            )}
          </div>

          <div className="flex-1 overflow-y-auto p-2 space-y-1">
            {loadingSessions && (
              <div className="space-y-1.5 px-1 pt-1">
                {[1, 2, 3].map((i) => <Skeleton key={i} className="h-12 w-full" />)}
              </div>
            )}
            {sessions?.length === 0 && !loadingSessions && (
              <p className="text-[10px] font-mono text-muted-foreground px-2 pt-3 text-center">
                Nenhuma conversa salva
              </p>
            )}
            {sessions?.map((s) => (
              <div
                key={s.id}
                onClick={() => { void loadSession(s.id); }}
                className={`group relative flex flex-col gap-0.5 px-3 py-2 rounded-md cursor-pointer transition-colors border ${
                  sessionId === s.id
                    ? "bg-primary/10 border-primary/30 text-foreground"
                    : "border-transparent hover:bg-secondary/60 text-muted-foreground hover:text-foreground"
                }`}
              >
                <span className="font-mono text-xs font-bold line-clamp-2 leading-tight pr-5">
                  {s.title}
                </span>
                <span className="flex items-center gap-1 font-mono text-[9px] text-muted-foreground mt-0.5">
                  <Clock className="h-2.5 w-2.5" />
                  {fmtDate(s.updatedAt)}
                </span>
                <span className="absolute top-1.5 right-1.5 opacity-0 group-hover:opacity-100 transition-opacity">
                  <button
                    type="button"
                    onClick={(e) => handleDeleteSession(e, s.id)}
                    className="p-0.5 rounded hover:text-red-400 text-muted-foreground transition-colors"
                  >
                    <Trash2 className="h-3 w-3" />
                  </button>
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* ── Chat panel ── */}
        <div className="flex-1 flex flex-col min-w-0">
          {isMobile && (
            <div className="flex items-center border-b border-border px-3 py-2">
              <button
                type="button"
                onClick={() => setSidebarOpen(true)}
                className="flex items-center gap-2 px-2 py-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors font-mono text-xs"
              >
                <Clock className="h-3.5 w-3.5" />
                Histórico
              </button>
            </div>
          )}
          {/* Messages */}
          <div className="flex-1 overflow-y-auto p-4 space-y-4">
            {empty && (
              <div className="flex flex-col items-center justify-center h-full text-center gap-4 py-8">
                <MessageSquare className="h-10 w-10 text-muted-foreground/30" />
                <p className="font-mono text-muted-foreground text-sm">
                  Faça uma pergunta sobre os ativos monitorados
                </p>
                <div className="flex flex-wrap gap-2 justify-center">
                  {SUGGESTIONS.map((q) => (
                    <button
                      key={q}
                      type="button"
                      onClick={() => { setInput(q); textareaRef.current?.focus(); }}
                      className="px-3 py-1.5 rounded-md bg-secondary border border-border text-xs font-mono text-muted-foreground hover:text-foreground hover:border-primary/40 transition-colors"
                    >
                      {q}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {messages.map((msg) => (
              <div key={msg.localId} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
                {msg.role === "user" ? (
                  <div className="max-w-[80%] bg-primary/10 border border-primary/30 rounded-lg px-4 py-2.5 font-mono text-sm text-foreground whitespace-pre-wrap">
                    {msg.content}
                  </div>
                ) : (
                  <div className="w-full border border-border rounded-lg bg-card overflow-hidden">
                    <div className="flex items-center gap-2 px-4 py-1.5 border-b border-border/50 bg-secondary/30">
                      <span className="text-[10px] font-mono text-primary uppercase tracking-widest font-bold">Agente</span>
                    </div>
                    <div className="px-4 py-3">
                      <MarkdownContent content={msg.content} />
                    </div>
                  </div>
                )}
              </div>
            ))}

            {loading && (
              <div className="flex justify-start">
                <div className="w-full border border-primary/20 rounded-lg bg-card overflow-hidden">
                  <div className="flex items-center gap-2 px-4 py-1.5 border-b border-primary/10 bg-secondary/30">
                    <span className="text-[10px] font-mono text-primary uppercase tracking-widest font-bold">Agente</span>
                    <span className="flex gap-0.5 ml-1">
                      {[0, 1, 2].map((i) => (
                        <span
                          key={i}
                          className="w-1.5 h-1.5 rounded-full bg-primary animate-bounce"
                          style={{ animationDelay: `${i * 0.12}s` }}
                        />
                      ))}
                    </span>
                  </div>
                  <div className="px-4 py-3">
                    {step
                      ? <span className="text-xs font-mono text-muted-foreground animate-pulse">&gt; {step}</span>
                      : <Skeleton className="h-4 w-48" />}
                  </div>
                </div>
              </div>
            )}

            <div ref={bottomRef} />
          </div>

          {/* Input */}
          <div className="border-t border-border p-3 flex flex-col gap-2">
            <div className="flex gap-2 items-end">
              <textarea
                ref={textareaRef}
                value={input}
                onChange={onInputChange}
                onKeyDown={onKeyDown}
                placeholder="Pergunte sobre os ativos... (Ctrl+Enter para enviar)"
                disabled={loading}
                rows={2}
                style={{ minHeight: "44px" }}
                className="flex-1 resize-none bg-secondary border border-border rounded-lg px-3 py-2.5 font-mono text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary/50 focus:border-primary/50 disabled:opacity-50 transition-colors overflow-hidden"
              />
              <Button
                onClick={() => void send()}
                disabled={loading || !input.trim()}
                className="font-mono font-bold flex-shrink-0 self-stretch px-4"
              >
                <Send className="h-4 w-4" />
              </Button>
            </div>
            <div className="flex items-center justify-between">
              <p className="text-[10px] font-mono text-muted-foreground">
                Ctrl+Enter para enviar · Histórico salvo automaticamente
              </p>
              {input.length > 0 && (
                <span className={`text-[10px] font-mono ${input.length > 1000 ? "text-amber-500" : "text-muted-foreground"}`}>
                  {input.length} chars
                </span>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
