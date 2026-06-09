import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Activity, Lock } from "lucide-react";
import { useTranslation } from "react-i18next";

interface LoginProps {
  onSuccess: () => void;
}

export default function LoginPage({ onSuccess }: LoginProps) {
  const { t } = useTranslation();
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ password }),
      });
      if (res.ok) {
        onSuccess();
      } else {
        setError(t("login.errors.wrongPassword"));
      }
    } catch {
      setError(t("login.errors.connectionError"));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen bg-background flex items-center justify-center">
      <div className="w-full max-w-sm p-8 space-y-6">
        <div className="flex flex-col items-center gap-2">
          <div className="flex items-center gap-2">
            <Activity className="h-7 w-7 text-primary" />
            <span className="text-2xl font-bold tracking-tight text-primary">{t("login.title")}</span>
          </div>
          <p className="text-sm text-muted-foreground">{t("login.subtitle")}</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-2">
            <label className="text-sm font-medium text-foreground flex items-center gap-1">
              <Lock className="h-3.5 w-3.5" />
              {t("login.passwordLabel")}
            </label>
            <Input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••••••••••"
              autoFocus
              required
            />
          </div>

          {error && (
            <p className="text-sm text-destructive">{error}</p>
          )}

          <Button type="submit" className="w-full" disabled={loading || !password}>
            {loading ? t("login.submitting") : t("login.submit")}
          </Button>
        </form>
      </div>
    </div>
  );
}
