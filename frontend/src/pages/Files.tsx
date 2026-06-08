import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createFileEmployee, createFileMonth, createFileManager, deleteFolder, fileContentUrl,
  listFileManagers, listFileEmployees, listFileItems, listFileMonths, renameFolder, 
  downloadZipUrl, type FileItem
} from "../api/client";
import { Spinner, Badge, Button } from "../components/ui";
import { ConfirmDialog, FilePreview, Modal, fileKindLabel } from "../components/Modal";

export default function Files() {
  const qc = useQueryClient();
  const [manager, setManager] = useState<string | null>(null);
  const [emp, setEmp] = useState<string | null>(null);
  const [month, setMonth] = useState<string | null>(null);
  const [preview, setPreview] = useState<FileItem | null>(null);
  const [prompt, setPrompt] = useState<{ kind: "mgr" | "emp" | "month" | "rename"; rel?: string } | null>(null);
  const [confirm, setConfirm] = useState<{ rel: string; label: string } | null>(null);

  const managers = useQuery({ queryKey: ["fManagers"], queryFn: listFileManagers });
  const employees = useQuery({ queryKey: ["fEmployees", manager], queryFn: () => listFileEmployees(manager!), enabled: !!manager });
  const months = useQuery({ queryKey: ["fMonths", manager, emp], queryFn: () => listFileMonths(manager!, emp!), enabled: !!manager && !!emp });
  const items = useQuery({ queryKey: ["fItems", manager, emp, month], queryFn: () => listFileItems(manager!, emp!, month!), enabled: !!manager && !!emp && !!month });

  const refresh = () => qc.invalidateQueries({ predicate: (q: any) => String(q.queryKey[0]).startsWith("f") });
  const delFolder = useMutation({ 
    mutationFn: (rel: string) => deleteFolder(rel), 
    onSuccess: () => { setMonth(null); refresh(); } 
  });

  return (
    <div className="space-y-10">
      <header className="flex flex-wrap items-center justify-between gap-6">
        <div>
          <h1 className="text-4xl font-bold tracking-tight text-ink">File Archive</h1>
          <p className="mt-2 text-slate-500 font-medium max-w-xl">
            Centralized repository for processed timesheets. Organized by Account Manager, Employee, and Period. 
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Badge tone="slate">Manual filing enabled</Badge>
          <a href={downloadZipUrl()} download className="inline-flex">
             <Button variant="secondary" className="shadow-sm">
               <DownloadIcon className="w-4 h-4" /> Download Full Library (ZIP)
             </Button>
          </a>
        </div>
      </header>

      <div className="grid gap-8 lg:grid-cols-3">
        {/* Managers */}
        <Column 
          title="Managers" 
          onAdd={() => setPrompt({ kind: "mgr" })} 
          loading={managers.isLoading}
        >
          {(managers.data ?? []).map((m) => (
            <FolderRow
              key={m.rel_path}
              label={m.name}
              meta={`${m.employee_count} employee(s)`}
              active={manager === m.name}
              onClick={() => { setManager(m.name); setEmp(null); setMonth(null); }}
              onRename={() => setPrompt({ kind: "rename", rel: m.rel_path })}
              onDelete={() => setConfirm({ rel: m.rel_path, label: m.name })}
              onDownload={() => window.open(downloadZipUrl(m.name))}
            />
          ))}
          {managers.data && managers.data.length === 0 && <Empty text="No manager folders yet." />}
        </Column>

        {/* Employees */}
        <Column
          title={manager ? `${manager} — Team` : "Select Manager"}
          onAdd={manager ? () => setPrompt({ kind: "emp" }) : undefined}
          loading={!!manager && employees.isLoading}
        >
          {!manager ? <Empty text="Start by selecting a manager." icon={<UserIcon className="w-8 h-8 opacity-20" />} /> : (employees.data ?? []).map((e) => (
            <FolderRow
              key={e.rel_path}
              label={e.name}
              meta={`${e.month_count} registered month(s)`}
              active={emp === e.name}
              onClick={() => { setEmp(e.name); setMonth(null); }}
              onRename={() => setPrompt({ kind: "rename", rel: e.rel_path })}
              onDelete={() => setConfirm({ rel: e.rel_path, label: e.name })}
            />
          ))}
          {manager && employees.data && employees.data.length === 0 && <Empty text="No employee folders assigned." />}
        </Column>

        {/* Months / Files Combined */}
        <div className="flex flex-col gap-6">
          <Column
            title={emp ? `${emp} — History` : "Select Employee"}
            onAdd={emp ? () => setPrompt({ kind: "month" }) : undefined}
            loading={!!emp && months.isLoading}
          >
            {!emp ? <Empty text="Pick an employee to view months." icon={<CalendarIcon className="w-8 h-8 opacity-20" />} /> : (months.data ?? []).map((m) => (
              <FolderRow
                key={m.rel_path}
                label={m.name}
                meta={`${m.file_count} documents`}
                active={month === m.name}
                onClick={() => setMonth(m.name)}
                onRename={() => setPrompt({ kind: "rename", rel: m.rel_path })}
                onDelete={() => setConfirm({ rel: m.rel_path, label: `${emp}/${m.name}` })}
              />
            ))}
            {emp && months.data && months.data.length === 0 && <Empty text="No monthly records found." />}
          </Column>

          <div className="rounded-[2.5rem] border border-slate-200 bg-white shadow-soft overflow-hidden">
             <div className="bg-slate-50/50 px-8 py-5 border-b border-slate-100 flex items-center justify-between">
                <span className="text-[10px] font-bold uppercase tracking-widest text-slate-400">Selected Files</span>
                {month && <Badge tone="petrol">{items.data?.length || 0} Files</Badge>}
             </div>
             <div className="p-4 space-y-2 max-h-[300px] overflow-auto custom-scrollbar">
                {!month ? (
                  <div className="py-12 text-center">
                    <FileIcon className="w-8 h-8 text-slate-100 mx-auto mb-2" />
                    <p className="text-[10px] font-bold text-slate-300 uppercase tracking-widest leading-loose">No month selected</p>
                  </div>
                ) : items.data?.length === 0 ? (
                  <Empty text="Empty directory." />
                ) : (items.data ?? []).map((f) => (
                  <button 
                    key={f.rel_path} 
                    onClick={() => setPreview(f)}
                    className="flex w-full items-center justify-between rounded-2xl border border-slate-100 p-4 text-left shadow-sm group hover:border-petrol-500 hover:bg-petrol-50/30 transition-all duration-200"
                  >
                    <div className="flex items-center gap-3 min-w-0">
                       <FileIcon className="w-4 h-4 text-slate-300 group-hover:text-petrol-500 transition-colors" />
                       <span className="truncate text-xs font-bold text-ink uppercase tracking-tight">{f.name}</span>
                    </div>
                    <span className="ml-3 shrink-0 text-[10px] font-bold text-slate-400 font-mono">{(f.size / 1024).toFixed(0)} KB</span>
                  </button>
                ))}
             </div>
          </div>
        </div>
      </div>

      {/* Preview Modal */}
      <Modal open={!!preview} title={preview?.name ?? ""} onClose={() => setPreview(null)} width="max-w-4xl">
        {preview && <FilePreview url={fileContentUrl(preview.rel_path)} name={preview.name} contentType={preview.content_type} />}
      </Modal>

      {/* Modals for Input */}
      <ManagerNamePrompt
        prompt={prompt}
        onClose={() => setPrompt(null)}
        onSubmit={async (value) => {
          if (!prompt) return;
          if (prompt.kind === "mgr") await createFileManager(value);
          else if (prompt.kind === "emp" && manager) await createFileEmployee(manager, value);
          else if (prompt.kind === "month" && manager && emp) await createFileMonth(manager, emp, value);
          else if (prompt.kind === "rename" && prompt.rel) await renameFolder(prompt.rel, value);
          setPrompt(null);
          refresh();
        }}
      />

      <ConfirmDialog
        open={!!confirm}
        title="Permanently remove?"
        danger
        confirmLabel="Destroy"
        message={`This will irrevocably delete "${confirm?.label}" and all child documents inside it. This action is synced to storage.`}
        onConfirm={() => confirm && delFolder.mutate(confirm.rel)}
        onClose={() => setConfirm(null)}
      />
    </div>
  );
}

