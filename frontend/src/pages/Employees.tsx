import { useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Users, Plus, Pencil, Trash2, FileSpreadsheet, MapPin, Copy } from "lucide-react";
import {
  createEmployee,
  deleteEmployee,
  fetchEmployeeMatcher,
  importEmployees,
  updateEmployee,
  type Employee,
  type EmployeeInput,
  type ImportSummary,
} from "../api/client";
import { locationBadgeTone } from "../lib/theme";
import { avatarColor, cn, initials } from "../lib/utils";
import { Badge, Button, Card, EmptyState, Field, Input, Modal, PageHeader, Select, Skeleton } from "../components/ui";
import { useToast } from "../components/toast";

const EMPTY: EmployeeInput = {
  employee_id: "",
  name: "",
  dco_number: null,
  account_manager: null,
  employee_email_id: null,
  project: null,
  contact_no: null,
  location: null,
  all_emails: null,
};

export default function EmployeesPage() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const fileRef = useRef<HTMLInputElement>(null);
  const [q, setQ] = useState("");
  const [loc, setLoc] = useState("");
  const [modal, setModal] = useState<{ mode: "create" } | { mode: "edit"; row: Employee } | null>(null);
  const [form, setForm] = useState<EmployeeInput>(EMPTY);
  const [importResult, setImportResult] = useState<ImportSummary | null>(null);

  const { data: rows, isLoading } = useQuery({
    queryKey: ["employee-matcher"],
    queryFn: fetchEmployeeMatcher,
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: ["employee-matcher"] });

  const createMut = useMutation({
    mutationFn: () => createEmployee(form),
    onSuccess: () => {
      toast("success", "Employee added");
      setModal(null);
      invalidate();
    },
    onError: (e: any) => toast("error", "Could not add", e?.response?.data?.detail ?? String(e)),
  });
  const updateMut = useMutation({
    mutationFn: (pk: string) => updateEmployee(pk, form),
    onSuccess: () => {
      toast("success", "Employee updated");
      setModal(null);
      invalidate();
    },
    onError: (e: any) => toast("error", "Could not update", e?.response?.data?.detail ?? String(e)),
  });
  const deleteMut = useMutation({
    mutationFn: deleteEmployee,
    onSuccess: () => {
      toast("info", "Employee removed");
      invalidate();
    },
  });
  const importMut = useMutation({
    mutationFn: importEmployees,
    onSuccess: (s) => {
      setImportResult(s);
      toast("success", "Import finished", `${s.inserted} inserted · ${s.updated} updated · ${s.skipped} skipped`);
      invalidate();
    },
    onError: (e: any) => toast("error", "Import failed", e?.response?.data?.detail ?? String(e)),
  });

  // employee_ids that exist more than once (AUH + DXB share IDs)
  const sharedIds = useMemo(() => {
    const count: Record<string, number> = {};
    rows?.forEach((r) => (count[r.employee_id] = (count[r.employee_id] ?? 0) + 1));
    return new Set(Object.keys(count).filter((k) => count[k]! > 1));
  }, [rows]);

  const visible = useMemo(
    () =>
      (rows ?? []).filter(
        (r) =>
          (!loc || r.location === loc) &&
          (!q ||
            r.name.toLowerCase().includes(q.toLowerCase()) ||
            r.employee_id.toLowerCase().includes(q.toLowerCase()) ||
            (r.account_manager ?? "").toLowerCase().includes(q.toLowerCase()))
      ),
    [rows, q, loc]
  );

  const openCreate = () => {
    setForm(EMPTY);
    setModal({ mode: "create" });
  };
  const openEdit = (row: Employee) => {
    const { id: _id, ...rest } = row;
    setForm(rest);
    setModal({ mode: "edit", row });
  };

  return (
    <div className="animate-fade-up">
      <PageHeader
        title="Employee matcher"
        subtitle="The authoritative list extracted timesheets are matched against. The same ID can exist in both AUH and DXB — the name tells them apart."
        actions={
          <>
            <input
              ref={fileRef}
              id="employee-import"
              name="employee-import"
              type="file"
              accept=".xlsx"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) importMut.mutate(f);
                e.target.value = "";
              }}
            />
            <Button variant="secondary" onClick={() => fileRef.current?.click()} disabled={importMut.isPending}>
              <FileSpreadsheet className="h-4 w-4" />
              {importMut.isPending ? "Importing…" : "Import Excel"}
            </Button>
            <Button onClick={openCreate}>
              <Plus className="h-4 w-4" /> Add employee
            </Button>
          </>
        }
      />

      <Card>
        <div className="flex flex-wrap items-center gap-3 border-b border-slate-100 px-5 py-3.5">
          <input
            id="employee-search"
            name="employee-search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search name, ID or manager…"
            className="w-72 rounded-lg border border-slate-200 bg-slate-50 px-3 py-1.5 text-sm placeholder:text-slate-400 focus:border-brand-400 focus:bg-white focus:outline-none"
          />
          <Select
            id="employee-location-filter"
            name="employee-location-filter"
            value={loc}
            onChange={(e) => setLoc(e.target.value)}
            className="py-1.5 text-xs"
          >
            <option value="">All locations</option>
            <option value="DXB">DXB</option>
            <option value="AUH">AUH</option>
          </Select>
          <p className="ml-auto text-xs text-slate-400">
            {visible.length} of {rows?.length ?? 0}
          </p>
        </div>

        {isLoading ? (
          <div className="space-y-2 p-6">
            <Skeleton className="h-12" />
            <Skeleton className="h-12" />
          </div>
        ) : visible.length === 0 ? (
          <EmptyState icon={<Users className="h-6 w-6" />} title="No employees" detail="Add one manually or import your Excel (DXB + AUH sheets)." />
        ) : (
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-slate-100 text-[11px] uppercase tracking-wide text-slate-400">
                <th className="px-5 py-2.5 font-semibold">Employee</th>
                <th className="px-3 py-2.5 font-semibold">ID</th>
                <th className="px-3 py-2.5 font-semibold">Location</th>
                <th className="hidden px-3 py-2.5 font-semibold lg:table-cell">Manager</th>
                <th className="hidden px-3 py-2.5 font-semibold xl:table-cell">Project</th>
                <th className="hidden px-3 py-2.5 font-semibold xl:table-cell">Email</th>
                <th className="px-3 py-2.5" />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {visible.map((r) => (
                <tr key={r.id} className="transition-colors hover:bg-slate-50">
                  <td className="px-5 py-2.5">
                    <div className="flex items-center gap-2.5">
                      <span
                        className={cn(
                          "flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-[11px] font-bold",
                          avatarColor(r.name)
                        )}
                      >
                        {initials(r.name)}
                      </span>
                      <span className="font-semibold text-slate-800">{r.name}</span>
                    </div>
                  </td>
                  <td className="px-3 py-2.5">
                    <span className="flex items-center gap-1.5 font-mono text-xs text-slate-600">
                      {r.employee_id}
                      {sharedIds.has(r.employee_id) && (
                        <span title="This ID exists in both teams — matching uses ID + name">
                          <Copy className="h-3.5 w-3.5 text-amber-500" />
                        </span>
                      )}
                    </span>
                  </td>
                  <td className="px-3 py-2.5">
                    {r.location ? (
                      <Badge tone={locationBadgeTone(r.location)}>
                        <MapPin className="h-3 w-3" /> {r.location}
                      </Badge>
                    ) : (
                      <span className="text-xs text-slate-300">—</span>
                    )}
                  </td>
                  <td className="hidden px-3 py-2.5 text-slate-600 lg:table-cell">{r.account_manager ?? "—"}</td>
                  <td className="hidden px-3 py-2.5 text-xs text-slate-500 xl:table-cell">{r.project ?? "—"}</td>
                  <td className="hidden max-w-[200px] truncate px-3 py-2.5 text-xs text-slate-500 xl:table-cell">
                    {r.employee_email_id ?? "—"}
                  </td>
                  <td className="px-3 py-2.5">
                    <div className="flex justify-end gap-1">
                      <button
                        onClick={() => openEdit(r)}
                        className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-brand-600"
                      >
                        <Pencil className="h-4 w-4" />
                      </button>
                      <button
                        onClick={() => {
                          if (confirm(`Remove ${r.name} (${r.employee_id})?`)) deleteMut.mutate(r.id);
                        }}
                        className="rounded-lg p-1.5 text-slate-400 hover:bg-rose-50 hover:text-rose-500"
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      {/* -------- add / edit modal -------- */}
      <Modal
        open={!!modal}
        onClose={() => setModal(null)}
        title={modal?.mode === "edit" ? "Edit employee" : "Add employee"}
        subtitle="Identity is employee ID + name — the same ID may exist once per team."
      >
        <div className="grid grid-cols-2 gap-3">
          <Field label="Employee ID" name="employee_id">
            <Input
              value={form.employee_id}
              onChange={(e) => setForm({ ...form, employee_id: e.target.value })}
              placeholder="EMP-1001"
            />
          </Field>
          <Field label="Full name" name="name">
            <Input
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="Jane Doe"
            />
          </Field>
          <Field label="Location" name="location">
            <Select
              className="w-full"
              value={form.location ?? ""}
              onChange={(e) => setForm({ ...form, location: e.target.value || null })}
            >
              <option value="">—</option>
              <option value="DXB">DXB</option>
              <option value="AUH">AUH</option>
            </Select>
          </Field>
          <Field label="Account manager" name="account_manager">
            <Input
              value={form.account_manager ?? ""}
              onChange={(e) => setForm({ ...form, account_manager: e.target.value || null })}
            />
          </Field>
          <Field label="DCO number" name="dco_number">
            <Input
              value={form.dco_number ?? ""}
              onChange={(e) => setForm({ ...form, dco_number: e.target.value || null })}
            />
          </Field>
          <Field label="Project" name="project">
            <Input
              value={form.project ?? ""}
              onChange={(e) => setForm({ ...form, project: e.target.value || null })}
            />
          </Field>
          <Field label="Email" name="employee_email_id">
            <Input
              value={form.employee_email_id ?? ""}
              onChange={(e) => setForm({ ...form, employee_email_id: e.target.value || null })}
            />
          </Field>
          <Field label="Contact no." name="contact_no">
            <Input
              value={form.contact_no ?? ""}
              onChange={(e) => setForm({ ...form, contact_no: e.target.value || null })}
            />
          </Field>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="secondary" onClick={() => setModal(null)}>
            Cancel
          </Button>
          <Button
            disabled={!form.employee_id.trim() || !form.name.trim() || createMut.isPending || updateMut.isPending}
            onClick={() =>
              modal?.mode === "edit" ? updateMut.mutate(modal.row.id) : createMut.mutate()
            }
          >
            {modal?.mode === "edit" ? "Save changes" : "Add employee"}
          </Button>
        </div>
      </Modal>

      {/* -------- import result modal -------- */}
      <Modal
        open={!!importResult}
        onClose={() => setImportResult(null)}
        title="Import summary"
        wide
      >
        {importResult && (
          <>
            <div className="mb-4 grid grid-cols-3 gap-3 text-center">
              <div className="rounded-xl bg-emerald-50 p-3">
                <p className="text-2xl font-bold text-emerald-600">{importResult.inserted}</p>
                <p className="text-xs font-medium text-emerald-700">Inserted</p>
              </div>
              <div className="rounded-xl bg-brand-50 p-3 ring-1 ring-inset ring-brand-100">
                <p className="text-2xl font-bold text-brand-700">{importResult.updated}</p>
                <p className="text-xs font-medium text-brand-800">Updated</p>
              </div>
              <div className="rounded-xl bg-amber-50 p-3">
                <p className="text-2xl font-bold text-amber-600">{importResult.skipped}</p>
                <p className="text-xs font-medium text-amber-700">Skipped</p>
              </div>
            </div>
            {(importResult.skipped_details?.length ?? 0) > 0 && (
              <div className="max-h-64 overflow-y-auto rounded-lg border border-slate-200">
                <table className="w-full text-left text-xs">
                  <thead className="sticky top-0 bg-slate-50 text-slate-500">
                    <tr>
                      <th className="px-3 py-2 font-semibold">Sheet</th>
                      <th className="px-3 py-2 font-semibold">Row</th>
                      <th className="px-3 py-2 font-semibold">ID</th>
                      <th className="px-3 py-2 font-semibold">Name</th>
                      <th className="px-3 py-2 font-semibold">Reason</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {importResult.skipped_details!.map((s, i) => (
                      <tr key={i}>
                        <td className="px-3 py-1.5">{s.sheet}</td>
                        <td className="px-3 py-1.5">{s.row}</td>
                        <td className="px-3 py-1.5 font-mono">{s.id}</td>
                        <td className="px-3 py-1.5">{s.name}</td>
                        <td className="px-3 py-1.5 text-amber-700">{s.reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </Modal>
    </div>
  );
}
