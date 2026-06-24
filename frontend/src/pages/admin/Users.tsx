import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { UserPlus, Shield, Eye, User as UserIcon, Mail, KeyRound, Trash2, Power, Pencil } from "lucide-react";
import {
  adminCreateUser,
  adminDeleteUser,
  adminListUsers,
  adminUpdateUser,
  type AuthModeT,
  type AuthRole,
  type AuthUser,
} from "../../api/client";
import { avatarColor, cn, initials } from "../../lib/utils";
import { Badge, Button, Card, EmptyState, Field, Input, Modal, PageHeader, Select, Skeleton } from "../../components/ui";
import { useToast } from "../../components/toast";
import { useAuth } from "../../lib/auth";

type Form = { username: string; password: string; email: string; role: AuthRole; auth_mode: AuthModeT };
const EMPTY: Form = { username: "", password: "", email: "", role: "user", auth_mode: "otp" };

function RoleBadge({ role }: { role: AuthRole }) {
  if (role === "admin") return <Badge tone="violet"><Shield className="h-3 w-3" /> admin</Badge>;
  if (role === "viewer") return <Badge tone="amber"><Eye className="h-3 w-3" /> viewer</Badge>;
  return <Badge tone="slate">user</Badge>;
}

export default function AdminUsers() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const { user: me } = useAuth();
  const { data: users, isLoading } = useQuery({ queryKey: ["admin-users"], queryFn: adminListUsers });
  const [modal, setModal] = useState<{ mode: "create" } | { mode: "edit"; user: AuthUser } | null>(null);
  const [form, setForm] = useState<Form>(EMPTY);

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
    onSuccess: () => { toast("success", "User created"); setModal(null); invalidate(); },
    onError: (e: any) => toast("error", "Could not create user", e?.response?.data?.detail ?? String(e)),
  });

  const updateMut = useMutation({
    mutationFn: (id: string) =>
      adminUpdateUser(id, {
        email: form.email || null,
        role: form.role,
        auth_mode: form.auth_mode,
        ...(form.password ? { password: form.password } : {}),
      }),
    onSuccess: () => { toast("success", "User updated"); setModal(null); invalidate(); },
    onError: (e: any) => toast("error", "Could not update user", e?.response?.data?.detail ?? String(e)),
  });

  const toggleActive = useMutation({
    mutationFn: (u: AuthUser) => adminUpdateUser(u.id, { is_active: !u.is_active }),
    onSuccess: () => invalidate(),
    onError: (e: any) => toast("error", "Could not update", e?.response?.data?.detail ?? String(e)),
  });

  const deleteMut = useMutation({
    mutationFn: adminDeleteUser,
    onSuccess: () => { toast("info", "User removed"); invalidate(); },
    onError: (e: any) => toast("error", "Delete failed", e?.response?.data?.detail ?? String(e)),
  });

  const openCreate = () => { setForm({ ...EMPTY }); setModal({ mode: "create" }); };
  const openEdit = (u: AuthUser) => {
    setForm({ username: u.username, password: "", email: u.email ?? "", role: u.role, auth_mode: u.auth_mode });
    setModal({ mode: "edit", user: u });
  };
  const isEdit = modal?.mode === "edit";

  return (
    <div className="animate-fade-up">
      <PageHeader
        title="Users & access"
        subtitle="Create users and edit their role, email and 2-factor method. Every role (incl. admin) signs in with 2FA."
        actions={<Button onClick={openCreate}><UserPlus className="h-4 w-4" /> Add user</Button>}
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
                  <td className="px-3 py-2.5"><RoleBadge role={u.role} /></td>
                  <td className="px-3 py-2.5 text-slate-600">
                    <span className="flex items-center gap-1 text-xs"><Mail className="h-3 w-3 text-slate-400" />{u.email ?? "—"}</span>
                  </td>
                  <td className="px-3 py-2.5">
                    <span className="text-xs font-medium uppercase text-slate-600">{u.auth_mode}</span>
                  </td>
                  <td className="px-3 py-2.5">
                    {u.is_active ? <Badge tone="green">active</Badge> : <Badge tone="rose">disabled</Badge>}
                  </td>
                  <td className="px-3 py-2.5">
                    <div className="flex justify-end gap-1">
                      <button onClick={() => openEdit(u)} className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-brand-600" title="Edit">
                        <Pencil className="h-4 w-4" />
                      </button>
                      <button onClick={() => toggleActive.mutate(u)} className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-amber-600" title="Enable/disable">
                        <Power className="h-4 w-4" />
                      </button>
                      {u.id !== me?.id && (
                        <button onClick={() => { if (confirm(`Delete ${u.username}?`)) deleteMut.mutate(u.id); }} className="rounded-lg p-1.5 text-slate-400 hover:bg-rose-50 hover:text-rose-500" title="Delete">
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

      <Modal
        open={!!modal}
        onClose={() => setModal(null)}
        title={isEdit ? `Edit ${modal && "user" in modal ? modal.user.username : ""}` : "Add user"}
        subtitle={isEdit
          ? "Update role, email, 2-factor method, or set a new password."
          : "OTP users need an email for code delivery; admins default to CAPTCHA."}
      >
        <div className="grid grid-cols-2 gap-3">
          <Field label="Username">
            <Input value={form.username} disabled={isEdit}
                   onChange={(e) => setForm({ ...form, username: e.target.value })} />
          </Field>
          <Field label={isEdit ? "New password (blank = keep)" : "Password"}>
            <Input type="password" value={form.password}
                   onChange={(e) => setForm({ ...form, password: e.target.value })}
                   placeholder={isEdit ? "leave blank to keep" : "min 8 characters"} />
          </Field>
          <Field label="Email (for OTP)">
            <Input value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} placeholder="user@company.com" />
          </Field>
          <Field label="Role">
            <Select className="w-full" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as AuthRole })}>
              <option value="admin">admin — full access</option>
              <option value="user">user — read &amp; write</option>
              <option value="viewer">viewer — read-only</option>
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
          <Button variant="secondary" onClick={() => setModal(null)}>Cancel</Button>
          <Button
            disabled={
              !form.username ||
              (!isEdit && !form.password) ||
              createMut.isPending || updateMut.isPending
            }
            onClick={() => (isEdit && modal && "user" in modal ? updateMut.mutate(modal.user.id) : createMut.mutate())}
          >
            <KeyRound className="h-4 w-4" /> {isEdit ? "Save changes" : "Create user"}
          </Button>
        </div>
      </Modal>
    </div>
  );
}
