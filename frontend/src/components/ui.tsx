import {
  Children,
  cloneElement,
  isValidElement,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactElement,
  type ReactNode,
  type SelectHTMLAttributes,
  useEffect,
} from "react";
import { createPortal } from "react-dom";
import { X, Inbox, Sparkles } from "lucide-react";
import { cn } from "../lib/utils";
import { BADGE_TONES, type BadgeTone } from "../lib/theme";

function fieldSlug(label: string) {
  return label.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "");
}

/* ----------------------------- Button ----------------------------- */
type Variant = "primary" | "secondary" | "ghost" | "danger" | "success";
const VARIANTS: Record<Variant, string> = {
  primary:
    "bg-gradient-to-b from-brand-700 to-brand-800 text-white shadow-sheen hover:from-brand-800 hover:to-brand-900 active:to-brand-950 disabled:from-brand-300 disabled:to-brand-300 disabled:shadow-none",
  secondary:
    "bg-white text-slate-700 ring-1 ring-inset ring-slate-200 shadow-xs hover:bg-slate-50 hover:text-slate-900 hover:ring-slate-300 disabled:text-slate-400",
  ghost: "text-slate-600 hover:bg-slate-100 hover:text-slate-900 disabled:text-slate-300",
  danger: "bg-gradient-to-b from-rose-600 to-rose-700 text-white shadow-xs ring-1 ring-inset ring-rose-700/60 hover:to-rose-800 disabled:opacity-50",
  success: "bg-gradient-to-b from-emerald-600 to-emerald-700 text-white shadow-xs ring-1 ring-inset ring-emerald-700/60 hover:to-emerald-800 disabled:opacity-50",
};

export function Button({
  variant = "primary",
  size = "md",
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: "sm" | "md" }) {
  return (
    <button
      className={cn(
        "inline-flex select-none items-center justify-center gap-1.5 rounded-lg font-semibold tracking-[-0.006em] transition-all duration-150 ease-out focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 focus-visible:ring-offset-2 active:scale-[0.98] disabled:cursor-not-allowed disabled:active:scale-100",
        size === "sm" ? "h-8 px-3 text-xs" : "h-9 px-3.5 text-sm",
        VARIANTS[variant],
        className
      )}
      {...props}
    />
  );
}

/* ----------------------------- Card ----------------------------- */
export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <div className={cn("rounded-xl border border-slate-200/80 bg-white shadow-card transition-shadow duration-200", className)}>
      {children}
    </div>
  );
}

/* ----------------------------- Badge ----------------------------- */
export function Badge({
  tone = "slate",
  className,
  children,
}: {
  tone?: BadgeTone;
  className?: string;
  children: ReactNode;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[11px] font-semibold ring-1 ring-inset",
        BADGE_TONES[tone],
        className
      )}
    >
      {children}
    </span>
  );
}

/* ----------------------------- Inputs ----------------------------- */
export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        "w-full rounded-lg border border-slate-300/80 bg-white px-3 py-2 text-sm text-slate-900 shadow-xs transition-colors placeholder:text-slate-400 hover:border-slate-400/70 focus:border-brand-600 focus:outline-none focus:ring-[3px] focus:ring-brand-600/15 disabled:bg-slate-50 disabled:text-slate-500",
        className
      )}
      {...props}
    />
  );
}

export function Select({ className, children, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={cn(
        "rounded-lg border border-slate-300/80 bg-white px-3 py-2 text-sm text-slate-900 shadow-xs transition-colors hover:border-slate-400/70 focus:border-brand-600 focus:outline-none focus:ring-[3px] focus:ring-brand-600/15",
        className
      )}
      {...props}
    >
      {children}
    </select>
  );
}

export function Field({
  label,
  name,
  children,
}: {
  label: string;
  name?: string;
  children: ReactNode;
}) {
  const fieldName = name ?? fieldSlug(label);
  const child = Children.only(children);
  const field = isValidElement(child)
    ? cloneElement(child as ReactElement<{ id?: string; name?: string }>, {
        id: child.props.id ?? fieldName,
        name: child.props.name ?? fieldName,
      })
    : children;

  return (
    <label className="block" htmlFor={fieldName}>
      <span className="mb-1.5 block text-xs font-semibold text-slate-700">{label}</span>
      {field}
    </label>
  );
}

