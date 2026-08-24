import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { UserPlus, Power, Trash2, KeyRound, Users } from "lucide-react";
import {
  adminCreatePortalUser,
  adminDeletePortalUser,
  adminListPortalUsers,
  adminUpdatePortalUser,
  fetchEmployeeMatcher,
  type PortalUser,
} from "../../api/client";
import { Badge, Button, Card, EmptyState, Field, Input, Modal, PageHeader, Skeleton } from "../../components/ui";
import { useToast } from "../../components/toast";

type Form = { username: string; password: string; employee_pk: string };
const EMPTY: Form = { username: "", password: "", employee_pk: "" };

/** "John Doe" -> "john.doe" — a reasonable starting username, never forced:
 *  only pre-fills an EMPTY username field, the admin can still change it. */
function suggestUsername(name: string): string {
  return name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, ".")
    .replace(/^\.+|\.+$/g, "");
}

export default function AdminPortalUsers() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const { data: users, isLoading } = useQuery({ queryKey: ["admin-portal-users"], queryFn: adminListPortalUsers });
  const { data: employees } = useQuery({ queryKey: ["employee-matcher"], queryFn: fetchEmployeeMatcher });
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<Form>(EMPTY);
  const [employeeQuery, setEmployeeQuery] = useState("");

  const invalidate = () => qc.invalidateQueries({ queryKey: ["admin-portal-users"] });

  const matchingEmployees = useMemo(() => {
    const q = employeeQuery.trim().toLowerCase();
    if (!q) return [];
    return (employees || [])
      .filter((e) => e.name.toLowerCase().includes(q) || e.employee_id.toLowerCase().includes(q))
      .slice(0, 8);
  }, [employees, employeeQuery]);
  const selectedEmployee = useMemo(
    () => (employees || []).find((e) => e.id === form.employee_pk) || null,
    [employees, form.employee_pk]
  );

  const closeModal = () => {
    setOpen(false);
    setForm(EMPTY);
    setEmployeeQuery("");
  };

  const createMut = useMutation({
    mutationFn: () =>
      adminCreatePortalUser({ username: form.username, password: form.password, employee_pk: form.employee_pk }),
    onSuccess: () => {
      toast("success", "Employee account created");
      closeModal();
      invalidate();
    },
    onError: (e: any) => toast("error", "Could not create account", e?.response?.data?.detail ?? String(e)),
  });

  const toggleActive = (u: PortalUser) =>
    adminUpdatePortalUser(u.id, { is_active: !u.is_active }).then(invalidate);

  const remove = (u: PortalUser) => {
    if (!confirm(`Delete the portal account "${u.username}"? This cannot be undone.`)) return;
    adminDeletePortalUser(u.id).then(invalidate);
  };

  return (
    <div className="mx-auto max-w-4xl animate-fade-up">
      <PageHeader
        title="Portal accounts"
        subtitle="Employee logins for the self-service timesheet portal."
        actions={
          <Button onClick={() => setOpen(true)}>
            <UserPlus className="h-4 w-4" />
            New employee account
          </Button>
        }
      />

      <Card className="p-2">
        {isLoading ? (
          <div className="space-y-2 p-4">
            {[1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        ) : !users?.length ? (
          <EmptyState icon={<Users className="h-7 w-7" />} title="No employee accounts yet" />
        ) : (
          <div className="divide-y divide-slate-100">
            {users.map((u) => (
              <div key={u.id} className="flex items-center justify-between gap-4 px-4 py-3">
                <div>
                  <p className="flex items-center gap-2 text-sm font-semibold text-slate-800">
                    {u.username}
                    {!u.is_active && <Badge tone="warning">inactive</Badge>}
                  </p>
                  <p className="mt-0.5 text-xs text-slate-500">
                    {u.employee_name || "?"} ({u.employee_id || "?"})
                  </p>
                </div>
                <div className="flex items-center gap-1">
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      const pw = prompt(`New password for ${u.username}:`);
                      if (pw) adminUpdatePortalUser(u.id, { password: pw }).then(invalidate);
                    }}
                  >
                    <KeyRound className="h-3.5 w-3.5" />
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => toggleActive(u)}>
                    <Power className="h-3.5 w-3.5" />
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => remove(u)}>
                    <Trash2 className="h-3.5 w-3.5 text-rose-500" />
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      <Modal
        open={open}
        onClose={closeModal}
        title="New employee account"
        subtitle="Search the Employee Matcher to link this login to one person."
      >
        <div className="space-y-4">
          <Field label="Username">
            <Input value={form.username} onChange={(e) => setForm((f) => ({ ...f, username: e.target.value }))} />
          </Field>
          <Field label="Password">
            <Input
              type="text"
              value={form.password}
              onChange={(e) => setForm((f) => ({ ...f, password: e.target.value }))}
            />
          </Field>
          {/* NOTE: extra content (the suggestion list, the manager readout)
              must live OUTSIDE <Field> — Field clones a SINGLE child via
              Children.only, so giving it more than one sibling throws at
              render time. */}
          <div>
            <Field label="Employee">
              <Input
                placeholder="Search by name or employee ID…"
                value={employeeQuery}
                onChange={(e) => {
                  setEmployeeQuery(e.target.value);
                  setForm((f) => ({ ...f, employee_pk: "" }));
                }}
              />
            </Field>
            {matchingEmployees.length > 0 && !form.employee_pk && (
              <div className="mt-1 max-h-40 overflow-y-auto rounded-lg border border-slate-200 bg-white shadow-xs">
                {matchingEmployees.map((e) => (
                  <button
                    key={e.id}
                    type="button"
                    className="block w-full px-3 py-1.5 text-left text-sm hover:bg-slate-50"
                    onClick={() => {
                      setForm((f) => ({
                        ...f,
                        employee_pk: e.id,
                        // Pre-fill only if the admin hasn't typed one yet —
                        // never clobber something they already entered.
                        username: f.username || suggestUsername(e.name),
                      }));
                      setEmployeeQuery(`${e.name} (${e.employee_id})`);
                    }}
                  >
                    {e.name} <span className="text-slate-400">({e.employee_id})</span>
                  </button>
                ))}
              </div>
            )}
            {selectedEmployee && (
              <p className="mt-1.5 text-xs text-slate-500">
                Manager: <span className="font-medium text-slate-700">{selectedEmployee.account_manager || "—"}</span>
              </p>
            )}
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="secondary" onClick={closeModal}>
              Cancel
            </Button>
            <Button
              disabled={!form.username || !form.password || !form.employee_pk || createMut.isPending}
              onClick={() => createMut.mutate()}
            >
              Create account
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
