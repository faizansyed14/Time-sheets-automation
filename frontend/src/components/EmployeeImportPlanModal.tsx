/**
 * Employee import — confirmation overlay.
 *
 * An uploaded .xlsx is first sent to /employee-matcher/import/preview, which
 * writes nothing and returns exactly what it WOULD do. This shows that plan
 * in full so a reviewer can check it before committing:
 *
 *   New            — people not in the matcher yet
 *   Updates        — matched people, with the exact field-level old -> new
 *   Unchanged      — matched, nothing to change
 *   Not in file    — in the matcher but absent from this sheet (likely
 *                    leavers). NEVER deleted by an import — listed so they
 *                    can be marked inactive deliberately instead.
 *   Skipped        — unusable rows (no ID/name, or duplicated in the file)
 *
 * "Update matcher" re-sends the same file to /employee-matcher/import.
 */
import { useState } from "react";
import { createPortal } from "react-dom";
import {
  AlertTriangle,
  ArrowRight,
  ChevronDown,
  ChevronRight,
  FileSpreadsheet,
  MinusCircle,
  PencilLine,
  UserPlus,
  X,
} from "lucide-react";
import type { ImportPlan } from "../api/client";
import { Badge, Button, Spinner } from "./ui";
import { cn } from "../lib/utils";

const FIELD_LABELS: Record<string, string> = {
  name: "Name",
  aco_number: "ACO number",
  dco_number: "DCO number",
  account_manager: "Account manager",
  employee_email_id: "Email",
  project: "Project",
  contact_no: "Contact no.",
  location: "Location",
  all_emails: "All emails",
};

const dash = (v: string | null | undefined) => (v && v.trim() ? v : "—");

function StatChip({
  label,
  value,
  tone,
  icon,
}: {
  label: string;
  value: number;
  tone: "emerald" | "brand" | "slate" | "amber" | "rose";
  icon: React.ReactNode;
}) {
  const tones = {
    emerald: "border-emerald-200 bg-emerald-50/60 text-emerald-700",
    brand: "border-brand-200 bg-brand-50/60 text-brand-700",
    slate: "border-slate-200 bg-slate-50 text-slate-600",
    amber: "border-amber-200 bg-amber-50/60 text-amber-700",
    rose: "border-rose-200 bg-rose-50/60 text-rose-700",
  };
  return (
    <div className={cn("flex items-center gap-2 rounded-lg border px-3 py-2", tones[tone])}>
      <span className="shrink-0">{icon}</span>
      <span>
        <span className="block text-lg font-bold leading-none tabular-nums">{value}</span>
        <span className="block text-[11px] font-medium opacity-80">{label}</span>
      </span>
    </div>
  );
}

