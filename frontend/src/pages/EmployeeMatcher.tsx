import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createEmployee,
  deleteEmployee,
  fetchEmployeeMatcher,
  updateEmployee,
  type Employee,
  type EmployeeInput,
} from "../api/client";
import { Spinner } from "../components/ui";

const EMPTY: EmployeeInput = {
  employee_id: "",
  name: "",
  dco_number: "",
  account_manager: "",
  employee_email_id: "",
};

export default function EmployeeMatcher() {
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const [editing, setEditing] = useState<Employee | null>(null);
  const [creating, setCreating] = useState(false);

  const { data, isLoading } = useQuery({ queryKey: ["employeeMatcher"], queryFn: fetchEmployeeMatcher });

  const rows = useMemo(() => {
    let r = data ?? [];
    if (q.trim()) {
      const t = q.toLowerCase();
      r = r.filter(
        (x) =>
          x.name.toLowerCase().includes(t) ||
          x.employee_id.toLowerCase().includes(t) ||
          (x.account_manager ?? "").toLowerCase().includes(t) ||
          (x.dco_number ?? "").toLowerCase().includes(t)
      );
    }
    return r;
  }, [data, q]);

  const del = useMutation({
    mutationFn: (id: string) => deleteEmployee(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["employeeMatcher"] }),
  });

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-ink">Employee Matcher</h1>
          <p className="mt-1 text-sm text-slate-500">
            <span className="font-mono">all_employee_data</span> — extracted timesheets are matched against this list.
          </p>
        </div>
        <button
          onClick={() => setCreating(true)}
          className="rounded-lg bg-petrol-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-petrol-700"
        >
          + Add employee
        </button>
      </div>

      <div className="relative max-w-sm">
        <SearchIcon />
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search name, ID, DCO, manager…"
          className="w-full rounded-lg border border-slate-200 bg-white py-2.5 pl-9 pr-3 text-sm shadow-panel focus:border-petrol-500 focus:outline-none"
        />
      </div>

      {isLoading ? (
        <Spinner />
      ) : (
        <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-panel">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-slate-200 bg-slate-50/80 text-xs uppercase tracking-wide text-slate-500">
                <th className="px-5 py-3 font-semibold">Employee ID</th>
                <th className="px-5 py-3 font-semibold">Name</th>
                <th className="px-5 py-3 font-semibold">DCO</th>
                <th className="px-5 py-3 font-semibold">Account Manager</th>
                <th className="px-5 py-3 font-semibold">Email</th>
                <th className="px-5 py-3"></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((e) => (
                <tr key={e.id} className="border-b border-slate-100 last:border-0 hover:bg-slate-50/60">
                  <td className="px-5 py-3 font-mono text-xs text-slate-600">{e.employee_id}</td>
                  <td className="px-5 py-3 font-medium text-ink">{e.name}</td>
                  <td className="px-5 py-3 font-mono text-xs text-slate-500">{e.dco_number ?? "—"}</td>
                  <td className="px-5 py-3 text-slate-600">{e.account_manager ?? "—"}</td>
                  <td className="px-5 py-3 text-slate-500">{e.employee_email_id ?? "—"}</td>
                  <td className="px-5 py-3">
                    <div className="flex justify-end gap-1.5">
                      <button
                        onClick={() => setEditing(e)}
                        className="rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs font-medium text-slate-600 hover:border-petrol-300 hover:text-petrol-700"
                      >
                        Edit
                      </button>
                      <button
                        onClick={() => {
                          if (confirm(`Delete ${e.name}?`)) del.mutate(e.id);
                        }}
                        className="rounded-lg border border-rose-200 px-2.5 py-1.5 text-xs font-medium text-rose-600 hover:bg-rose-50"
                      >
                        Delete
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-5 py-10 text-center text-sm text-slate-400">
                    No employees match.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {(creating || editing) && (
        <EmployeeModal
          initial={editing ?? EMPTY}
          isEdit={!!editing}
          onClose={() => {
            setCreating(false);
            setEditing(null);
          }}
        />
      )}
    </div>
  );
}

function EmployeeModal({
  initial,
  isEdit,
  onClose,
}: {
  initial: EmployeeInput | Employee;
  isEdit: boolean;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [form, setForm] = useState<EmployeeInput>({
    employee_id: initial.employee_id,
    name: initial.name,
    dco_number: initial.dco_number ?? "",
    account_manager: initial.account_manager ?? "",
    employee_email_id: initial.employee_email_id ?? "",
  });
  const [err, setErr] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: () =>
      isEdit ? updateEmployee((initial as Employee).id, form) : createEmployee(form),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["employeeMatcher"] });
      onClose();
    },
    onError: (e: any) => setErr(e?.response?.data?.detail ?? "Save failed"),
  });

  const field = (key: keyof EmployeeInput, label: string, required = false) => (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-slate-500">
        {label} {required && <span className="text-rose-500">*</span>}
      </span>
      <input
        value={form[key] ?? ""}
        onChange={(e) => setForm({ ...form, [key]: e.target.value })}
        className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm focus:border-petrol-500 focus:outline-none"
      />
    </label>
  );

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-ink/40 p-4" onClick={onClose}>
      <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-lift" onClick={(e) => e.stopPropagation()}>
        <h2 className="text-lg font-semibold text-ink">{isEdit ? "Edit employee" : "Add employee"}</h2>
        <div className="mt-4 space-y-3">
          {field("employee_id", "Employee ID", true)}
          {field("name", "Name", true)}
          {field("dco_number", "DCO number")}
          {field("account_manager", "Account manager")}
          {field("employee_email_id", "Email")}
        </div>
        {err && <div className="mt-3 rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700">{err}</div>}
        <div className="mt-5 flex justify-end gap-2">
          <button onClick={onClose} className="rounded-lg border border-slate-200 px-4 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50">
            Cancel
          </button>
          <button
            onClick={() => {
              setErr(null);
              if (!form.employee_id.trim() || !form.name.trim()) {
                setErr("Employee ID and Name are required.");
                return;
              }
              save.mutate();
            }}
            disabled={save.isPending}
            className="rounded-lg bg-petrol-600 px-4 py-2 text-sm font-semibold text-white hover:bg-petrol-700 disabled:opacity-50"
          >
            {save.isPending ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}

function SearchIcon() {
  return (
    <svg className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" />
    </svg>
  );
}