function Column({ title, onAdd, loading, children }: { title: string; onAdd?: () => void; loading?: boolean; children: React.ReactNode }) {
  return (
    <div className="bg-white rounded-[2.5rem] border border-slate-200 shadow-soft overflow-hidden flex flex-col">
      <div className="flex items-center justify-between border-b border-slate-100 px-8 py-5 bg-slate-50/50">
        <span className="text-[10px] font-bold uppercase tracking-[0.2em] text-slate-400">{title}</span>
        {onAdd && (
          <button onClick={onAdd} className="h-8 w-8 grid place-items-center rounded-xl bg-petrol-600 text-white shadow-lg shadow-petrol-200 hover:bg-petrol-700 transition-all active:scale-90">
            <PlusIcon className="w-5 h-5" />
          </button>
        )}
      </div>
      <div className="flex-1 max-h-[60vh] space-y-2 overflow-auto p-5 custom-scrollbar">{loading ? <div className="py-20"><Spinner /></div> : children}</div>
    </div>
  );
}

function FolderRow({ label, meta, active, onClick, onRename, onDelete, onDownload }: {
  label: string; meta: string; active: boolean; onClick: () => void; onRename: () => void; onDelete: () => void; onDownload?: () => void;
}) {
  return (
    <div className={`group flex items-center justify-between rounded-2xl border p-4 transition-all duration-300 relative ${active ? "bg-white border-petrol-500 shadow-lift ring-1 ring-petrol-100" : "bg-white border-slate-100/80 hover:bg-slate-50 shadow-sm"}`}>
      {active && <div className="absolute left-0 top-1/2 -translate-y-1/2 w-1.5 h-6 bg-petrol-500 rounded-r-full" />}
      <button onClick={onClick} className="flex min-w-0 items-center gap-4 text-left">
        <div className={`h-10 w-10 shrink-0 rounded-xl flex items-center justify-center transition-all duration-300 ${active ? 'bg-petrol-500 text-white shadow-lg shadow-petrol-200' : 'bg-slate-50 text-slate-300 group-hover:text-petrol-400'}`}>
           <FolderIcon className="w-5 h-5" />
        </div>
        <div className="min-w-0">
          <span className="block truncate text-xs font-bold text-ink uppercase tracking-tight">{label}</span>
          <span className="block text-[10px] font-bold text-slate-400 uppercase tracking-widest mt-1 opacity-70">{meta}</span>
        </div>
      </button>
      <div className={`flex shrink-0 gap-1.5 transition-all duration-300 ${active ? 'opacity-100' : 'opacity-0 scale-95 pointer-events-none group-hover:opacity-100 group-hover:scale-100 group-hover:pointer-events-auto'}`}>
        {onDownload && (
          <button onClick={onDownload} title="Download ZIP" className="h-8 w-8 grid place-items-center rounded-xl bg-slate-100 text-slate-500 hover:bg-sky-50 hover:text-sky-600 transition-colors">
            <DownloadIcon className="w-4 h-4" />
          </button>
        )}
        <button onClick={onRename} title="Rename" className="h-8 w-8 grid place-items-center rounded-xl bg-slate-100 text-slate-500 hover:bg-amber-50 hover:text-amber-600 transition-colors">
          <EditIcon className="w-4 h-4" />
        </button>
        <button onClick={onDelete} title="Delete" className="h-8 w-8 grid place-items-center rounded-xl bg-slate-100 text-slate-500 hover:bg-rose-50 hover:text-rose-600 transition-colors">
          <TrashIcon className="w-4 h-4" />
        </button>
      </div>
    </div>
  );
}