/* ----------------------------- Modal ----------------------------- */
export function Modal({
  open,
  onClose,
  title,
  subtitle,
  children,
  wide,
  dismissible = true,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  subtitle?: string;
  children: ReactNode;
  wide?: boolean;
  /** false = no close "X", Escape and backdrop clicks do nothing — the caller
   *  must offer its own button (used by the system notice's single "OK"). */
  dismissible?: boolean;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && dismissible && onClose();
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = prevOverflow;
      window.removeEventListener("keydown", onKey);
    };
  }, [open, onClose, dismissible]);

  if (!open) return null;
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div
        className="absolute inset-0 bg-slate-950/40 backdrop-blur-[3px] animate-overlay-in"
        onClick={dismissible ? onClose : undefined}
      />
      <div
        className={cn(
          "relative max-h-[90vh] w-full overflow-y-auto rounded-2xl border border-slate-200/80 bg-white p-6 shadow-pop animate-scale-in",
          wide ? "max-w-3xl" : "max-w-lg"
        )}
      >
        <div className="mb-5 flex items-start justify-between gap-4 border-b border-slate-100 pb-4">
          <div>
            <h3 className="font-display text-lg font-semibold tracking-[-0.02em] text-slate-900">{title}</h3>
            {subtitle && <p className="mt-1 text-sm text-slate-500">{subtitle}</p>}
          </div>
          {dismissible && (
            <button
              type="button"
              onClick={onClose}
              aria-label="Close"
              className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600"
            >
              <X className="h-5 w-5" />
            </button>
          )}
        </div>
        {children}
      </div>
    </div>,
    document.body
  );
}

/* ----------------------------- Misc ----------------------------- */
export function Spinner({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "inline-block h-4 w-4 animate-spin rounded-full border-2 border-slate-200 border-t-brand-600",
        className
      )}
    />
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={cn("skeleton", className)} />;
}

export function EmptyState({
  icon,
  title,
  detail,
  action,
}: {
  icon?: ReactNode;
  title: string;
  detail?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 px-6 py-16 text-center">
      <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-white text-slate-400 shadow-card ring-1 ring-slate-200/80">
        {icon ?? <Inbox className="h-7 w-7" />}
      </div>
      <p className="text-sm font-semibold text-slate-800">{title}</p>
      {detail && <p className="max-w-sm text-sm leading-relaxed text-slate-500">{detail}</p>}
      {action && <div className="mt-1">{action}</div>}
    </div>
  );
}

/** Placeholder shown to a role a feature isn't rolled out to yet — a
 *  product/UX choice (the backend route may not even be role-restricted),
 *  not a permissions error. Used standalone (e.g. the Bulk upload tab); for
 *  gating live content underneath instead of replacing it entirely,
 *  blur/disable that content and overlay this (see AgenticChat's own inline
 *  version — a different composition, not reused here). */
export function ComingSoon({ feature }: { feature: string }) {
  return (
    <Card className="flex flex-col items-center justify-center gap-4 p-16 text-center">
      <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-gradient-to-br from-brand-500 to-brand-700 text-white shadow-card">
        <Sparkles className="h-8 w-8" />
      </div>
      <div>
        <p className="text-xs font-bold uppercase tracking-[0.24em] text-brand-600">Coming soon</p>
        <h3 className="mt-2 text-2xl font-bold text-slate-900">Working on something exciting</h3>
        <p className="mx-auto mt-2 max-w-sm text-sm leading-6 text-slate-600">
          {feature} is being polished for non-admin users. More to come soon.
        </p>
      </div>
    </Card>
  );
}

export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4 animate-fade-up">
      <div>
        <h1 className="font-display text-[1.625rem] font-semibold leading-tight tracking-[-0.03em] text-slate-900">{title}</h1>
        {subtitle && <p className="mt-1.5 max-w-2xl text-sm text-slate-500">{subtitle}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}
