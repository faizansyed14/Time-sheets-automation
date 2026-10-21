import { useState } from "react";
import { CheckCheck } from "lucide-react";
import {
  BULK_ACCEPT_PROJECTS,
  bulkAcceptPipeline,
  MONTHS_LONG,
  type BulkAcceptHeld,
  type BulkAcceptResult,
  type BulkAcceptedItem,
} from "../api/client";
import BulkAcceptReport, { type BulkAcceptReportData } from "./BulkAcceptReport";
import { Button, Modal, Spinner } from "./ui";
import { useToast } from "./toast";

type Period = { month: number; year: number } | null;

/** "Accept all PGC" / "Accept all MOD" for the review queue.
 *
 *  Clicking first asks the server what WOULD be accepted (a dry run) and shows
 *  it — how many are clean, which are held back and why — and only a second
 *  click on Accept files anything. The server files in small batches, so this
 *  keeps calling until nothing is left (or a pass files nothing, so a failing
 *  item can't loop forever). Respects the month/year filter when one is set. */
export default function BulkAccept({ period, onDone }: { period: Period; onDone: () => void }) {
  const { toast } = useToast();
  const [project, setProject] = useState<string | null>(null);
  const [plan, setPlan] = useState<BulkAcceptResult | null>(null);
  const [checking, setChecking] = useState(false);
  const [running, setRunning] = useState(false);
  const [done, setDone] = useState(0);
  const [report, setReport] = useState<BulkAcceptReportData | null>(null);

  const scope = period ? { month: period.month, year: period.year } : {};

  const open = async (p: string) => {
    setProject(p);
    setPlan(null);
    setDone(0);
    setChecking(true);
    try {
      setPlan(await bulkAcceptPipeline({ project: p, dry_run: true, ...scope }));
    } catch (e: any) {
      toast("error", "Could not check the queue", e?.response?.data?.detail ?? String(e));
      setProject(null);
    } finally {
      setChecking(false);
    }
  };

  const close = () => {
    if (running) return;
    setProject(null);
    setPlan(null);
  };

  const run = async () => {
    if (!project) return;
    setRunning(true);
    let total = 0;
    const failures: string[] = [];
    const items: BulkAcceptedItem[] = [];
    let held: BulkAcceptHeld[] = [];
    try {
      for (;;) {
        const r = await bulkAcceptPipeline({ project, ...scope });
        total += r.accepted;
        items.push(...r.accepted_items);
        held = r.held;
        failures.push(...r.failed.map((f) => `${f.employee_name ?? f.id}: ${f.error}`));
        setDone(total);
        if (r.remaining <= 0 || r.accepted === 0) break;
      }
      toast(
        failures.length ? "warning" : "success",
        `${total} ${project} record${total === 1 ? "" : "s"} accepted`,
        failures.length ? `${failures.length} failed — ${failures[0]}` : undefined,
      );
    } catch (e: any) {
      toast("error", `Stopped after ${total} accepted`, e?.response?.data?.detail ?? String(e));
    } finally {
      setReport({ accepted: items, held, failed: failures });
      setRunning(false);
      setProject(null);
      setPlan(null);
      onDone();
    }
  };

  const periodLabel = period ? `${MONTHS_LONG[period.month]} ${period.year}` : "all periods";

  return (
    <>
      {BULK_ACCEPT_PROJECTS.map((p) => (
        <button
          key={p}
          type="button"
          onClick={() => open(p)}
          disabled={checking || running}
          title={`Accept every clean ${p === "PGC" ? "PGC (Technical Support, Multimedia, Programming)" : "MOD (Ministry of Defence)"} item waiting for review (${periodLabel})`}
          className="inline-flex items-center gap-1.5 rounded-lg border border-emerald-200 bg-emerald-50 px-2.5 py-1.5 text-xs font-semibold text-emerald-700 transition-colors hover:bg-emerald-100 disabled:opacity-50"
        >
          <CheckCheck className="h-3.5 w-3.5" />
          Accept all {p}
        </button>
      ))}

      <Modal open={!!project} onClose={close} dismissible={!running} title={`Accept all ${project ?? ""}`} subtitle={periodLabel}>
        {!plan ? (
          <div className="flex items-center gap-2 py-6 text-sm text-slate-500"><Spinner /> Checking the review queue…</div>
        ) : (
          <div className="space-y-4 text-sm text-slate-700">
            <p>
              <span className="text-lg font-bold text-slate-900">{plan.ready}</span> clean{" "}
              {project} item{plan.ready === 1 ? "" : "s"} will be filed to the vault and records.
              {plan.held_count > 0 && (
                <> <span className="font-semibold text-amber-700">{plan.held_count}</span> need a human and stay in the queue.</>
              )}
            </p>
            <p className="rounded-lg bg-slate-50 p-3 text-xs text-slate-500">
              Same as pressing Accept on each one. Items with a flag, an unmatched employee or missing days are never
              included. Approval is recorded as <b>approved</b> only when the sheet carried evidence, otherwise{" "}
              <b>not approved</b> — you can sign it off later on the record.
            </p>
            {plan.held.length > 0 && (
              <details className="rounded-lg border border-amber-200 bg-amber-50/60 p-3 text-xs">
                <summary className="cursor-pointer font-semibold text-amber-800">Why {plan.held_count} are held</summary>
                <ul className="mt-2 max-h-40 space-y-1 overflow-y-auto">
                  {plan.held.map((h) => (
                    <li key={h.id}><b>{h.employee_name ?? "Unknown"}</b> — {h.reasons.join("; ")}</li>
                  ))}
                </ul>
              </details>
            )}
            <div className="flex items-center justify-end gap-2">
              {running && <span className="mr-auto text-xs text-slate-500"><Spinner className="mr-1.5 inline-block" />Filed {done} of {plan.ready}…</span>}
              <Button variant="ghost" onClick={close} disabled={running}>Cancel</Button>
              <Button onClick={run} disabled={running || plan.ready === 0}>
                Accept {plan.ready} {project}
              </Button>
            </div>
          </div>
        )}
      </Modal>
      <BulkAcceptReport report={report} onClose={() => setReport(null)} />
    </>
  );
}
