import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios from "axios";
import { useState } from "react";
import {
  approveRecord, deleteRecord, recordSources, updateRecord, verifyRecord,
  MONTHS_LONG, fileContentUrl, type TimesheetUpdate
} from "../api/client";
import { Badge, Button, Spinner } from "./ui";
import { Modal, FilePreview, fileKindLabel } from "./Modal";

export function RecordDetailModal({
  recordId,
  onClose,
  onUpdated,
}: {
  recordId: string;
  onClose: () => void;
  onUpdated: () => void;
}) {
  const qc = useQueryClient();
  const [tab, setTab] = useState<"summary" | "files">("summary");
  const [editing, setEditing] = useState(false);
  const [editState, setEditState] = useState<TimesheetUpdate | null>(null);
  const [preview, setPreview] = useState<{ url: string; name: string; ct: string } | null>(null);

  const recordQuery = useQuery({
    queryKey: ["record", recordId],
    queryFn: () => axios.get(`/api/v1/timesheets/${recordId}`).then(r => r.data),
  });
  const record = recordQuery.data;
  const isLoading = recordQuery.isLoading;

  const sources = useQuery({
    queryKey: ["sources", recordId],
    queryFn: () => recordSources(recordId),
  });

  const verifier = useMutation({
    mutationFn: () => verifyRecord(recordId),
    onSuccess: () => { onUpdated(); onClose(); }
  });

  const approver = useMutation({
    mutationFn: (appr: boolean) => approveRecord(recordId, appr),
    onSuccess: () => { 
      qc.invalidateQueries({ queryKey: ["record", recordId] });
      qc.invalidateQueries({ queryKey: ["employeeRecords"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      onUpdated(); 
    }
  });

  const deleter = useMutation({
    mutationFn: () => deleteRecord(recordId),
    onSuccess: () => { onUpdated(); onClose(); }
  });

  const updater = useMutation({
    mutationFn: (body: TimesheetUpdate) => updateRecord(recordId, body),
    onSuccess: () => { 
      setEditing(false); 
      qc.invalidateQueries({ queryKey: ["record", recordId] });
      qc.invalidateQueries({ queryKey: ["employeeRecords"] });
      onUpdated(); 
    }
  });

  if (isLoading || !record) return <Modal open title="Loading..." onClose={onClose}><Spinner /></Modal>;

  return (
    <Modal open title={`${MONTHS_LONG[record.month]} ${record.year} — Submission`} onClose={onClose} width="max-w-5xl">
       <div className="grid gap-10 lg:grid-cols-[1fr_350px]">
          {/* Main Workspace */}
          <div className="space-y-10">
              <div className="flex items-center justify-between gap-4">
                <div className="flex gap-1.5 p-1 bg-slate-50 rounded-[1.25rem] w-fit border border-slate-100">
                   <button onClick={() => setTab("summary")} className={`px-6 py-2.5 text-xs font-bold rounded-xl transition-all ${tab === 'summary' ? 'bg-white shadow-sm text-ink ring-1 ring-slate-100' : 'text-slate-400 hover:text-slate-600'}`}>Analysis Result</button>
                   <button onClick={() => setTab("files")} className={`px-6 py-2.5 text-xs font-bold rounded-xl transition-all ${tab === 'files' ? 'bg-white shadow-sm text-ink ring-1 ring-slate-100' : 'text-slate-400 hover:text-slate-600'}`}>Raw Evidence ({sources.data?.length || 0})</button>
                </div>
                {tab === "summary" && !editing && (
                   <button 
                    onClick={() => {
                      setEditing(true);
                      setEditState({
                        annual_leave_dates: record.annual_leave_dates,
                        sick_leave_dates: record.sick_leave_dates,
                        remote_work_dates: record.remote_work_dates,
                        unpaid_leave_dates: record.unpaid_leave_dates,
                        absent_dates: record.absent_dates,
                        month: record.month,
                        year: record.year
                      });
                    }}
                    className="flex items-center gap-2 px-4 py-2 rounded-xl bg-slate-900 text-white text-[10px] font-bold uppercase tracking-widest hover:bg-black transition-all shadow-lift"
                   >
                     <EditIcon className="w-3.5 h-3.5" /> Edit Analysis
                   </button>
                )}
             </div>

             {tab === "summary" ? (
               <div className="space-y-8 animate-in slide-in-from-left-4 duration-500">
                  {editing && editState ? (
                    <div className="space-y-8 bg-slate-50/50 p-8 rounded-[2.5rem] border border-slate-200 shadow-inner">
                       <div className="flex items-center justify-between gap-4">
                          <h3 className="text-sm font-black text-ink uppercase tracking-widest flex items-center gap-2">
                             <div className="h-2 w-2 rounded-full bg-petrol-500 animate-pulse" /> Modifying Meta-Analysis
                          </h3>
                          <div className="flex items-center gap-2">
                             <button onClick={() => setEditing(false)} className="px-4 py-2 text-[10px] font-bold text-slate-400 hover:text-slate-600 uppercase tracking-widest">Discard</button>
                             <Button size="sm" onClick={() => updater.mutate(editState)} loading={updater.isPending}>Save Changes</Button>
                          </div>
                       </div>

                       <div className="grid gap-6 md:grid-cols-2">
                          <EditBlock 
                            title="Annual Leave" 
                            dates={editState.annual_leave_dates || []} 
                            onChange={(d) => setEditState({...editState, annual_leave_dates: d})} 
                            tone="emerald" 
                          />
                          <EditBlock 
                            title="Sick Leave" 
                            dates={editState.sick_leave_dates || []} 
                            onChange={(d) => setEditState({...editState, sick_leave_dates: d})} 
                            tone="rose" 
                          />
                          <EditBlock 
                            title="Remote Work" 
                            dates={editState.remote_work_dates || []} 
                            onChange={(d) => setEditState({...editState, remote_work_dates: d})} 
                            tone="petrol" 
                          />
                          <EditBlock 
                            title="Unpaid / Absent" 
                            dates={editState.unpaid_leave_dates || []} 
                            onChange={(d) => setEditState({...editState, unpaid_leave_dates: d})} 
                            tone="amber" 
                          />
                       </div>
                    </div>
                  ) : (
                    <>
                      <div className="bg-white rounded-[2rem] border border-slate-200/60 p-8 shadow-sm">
                         <div className="flex items-center justify-between mb-6">
                            <span className="text-[10px] font-bold uppercase tracking-widest text-slate-400">AI Narrative Summary</span>
                            <Badge tone={record.validation_status === 'verified' ? 'emerald' : 'amber'}>{record.validation_status}</Badge>
                         </div>
                         <p className="text-base font-bold text-ink leading-relaxed">“{record.llm_summary}”</p>
                      </div>

                      <div className="grid gap-6 md:grid-cols-2">
                         <DataBlock title="Annual Leave" count={record.annual_leave_count} dates={record.annual_leave_dates} tone="emerald" />
                         <DataBlock title="Sick Leave" count={record.sick_leave_count} dates={record.sick_leave_dates} tone="rose" />
                         <DataBlock title="Remote Work" count={record.remote_work_count} dates={record.remote_work_dates} tone="petrol" />
                         <DataBlock title="Unpaid / Other" count={record.unpaid_leave_count + record.absent_count} dates={[...record.unpaid_leave_dates, ...record.absent_dates]} tone="amber" />
                      </div>

                      {record.hr_flags.length > 0 && (
                        <div className="p-8 bg-rose-50/50 rounded-[2rem] border border-rose-100">
                           <span className="text-[10px] font-bold uppercase tracking-widest text-rose-400 block mb-4">Detected Discrepancies</span>
                           <ul className="space-y-3">
                              {record.hr_flags.map((f: string, i: number) => (
                                <li key={i} className="flex gap-3 text-sm font-bold text-rose-800">
                                   <div className="h-5 w-5 shrink-0 rounded-full bg-white flex items-center justify-center shadow-sm text-rose-500 text-[10px]">!</div>
                                   {f}
                                </li>
                              ))}
                           </ul>
                        </div>
                      )}
                    </>
                  )}
               </div>
             ) : (
               <div className="space-y-6 animate-in slide-in-from-right-4 duration-500">
                  <div className="grid gap-4 sm:grid-cols-2">
                     {sources.data?.map(s => (
                        <button 
                          key={s.rel_path}
                          onClick={() => setPreview({ url: fileContentUrl(s.rel_path), name: s.name, ct: s.content_type })}
                          className="flex items-center justify-between p-5 rounded-3xl bg-white border border-slate-100 hover:border-petrol-500 hover:shadow-soft transition-all text-left"
                        >
                           <div className="flex items-center gap-4">
                              <div className="h-10 w-10 bg-slate-50 rounded-xl flex items-center justify-center text-slate-300">
                                 <FileIcon className="w-5 h-5" />
                              </div>
                              <div>
                                 <p className="text-xs font-bold text-ink uppercase tracking-tight line-clamp-1">{s.name}</p>
                                 <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mt-0.5">{(s.size/1024).toFixed(0)} KB</p>
                              </div>
                           </div>
                        </button>
                     ))}
                  </div>
                  {preview && (
                     <div className="mt-4 pt-6 border-t border-slate-100">
                        <div className="flex items-center justify-between mb-4">
                           <span className="text-[10px] font-bold uppercase tracking-widest text-slate-400">In-situ Preview</span>
                           <button onClick={() => setPreview(null)} className="text-xs font-bold text-rose-500 hover:underline">Close Preview</button>
                        </div>
                        <FilePreview {...preview} />
                     </div>
                  )}
               </div>
             )}
          </div>

          {/* Action Sidebar */}
          <div className="flex flex-col gap-6">
             <div className="rounded-[2.5rem] bg-white border border-slate-200/60 p-8 shadow-soft flex flex-col gap-8">
                <div className="flex items-center justify-between">
                   <div className="flex flex-col gap-1">
                      <span className="text-[10px] font-black uppercase tracking-widest text-slate-400">Approval Status</span>
                      <div className="flex items-center gap-2">
                         <div className={`h-2 w-2 rounded-full ${record.approval_status === 'approved' ? 'bg-emerald-500' : 'bg-slate-300'}`} />
                         <span className="text-sm font-bold text-ink capitalize tracking-tight">{record.approval_status}</span>
                      </div>
                   </div>
                   <button 
                     onClick={() => approver.mutate(record.approval_status !== 'approved')}
                     disabled={approver.isPending}
                     className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus:ring-2 focus:ring-petrol-500 focus:ring-offset-2 ${record.approval_status === 'approved' ? 'bg-emerald-500' : 'bg-slate-300'}`}
                   >
                     <span className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out ${record.approval_status === 'approved' ? 'translate-x-5' : 'translate-x-0'}`} />
                   </button>
                </div>

                {record.approval_detail && (
                  <div className="p-4 bg-slate-50 rounded-2xl border border-slate-100 flex gap-3">
                     <span className="text-slate-300 text-lg">“</span>
                     <p className="text-[10px] font-medium text-slate-500 italic leading-relaxed">{record.approval_detail}</p>
                  </div>
                )}

                <div className="space-y-3 pt-6 border-t border-slate-50">
                   {record.validation_status === 'manual_review' && (
                      <Button className="w-full py-4 shadow-lift" onClick={() => verifier.mutate()} loading={verifier.isPending}>
                        Mark as Verified
                      </Button>
                   )}
                </div>
             </div>

             <div className="rounded-[2.5rem] bg-rose-50/30 border border-rose-100/50 p-8">
                <span className="text-[10px] font-bold uppercase tracking-widest text-rose-400 block mb-4">Destructive Actions</span>
                <p className="text-[10px] font-medium text-slate-400 mb-4 leading-relaxed tracking-tight">Irrevocably remove this month's record from the database. File evidence will persist in the archive.</p>
                <Button variant="danger" className="w-full text-[10px] uppercase font-bold tracking-widest" onClick={() => {
                   if (confirm("Delete Record? This will remove the analysis from the database but keep your files.")) {
                      deleter.mutate();
                   }
                }} loading={deleter.isPending}>
                   Delete Record
                </Button>
             </div>
          </div>
       </div>
    </Modal>
  );
}

function EditBlock({ title, dates, onChange, tone }: { title: string; dates: string[]; onChange: (d: string[]) => void; tone: string }) {
  const [newDate, setNewDate] = useState("");
  const tones: any = {
    emerald: "bg-emerald-50 border-emerald-200 text-emerald-900 border-l-4 border-l-emerald-500",
    rose: "bg-rose-50 border-rose-200 text-rose-900 border-l-4 border-l-rose-500",
    petrol: "bg-petrol-50 border-petrol-200 text-petrol-900 border-l-4 border-l-petrol-500",
    amber: "bg-amber-50 border-amber-200 text-amber-900 border-l-4 border-l-amber-500",
  };

  const add = () => {
    const d = newDate.trim();
    if (d && !dates.includes(d)) {
      onChange([...dates, d].sort());
      setNewDate("");
    }
  };

  return (
    <div className={`p-6 rounded-3xl border ${tones[tone]} shadow-sm flex flex-col gap-4 transition-all`}>
       <div className="flex items-center justify-between">
          <span className="text-[10px] font-black uppercase tracking-widest opacity-80">{title}</span>
          <span className="text-xl font-black">{dates.length}</span>
       </div>
       
       <div className="flex flex-wrap gap-1.5 min-h-[40px]">
          {dates.map(d => (
            <span key={d} className="inline-flex items-center gap-1.5 pl-2 pr-1 py-1 bg-white text-[10px] font-bold rounded-lg shadow-sm border border-black/5">
              {d}
              <button onClick={() => onChange(dates.filter(x => x !== d))} className="h-4 w-4 grid place-items-center rounded-md hover:bg-slate-100 text-slate-300 hover:text-rose-500">×</button>
            </span>
          ))}
       </div>

       <div className="flex gap-2 mt-2">
          <div className="relative flex-1 group">
            <CalendarIcon className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400 group-focus-within:text-petrol-500 pointer-events-none" />
            <input 
              type="date" 
              value={newDate}
              onChange={e => setNewDate(e.target.value)}
              className="w-full pl-9 pr-3 py-2 text-xs font-bold rounded-xl border border-black/10 focus:ring-4 focus:ring-petrol-500/10 outline-none text-black bg-white/50"
            />
          </div>
          <button 
            disabled={!newDate}
            onClick={add} 
            className="px-4 py-2 bg-slate-900 text-white rounded-xl text-[10px] font-black uppercase tracking-widest hover:bg-black disabled:opacity-30 disabled:cursor-not-allowed transition-all"
          >
            Add
          </button>
       </div>
    </div>
  );
}

function DataBlock({ title, count, dates, tone }: { title: string; count: number; dates: string[]; tone: string }) {
  const tones: any = {
    emerald: "bg-emerald-50 border-emerald-100 text-emerald-800",
    rose: "bg-rose-50 border-rose-100 text-rose-800",
    petrol: "bg-petrol-50 border-petrol-100 text-petrol-800",
    amber: "bg-amber-50 border-amber-100 text-amber-800",
  };
  return (
    <div className={`p-6 rounded-[2rem] border ${tones[tone]} shadow-sm`}>
       <div className="flex items-center justify-between mb-4">
          <span className="text-[10px] font-bold uppercase tracking-wider opacity-60 font-sans">{title}</span>
          <span className="text-lg font-bold">{count}</span>
       </div>
       <div className="flex flex-wrap gap-2">
          {dates.length > 0 ? dates.map(d => (
            <span key={d} className="text-[10px] font-bold px-2 py-1 bg-white/50 rounded-lg shadow-sm border border-black/5">{d}</span>
          )) : <span className="text-[10px] font-medium opacity-40">None recorded</span>}
       </div>
    </div>
  );
}

// Minimal single-record fetcher as a fallback
async function fetchRecordSingle(id: string) {
  const axios = (await import("axios")).default;
  return axios.get(`/api/v1/timesheets/${id}`).then(r => r.data);
}

function CalendarIcon({ className }: { className?: string }) { return <svg className={className} width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg> }
function EditIcon({ className }: { className?: string }) { return <svg className={className} width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" /><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" /></svg> }
function FileIcon({ className }: { className?: string }) { return <svg className={className} width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6" /></svg> }
