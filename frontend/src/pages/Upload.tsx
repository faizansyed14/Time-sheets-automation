import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { uploadTimesheets, MONTHS_LONG, type UploadResult } from "../api/client";
import { Badge, Button, useGlobalProgress } from "../components/ui";

export default function Upload() {
  const qc = useQueryClient();
  const { isProcessing, setIsProcessing } = useGlobalProgress();
  const inputRef = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [results, setResults] = useState<UploadResult[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [drag, setDrag] = useState(false);

  const add = (list: FileList | null) => {
    if (!list) return;
    setFiles((prev) => [...prev, ...Array.from(list)]);
    setResults(null); 
    setError(null);
  };

  const run = async () => {
    if (!files.length) return;
    setIsProcessing(true);
    setError(null);
    setResults(null);
    try {
      const res = await uploadTimesheets(files);
      setResults(res);
      setFiles([]);
      qc.invalidateQueries({ queryKey: ["dashboard"] });
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? "High-volume upload failed. Please check file formats.");
    } finally {
      setIsProcessing(false);
    }
  };

  return (
    <div className="space-y-10">
      <header>
        <h1 className="text-4xl font-bold tracking-tight text-ink">Direct Upload</h1>
        <p className="mt-2 text-slate-500 font-medium max-w-xl">
          Instantly process timesheets via manual drop. Supports batch extraction for PDF, DOCX, and high-res images.
        </p>
      </header>

      {/* Dropzone Container */}
      <div className="grid gap-8 lg:grid-cols-2">
        <div
          onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => { e.preventDefault(); setDrag(false); add(e.dataTransfer.files); }}
          className={`relative group cursor-pointer h-80 rounded-[2.5rem] border-2 border-dashed transition-all duration-500 flex flex-col items-center justify-center p-12 overflow-hidden ${
            drag ? "border-petrol-400 bg-petrol-50/50 scale-[1.01]" : "border-slate-200 bg-white hover:border-petrol-300 hover:shadow-soft"
          }`}
          onClick={() => inputRef.current?.click()}
        >
          {/* Animated Background Mesh */}
          <div className="absolute inset-0 opacity-[0.03] group-hover:opacity-[0.06] transition-opacity pointer-events-none">
             <div className="absolute -top-20 -left-20 w-80 h-80 bg-petrol-500 rounded-full blur-[100px] animate-pulse" />
             <div className="absolute -bottom-20 -right-20 w-80 h-80 bg-sky-500 rounded-full blur-[100px] animate-pulse" style={{animationDelay: '1s'}} />
          </div>

          <input
            ref={inputRef}
            type="file"
            multiple
            accept=".pdf,.docx,.xlsx,.png,.jpg,.jpeg,.eml"
            className="hidden"
            onChange={(e) => add(e.target.files)}
          />
          
          <div className="relative">
            <div className={`h-20 w-20 rounded-3xl mb-6 flex items-center justify-center transition-all duration-500 ${drag ? 'bg-petrol-500 text-white shadow-lift' : 'bg-slate-50 text-slate-400'}`}>
              <UploadCloudIcon className="w-10 h-10" />
            </div>
          </div>
          
          <div className="text-center relative z-10">
            <p className="text-base font-bold text-ink mb-1">Drop documents here to start extraction</p>
            <p className="text-xs font-bold text-slate-400 uppercase tracking-widest">Supports PDF, Word, Images & Excel</p>
          </div>
        </div>

        {/* Queue Panel */}
        <div className="flex flex-col">
           <div className={`flex-1 rounded-[2.5rem] border border-slate-200/60 bg-white p-8 shadow-soft flex flex-col ${files.length === 0 ? 'items-center justify-center border-dashed' : ''}`}>
             {files.length === 0 ? (
               <div className="text-center">
                 <div className="h-12 w-12 bg-slate-50 rounded-2xl flex items-center justify-center mx-auto mb-4">
                   <ListIcon className="w-6 h-6 text-slate-200" />
                 </div>
                 <p className="text-sm font-bold text-slate-300 uppercase tracking-widest leading-loose">Queue is currently empty</p>
               </div>
             ) : (
               <div className="w-full h-full flex flex-col">
                 <div className="flex items-center justify-between mb-8">
                    <span className="text-[11px] font-bold text-slate-400 uppercase tracking-[0.2em] px-2 border-l-4 border-petrol-500">Processing Queue</span>
                    <Badge tone="petrol">{files.length} Files Selected</Badge>
                 </div>
                 
                 <div className="flex-1 space-y-3 overflow-y-auto max-h-[300px] pr-2 custom-scrollbar">
                    {files.map((f, i) => (
                      <div key={i} className="group flex items-center justify-between p-4 rounded-2xl bg-slate-50 border border-slate-100 hover:bg-white hover:border-slate-200 transition-all duration-200 shadow-sm">
                        <div className="flex items-center gap-4 min-w-0">
                          <div className="h-10 w-10 shrink-0 bg-white rounded-xl flex items-center justify-center shadow-sm text-slate-300 group-hover:text-petrol-500 transition-colors">
                            <FileIcon className="w-5 h-5" />
                          </div>
                          <div className="truncate pr-4">
                            <p className="text-xs font-bold text-ink truncate uppercase tracking-tight">{f.name}</p>
                            <p className="text-[10px] font-medium text-slate-400">{(f.size / 1024).toFixed(0)} KB • Pending Analysis</p>
                          </div>
                        </div>
                        <button
                          onClick={() => setFiles((p) => p.filter((_, idx) => idx !== i))}
                          className="h-8 w-8 rounded-lg text-slate-300 hover:text-rose-500 hover:bg-rose-50 transition-all"
                        >
                          <TrashIcon className="w-4 h-4 mx-auto" />
                        </button>
                      </div>
                    ))}
                 </div>

                 <div className="mt-8 pt-6 border-t border-slate-100 flex items-center justify-between">
                    <button onClick={() => setFiles([])} className="text-xs font-bold text-slate-400 hover:text-ink transition-colors uppercase tracking-widest px-2">Clear All</button>
                    <Button 
                      onClick={run} 
                      loading={isProcessing} 
                      className="px-10 py-4 shadow-lift"
                    >
                      Extract & Match Identities
                    </Button>
                 </div>
               </div>
             )}
           </div>
        </div>
      </div>

      {error && (
        <div className="rounded-3xl border border-rose-200 bg-rose-50/50 p-6 flex items-center gap-4 animate-in slide-in-from-top-2">
           <div className="h-10 w-10 shrink-0 bg-white shadow-sm rounded-xl flex items-center justify-center text-rose-500">
             <AlertIcon className="w-5 h-5" />
           </div>
           <p className="text-sm font-bold text-rose-700">{error}</p>
        </div>
      )}

      {results && (
        <div className="space-y-6 animate-in slide-in-from-bottom-8 duration-700">
          <div className="flex items-center justify-between px-2">
            <h2 className="text-xl font-bold tracking-tight text-ink">Extraction Summary</h2>
            <Badge tone="emerald">Success</Badge>
          </div>
          
          <div className="overflow-hidden rounded-[2.5rem] border border-slate-200/60 bg-white shadow-lift">
             <table className="w-full text-left text-sm border-separate border-spacing-0">
               <thead>
                 <tr className="bg-slate-50/50 text-[10px] font-bold uppercase tracking-[0.15em] text-slate-400">
                    <th className="px-10 py-5 border-b border-slate-100">Identity Result</th>
                    <th className="px-10 py-5 border-b border-slate-100">Validation Status</th>
                    <th className="px-10 py-5 border-b border-slate-100">AI Intelligence Summary</th>
                 </tr>
               </thead>
               <tbody className="divide-y divide-slate-50">
                  {results.map((r) => (
                    <tr key={r.record_id} className="group hover:bg-slate-50/30 transition-colors">
                      <td className="px-10 py-6">
                         <div className="flex flex-col">
                           <span className="font-bold text-ink uppercase tracking-tight">{r.employee_name ?? "Unknown"}</span>
                           <span className="text-[10px] font-bold text-slate-400 font-mono mt-0.5 tracking-tighter">
                             {r.employee_id ?? "UNSPECIFIED"} • {r.month > 0 ? `${MONTHS_LONG[r.month]} ${r.year}` : '-'}
                           </span>
                         </div>
                      </td>
                      <td className="px-10 py-6">
                        {r.validation_status === "verified" ? <Badge tone="emerald">Verified</Badge> : <Badge tone="amber">Audit Needed</Badge>}
                      </td>
                      <td className="px-10 py-6">
                        <div className="max-w-md">
                          <p className="text-xs font-bold text-slate-600 line-clamp-1">{r.llm_summary}</p>
                          {r.match_note && <p className="text-[10px] font-medium text-slate-400 mt-1 italic italic-slate-300">“{r.match_note}”</p>}
                        </div>
                      </td>
                    </tr>
                  ))}
               </tbody>
             </table>
             <div className="px-10 py-6 bg-slate-50 flex items-center justify-between border-t border-slate-100">
                <span className="text-xs font-bold text-slate-400 uppercase tracking-widest italic">All results committed to archive metadata</span>
                <Link to="/" className="text-xs font-bold text-petrol-600 hover:text-petrol-700 flex items-center gap-2 group transition-all uppercase tracking-widest pr-4">
                  Go to Dashboard <ArrowRightIcon className="w-3 h-3 group-hover:translate-x-1 transition-transform" />
                </Link>
             </div>
          </div>
        </div>
      )}
    </div>
  );
}

// Icons
function UploadCloudIcon({ className }: { className?: string }) { return <svg className={className} width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M4 14.899A7 7 0 1 1 15.71 8h1.79a4.5 4.5 0 0 1 2.5 8.242M12 12v9m-4-4 4 4 4-4"/></svg> }
function ListIcon({ className }: { className?: string }) { return <svg className={className} width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg> }
function TrashIcon({ className }: { className?: string }) { return <svg className={className} width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M3 6h18M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2M10 11v6M14 11v6"/></svg> }
function FileIcon({ className }: { className?: string }) { return <svg className={className} width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6" /></svg> }
function AlertIcon({ className }: { className?: string }) { return <svg className={className} width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg> }
function ArrowRightIcon({ className }: { className?: string }) { return <svg className={className} width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M5 12h14M12 5l7 7-7 7"/></svg> }
