import { useState, useRef, useEffect } from "react";
import { MarkdownContent } from "@/components/markdown";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { Send, Trash2, MessageSquare } from "lucide-react";

interface ChatMsg {
  id: number;
  role: "user" | "assistant";
  content: string;
}

let _id = 0;
function uid() { return ++_id; }

const SUGGESTIONS = [
  "Como está NVDA hoje?",
  "Qual o Fear & Greed index?",
  "Análise técnica de MU",
  "Sentimento do setor de memória?",
  "Short interest de SMCI",
  "Ratings de analistas para TSM",
];

export default function Chat() {
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [step, setStep] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const msgsRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  async function send() {
    const text = input.trim();
    if (!text || loading) return;

    const userMsg: ChatMsg = { id: uid(), role: "user", content: text };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setLoading(true);
    setStep("Iniciando...");

    const historyPayload = messages.map((m) => ({ role: m.role, content: m.content }));

    try {
      const res = await fetch("/api/chat/message", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, history: historyPayload }),
        credentials: "include",
      });

      if (!res.ok || !res.body) {
        throw new Error(`HTTP ${res.status}`);
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

          let payload: string;
          try { payload = JSON.parse(rawData) as string; }
          catch { payload = rawData; }

          if (eventType === "step") {
            setStep(payload);
          } else if (eventType === "done" || eventType === "error") {
            const content = eventType === "error" ? `⚠️ ${payload}` : payload;
            setMessages((prev) => [...prev, { id: uid(), role: "assistant", content }]);
            setStep(null);
            setLoading(false);
          }
        }
      }
    } catch {
      setMessages((prev) => [
        ...prev,
        { id: uid(), role: "assistant", content: "⚠️ Erro de conexão com o agente." },
      ]);
      setStep(null);
      setLoading(false);
    }
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      void send();
    }
  }

  const empty = messages.length === 0 && !loading;

  return (
    <div className="space-y-4 animate-in fade-in duration-500">
      {/* Header */}
      <div className="border-b border-border pb-4 flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold font-mono text-foreground tracking-tight">CHAT</h1>
          <p className="text-muted-foreground font-mono text-sm mt-2">
            Consulte o agente sobre os ativos monitorados
          </p>
        </div>
        {messages.length > 0 && (
          <Button
            variant="ghost"
            size="sm"
            className="font-mono text-xs text-muted-foreground"
            onClick={() => { setMessages([]); setStep(null); setLoading(false); }}
          >
            <Trash2 className="h-3.5 w-3.5 mr-1.5" />
            Limpar
          </Button>
        )}
      </div>

      {/* Messages area */}
      <div
        ref={msgsRef}
        className="h-[55vh] overflow-y-auto space-y-4 border border-border rounded-lg p-4 bg-background/30"
      >
        {empty && (
          <div className="flex flex-col items-center justify-center h-full text-center gap-4">
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
          <div key={msg.id} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
            {msg.role === "user" ? (
              <div className="max-w-[75%] bg-primary/10 border border-primary/30 rounded-lg px-4 py-2.5 font-mono text-sm text-foreground whitespace-pre-wrap">
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
                {step ? (
                  <span className="text-xs font-mono text-muted-foreground animate-pulse">&gt; {step}</span>
                ) : (
                  <Skeleton className="h-4 w-48" />
                )}
              </div>
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="space-y-2">
        <div className="flex gap-3 items-end">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Pergunte sobre os ativos... (Ctrl+Enter para enviar)"
            disabled={loading}
            rows={2}
            className="flex-1 resize-none bg-secondary border border-border rounded-lg px-3 py-2.5 font-mono text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary/50 focus:border-primary/50 disabled:opacity-50 transition-colors"
          />
          <Button
            onClick={() => void send()}
            disabled={loading || !input.trim()}
            className="font-mono font-bold flex-shrink-0 self-stretch px-4"
          >
            <Send className="h-4 w-4" />
          </Button>
        </div>
        <p className="text-[10px] font-mono text-muted-foreground">
          Ctrl+Enter para enviar · Histórico apenas local — limpo ao sair da página
        </p>
      </div>
    </div>
  );
}
