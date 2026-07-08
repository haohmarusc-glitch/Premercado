import { useEffect, useRef } from "react";
import { useLocation } from "wouter";
import { useActivityHeartbeat } from "@workspace/api-client-react";
import { useAuth } from "@/lib/auth";

// Reporta a rota atual do frontend pra tela de administração de usuários
// (online/offline + última página visitada) -- ver routes/activity.ts.
const HEARTBEAT_INTERVAL_MS = 20_000;

export function useActivityHeartbeatEffect(): void {
  const [location] = useLocation();
  const { user } = useAuth();
  const heartbeat = useActivityHeartbeat();
  const heartbeatRef = useRef(heartbeat.mutate);
  heartbeatRef.current = heartbeat.mutate;

  useEffect(() => {
    if (!user) return;
    heartbeatRef.current({ data: { path: location } });
    const interval = setInterval(() => {
      heartbeatRef.current({ data: { path: location } });
    }, HEARTBEAT_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [user, location]);
}
