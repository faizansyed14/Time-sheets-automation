import {
  SkeletonBody,
  SkeletonHeader,
  SkeletonRegion,
  SkeletonRows,
  SkeletonSection,
  SkeletonStat,
  SkeletonTable,
} from "./blocks";

/** Page skeleton = header + body, matching PageHeader and a stat row + card. */
export function PageSkeleton({ label = "Loading page", withActions }: { label?: string; withActions?: boolean }) {
  return (
    <SkeletonRegion label={label}>
      <SkeletonHeader withActions={withActions} />
      <SkeletonBody />
    </SkeletonRegion>
  );
}

/** List pages (employees, activity log, inbox list, export, reminders ...). */
export function ListSkeleton({ columns, rows = 8, label = "Loading list", widths }: { columns: string[]; rows?: number; label?: string; widths?: string[] }) {
  return (
    <SkeletonRegion label={label}>
      <SkeletonTable columns={columns} rows={rows} widths={widths} />
    </SkeletonRegion>
  );
}

/** The three stat tiles above the dashboard table. */
export function StatsSkeleton({ count = 3 }: { count?: number }) {
  return (
    <SkeletonRegion label="Loading summary" className="grid gap-3 sm:grid-cols-3">
      {Array.from({ length: count }, (_, i) => (
        <SkeletonStat key={i} />
      ))}
    </SkeletonRegion>
  );
}

/** Settings-style pages: a stack of cards. */
export function SettingsSkeleton() {
  return (
    <SkeletonRegion label="Loading settings" className="space-y-5">
      <SkeletonHeader />
      <SkeletonSection />
      <SkeletonSection />
    </SkeletonRegion>
  );
}

/** A detail page (one record): header, two-column body. */
export function DetailSkeleton() {
  return (
    <SkeletonRegion label="Loading record">
      <SkeletonHeader withActions />
      <div className="grid gap-5 lg:grid-cols-[1.2fr_1fr]">
        <SkeletonSection />
        <SkeletonSection>
          <SkeletonRows rows={5} widths={["w-1/3", "w-1/2"]} className="-mx-5 -my-5" />
        </SkeletonSection>
      </div>
    </SkeletonRegion>
  );
}

/** Rows appended at the end of an infinite list while the next page loads. */
export function MoreRowsSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <SkeletonRegion label="Loading more">
      <SkeletonRows rows={rows} />
    </SkeletonRegion>
  );
}
