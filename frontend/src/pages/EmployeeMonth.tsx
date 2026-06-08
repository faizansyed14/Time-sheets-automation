import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { fetchEmployeeRecords, MONTHS_LONG, type TimesheetRecord } from "../api/client";
import { Spinner, Badge, Button } from "../components/ui";
import { RecordDetailModal } from "../components/RecordDetail";

export default function EmployeeMonth() {
  const { pk } = useParams();
  const [year, setYear] = useState<number | undefined>(undefined);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const list = useQuery({
    queryKey: ["employeeRecords", pk, year],
    queryFn: () => fetchEmployeeRecords(pk!, year),
    enabled: !!pk,
  });

  const records = list.data ?? [];
  const years = useMemo(() => Array.from(new Set(records.map(r => r.year))).sort((a,b) => b-a), [records]);
  const employee = records[0] ? { name: records[0].employee_name, id: records[0].employee_id, manager: records[0].account_manager, dco: records[0].dco_number } : null;

  return (
    <div className="space-y-10">
      <header className="flex flex-wrap items-end justify-between gap-6">
        <div>
           {employee ? (
             <>
               <div className="flex items-center gap-3 mb-3">
                 <h1 className="text-4xl font-bold tracking-tight text-ink">{employee.name}</h1>
                 {employee.dco && <Badge tone="slate">{employee.dco}</Badge>}
               </div>
               <div className="flex flex-wrap items-center gap-6 text-sm font-medium text-slate-500">
                  <span className="flex items-center gap-2"><div className="h-2 w-2 rounded-full bg-petrol-500" /> ID: <span className="text-ink font-bold font-mono tracking-tighter uppercase">{employee.id}</span></span>
                  <span className="flex items-center gap-2"><div className="h-2 w-2 rounded-full bg-amber-500" /> Manager: <span className="text-ink font-bold">{employee.manager || "Unassigned"}</span></span>
               </div>
             </>
           ) : (
             <h1 className="text-4xl font-bold tracking-tight text-ink">Identity Detailed View</h1>
           )}
        </div>
        <div className="flex items-center gap-3 bg-white p-1.5 rounded-2xl border border-slate-200 shadow-sm">
          <span className="pl-3 text-xs font-bold text-slate-400 uppercase tracking-wider">Historical Year</span>
          <select
            value={year ?? ""}
            onChange={(e) => setYear(e.target.value ? Number(e.target.value) : undefined)}
            className="rounded-xl border-none bg-slate-50 px-4 py-2 text-sm font-bold text-ink focus:ring-2 focus:ring-petrol-500/20 outline-none cursor-pointer"
          >
            <option value="">All years</option>
            {years.map((y) => (
              <option key={y} value={y}>{y}</option>
            ))}
          </select>
        </div>
      </header>

      {list.isLoading ? <Spinner /> : (
        <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
           {records.map(r => (
             <RecordCard key={r.id} record={r} onClick={() => setSelectedId(r.id)} />
           ))}
           {records.length === 0 && (
              <div className="col-span-full py-20 bg-white rounded-[2.5rem] border-2 border-dashed border-slate-100 flex flex-col items-center justify-center">
                <CalendarIcon className="w-12 h-12 text-slate-100 mb-4" />
                <p className="text-sm font-bold text-slate-300 uppercase tracking-widest">No historical records found</p>
              </div>
           )}
        </div>
      )}

      {selectedId && (
        <RecordDetailModal 
          recordId={selectedId} 
          onClose={() => setSelectedId(null)} 
          onUpdated={() => list.refetch()} 
        />
      )}
    </div>
  );
}

function RecordCard({ record, onClick }: { record: TimesheetRecord; onClick: () => void }) {
  const isVerified = record.validation_status === "verified";
  const isApproved = record.approval_status === "approved";
  
  return (
    <button 
      onClick={onClick}
      className="group premium-card p-6 text-left flex flex-col h-full hover:scale-[1.02] active:scale-[0.98]"
    >
       <div className="flex items-center justify-between mb-6">
          <div className="flex flex-col">
            <span className="text-xs font-bold text-slate-400 uppercase tracking-[0.2em]">{record.year}</span>
            <span className="text-xl font-bold text-ink">{MONTHS_LONG[record.month]}</span>
          </div>
          <div className={`h-10 w-10 flex items-center justify-center rounded-2xl ${isVerified ? 'bg-emerald-50 text-emerald-500' : 'bg-amber-50 text-amber-500'}`}>
             {isVerified ? <CheckIcon className="w-5 h-5" /> : <AlertIcon className="w-5 h-5" />}
          </div>
       </div>

       <div className="flex-1 space-y-3 mb-6">
          <div className="flex items-center justify-between">
            <span className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Validation</span>
            <Badge tone={isVerified ? 'emerald' : 'amber'}>{record.validation_status}</Badge>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Approval</span>
            <Badge tone={isApproved ? 'petrol' : 'slate'}>{record.approval_status}</Badge>
          </div>
       </div>

       <div className="pt-5 border-t border-slate-50 flex items-center justify-between">
          <div className="flex -space-x-1">
             <div className="h-6 w-6 rounded-full bg-emerald-100 ring-4 ring-white flex items-center justify-center text-[10px] font-bold text-emerald-700">{record.annual_leave_count}</div>
             <div className="h-6 w-6 rounded-full bg-slate-100 ring-4 ring-white flex items-center justify-center text-[10px] font-bold text-slate-600">{record.sick_leave_count}</div>
          </div>
          <span className="text-[10px] font-bold text-petrol-600 uppercase tracking-widest group-hover:underline underline-offset-4">View Record</span>
       </div>
    </button>
  );
}

// Icons
function CheckIcon({ className }: { className?: string }) { return <svg className={className} width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M20 6 9 17l-5-5"/></svg> }
function AlertIcon({ className }: { className?: string }) { return <svg className={className} width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg> }
function CalendarIcon({ className }: { className?: string }) { return <svg className={className} width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><rect x="3" y="4" width="18" height="18" rx="2" ry="2" /><line x1="16" y1="2" x2="16" y2="6" /><line x1="8" y1="2" x2="8" y2="6" /><line x1="3" y1="10" x2="21" y2="10" /></svg> }