function Section({
  title,
  detail,
  count,
  defaultOpen,
  children,
}: {
  title: string;
  detail: string;
  count: number;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(!!defaultOpen && count > 0);
  return (
    <div className="rounded-lg border border-slate-200">
      <button
        type="button"
        disabled={count === 0}
        onClick={() => setOpen((v) => !v)}
        className={cn(
          "flex w-full items-center gap-2 px-3 py-2.5 text-left",
          count === 0 ? "cursor-default opacity-60" : "hover:bg-slate-50"
        )}
      >
        {count > 0 &&
          (open ? (
            <ChevronDown className="h-4 w-4 shrink-0 text-slate-400" />
          ) : (
            <ChevronRight className="h-4 w-4 shrink-0 text-slate-400" />
          ))}
        <span className="min-w-0 flex-1">
          <span className="text-sm font-semibold text-slate-800">
            {title} <span className="tabular-nums text-slate-400">({count})</span>
          </span>
          <span className="mt-0.5 block text-xs text-slate-500">{detail}</span>
        </span>
      </button>
      {open && count > 0 && (
        <div className="max-h-72 overflow-auto border-t border-slate-100">{children}</div>
      )}
    </div>
  );
}

const TH = "sticky top-0 bg-slate-50 px-3 py-2 text-left font-semibold text-slate-500";
const TD = "px-3 py-1.5 align-top text-slate-700";

export default function EmployeeImportPlanModal({
  plan,
  fileName,
  pending,
  onCancel,
  onConfirm,
}: {
  plan: ImportPlan;
  fileName: string;
  pending: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const willWrite = plan.to_add.length + plan.to_update.length;

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div
        className="absolute inset-0 bg-slate-900/50 backdrop-blur-sm animate-overlay-in"
        onClick={pending ? undefined : onCancel}
      />
      <div className="relative flex max-h-[92vh] w-full max-w-5xl flex-col rounded-xl border border-slate-200 bg-white shadow-pop animate-scale-in">
        {/* header */}
        <div className="flex items-start justify-between gap-4 border-b border-slate-100 px-5 py-4">
          <div className="min-w-0">
            <h3 className="flex items-center gap-2 text-lg font-bold text-slate-900">
              <FileSpreadsheet className="h-5 w-5 shrink-0 text-brand-600" />
              Review import
            </h3>
            <p className="mt-0.5 truncate text-sm text-slate-500">
              {fileName} — nothing has been saved yet.
            </p>
          </div>
          <button
            type="button"
            onClick={onCancel}
            disabled={pending}
            aria-label="Close"
            className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600 disabled:opacity-50"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* body */}
        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-5 py-4">
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
            <StatChip label="New" value={plan.to_add.length} tone="emerald" icon={<UserPlus className="h-4 w-4" />} />
            <StatChip label="Updates" value={plan.to_update.length} tone="brand" icon={<PencilLine className="h-4 w-4" />} />
            <StatChip label="Unchanged" value={plan.unchanged.length} tone="slate" icon={<MinusCircle className="h-4 w-4" />} />
            <StatChip label="Not in this file" value={plan.missing_from_file.length} tone="amber" icon={<AlertTriangle className="h-4 w-4" />} />
            <StatChip label="Skipped rows" value={plan.skipped.length} tone="rose" icon={<X className="h-4 w-4" />} />
          </div>

          <Section
            title="New employees"
            detail="Not in the matcher yet — these will be added."
            count={plan.to_add.length}
            defaultOpen
          >
            <table className="w-full text-xs">
              <thead>
                <tr>
                  <th className={TH}>Name</th>
                  <th className={TH}>ID</th>
                  <th className={TH}>ACO / DCO</th>
                  <th className={TH}>Location</th>
                  <th className={TH}>Manager</th>
                  <th className={TH}>Email</th>
                  <th className={TH}>Contact</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {plan.to_add.map((r, i) => (
                  <tr key={`${r.employee_id}-${i}`}>
                    <td className={TD}>
                      <span className="font-semibold text-slate-800">{r.name}</span>
                      {r.possible_rename_of && (
                        <span className="mt-0.5 block text-[11px] text-amber-700">
                          Same ID + office as {r.possible_rename_of} — possibly a rename. Added as a
                          separate person; merge by hand if it's the same employee.
                        </span>
                      )}
                    </td>
                    <td className={cn(TD, "font-mono")}>{r.employee_id}</td>
                    <td className={cn(TD, "whitespace-nowrap font-mono")}>
                      {r.aco_number || r.dco_number
                        ? [r.aco_number && `ACO-${r.aco_number}`, r.dco_number && `DCO-${r.dco_number}`]
                            .filter(Boolean)
                            .join(" · ")
                        : "—"}
                    </td>
                    <td className={TD}>{dash(r.location)}</td>
                    <td className={TD}>{dash(r.account_manager)}</td>
                    <td className={TD}>{dash(r.employee_email_id)}</td>
                    <td className={TD}>{dash(r.contact_no)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Section>

          <Section
            title="Updates to existing employees"
            detail="Matched by ID + name. Only the fields listed change; blank cells in the sheet are ignored, never erased."
            count={plan.to_update.length}
            defaultOpen
          >
            <table className="w-full text-xs">
              <thead>
                <tr>
                  <th className={TH}>Employee</th>
                  <th className={TH}>Changes</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {plan.to_update.map((r) => (
                  <tr key={r.id}>
                    <td className={cn(TD, "whitespace-nowrap")}>
                      <span className="font-semibold text-slate-800">{r.name}</span>
                      <span className="mt-0.5 block font-mono text-[11px] text-slate-500">
                        {r.employee_id}
                        {r.location ? ` · ${r.location}` : ""}
                      </span>
                    </td>
                    <td className={TD}>
                      <ul className="space-y-1">
                        {r.changes.map((c, i) => (
                          <li key={i} className="flex flex-wrap items-center gap-1.5">
                            <span className="font-semibold text-slate-600">
                              {FIELD_LABELS[c.field] ?? c.field}
                            </span>
                            <span className="rounded bg-rose-50 px-1.5 py-0.5 text-rose-700 line-through decoration-rose-300">
                              {dash(c.old)}
                            </span>
                            <ArrowRight className="h-3 w-3 shrink-0 text-slate-400" />
                            <span className="rounded bg-emerald-50 px-1.5 py-0.5 font-medium text-emerald-800">
                              {dash(c.new)}
                            </span>
                          </li>
                        ))}
                      </ul>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Section>

          <Section
            title="In the matcher but not in this file"
            detail="Possibly left the company. They are KEPT — an import never deletes anyone. Mark them inactive from the list if they've left."
            count={plan.missing_from_file.length}
            defaultOpen
          >
            <table className="w-full text-xs">
              <thead>
                <tr>
                  <th className={TH}>Name</th>
                  <th className={TH}>ID</th>
                  <th className={TH}>Location</th>
                  <th className={TH}>Manager</th>
                  <th className={TH}>Email</th>
                  <th className={TH}>Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {plan.missing_from_file.map((r) => (
                  <tr key={r.id}>
                    <td className={cn(TD, "font-semibold text-slate-800")}>{r.name}</td>
                    <td className={cn(TD, "font-mono")}>{r.employee_id}</td>
                    <td className={TD}>{dash(r.location)}</td>
                    <td className={TD}>{dash(r.account_manager)}</td>
                    <td className={TD}>{dash(r.employee_email_id)}</td>
                    <td className={TD}>
                      <Badge tone={r.active ? "success" : "slate"}>
                        {r.active ? "Active" : "Inactive"}
                      </Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Section>

          <Section
            title="Unchanged"
            detail="Matched the matcher exactly — nothing to do."
            count={plan.unchanged.length}
          >
            <table className="w-full text-xs">
              <thead>
                <tr>
                  <th className={TH}>Name</th>
                  <th className={TH}>ID</th>
                  <th className={TH}>Location</th>
                  <th className={TH}>Manager</th>
                  <th className={TH}>Email</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {plan.unchanged.map((r) => (
                  <tr key={r.id}>
                    <td className={cn(TD, "font-semibold text-slate-800")}>{r.name}</td>
                    <td className={cn(TD, "font-mono")}>{r.employee_id}</td>
                    <td className={TD}>{dash(r.location)}</td>
                    <td className={TD}>{dash(r.account_manager)}</td>
                    <td className={TD}>{dash(r.employee_email_id)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Section>

          <Section
            title="Skipped rows"
            detail="Rows this file can't use — they are ignored entirely."
            count={plan.skipped.length}
          >
            <table className="w-full text-xs">
              <thead>
                <tr>
                  <th className={TH}>Sheet</th>
                  <th className={TH}>Row</th>
                  <th className={TH}>ID</th>
                  <th className={TH}>Name</th>
                  <th className={TH}>Reason</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {plan.skipped.map((r, i) => (
                  <tr key={i}>
                    <td className={TD}>{r.sheet}</td>
                    <td className={cn(TD, "tabular-nums")}>{r.row}</td>
                    <td className={cn(TD, "font-mono")}>{dash(r.id)}</td>
                    <td className={TD}>{dash(r.name)}</td>
                    <td className={cn(TD, "text-amber-700")}>{r.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Section>
        </div>

        {/* footer */}
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 px-5 py-3.5">
          <p className="text-xs text-slate-500">
            {willWrite === 0
              ? "Nothing to write — this file matches the matcher as it stands."
              : `Will add ${plan.to_add.length} and update ${plan.to_update.length} employee(s). Nobody is deleted.`}
          </p>
          <div className="flex gap-2">
            <Button variant="secondary" onClick={onCancel} disabled={pending}>
              Cancel
            </Button>
            <Button onClick={onConfirm} disabled={pending || willWrite === 0}>
              {pending ? <Spinner /> : null}
              {pending ? "Updating…" : "Update matcher"}
            </Button>
          </div>
        </div>
      </div>
    </div>,
    document.body
  );
}
