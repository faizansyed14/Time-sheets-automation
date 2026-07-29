import { useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Users, Plus, Pencil, UserX, UserCheck, FileSpreadsheet, MapPin, Copy } from "lucide-react";
import {
  createEmployee,
  fetchEmployeeMatcher,
  importEmployees,
  previewEmployeeImport,
  setEmployeeStatus,
  updateEmployee,
  type Employee,
  type EmployeeInput,
  type ImportPlan,
  type ImportSummary,
} from "../api/client";
import { locationBadgeTone } from "../lib/theme";
import { avatarColor, cn, initials } from "../lib/utils";
import { Badge, Button, Card, EmptyState, Field, Input, Modal, PageHeader, Select, Skeleton } from "../components/ui";
import EmployeeImportPlanModal from "../components/EmployeeImportPlanModal";
import { useToast } from "../components/toast";

const EMPTY: EmployeeInput = {
  employee_id: "",
  name: "",
  aco_number: null,
  dco_number: null,
  account_manager: null,
  employee_email_id: null,
  project: null,
  contact_no: null,
  location: null,
  all_emails: null,
  active: true,
};

type StatusFilter = "active" | "inactive" | "all";

export default function EmployeesPage() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const fileRef = useRef<HTMLInputElement>(null);
  const [q, setQ] = useState("");
  const [loc, setLoc] = useState("");
  const [status, setStatus] = useState<StatusFilter>("active");
  const [modal, setModal] = useState<{ mode: "create" } | { mode: "edit"; row: Employee } | null>(null);
  const [form, setForm] = useState<EmployeeInput>(EMPTY);
  const [importResult, setImportResult] = useState<ImportSummary | null>(null);
  // Two-step import: preview the file (writes nothing) -> confirm -> apply.
  // The chosen File is held so "Update matcher" can re-send the exact same
  // bytes that produced the plan on screen.
  const [importPlan, setImportPlan] = useState<{ plan: ImportPlan; file: File } | null>(null);

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
  const statusMut = useMutation({
    mutationFn: ({ id, active }: { id: string; active: boolean }) => setEmployeeStatus(id, active),
    onSuccess: (_data, vars) => {
      toast("info", vars.active ? "Employee reactivated" : "Employee marked inactive");
      invalidate();
    },
    onError: (e: any) => toast("error", "Could not update status", e?.response?.data?.detail ?? String(e)),
  });
  const previewMut = useMutation({
    mutationFn: async (file: File) => ({ plan: await previewEmployeeImport(file), file }),
    onSuccess: (r) => setImportPlan(r),
    onError: (e: any) => toast("error", "Could not read that file", e?.response?.data?.detail ?? String(e)),
  });
  const importMut = useMutation({
    mutationFn: importEmployees,
    onSuccess: (s) => {
      setImportPlan(null);
      setImportResult(s);
      toast("success", "Import finished", `${s.inserted} added · ${s.updated} updated · ${s.skipped} skipped`);
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

  const activeCount = useMemo(() => (rows ?? []).filter((r) => r.active).length, [rows]);

  const visible = useMemo(
    () =>
      (rows ?? []).filter(
        (r) =>
          (status === "all" || (status === "active" ? r.active : !r.active)) &&
          (!loc || r.location === loc) &&
          (!q ||
            r.name.toLowerCase().includes(q.toLowerCase()) ||
            r.employee_id.toLowerCase().includes(q.toLowerCase()) ||
            (r.account_manager ?? "").toLowerCase().includes(q.toLowerCase()))
      ),
    [rows, q, loc, status]
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
                if (f) previewMut.mutate(f);
                e.target.value = "";
              }}
            />
            <Button
              variant="secondary"
              onClick={() => fileRef.current?.click()}
              disabled={previewMut.isPending || importMut.isPending}
              title="Preview what the file would change before anything is saved"
            >
              <FileSpreadsheet className="h-4 w-4" />
              {previewMut.isPending ? "Reading…" : importMut.isPending ? "Importing…" : "Import Excel"}
            </Button>
            <Button onClick={openCreate}>
              <Plus className="h-4 w-4" /> Add employee
            </Button>
          </>
        }
      />

      <Card className="overflow-hidden p-0">
        <div className="flex flex-wrap items-center gap-3 border-b border-slate-100 px-4 py-2.5">
          <input
            id="employee-search"
            name="employee-search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search name, ID or manager…"
            className="w-64 rounded-lg border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs placeholder:text-slate-400 focus:border-brand-400 focus:bg-white focus:outline-none"
          />
          <Select
            id="employee-location-filter"
            name="employee-location-filter"
            value={loc}
            onChange={(e) => setLoc(e.target.value)}
            className="max-w-[110px] py-1.5 text-xs"
          >
            <option value="">All locations</option>
            <option value="DXB">DXB</option>
            <option value="AUH">AUH</option>
          </Select>
          <Select
            id="employee-status-filter"
            name="employee-status-filter"
            value={status}
            onChange={(e) => setStatus(e.target.value as StatusFilter)}
            className="max-w-[110px] py-1.5 text-xs"
          >
            <option value="active">Active</option>
            <option value="inactive">Inactive</option>
            <option value="all">All statuses</option>
          </Select>
          <p className="ml-auto text-[11px] text-slate-400">
            {visible.length} of {activeCount} active{(rows?.length ?? 0) > activeCount ? ` · ${(rows?.length ?? 0) - activeCount} inactive` : ""}
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
          <div className="overflow-x-auto">
            <table className="w-full table-fixed text-left text-xs">
              <colgroup>
                <col className="w-[20%]" />
                <col className="w-[12%]" />
                <col className="w-[8%]" />
                <col className="hidden lg:table-column lg:w-[14%]" />
                <col className="hidden xl:table-column xl:w-[10%]" />
                <col className="hidden xl:table-column xl:w-[16%]" />
                <col className="w-[10%]" />
                <col className="w-[10%]" />
              </colgroup>
              <thead>
                <tr className="border-b border-slate-100 bg-slate-50/80 text-[10px] font-bold uppercase tracking-wide text-slate-400">
                  <th className="px-2.5 py-2">Employee</th>
                  <th className="px-2 py-2">ID</th>
                  <th className="px-2 py-2">Loc</th>
                  <th className="hidden px-2 py-2 lg:table-cell">Manager</th>
                  <th className="hidden px-2 py-2 xl:table-cell">Project</th>
                  <th className="hidden px-2 py-2 xl:table-cell">Email</th>
                  <th className="px-2 py-2">Status</th>
                  <th className="px-2 py-2 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {visible.map((r) => (
                  <tr
                    key={r.id}
                    className={cn("transition-colors hover:bg-slate-50", !r.active && "opacity-60")}
                  >
                    <td className="px-2.5 py-1.5">
                      <div className="flex min-w-0 items-center gap-1.5">
                        <span
                          className={cn(
                            "flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[9px] font-bold",
                            avatarColor(r.name)
                          )}
                        >
                          {initials(r.name)}
                        </span>
                        <span className="min-w-0">
                          <span className="block truncate text-[11px] font-semibold text-slate-800" title={r.name}>
                            {r.name}
                          </span>
                          {/* Mirrors the File Vault folder name for this person. */}
                          {(r.aco_number || r.dco_number) && (
                            <span
                              className="block truncate font-mono text-[9px] text-slate-400"
                              title="Written into this employee's File Vault folder name"
                            >
                              {[r.aco_number && `ACO-${r.aco_number}`, r.dco_number && `DCO-${r.dco_number}`]
                                .filter(Boolean)
                                .join(" · ")}
                            </span>
                          )}
                        </span>
                      </div>
                    </td>
                    <td className="px-2 py-1.5">
                      <span className="flex items-center gap-1 truncate font-mono text-[10px] text-slate-600">
                        <span className="truncate">{r.employee_id}</span>
                        {sharedIds.has(r.employee_id) && (
                          <span
                            title="This ID exists in both teams — matching uses ID + name"
                            className="shrink-0"
                          >
                            <Copy className="h-3 w-3 text-amber-500" />
                          </span>
                        )}
                      </span>
                    </td>
                    <td className="px-2 py-1.5">
                      {r.location ? (
                        <Badge tone={locationBadgeTone(r.location)} className="px-1.5 py-0 text-[10px]">
                          <MapPin className="h-2.5 w-2.5" /> {r.location}
                        </Badge>
                      ) : (
                        <span className="text-[10px] text-slate-300">—</span>
                      )}
                    </td>
                    <td
                      className="hidden truncate px-2 py-1.5 text-[11px] text-slate-600 lg:table-cell"
                      title={r.account_manager ?? undefined}
                    >
                      {r.account_manager ?? "—"}
                    </td>
                    <td
                      className="hidden truncate px-2 py-1.5 text-[10px] text-slate-500 xl:table-cell"
                      title={r.project ?? undefined}
                    >
                      {r.project ?? "—"}
                    </td>
                    <td
                      className="hidden truncate px-2 py-1.5 text-[10px] text-slate-500 xl:table-cell"
                      title={r.employee_email_id ?? undefined}
                    >
                      {r.employee_email_id ?? "—"}
                    </td>
                    <td className="px-2 py-1.5">
                      <Badge tone={r.active ? "success" : "slate"} className="px-1.5 py-0 text-[10px]">
                        {r.active ? "Active" : "Inactive"}
                      </Badge>
                    </td>
                    <td className="px-2 py-1.5">
                      <div className="flex items-center justify-end gap-0.5">
                        <button
                          title="Edit"
                          onClick={() => openEdit(r)}
                          className="rounded-md p-1 text-slate-400 hover:bg-slate-100 hover:text-brand-600"
                        >
                          <Pencil className="h-3.5 w-3.5" />
                        </button>
                        <button
                          title={r.active ? "Mark inactive" : "Reactivate"}
                          onClick={() => {
                            const next = !r.active;
                            if (
                              !next &&
                              !confirm(
                                `Mark ${r.name} (${r.employee_id}) inactive? Their records and files are kept — this only hides them from active counts.`
                              )
                            )
                              return;
                            statusMut.mutate({ id: r.id, active: next });
                          }}
                          className={cn(
                            "rounded-md p-1 text-slate-400",
                            r.active ? "hover:bg-rose-50 hover:text-rose-500" : "hover:bg-emerald-50 hover:text-emerald-600"
                          )}
                        >
                          {r.active ? <UserX className="h-3.5 w-3.5" /> : <UserCheck className="h-3.5 w-3.5" />}
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
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
          <Field label="ACO number" name="aco_number">
            <Input
              value={form.aco_number ?? ""}
              onChange={(e) => setForm({ ...form, aco_number: e.target.value || null })}
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

      {/* -------- import review (dry run) — shown BEFORE anything is saved -------- */}
      {importPlan && (
        <EmployeeImportPlanModal
          plan={importPlan.plan}
          fileName={importPlan.file.name}
          pending={importMut.isPending}
          onCancel={() => setImportPlan(null)}
          onConfirm={() => importMut.mutate(importPlan.file)}
        />
      )}

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
                <p className="text-xs font-medium text-emerald-700">Added</p>
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
