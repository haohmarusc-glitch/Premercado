import { createContext, useContext, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  useGetAuthMe,
  getGetAuthMeQueryKey,
  useAuthLogin,
  useAuthSignup,
  useAuthLogout,
  useAuthClaimSeedAccount,
} from "@workspace/api-client-react";

interface AuthUser {
  id: number;
  email: string;
}

interface AuthContextValue {
  user: AuthUser | null;
  isLoading: boolean;
  login: (email: string, password: string) => Promise<void>;
  signup: (email: string, password: string) => Promise<void>;
  claimSeedAccount: (email: string, newPassword: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();

  const { data, isLoading } = useGetAuthMe({
    query: { queryKey: getGetAuthMeQueryKey() },
  });

  // Depois de qualquer mudança de sessão, invalida tudo -- não só o /auth/me:
  // portfolio e alertas agora são por usuário, então trocar de conta precisa
  // refazer essas queries também, não só a de "quem sou eu".
  const onSessionChange = () => queryClient.invalidateQueries();

  const loginMutation = useAuthLogin();
  const signupMutation = useAuthSignup();
  const logoutMutation = useAuthLogout();
  const claimMutation = useAuthClaimSeedAccount();

  const login = async (email: string, password: string) => {
    await loginMutation.mutateAsync({ data: { email, password } });
    await onSessionChange();
  };

  const signup = async (email: string, password: string) => {
    await signupMutation.mutateAsync({ data: { email, password } });
    await onSessionChange();
  };

  const claimSeedAccount = async (email: string, newPassword: string) => {
    await claimMutation.mutateAsync({ data: { email, newPassword } });
    await onSessionChange();
  };

  const logout = async () => {
    await logoutMutation.mutateAsync();
    await onSessionChange();
  };

  return (
    <AuthContext.Provider
      value={{ user: data?.user ?? null, isLoading, login, signup, claimSeedAccount, logout }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth deve ser usado dentro de AuthProvider");
  return ctx;
}
