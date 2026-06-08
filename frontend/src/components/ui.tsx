import type { ReactNode } from "react";

export function StatusDot({ status }: { status: "green" | "yellow" }) {
  const ok = status === "green";
  return (
    <span className="inline-flex items-center gap-2">
      <span className={`relative flex h-2.5 w-2.5`}>
        <span
          className={`absolute inline-flex h-full w-full rounded-full opacity-60 ${
            ok ? "bg-emerald-400" : "bg-amber-400"
          }`}
        />
        <span className={`relative inline-flex h-2.5 w-2.5 rounded-full ${ok ? "bg-emerald-500" : "bg-amber-500"}`} />
      </span>
      <span className={`text-xs font-medium ${ok ? "text-emerald-700" : "text-amber-700"}`}>
        {ok ? "Clear" : "Review"}
      </span>
    </span>
  );
}

export function Pill({
  children,
  tone = "slate",
}: {
  children: ReactNode;
  tone?: "slate" | "emerald" | "amber" | "rose" | "petrol";
}) {
  const tones: Record<string, string> = {
    slate: "bg-slate-100 text-slate-700 ring-slate-200",
    emerald: "bg-emerald-50 text-emerald-700 ring-emerald-200",
    amber: "bg-amber-50 text-amber-800 ring-amber-200",
    rose: "bg-rose-50 text-rose-700 ring-rose-200",
    petrol: "bg-petrol-50 text-petrol-700 ring-petrol-100",
  };
  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ${tones[tone]}`}>
      {children}
    </span>
  );
}

export function LeaveChip({ label, count, tone }: { label: string; count: number; tone: string }) {
  return (
    <div className="flex items-center justify-between rounded-xl border border-slate-200 bg-white px-3 py-2.5">
      <span className="text-xs font-medium text-slate-500">{label}</span>
      <span className={`tabular font-mono text-lg font-semibold ${count > 0 ? tone : "text-slate-300"}`}>
        {count}
      </span>
    </div>
  );
}

export function Spinner({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex items-center gap-3 rounded-2xl border border-slate-200 bg-white px-5 py-4 text-sm text-slate-500 shadow-panel">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-petrol-500 border-t-transparent" />
      {label}
    </div>
  );
}
