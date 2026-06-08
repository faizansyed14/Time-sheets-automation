import { useRef, useState, useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createEmployee, deleteEmployee, fetchEmployeeMatcher, importEmployees, updateEmployee,
  type Employee, type EmployeeInput
} from "../api/client";
import { Badge, Button, Spinner } from "../components/ui";
import { ConfirmDialog, Modal } from "../components/Modal";

export default function EmployeeMatcher() {
  const qc = useQueryClient();
  const importRef = useRef<HTMLInputElement>(null);
  const [q, setQ] = useState("");
  const [filterLoc, setFilterLoc] = useState("");
  const [filterMgr, setFilterMgr] = useState("");
  
  const [editing, setEditing] = useState<Employee | "new" | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<Employee | null>(null);
  const [importResults, setImportResults] = useState<{ summary: any, showSkips: boolean } | null>(null);

  const { data: list = [], isLoading } = useQuery({ queryKey: ["employeeMatcher"], queryFn: fetchEmployeeMatcher });

  // Compute unique managers for the dropdown
  const managerList = useMemo(() => {
    const managers = new Set(list.map(e => e.account_manager).filter(Boolean));
    return Array.from(managers).sort() as string[];
  }, [list]);

  const rows = list.filter((e) => {
    const t = q.toLowerCase();
    const matchesSearch = e.name.toLowerCase().includes(t) || e.employee_id.toLowerCase().includes(t) || (e.project ?? "").toLowerCase().includes(t);
    const matchesLoc = filterLoc === "" || e.location === filterLoc;
    const matchesMgr = filterMgr === "" || e.account_manager === filterMgr;
    return matchesSearch && matchesLoc && matchesMgr;
  });

  const importer = useMutation({
    mutationFn: (file: File) => importEmployees(file),
    onSuccess: (res) => {
      setImportResults({ summary: res, showSkips: false });
      qc.invalidateQueries({ queryKey: ["employeeMatcher"] });
    }
  });

  const save = useMutation({
    mutationFn: (payload: { id?: string; input: EmployeeInput }) =>
      payload.id ? updateEmployee(payload.id, payload.input) : createEmployee(payload.input),
    onSuccess: () => { setEditing(null); qc.invalidateQueries({ queryKey: ["employeeMatcher"] }); }
  });

  const del = useMutation({
    mutationFn: (id: string) => deleteEmployee(id),
    onSuccess: () => { setConfirmDelete(null); qc.invalidateQueries({ queryKey: ["employeeMatcher"] }); }
  });

  return (
    <div className="h-[calc(100vh-140px)] flex flex-col space-y-4">
      <header className="flex flex-wrap items-center justify-between gap-4 shrink-0 px-2">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-black">Employee Matcher</h1>
          <p className="mt-1 text-xs font-semibold text-slate-500 max-w-xl">
            Identity database for DXB/AUH project assignments. Manage real employee mappings here.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <input ref={importRef} type="file" hidden accept=".xlsx,.xls" onChange={(e) => e.target.files?.[0] && importer.mutate(e.target.files[0])} />
          <Button variant="secondary" onClick={() => importRef.current?.click()} loading={importer.isPending}>
            <ImportIcon className="w-4 h-4 mr-1" /> Import Excel
          </Button>
          <Button onClick={() => setEditing("new")} className="shadow-lift px-6">
            <PlusIcon className="w-4 h-4 mr-1" /> Add Identity
          </Button>
        </div>
      </header>

      {importResults && (
        <div className="mx-2 p-6 rounded-[2rem] bg-emerald-50 border border-emerald-100 flex items-center justify-between animate-in slide-in-from-top-4">
           <div className="flex items-center gap-6">
              <div className="h-12 w-12 rounded-2xl bg-white shadow-sm flex items-center justify-center text-emerald-500">
                 <ImportIcon className="w-6 h-6" />
              </div>
              <div className="flex gap-8">
                 <div className="flex flex-col">
                    <span className="text-[10px] font-black text-emerald-600/50 uppercase tracking-widest">Inserted</span>
                    <span className="text-xl font-bold text-emerald-900">{importResults.summary.inserted}</span>
                 </div>
                 <div className="flex flex-col">
                    <span className="text-[10px] font-black text-emerald-600/50 uppercase tracking-widest">Updated</span>
                    <span className="text-xl font-bold text-emerald-900">{importResults.summary.updated}</span>
                 </div>
                 <div className="flex flex-col">
                    <span className="text-[10px] font-black text-emerald-600/50 uppercase tracking-widest">Skipped</span>
                    <span className="text-xl font-bold text-emerald-900">{importResults.summary.skipped}</span>
                 </div>
              </div>
           </div>
           <div className="flex gap-3">
              {importResults.summary.skipped > 0 && (
                <Button variant="secondary" size="sm" onClick={() => setImportResults({ ...importResults, showSkips: true })}>
                  View details
                </Button>
              )}
              <Button variant="secondary" size="sm" onClick={() => setImportResults(null)}>Dismiss</Button>
           </div>
        </div>
      )}

      {importResults?.showSkips && (
        <Modal open title="Skipped Rows Analysis" onClose={() => setImportResults({ ...importResults, showSkips: false })} width="max-w-4xl">
          <div className="p-2">
            <p className="text-xs font-bold text-slate-500 mb-6 uppercase tracking-widest bg-slate-50 p-4 rounded-2xl border border-slate-100">
              The following {importResults.summary.skipped_details?.length} rows were not imported from your Excel file.
            </p>
            
            <div className="rounded-3xl border border-slate-200 overflow-hidden text-black">
              <table className="w-full text-left text-sm border-separate border-spacing-0">
                <thead>
                  <tr className="bg-slate-50 text-[10px] font-black uppercase tracking-widest text-slate-400">
                    <th className="px-6 py-4 border-b border-slate-200">Sheet</th>
                    <th className="px-6 py-4 border-b border-slate-200">Row</th>
                    <th className="px-6 py-4 border-b border-slate-200">ID / Name</th>
                    <th className="px-6 py-4 border-b border-slate-200">Reason for Skipping</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 italic">
                  {importResults.summary.skipped_details?.map((s: any, i: number) => (
                    <tr key={i} className="hover:bg-slate-50/50 transition-colors">
                      <td className="px-6 py-3 font-bold text-petrol-600 uppercase tracking-tighter">{s.sheet}</td>
                      <td className="px-6 py-3 font-mono font-bold">{s.row}</td>
                      <td className="px-6 py-3">
                        <div className="flex flex-col">
                          <span className="font-bold text-black">{s.name || "—"}</span>
                          <span className="text-[10px] font-bold text-slate-400">{s.id || "EMPTY ID"}</span>
                        </div>
                      </td>
                      <td className="px-6 py-3">
                        <Badge tone={s.reason.includes('Duplicate') ? 'amber' : 'rose'}>{s.reason}</Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            
            <div className="mt-8 flex justify-end">
              <Button onClick={() => setImportResults({ ...importResults, showSkips: false })}>I Understand</Button>
            </div>
          </div>
        </Modal>
      )}

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-3 shrink-0 px-2">
        <div className="relative group max-w-xs flex-1">
          <SearchIcon className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400 group-focus-within:text-petrol-500 transition-colors" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search..."
            className="w-full rounded-2xl border border-slate-200 bg-white py-2.5 pl-11 pr-4 text-sm font-bold shadow-sm outline-none focus:ring-4 focus:ring-petrol-500/5 transition-all text-black"
          />
        </div>

        <select 
          value={filterLoc} 
          onChange={(e) => setFilterLoc(e.target.value)}
          className="rounded-2xl border border-slate-200 bg-white px-4 py-2.5 text-xs font-bold text-black outline-none shadow-sm cursor-pointer hover:border-petrol-300 transition-colors"
        >
          <option value="">All Locations</option>
          <option value="DXB">DXB</option>
          <option value="AUH">AUH</option>
        </select>

        <select 
          value={filterMgr} 
          onChange={(e) => setFilterMgr(e.target.value)}
          className="rounded-2xl border border-slate-200 bg-white px-4 py-2.5 text-xs font-bold text-black outline-none shadow-sm cursor-pointer hover:border-petrol-300 transition-colors max-w-[180px]"
        >
          <option value="">All Managers</option>
          {managerList.map(m => <option key={m} value={m}>{m}</option>)}
        </select>

        <Badge tone="slate">{rows.length} Visible</Badge>
      </div>

      {/* Scrolling Table Container */}
      <div className="flex-1 overflow-hidden rounded-3xl border border-slate-200/60 bg-white shadow-soft flex flex-col min-h-[400px]">
        {isLoading ? <div className="flex-1 flex items-center justify-center"><Spinner /></div> : (
          <div className="flex-1 overflow-auto custom-scrollbar relative">
            <table className="w-full text-left text-sm border-separate border-spacing-0">
              <thead className="sticky top-0 z-10">
                <tr className="bg-ink text-[9px] font-bold uppercase tracking-[0.15em] text-white">
                  <th className="px-6 py-4 border-b border-ink">Name</th>
                  <th className="px-6 py-4 border-b border-ink">Emp ID</th>
                  <th className="px-6 py-4 border-b border-ink">Email Address</th>
                  <th className="px-6 py-4 border-b border-ink">Contact</th>
                  <th className="px-6 py-4 border-b border-ink">Project</th>
                  <th className="px-6 py-4 border-b border-ink">Location</th>
                  <th className="px-6 py-4 border-b border-ink">Manager</th>
                  <th className="px-6 py-4 border-b border-ink text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-50">
                {rows.map((row) => (
                  <tr key={row.id} className="group hover:bg-slate-50/50 transition-colors">
                    <td className="px-6 py-3.5">
                      <span className="font-bold text-black">{row.name}</span>
                    </td>
                    <td className="px-6 py-3.5">
                      <span className="text-[10px] font-black text-black font-mono uppercase">{row.employee_id}</span>
                    </td>
                    <td className="px-6 py-3.5">
                      <span className="text-xs font-bold text-black">{row.employee_email_id || "—"}</span>
                    </td>
                    <td className="px-6 py-3.5">
                      <span className="text-xs font-bold text-black">{row.contact_no || "—"}</span>
                    </td>
                    <td className="px-6 py-3.5">
                      <span className="text-xs font-black text-black">{row.project || "—"}</span>
                    </td>
                    <td className="px-6 py-3.5">
                       {row.location ? <Badge tone={row.location === 'DXB' ? 'petrol' : 'amber'}>{row.location}</Badge> : "—"}
                    </td>
                    <td className="px-6 py-3.5">
                      <span className="text-xs font-bold text-black">{row.account_manager || "—"}</span>
                    </td>
                    <td className="px-6 py-3.5 text-right">
                      <div className="flex justify-end gap-2">
                         <button onClick={() => setEditing(row)} className="h-8 w-8 grid place-items-center rounded-xl bg-slate-50 text-slate-400 hover:bg-amber-50 hover:text-amber-600 transition-all shadow-sm border border-slate-100"><EditIcon className="w-3.5 h-3.5" /></button>
                         <button onClick={() => setConfirmDelete(row)} className="h-8 w-8 grid place-items-center rounded-xl bg-slate-50 text-slate-400 hover:bg-rose-50 hover:text-rose-600 transition-all shadow-sm border border-slate-100"><TrashIcon className="w-3.5 h-3.5" /></button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {rows.length === 0 && (
              <div className="py-20 text-center">
                <p className="text-xs font-black text-slate-300 uppercase tracking-widest leading-loose">No matches found for active filters</p>
              </div>
            )}
          </div>
        )}
      </div>

      {editing && (
        <EditModal 
          employee={editing === "new" ? null : editing} 
          onClose={() => setEditing(null)} 
          loading={save.isPending}
          onSave={(input) => save.mutate({ id: editing === "new" ? undefined : editing.id, input })} 
        />
      )}

      <ConfirmDialog
        open={!!confirmDelete}
        title="Delete Identity?"
        message={`Warning: Removing "${confirmDelete?.name}" will break future auto-matching. Recorded data remains in archive.`}
        confirmLabel="Remove"
        danger
        onConfirm={() => confirmDelete && del.mutate(confirmDelete.id)}
        onClose={() => setConfirmDelete(null)}
      />
    </div>
  );
}

function EditModal({ employee, onClose, onSave, loading }: { employee: Employee | null; onClose: () => void; onSave: (i: EmployeeInput) => void; loading: boolean }) {
  const [form, setForm] = useState<EmployeeInput>(employee ? 
    { employee_id: employee.employee_id, name: employee.name, dco_number: employee.dco_number, account_manager: employee.account_manager, employee_email_id: employee.employee_email_id, project: employee.project, contact_no: employee.contact_no, location: employee.location, all_emails: employee.all_emails } : 
    { employee_id: "", name: "", dco_number: "", account_manager: "", employee_email_id: "", project: "", contact_no: "", location: "", all_emails: "" }
  );

  return (
    <Modal open title={employee ? "Edit Identity record" : "New identity record"} onClose={onClose} width="max-w-xl">
      <div className="grid gap-6 py-2 pb-6">
        <div className="grid grid-cols-2 gap-4">
           <Input label="Emp ID" value={form.employee_id} onChange={(v) => setForm({...form, employee_id: v})} placeholder="EMP-XXXX" />
           <Input label="Full Name" value={form.name} onChange={(v) => setForm({...form, name: v})} placeholder="Jane Doe" />
        </div>
        <div className="grid grid-cols-2 gap-4">
           <Input label="Primary Email" value={form.employee_email_id || ""} onChange={(v) => setForm({...form, employee_email_id: v})} />
           <Input label="DCO Number" value={form.dco_number || ""} onChange={(v) => setForm({...form, dco_number: v})} />
        </div>
        <div className="grid grid-cols-2 gap-4">
           <Input label="Account Manager" value={form.account_manager || ""} onChange={(v) => setForm({...form, account_manager: v})} />
           <Input label="Project Name" value={form.project || ""} onChange={(v) => setForm({...form, project: v})} />
        </div>
        <div className="grid grid-cols-3 gap-4">
           <div className="col-span-2">
             <Input label="Contact Number" value={form.contact_no || ""} onChange={(v) => setForm({...form, contact_no: v})} />
           </div>
           <div>
             <label className="block text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-2 pl-1">Location</label>
             <select value={form.location || ""} onChange={(e) => setForm({...form, location: e.target.value})} className="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3.5 text-sm font-bold text-ink outline-none">
               <option value="">N/A</option>
               <option value="DXB">DXB</option>
               <option value="AUH">AUH</option>
             </select>
           </div>
        </div>
        
        <div className="flex justify-end gap-3 mt-4 border-t border-slate-100 pt-8">
           <Button variant="secondary" onClick={onClose}>Discard</Button>
           <Button loading={loading} onClick={() => onSave(form)}>Commit Record</Button>
        </div>
      </div>
    </Modal>
  );
}

function Input({ label, value, onChange, placeholder }: { label: string; value: string; onChange: (v: string) => void; placeholder?: string }) {
  return (
    <label className="block">
      <span className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-2 pl-1 block">{label}</span>
      <input 
        value={value} 
        onChange={(e) => onChange(e.target.value)} 
        placeholder={placeholder}
        className="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3.5 text-sm font-bold text-ink outline-none focus:bg-white focus:ring-4 focus:ring-petrol-400/10 focus:border-petrol-300 transition-all font-sans tracking-tight" 
      />
    </label>
  );
}

// Icons
function ImportIcon({ className }: { className?: string }) { return <svg className={className} width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M4 14.899A7 7 0 1 1 15.71 8h1.79a4.5 4.5 0 0 1 2.5 8.242M12 12v9m-4-4 4 4 4-4"/></svg> }
function PlusIcon({ className }: { className?: string }) { return <svg className={className} width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg> }
function SearchIcon({ className }: { className?: string }) { return <svg className={className} width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg> }
function EditIcon({ className }: { className?: string }) { return <svg className={className} width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/></svg> }
function TrashIcon({ className }: { className?: string }) { return <svg className={className} width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M3 6h18M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2M10 11v6M14 11v6"/></svg> }
