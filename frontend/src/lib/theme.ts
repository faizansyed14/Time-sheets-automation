/**
 * Single source of truth for UI colors.
 * Palette: brand (indigo) · slate (neutral) · emerald · amber · rose
 */

export type BadgeTone = "slate" | "brand" | "success" | "warning" | "danger";

export const BADGE_TONES: Record<BadgeTone, string> = {
  slate: "bg-slate-100 text-slate-700 ring-slate-200",
  brand: "bg-brand-50 text-brand-700 ring-brand-200",
  success: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  warning: "bg-amber-50 text-amber-800 ring-amber-200",
  danger: "bg-rose-50 text-rose-700 ring-rose-200",
};

/** Leave buckets — distinct but all from the same palette family */
export const LEAVE_BUCKET_TONE: Record<string, string> = {
  annual: "bg-brand-50 text-brand-800 ring-brand-200",
  remote: "bg-slate-100 text-slate-700 ring-slate-200",
  sick: "bg-amber-50 text-amber-800 ring-amber-200",
  maternity: "bg-rose-50 text-rose-800 ring-rose-200",
  unpaid: "bg-slate-200/60 text-slate-700 ring-slate-300",
  absent: "bg-rose-100 text-rose-800 ring-rose-300",
  public_holiday: "bg-emerald-50 text-emerald-800 ring-emerald-200",
};

export const LEAVE_BUCKET_LABELS: Record<string, string> = {
  annual: "Annual leave",
  remote: "Remote / WFH",
  sick: "Sick leave",
  maternity: "Maternity leave",
  unpaid: "Unpaid leave",
  absent: "Absent",
  public_holiday: "Public holiday",
};

export const LEAVE_BUCKET_KEYS = [
  "annual", "remote", "sick", "maternity", "unpaid", "absent", "public_holiday",
] as const;

export function leaveBucketDefs() {
  return LEAVE_BUCKET_KEYS.map((key) => ({
    key,
    label: LEAVE_BUCKET_LABELS[key]!,
    tone: LEAVE_BUCKET_TONE[key]!,
  }));
}

export function locationBadgeTone(location: string): BadgeTone {
  return location === "DXB" ? "brand" : "slate";
}

/** Avatar backgrounds — brand + slate shades only */
export const AVATAR_COLORS = [
  "bg-brand-100 text-brand-800",
  "bg-brand-200 text-brand-900",
  "bg-slate-200 text-slate-800",
  "bg-slate-300 text-slate-900",
  "bg-emerald-100 text-emerald-800",
  "bg-amber-100 text-amber-900",
  "bg-rose-100 text-rose-800",
] as const;
