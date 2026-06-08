import { ReactNode, useEffect } from "react";
import { createPortal } from "react-dom";

export function Modal({ 
  open, 
  title, 
  onClose, 
  children, 
  width = "max-w-2xl" 
}: { 
  open: boolean; 
  title: string; 
  onClose: () => void; 
  children: ReactNode;
  width?: string;
}) {
  useEffect(() => {
    if (open) document.body.classList.add("modal-open");
    else document.body.classList.remove("modal-open");
    
    return () => {
      document.body.classList.remove("modal-open");
    };
  }, [open]);

  if (!open) return null;

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center p-6 md:p-12 overflow-hidden">
      <div className="absolute inset-0 bg-ink/60 backdrop-blur-sm animate-in fade-in duration-300" onClick={onClose} />
      <div className={`relative w-full ${width} max-h-full bg-white rounded-[2.5rem] shadow-2xl flex flex-col border border-white/20 animate-in zoom-in-95 slide-in-from-bottom-8 duration-500`}>
        <div className="flex items-center justify-between px-10 py-8 border-b border-slate-100">
          <h2 className="text-2xl font-bold tracking-tight text-ink">{title}</h2>
          <button onClick={onClose} className="h-10 w-10 grid place-items-center rounded-xl hover:bg-slate-50 text-slate-300 hover:text-ink transition-all">
            <XIcon className="w-6 h-6" />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-10 py-8 custom-scrollbar">
          {children}
        </div>
      </div>
    </div>,
    document.body
  );
}

export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = "Confirm",
  danger = false,
  onConfirm,
  onClose
}: {
  open: boolean;
  title: string;
  message: string;
  confirmLabel?: string;
  danger?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}) {
  return (
    <Modal open={open} title={title} onClose={onClose} width="max-w-md">
      <div className="space-y-6">
        <p className="text-sm font-medium text-slate-500 leading-relaxed">{message}</p>
        <div className="flex justify-end gap-3 pt-4">
           <button onClick={onClose} className="px-6 py-3 text-xs font-bold text-slate-500 hover:text-ink transition-colors uppercase tracking-widest">Cancel</button>
           <button 
            onClick={() => { onConfirm(); onClose(); }}
            className={`px-8 py-3 rounded-2xl text-xs font-bold text-white shadow-lift transition-all active:scale-95 uppercase tracking-widest ${danger ? 'bg-rose-500 hover:bg-rose-600 shadow-rose-200' : 'bg-petrol-600 hover:bg-petrol-700 shadow-petrol-200'}`}
           >
             {confirmLabel}
           </button>
        </div>
      </div>
    </Modal>
  );
}

export function FilePreview({ url, name, contentType }: { url: string; name: string; contentType?: string }) {
  const isImage = contentType?.startsWith("image/");
  const isPdf = contentType === "application/pdf" || name.toLowerCase().endsWith(".pdf");

  if (isImage) {
    return (
      <div className="flex flex-col gap-6">
        <div className="rounded-[2rem] border border-slate-200 overflow-hidden bg-slate-900 shadow-lift group relative">
          <img src={url} alt={name} className="max-h-[70vh] w-full object-contain" />
          <div className="absolute inset-0 bg-black/0 group-hover:bg-black/10 transition-all pointer-events-none" />
        </div>
        <div className="flex items-center justify-between px-4">
          <span className="text-xs font-bold text-slate-400 font-mono tracking-tighter uppercase">{name}</span>
          <a href={url} target="_blank" rel="noreferrer" className="text-xs font-bold text-petrol-600 hover:underline">View Full Size</a>
        </div>
      </div>
    );
  }
  
  if (isPdf) {
    return (
      <div className="h-[75vh] w-full flex flex-col gap-4">
        <iframe 
          src={`${url}#toolbar=0`} 
          className="flex-1 w-full rounded-[2rem] border border-slate-200 shadow-lift bg-slate-50"
          title={name}
        />
        <div className="flex items-center justify-between px-4">
          <span className="text-xs font-bold text-slate-400 font-mono tracking-tighter uppercase">{name}</span>
          <a href={url} target="_blank" rel="noreferrer" className="text-xs font-bold text-petrol-600 hover:underline">Open in Browser</a>
        </div>
      </div>
    );
  }

  return (
    <div className="py-20 flex flex-col items-center justify-center bg-slate-50 rounded-[2rem] border-2 border-dashed border-slate-200">
      <div className="h-20 w-20 bg-white rounded-3xl shadow-soft flex items-center justify-center mb-6 ring-1 ring-slate-100">
         <FileIcon className="w-10 h-10 text-slate-200" />
      </div>
      <p className="text-sm font-bold text-ink mb-1">{name}</p>
      <p className="text-xs font-medium text-slate-400 mb-8 italic">Generic or unknown content type</p>
      <div className="flex gap-4">
        <a href={url} download={name} className="px-8 py-3 bg-white rounded-2xl text-xs font-bold text-slate-700 shadow-sm border border-slate-200 hover:border-petrol-500 hover:text-petrol-700 transition-all uppercase tracking-widest">
          Download File
        </a>
        <a href={url} target="_blank" rel="noreferrer" className="px-8 py-3 bg-slate-900 rounded-2xl text-xs font-bold text-white shadow-lift hover:bg-black transition-all uppercase tracking-widest">
          Force Open
        </a>
      </div>
    </div>
  );
}

export function fileKindLabel(kind: string) {
  if (kind === "timesheet") return "Timesheet Document";
  if (kind === "approval_screenshot") return "Manager Approval";
  return "Supporting File";
}

function XIcon({ className }: { className?: string }) { return <svg className={className} width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M18 6 6 18M6 6l12 12"/></svg> }
function FileIcon({ className }: { className?: string }) { return <svg className={className} width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6" /></svg> }
