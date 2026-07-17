import { useState } from "react";
import {
  useListAdminUsers,
  getListAdminUsersQueryKey,
  useUpdateUserPassword,
  useDeleteAdminUser,
} from "@workspace/api-client-react";
import { useQueryClient } from "@tanstack/react-query";
import { useAuth } from "@/lib/auth";
import { useToast } from "@/hooks/use-toast";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { ShieldAlert, KeyRound, Clock, Trash2 } from "lucide-react";

function formatLastSeen(iso: string | null): string {
  if (!iso) return "nunca";
  const diffMs = Date.now() - new Date(iso).getTime();
  const diffMin = Math.floor(diffMs / 60_000);
  if (diffMin < 1) return "agora";
  if (diffMin < 60) return `há ${diffMin}m`;
  const diffH = Math.floor(diffMin / 60);
  if (diffH < 24) return `há ${diffH}h`;
  const diffD = Math.floor(diffH / 24);
  return `há ${diffD}d`;
}

export default function AdminUsersPage() {
  const { user } = useAuth();
  const { toast } = useToast();
  const qc = useQueryClient();
  const [passwordTarget, setPasswordTarget] = useState<{ id: number; email: string } | null>(null);
  const [newPassword, setNewPassword] = useState("");

  const { data: users, isLoading } = useListAdminUsers({
    query: { queryKey: getListAdminUsersQueryKey(), refetchInterval: 15_000, enabled: !!user?.isAdmin },
  });

  const updatePassword = useUpdateUserPassword();
  const deleteUser = useDeleteAdminUser();

  const handleDelete = (id: number, email: string) => {
    if (!confirm(`Excluir a conta ${email}? Isso remove permanentemente carteira, alertas, watchlist, diário e conversas dessa conta.`)) return;
    deleteUser.mutate(
      { id },
      {
        onSuccess: () => {
          qc.invalidateQueries({ queryKey: getListAdminUsersQueryKey() });
          toast({ title: "Usuário excluído", description: `${email} e todos os seus dados foram removidos.` });
        },
        onError: () => toast({ variant: "destructive", title: "Erro ao excluir usuário" }),
      },
    );
  };

  if (!user?.isAdmin) {
    return (
      <div className="border border-border rounded-lg p-12 text-center">
        <ShieldAlert className="h-8 w-8 text-muted-foreground mx-auto mb-3" />
        <p className="font-mono text-muted-foreground text-sm">Acesso restrito ao administrador.</p>
      </div>
    );
  }

  const closeDialog = () => {
    setPasswordTarget(null);
    setNewPassword("");
  };

  const savePassword = () => {
    if (!passwordTarget || newPassword.length < 8) return;
    updatePassword.mutate(
      { id: passwordTarget.id, data: { newPassword } },
      {
        onSuccess: () => {
          toast({ title: "Senha atualizada", description: `Nova senha definida para ${passwordTarget.email}.` });
          closeDialog();
        },
        onError: () => {
          toast({ title: "Erro ao atualizar senha", variant: "destructive" });
        },
      },
    );
  };

  return (
    <div>
      <div className="mb-8">
        <h1 className="text-3xl font-bold font-mono tracking-tight" data-testid="text-admin-users-title">
          USUÁRIOS
        </h1>
        <p className="text-muted-foreground font-mono text-sm mt-2">
          Contas cadastradas, status de conexão e senha
        </p>
      </div>

      {isLoading && (
        <div className="flex items-center justify-center h-40">
          <div className="text-muted-foreground font-mono text-sm animate-pulse">Carregando usuários...</div>
        </div>
      )}

      {!isLoading && users && users.length > 0 && (
        <div className="border border-border rounded-lg overflow-hidden overflow-x-auto">
          <table className="w-full text-sm font-mono">
            <thead>
              <tr className="border-b border-border bg-secondary/50">
                <th className="text-left px-4 py-3 text-xs text-muted-foreground uppercase tracking-widest">E-mail</th>
                <th className="text-left px-4 py-3 text-xs text-muted-foreground uppercase tracking-widest">Perfil</th>
                <th className="text-left px-4 py-3 text-xs text-muted-foreground uppercase tracking-widest">Status</th>
                <th className="text-left px-4 py-3 text-xs text-muted-foreground uppercase tracking-widest">Última página</th>
                <th className="text-left px-4 py-3 text-xs text-muted-foreground uppercase tracking-widest">Último acesso</th>
                <th className="text-left px-4 py-3 text-xs text-muted-foreground uppercase tracking-widest">Ações</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u, i) => (
                <tr
                  key={u.id}
                  className={`border-b border-border/50 hover:bg-secondary/30 transition-colors ${i % 2 === 0 ? "" : "bg-secondary/10"}`}
                  data-testid={`row-user-${u.id}`}
                >
                  <td className="px-4 py-3 text-foreground text-xs">{u.email}</td>
                  <td className="px-4 py-3">
                    {u.isAdmin ? (
                      <Badge variant="outline" className="font-mono text-[10px] uppercase text-primary border-primary/30 bg-primary/10">
                        admin
                      </Badge>
                    ) : (
                      <Badge variant="outline" className="font-mono text-[10px] uppercase text-muted-foreground border-border">
                        comum
                      </Badge>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <span className={`flex items-center gap-1.5 text-xs ${u.online ? "text-green-400" : "text-muted-foreground"}`}>
                      <span className={`inline-flex h-2 w-2 rounded-full ${u.online ? "bg-green-400" : "bg-muted-foreground/40"}`} />
                      {u.online ? "online" : "offline"}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-muted-foreground text-xs">{u.lastPath ?? "—"}</td>
                  <td className="px-4 py-3 text-muted-foreground text-xs">
                    <span className="flex items-center gap-1">
                      <Clock className="h-3 w-3" />
                      {formatLastSeen(u.lastSeenAt)}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        className="h-7 text-xs gap-1.5"
                        onClick={() => setPasswordTarget({ id: u.id, email: u.email })}
                        data-testid={`button-change-password-${u.id}`}
                      >
                        <KeyRound className="h-3 w-3" />
                        Trocar senha
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        className="h-7 w-7 p-0 text-muted-foreground hover:text-destructive"
                        onClick={() => handleDelete(u.id, u.email)}
                        disabled={u.id === user?.id || deleteUser.isPending}
                        title={u.id === user?.id ? "Não é possível excluir sua própria conta" : "Excluir usuário"}
                        data-testid={`button-delete-user-${u.id}`}
                      >
                        <Trash2 className="h-3 w-3" />
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <Dialog open={!!passwordTarget} onOpenChange={(o) => { if (!o) closeDialog(); }}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle className="font-mono text-sm">
              Trocar senha — {passwordTarget?.email}
            </DialogTitle>
          </DialogHeader>
          <div>
            <label className="font-mono text-xs uppercase text-muted-foreground block mb-2">Nova senha</label>
            <Input
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              type="password"
              placeholder="mínimo 8 caracteres"
              className="font-mono bg-secondary border-border"
              data-testid="input-new-password"
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={closeDialog}>Cancelar</Button>
            <Button
              onClick={savePassword}
              disabled={newPassword.length < 8 || updatePassword.isPending}
              data-testid="button-save-password"
            >
              Salvar
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
