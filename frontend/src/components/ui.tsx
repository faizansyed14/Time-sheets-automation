import { createContext, useContext, useState, ReactNode } from "react";

interface ProgressContextType {
  isProcessing: boolean;
  setIsProcessing: (val: boolean) => void;
}

const ProgressContext = createContext<ProgressContextType | undefined>(undefined);

export function ProgressProvider({ children }: { children: ReactNode }) {
  const [isProcessing, setIsProcessing] = useState(false);
  return (
    <ProgressContext.Provider value={{ isProcessing, setIsProcessing }}>
      {children}
    </ProgressContext.Provider>
  );
}

export function useGlobalProgress() {
  const context = useContext(ProgressContext);
  if (!context) throw new Error("useGlobalProgress must be used within ProgressProvider");
  return context;
}

export function TopProgressBar({ active }: { active: boolean }) {
  if (!active) return null;
  return (
    <div className="fixed top-0 left-0 right-0 z-[100] h-1.5 bg-petrol-100 overflow-hidden">
      <div className="h-full bg-petrol-600 w-full origin-left animate-pulse progress-bar-shine" 
           style={{ animation: 'indeterminate 2s infinite linear' }} />
      <style>{`
        @keyframes indeterminate {
          0% { transform: translateX(-100%) scaleX(0.2); }
          50% { transform: translateX(0%) scaleX(0.5); }
          100% { transform: translateX(100%) scaleX(0.2); }
        }
      `}</style>
    </div>
  );
}

export function Button({ 
  children, 
  variant = 'primary', 
  size = 'md', 
  className = '', 
  loading = false,
  ...props 
}: any) {
  const base = "inline-flex items-center justify-center gap-2 rounded-xl font-semibold transition-all duration-200 active:scale-[0.98] disabled:opacity-50 disabled:pointer-events-none";
  const variants: any = {
    primary: "bg-petrol-600 text-white shadow-sm hover:bg-petrol-700 hover:shadow-petrol-200/50",
    secondary: "bg-white text-ink border border-slate-200 shadow-sm hover:bg-slate-50 hover:border-slate-300",
    ghost: "bg-transparent text-slate-600 hover:bg-slate-100 hover:text-ink",
    danger: "bg-rose-50 text-rose-600 hover:bg-rose-100 border border-rose-100",
  };
  const sizes: any = {
    sm: "px-3 py-1.5 text-xs",
    md: "px-5 py-2.5 text-sm",
    lg: "px-6 py-3 text-base",
  };

  return (
    <button className={`${base} ${variants[variant]} ${sizes[size]} ${className}`} disabled={loading} {...props}>
      {loading && <span className="h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent" />}
      {children}
    </button>
  );
}

export function Badge({ children, tone = 'slate' }: { children: ReactNode, tone?: any }) {
  const tones: any = {
    slate: "bg-slate-100 text-slate-600 ring-slate-200/50",
    emerald: "bg-emerald-50 text-emerald-700 ring-emerald-200/50",
    amber: "bg-amber-50 text-amber-700 ring-amber-200/50",
    rose: "bg-rose-50 text-rose-700 ring-rose-200/50",
    petrol: "bg-petrol-50 text-petrol-700 ring-petrol-200/50",
  };
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-bold uppercase tracking-wider ring-1 ${tones[tone]}`}>
      {children}
    </span>
  );
}

export function Spinner({ label = "Loading contents..." }: { label?: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-12 gap-4">
      <div className="relative h-10 w-10">
        <div className="absolute inset-0 rounded-full border-4 border-slate-100" />
        <div className="absolute inset-0 rounded-full border-4 border-petrol-500 border-t-transparent animate-spin" />
      </div>
      <p className="text-sm font-medium text-slate-400">{label}</p>
    </div>
  );
}

export function StatusDot({ status, labelOverride }: { status: "green" | "yellow" | "rose"; labelOverride?: string }) {
  const tones: any = {
    green: "text-emerald-600 bg-emerald-500",
    yellow: "text-amber-600 bg-amber-500",
    rose: "text-rose-600 bg-rose-500",
  };
  
  const current = status || "yellow";

  return (
    <div className="flex items-center gap-2">
      <div className={`h-1.5 w-1.5 rounded-full ${tones[current].split(' ')[1]}`} />
      <span className={`text-[11px] font-bold uppercase tracking-widest ${tones[current].split(' ')[0]}`}>
        {labelOverride || (current === 'green' ? "Verified" : current === 'rose' ? "Pending Approval" : "Review Required")}
      </span>
    </div>
  );
}
