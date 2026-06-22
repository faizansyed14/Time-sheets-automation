import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { UserPlus, Shield, User as UserIcon, Mail, KeyRound, Trash2, Power } from "lucide-react";
import {
  adminCreateUser,
  adminDeleteUser,
  adminListUsers,
  adminSwitchAuthMode,
  adminUpdateUser,
  type AuthModeT,
  type AuthRole,
  type AuthUser,
} from "../../api/client";
import { avatarColor, cn, initials } from "../../lib/utils";
import { Badge, Button, Card, EmptyState, Field, Input, Modal, PageHeader, Select, Skeleton } from "../../components/ui";
import { useToast } from "../../components/toast";
import { useAuth } from "../../lib/auth";

const EMPTY = { username: "", password: "", email: "", role: "user" as AuthRole, auth_mode: "otp" as AuthModeT };

export default function AdminUsers() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const { user: me } = useAuth();
  const { data: users, isLoading } = useQuery({ queryKey: ["admin-users"], queryFn: adminListUsers });
  const [createOpen, setCreateOpen] = useState(false);
  const [form, setForm] = useState(EMPTY);

  const invalidate = () => qc.invalidateQueries({ queryKey: ["admin-users"] });

  const createMut = useMutation({
    mutationFn: () =>
      adminCreateUser({
        username: form.username,
        password: form.password,
        email: form.email || null,
        role: form.role,
        auth_mode: form.auth_mode,
      }),
    onSuccess: () => {
      toast("success", "User created");
      setCreateOpen(false);
      setForm(EMPTY);
      invalidate();
    },
    onError: (e: any) => toast("error", "Could not create user", e?.response?.data?.detail ?? String(e)),
  });

  const switchMut = useMutation({
    mutationFn: ({ id, mode }: { id: string; mode: AuthModeT }) => adminSwitchAuthMode(id, mode),
    onSuccess: (u) => { toast("success", `Switched ${u.username} to ${u.auth_mode}`); invalidate(); },
    onError: (e: any) => toast("error", "Could not switch", e?.response?.data?.detail ?? String(e)),
  });

  const toggleActive = useMutation({
    mutationFn: (u: AuthUser) => adminUpdateUser(u.id, { is_active: !u.is_active }),
    onSuccess: () => invalidate(),
  });

  const deleteMut = useMutation({
    mutationFn: adminDeleteUser,
    onSuccess: () => { toast("info", "User removed"); invalidate(); },
    onError: (e: any) => toast("error", "Delete failed", e?.response?.data?.detail ?? String(e)),
  });

  return (
    <div className="animate-fade-up">
      <PageHeader
        title="Users & access"
        subtitle="Create users, assign an email for OTP delivery, and switch each user between OTP and CAPTCHA."
        actions={<Button onClick={() => { setForm(EMPTY); setCreateOpen(true); }}><UserPlus className="h-4 w-4" /> Add user</Button>}
      />

      <Card>
        {isLoading ? (
          <div className="space-y-2 p-6"><Skeleton className="h-12" /><Skeleton className="h-12" /></div>
        ) : !users?.length ? (
          <EmptyState icon={<UserIcon className="h-6 w-6" />} title="No users" />
        ) : (
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-slate-100 text-[11px] uppercase tracking-wide text-slate-400">
                <th className="px-5 py-2.5 font-semibold">User</th>
                <th className="px-3 py-2.5 font-semibold">Role</th>
                <th className="px-3 py-2.5 font-semibold">Email (OTP)</th>
                <th className="px-3 py-2.5 font-semibold">2-factor</th>
                <th className="px-3 py-2.5 font-semibold">Status</th>
                <th className="px-3 py-2.5" />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {users.map((u) => (
                <tr key={u.id} className="hover:bg-slate-50">
                  <td className="px-5 py-2.5">
                    <div className="flex items-center gap-2.5">
                      <span className={cn("flex h-8 w-8 items-center justify-center rounded-full text-[11px] font-bold", avatarColor(u.username))}>
                        {initials(u.username)}
                      </span>
                      <span className="font-semibold text-slate-800">{u.username}</span>
                    </div>
                  </td>
                  <td className="px-3 py-2.5">
                    {u.role === "admin"
                      ? <Badge tone="violet"><Shield className="h-3 w-3" /> admin</Badge>
                      : <Badge tone="slate">user</Badge>}
                  </td>
                  <td className="px-3 py-2.5 text-slate-600">
                    <span className="flex items-center gap-1 text-xs"><Mail className="h-3 w-3 text-slate-400" />{u.email ?? "—"}</span>
                  </td>
                  <td className="px-3 py-2.5">
                    {u.role === "admin" ? (
                      <span className="text-xs text-slate-400">bypasses 2FA</span>
                    ) : (
                      <Select
                        value={u.auth_mode}
                        onChange={(e) => switchMut.mutate({ id: u.id, mode: e.target.value as AuthModeT })}
                        className="py-1 text-xs"
                      >
                        <option value="otp">OTP (email)</option>
                        <option value="captcha">CAPTCHA</option>
                      </Select>
                    )}
                  </td>
                  <td className="px-3 py-2.5">
                    {u.is_active ? <Badge tone="green">active</Badge> : <Badge tone="rose">disabled</Badge>}
                  </td>
                  <td className="px-3 py-2.5">
                    <div className="flex justify-end gap-1">
                      <button onClick={() => toggleActive.mutate(u)} className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-amber-600" title="Enable/disable">
                        <Power className="h-4 w-4" />
                      </button>
                      {u.id !== me?.id && (
                        <button onClick={() => { if (confirm(`Delete ${u.username}?`)) deleteMut.mutate(u.id); }} className="rounded-lg p-1.5 text-slate-400 hover:bg-rose-50 hover:text-rose-500">
                          <Trash2 className="h-4 w-4" />
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Modal open={createOpen} onClose={() => setCreateOpen(false)} title="Add user"
        subtitle="OTP users need an email for code delivery; admins bypass 2FA.">
        <div className="grid grid-cols-2 gap-3">
          <Field label="Username"><Input value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} /></Field>
          <Field label="Password"><Input type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} /></Field>
          <Field label="Email (for OTP)"><Input value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} placeholder="user@company.com" /></Field>
          <Field label="Role">
            <Select className="w-full" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as AuthRole })}>
              <option value="user">user</option>
              <option value="admin">admin</option>
            </Select>
          </Field>
          <Field label="2-factor mode">
            <Select className="w-full" value={form.auth_mode} onChange={(e) => setForm({ ...form, auth_mode: e.target.value as AuthModeT })}>
              <option value="otp">OTP (email)</option>
              <option value="captcha">CAPTCHA</option>
            </Select>
          </Field>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="secondary" onClick={() => setCreateOpen(false)}>Cancel</Button>
          <Button disabled={!form.username || !form.password || createMut.isPending} onClick={() => createMut.mutate()}>
            <KeyRound className="h-4 w-4" /> Create user
          </Button>
        </div>
      </Modal>
    </div>
  );
}
