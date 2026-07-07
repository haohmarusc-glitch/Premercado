import { useState } from "react";
import { Activity, Loader2 } from "lucide-react";
import { useAuth } from "@/lib/auth";
import { ApiError } from "@workspace/api-client-react";

type Mode = "login" | "signup";

function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    const data = err.data as { error?: string } | null;
    return data?.error ?? fallback;
  }
  return fallback;
}

export default function LoginPage() {
  const { login, signup } = useAuth();
  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      if (mode === "login") await login(email, password);
      else await signup(email, password);
    } catch (err) {
      setError(errorMessage(err, "Algo deu errado. Tente de novo."));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-background px-4">
      <div className="w-full max-w-sm border border-border rounded-lg bg-card p-6 font-mono">
        <div className="flex items-center gap-2 text-primary font-bold text-xl mb-1">
          <Activity className="h-6 w-6" />
          <span>PRÉ-MERCADO</span>
        </div>
        <p className="text-xs text-muted-foreground mb-6">Agent Command Center</p>

        <div className="flex gap-1 mb-5 border border-border rounded-md p-1">
          {([
            ["login", "Entrar"],
            ["signup", "Criar conta"],
          ] as const).map(([key, label]) => (
            <button
              key={key}
              type="button"
              onClick={() => { setMode(key); setError(null); }}
              className={`flex-1 py-1.5 rounded text-xs font-bold transition-colors ${
                mode === key
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label className="text-[10px] text-muted-foreground uppercase tracking-widest">Email</label>
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="mt-1 w-full bg-background border border-border rounded px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
              autoComplete="email"
            />
          </div>
          <div>
            <label className="text-[10px] text-muted-foreground uppercase tracking-widest">Senha</label>
            <input
              type="password"
              required
              minLength={8}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="mt-1 w-full bg-background border border-border rounded px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
              autoComplete={mode === "login" ? "current-password" : "new-password"}
            />
          </div>

          {error && <p className="text-xs text-red-400">{error}</p>}

          <button
            type="submit"
            disabled={submitting}
            className="w-full mt-2 bg-primary text-primary-foreground rounded py-2 text-sm font-bold flex items-center justify-center gap-2 disabled:opacity-50"
          >
            {submitting && <Loader2 className="h-4 w-4 animate-spin" />}
            {mode === "login" ? "Entrar" : "Criar conta"}
          </button>
        </form>

      </div>
    </div>
  );
}