function ManagerNamePrompt({ prompt, onClose, onSubmit }: {
  prompt: { kind: "mgr" | "emp" | "month" | "rename" } | null; onClose: () => void; onSubmit: (v: string) => void;
}) {
  const [val, setVal] = useState("");
  const titles: any = { mgr: "New Manager Cluster", emp: "Assign Employee Folder", month: "Define Period Label", rename: "Rename Directory" };
  const ph = prompt?.kind === "month" ? "e.g. March-2026" : "Folder Label";
  
  return (
    <Modal open={!!prompt} title={titles[prompt?.kind!] || "Folder Action"} onClose={() => { setVal(""); onClose(); }} width="max-w-md">
      <div className="space-y-6 pt-2">
        <label className="block">
          <span className="text-[10px] font-bold uppercase tracking-[0.2em] text-slate-400 block mb-3 pl-1">Directory Name</span>
          <input 
            autoFocus 
            value={val} 
            onChange={(e) => setVal(e.target.value)} 
            placeholder={ph}
            onKeyDown={(e) => { if (e.key === "Enter" && val.trim()) { onSubmit(val.trim()); setVal(""); } }}
            className="w-full rounded-[1.25rem] border border-slate-200 bg-slate-50 px-5 py-4 text-sm font-bold text-ink outline-none focus:ring-4 focus:ring-petrol-500/10 focus:border-petrol-300 transition-all font-mono tracking-tight" 
          />
        </label>
        
        <div className="flex justify-end gap-3 border-t border-slate-100 pt-6">
          <Button variant="secondary" onClick={() => { setVal(""); onClose(); }}>Cancel</Button>
          <Button onClick={() => { if (val.trim()) { onSubmit(val.trim()); setVal(""); } }}>Commit Changes</Button>
        </div>
      </div>
    </Modal>
  );
}

