import { CheckCircle2, AlertTriangle } from "lucide-react";
import { MONTHS, type BulkAcceptHeld, type BulkAcceptedItem } from "../api/client";
import { Button, Modal } from "./ui";

export interface BulkAcceptReportData {
  accepted: BulkAcceptedItem[];
  held: BulkAcceptHeld[];
  failed: string[];
}

/** What a bulk accept actually did: every employee that was filed, with their
 *  month and leave dates, and every one that was kept for review and why. */
export default function BulkAcceptReport({
  report, onClose,
}: { report: BulkAcceptReportData | null; onClose: () => void }) {
  if (!report) return null;
  const { accepted, held, failed } = report;

  return (
    <Modal
      open
      onClose={onClose}
      wide
      title="Bulk accept result"
      subtitle={`${accepted.length} accepted · ${held.length} kept for review${failed.length ? ` · ${failed.length} failed` : ""}`}
    >
      <div className="space-y-5 text-sm">
        <section>
          <h4 className="mb-2 flex items-center gap-1.5 font-bold text-emerald-700">
            <CheckCircle2 className="h-4 w-4" /> Accepted ({accepted.length})
          </h4>
          {accepted.length === 0 ? (
            <p className="text-xs text-slate-500">Nothing was accepted.</p>
          ) : (
            <div className="max-h-72 overflow-auto rounded-lg border border-slate-200">
              <table className="w-full text-left text-xs">
                <thead className="sticky top-0 bg-slate-50 text-[10px] uppercase tracking-wide text-slate-400">
                  <tr>
                    <th className="px-3 py-2">Employee</th>
                    <th className="px-2 py-2">Period</th>
                    <th className="px-2 py-2">Leave (day of month)</th>
                    <th className="px-2 py-2 text-right">Worked</th>
                    <th className="px-2 py-2">Approval</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {accepted.map((a) => {
                    const leaves = Object.entries(a.buckets);
                    return (
                      <tr key={a.id} className="align-top">
                        <td className="px-3 py-1.5">
                          <div className="font-semibold text-slate-800">{a.employee_name ?? "—"}</div>
                          <div className="text-[10px] text-slate-400">
                            {a.employee_id ?? ""}{a.project ? ` · ${a.project}` : ""}
                          </div>
                        </td>
                        <td className="whitespace-nowrap px-2 py-1.5">{MONTHS[a.month]} {a.year}</td>
                        <td className="px-2 py-1.5">
                          {leaves.length === 0 ? (
                            <span className="text-slate-400">none</span>
                          ) : (
                            leaves.map(([b, d]) => (
                              <div key={b}>
                                <b className="capitalize">{b.replace("_", " ")}</b> ({d.length}): {d.map((x) => x.slice(8)).join(", ")}
                              </div>
                            ))
                          )}
                        </td>
                        <td className="px-2 py-1.5 text-right tabular-nums">{a.working_days}</td>
                        <td className="px-2 py-1.5">
                          {a.approved ? "Approved" : <span className="text-amber-700">Not approved</span>}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {held.length > 0 && (
          <section>
            <h4 className="mb-2 flex items-center gap-1.5 font-bold text-amber-700">
              <AlertTriangle className="h-4 w-4" /> Kept for review ({held.length})
            </h4>
            <ul className="max-h-48 space-y-1 overflow-y-auto rounded-lg border border-amber-200 bg-amber-50/60 p-3 text-xs">
              {held.map((h) => (
                <li key={h.id}><b>{h.employee_name ?? "Unknown"}</b> — {h.reasons.join("; ")}</li>
              ))}
            </ul>
          </section>
        )}

        {failed.length > 0 && (
          <section>
            <h4 className="mb-2 font-bold text-rose-700">Failed ({failed.length})</h4>
            <ul className="max-h-32 space-y-1 overflow-y-auto rounded-lg border border-rose-200 bg-rose-50 p-3 text-xs">
              {failed.map((f, i) => <li key={i}>{f}</li>)}
            </ul>
          </section>
        )}

        <div className="flex justify-end"><Button onClick={onClose}>Close</Button></div>
      </div>
    </Modal>
  );
}
