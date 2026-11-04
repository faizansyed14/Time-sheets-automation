import { SkeletonBody, SkeletonHeader, SkeletonRegion } from "./blocks";

/** The whole shell (sidebar + header + body) while the session loads, so the
 *  page never flashes a bare spinner. Mirrors Shell's widths and heights. */
export function WorkspaceSkeleton() {
  return (
    <SkeletonRegion label="Loading workspace" className="flex h-screen overflow-hidden app-canvas">
      <aside aria-hidden className="hidden w-[264px] shrink-0 flex-col border-r border-slate-200/80 bg-white/70 lg:flex">
        <div className="flex h-14 items-center gap-3 border-b border-slate-200/80 px-4">
          <div className="skeleton h-8 w-8" />
          <div className="skeleton h-5 w-28" />
        </div>
        <div className="space-y-1.5 px-3 py-4">
          {Array.from({ length: 8 }, (_, i) => (
            <div key={i} className="skeleton h-9 w-full" />
          ))}
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <div aria-hidden className="flex h-14 shrink-0 items-center border-b border-slate-200/80 px-6">
          <div className="skeleton h-5 w-40" />
        </div>
        <div className="min-h-0 flex-1 overflow-hidden px-6 py-6 lg:px-10">
          <SkeletonHeader />
          <SkeletonBody />
        </div>
      </div>
    </SkeletonRegion>
  );
}