function Empty({ text, icon }: { text: string; icon?: any }) {
  return (
    <div className="flex flex-col items-center justify-center py-20 px-8 opacity-40">
       {icon || <InboxIcon className="w-8 h-8 mb-4" />}
       <p className="text-[11px] font-bold uppercase tracking-[0.2em] text-center leading-loose">{text}</p>
    </div>
  );
}

// Icons
function FolderIcon({ className }: { className?: string }) { return <svg className={className} width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" /></svg> }
function FileIcon({ className }: { className?: string }) { return <svg className={className} width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6" /></svg> }
function PlusIcon({ className }: { className?: string }) { return <svg className={className} width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg> }
function EditIcon({ className }: { className?: string }) { return <svg className={className} width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/></svg> }
function TrashIcon({ className }: { className?: string }) { return <svg className={className} width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M3 6h18M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2M10 11v6M14 11v6"/></svg> }
function DownloadIcon({ className }: { className?: string }) { return <svg className={className} width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="7 10 12 15 17 10" /><line x1="12" y1="15" x2="12" y2="3" /></svg> }
function InboxIcon({ className }: { className?: string }) { return <svg className={className} width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><polyline points="22 12 16 12 14 15 10 15 8 12 2 12" /><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z" /></svg> }
function UserIcon({ className }: { className?: string }) { return <svg className={className} width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2" /><circle cx="12" cy="7" r="4" /></svg> }
function CalendarIcon({ className }: { className?: string }) { return <svg className={className} width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><rect x="3" y="4" width="18" height="18" rx="2" ry="2" /><line x1="16" y1="2" x2="16" y2="6" /><line x1="8" y1="2" x2="8" y2="6" /><line x1="3" y1="10" x2="21" y2="10" /></svg> }
