import type { ReactNode } from "react";
import { cn } from "../../lib/utils";

/**
 * Shared skeleton pieces. A skeleton copies the paddings, borders, radii and
 * column widths of the real component so nothing shifts when data arrives.
 * Shapes are aria-hidden; the region announces one polite "Loading" label.
 */

const bar = (className?: string) => <div aria-hidden className={cn("skeleton", className)} />;

export function SkeletonRegion({
  label = "Loading",
  className,
  children,
}: {
  label?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div role="status" aria-busy="true" aria-live="polite" className={className}>
      <span className="sr-only">{label}</span>
      {children}
    </div>
  );
}

/** n text lines, the last one shorter. */
export function SkeletonLines({ lines = 3, className }: { lines?: number; className?: string }) {
  return (
    <div aria-hidden className={cn("space-y-2", className)}>
      {Array.from({ length: lines }, (_, i) => (
        <div key={i} className={cn("skeleton h-3.5", i === lines - 1 && lines > 1 ? "w-2/3" : "w-full")} />
      ))}
    </div>
  );
}

/** Same spacing as PageHeader: title bar and one description line. */
export function SkeletonHeader({ withActions }: { withActions?: boolean }) {
  return (
    <div aria-hidden className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0 space-y-2.5">
        {bar("h-7 w-56")}
        {bar("h-3.5 w-96 max-w-full")}
      </div>
      {withActions && <div className="flex gap-2">{bar("h-9 w-24")}{bar("h-9 w-28")}</div>}
    </div>
  );
}

/** A card with a title bar (same chrome as <Section>). */
export function SkeletonSection({ children, className }: { children?: ReactNode; className?: string }) {
  return (
    <div aria-hidden className={cn("rounded-2xl border border-slate-200/80 bg-white shadow-card", className)}>
      <div className="border-b border-slate-200/80 px-5 py-3.5">{bar("h-4 w-40")}</div>
      <div className="p-5">{children ?? <SkeletonLines lines={4} />}</div>
    </div>
  );
}

/** Same chrome as <Stat>. */
export function SkeletonStat() {
  return (
    <div aria-hidden className="rounded-xl border border-slate-200/80 bg-white p-4 shadow-card">
      {bar("h-2.5 w-20")}
      <div className="mt-3">{bar("h-8 w-24")}</div>
      <div className="mt-2">{bar("h-3 w-32")}</div>
    </div>
  );
}

/** A row of n cells with the given relative widths (tailwind width classes). */
export function SkeletonListRow({ widths = ["w-1/4", "w-1/3", "w-1/6"], className }: { widths?: string[]; className?: string }) {
  return (
    <div aria-hidden className={cn("flex items-center gap-4 px-4 py-3", className)}>
      {widths.map((w, i) => (
        <div key={i} className={cn("skeleton h-3.5", w)} />
      ))}
    </div>
  );
}

export function SkeletonRows({ rows = 6, widths, className }: { rows?: number; widths?: string[]; className?: string }) {
  return (
    <div aria-hidden className={cn("divide-y divide-slate-100", className)}>
      {Array.from({ length: rows }, (_, i) => (
        <SkeletonListRow key={i} widths={widths} />
      ))}
    </div>
  );
}

/** Table skeleton with the REAL column headings over skeleton rows. */
export function SkeletonTable({
  columns,
  rows = 6,
  widths,
}: {
  columns: string[];
  rows?: number;
  widths?: string[];
}) {
  const w = widths ?? columns.map((_, i) => (i === 0 ? "w-40" : "w-20"));
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-slate-200/80">
            {columns.map((c) => (
              <th key={c} className="px-4 py-2.5 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody aria-hidden className="divide-y divide-slate-100">
          {Array.from({ length: rows }, (_, r) => (
            <tr key={r}>
              {columns.map((c, i) => (
                <td key={c} className="px-4 py-3">
                  {bar(cn("h-3.5", w[i % w.length]))}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Generic page body: stat tiles, then a card with rows. */
export function SkeletonBody() {
  return (
    <div aria-hidden className="space-y-5">
      <div className="grid gap-3 sm:grid-cols-3">
        <SkeletonStat />
        <SkeletonStat />
        <SkeletonStat />
      </div>
      <SkeletonSection>
        <SkeletonRows rows={5} className="-mx-5 -my-5" />
      </SkeletonSection>
    </div>
  );
}
