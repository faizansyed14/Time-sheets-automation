import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  attachmentUrl,
  decideEmail,
  fetchEmail,
  fetchInbox,
  restoreEmail,
  rerunExtraction,
  type Attachment,
} from "../api/client";
import { Badge, Spinner, Button, useGlobalProgress } from "../components/ui";

export default function Inbox() {
  const qc = useQueryClient();
  const { isProcessing, setIsProcessing } = useGlobalProgress();
  const [q, setQ] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [selected, setSelected] = useState<string | null>(null);

  const list = useQuery({
    queryKey: ["inbox", q, statusFilter],
    queryFn: () => fetchInbox(q, statusFilter),
  });

  useEffect(() => {
    if (!selected && list.data && list.data.length) setSelected(list.data[0].provider_message_id);
  }, [list.data, selected]);

  return (
    <div className="space-y-8">
      <header className="flex flex-wrap items-end justify-between gap-6">
        <div>
          <h1 className="text-4xl font-bold tracking-tight text-ink">Email Inbox</h1>
          <p className="mt-2 text-slate-500 font-medium max-w-xl">
            Review incoming timesheet submissions. Accept to start AI extraction, or reject to archive.
          </p>
        </div>
        <div className="flex gap-1.5 bg-white p-1.5 rounded-2xl border border-slate-200 shadow-sm">
          {[
            ["", "All Mail"],
            ["new", "New"],
            ["ingested", "Accepted"],
            ["archived", "Archived"],
          ].map(([val, lbl]) => (
            <button
              key={val}
              onClick={() => setStatusFilter(val)}
              className={`rounded-xl px-5 py-2.5 text-xs font-bold transition-all duration-200 ${statusFilter === val
                  ? "bg-ink text-white shadow-md active:scale-95"
                  : "text-slate-500 hover:bg-slate-50 hover:text-ink"
                }`}
            >
              {lbl}
            </button>
          ))}
        </div>
      </header>

      <div className="grid gap-8 lg:grid-cols-[400px_1fr]">
        {/* Sidebar List */}
        <div className="space-y-4">
          <div className="relative group">
            <SearchIcon className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400 group-focus-within:text-petrol-500 transition-colors" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search subjects or senders..."
              className="w-full rounded-2xl border border-slate-200 bg-white py-3.5 pl-11 pr-4 text-sm font-medium shadow-sm outline-none focus:ring-4 focus:ring-petrol-500/5 transition-all"
            />
          </div>

          <div className="space-y-3 max-h-[calc(100vh-320px)] overflow-y-auto pr-2 custom-scrollbar">
            {list.isLoading ? (
              <div className="py-20 flex justify-center"><Spinner label="Syncing mail..." /></div>
            ) : (
              (list.data ?? []).map((m) => (
                <button
                  key={m.provider_message_id}
                  onClick={() => setSelected(m.provider_message_id)}
                  className={`w-full text-left p-5 rounded-3xl border transition-all duration-300 relative ${selected === m.provider_message_id
                      ? "bg-white border-petrol-500 shadow-lift ring-1 ring-petrol-100"
                      : "bg-white border-slate-200/60 hover:bg-slate-50 shadow-sm"
                    }`}
                >
                  <div className="flex items-center justify-between mb-2">
                    <span className="truncate text-sm font-bold text-ink pr-2">{m.sender_name ?? "Unknown Sender"}</span>
                    <StatusTag status={m.status} />
                  </div>
                  <div className="truncate text-xs font-medium text-slate-600 mb-3">{m.subject ?? "(No Subject)"}</div>
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-3">
                      <span className="flex items-center gap-1.5 text-[10px] font-bold text-slate-400 uppercase tracking-widest">
                        <ClipIcon className="w-3 h-3" /> {m.attachment_count}
                      </span>
                      {m.has_approval_screenshot && <Badge tone="petrol">Managed</Badge>}
                    </div>
                    <span className="text-[10px] font-bold text-slate-400 bg-slate-50 px-2.5 py-1 rounded-lg ring-1 ring-slate-100">
                      {m.received_at ? new Date(m.received_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) : ""}
                    </span>
                  </div>
                </button>
              ))
            )}
            {list.data && list.data.length === 0 && (
              <div className="rounded-3xl border-2 border-dashed border-slate-200 bg-white/50 px-8 py-20 text-center">
                <p className="text-sm font-bold text-slate-400">Your inbox is clear.</p>
                <p className="text-xs text-slate-300 mt-1 uppercase tracking-widest">Nice work.</p>
              </div>
            )}
          </div>
        </div>

        {/* Detail Panel */}
        <div className="sticky top-28 h-fit">
          {selected ? (
            <EmailPanel
              id={selected}
              onProcessingChange={setIsProcessing}
              onDecided={() => {
                qc.invalidateQueries({ queryKey: ["inbox"] });
                qc.invalidateQueries({ queryKey: ["dashboard"] });
              }}
            />
          ) : (
            <div className="flex flex-col items-center justify-center rounded-[2.5rem] border-2 border-dashed border-slate-200 bg-white py-40 shadow-soft">
              <div className="h-20 w-20 rounded-full bg-slate-50 flex items-center justify-center mb-6">
                <MailIcon className="w-8 h-8 text-slate-200" />
              </div>
              <p className="text-sm font-bold text-slate-400 uppercase tracking-widest">Select a message</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function EmailPanel({ id, onDecided, onProcessingChange }: { id: string; onDecided: () => void; onProcessingChange: (v: boolean) => void }) {
  const { data, isLoading } = useQuery({ queryKey: ["email", id], queryFn: () => fetchEmail(id) });
  const qc = useQueryClient();
  const [preview, setPreview] = useState<Attachment | null>(null);
  const [msg, setMsg] = useState<{ text: string; type: 'success' | 'info' | 'error' | null }>({ text: '', type: null });

  useEffect(() => {
    setPreview(data?.attachments?.[0] ?? null);
    setMsg({ text: '', type: null });
  }, [data]);

  const decide = useMutation({
    mutationFn: (accepted: boolean) => {
      if (accepted) onProcessingChange(true);
      return decideEmail(id, accepted);
    },
    onSuccess: (res: any) => {
      const isIngested = res.status === "ingested";
      const txt = isIngested
        ? `Successfully accepted. ${res.records_created} timesheet(s) extracted and filed in the archive.`
        : "Email safely archived. You can restore it later if needed.";
      
      setMsg({ 
        text: txt,
        type: isIngested ? (res.errors_count > 0 ? 'info' : 'success') : 'info'
      });
      onDecided();
    },
    onError: (e: any) => {
      const errorMsg = e?.response?.data?.detail ?? "AI Ingestion failed. This usually means the system couldn't parse the file or identify the employee identity. Please check the Employee Matcher database.";
      setMsg({
        text: errorMsg,
        type: 'error'
      });
      alert(`ERROR: ${errorMsg}`);
    },
    onSettled: () => onProcessingChange(false)
  });

  const restore = useMutation({
    mutationFn: () => restoreEmail(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["inbox"] });
      setMsg({ text: "Conversation restored to Inbox.", type: 'info' });
    }
  });

  const rerun = useMutation({
    mutationFn: () => {
      onProcessingChange(true);
      return rerunExtraction(id);
    },
    onSuccess: (res: any) => {
      const txt = `Re-extraction complete. ${res.records_count} record(s) processed.`;
      setMsg({ 
        text: txt, 
        type: 'success' 
      });
      onDecided();
    },
    onError: (e: any) => {
      const errorMsg = e?.response?.data?.detail ?? "Re-extraction failed.";
      setMsg({ text: errorMsg, type: 'error' });
      alert(`RE-RUN FAILED: ${errorMsg}`);
    },
    onSettled: () => onProcessingChange(false)
  });

  if (isLoading || !data) return <div className="p-20 flex justify-center bg-white rounded-[2.5rem] shadow-soft border border-slate-200/60"><Spinner label="Loading conversation..." /></div>;

  const status = data.status;
  const isArchived = status === "archived";
  const isIngested = status === "ingested";
  const isLocked = isIngested || decide.isPending;

  return (
    <div className="bg-white rounded-[2.5rem] shadow-lift border border-slate-200/60 overflow-hidden flex flex-col">
      {/* Detail Header */}
      <div className="px-10 py-8 border-b border-slate-100 bg-slate-50/30">
        <div className="flex items-start justify-between gap-6 mb-6">
          <div className="min-w-0">
            <h2 className="text-2xl font-bold tracking-tight text-ink mb-1">
              {data.subject ?? "(No Subject)"}
            </h2>
            <div className="flex items-center gap-3">
              <div className="flex items-center gap-2 pr-3 border-r border-slate-200">
                <span className="text-sm font-bold text-petrol-600">
                  {data.sender_name}
                </span>
                <span className="text-xs font-medium text-slate-400 font-mono tracking-tighter">
                  &lt;{data.sender_email}&gt;
                </span>
              </div>
              <StatusTag status={status} />
              <span className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">
                {data.received_at ? new Date(data.received_at).toLocaleString(undefined, {
                  weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
                }) : ""}
              </span>
            </div>
          </div>
          
          <div className="flex items-center gap-3 shrink-0">
            {isArchived ? (
              <Button variant="primary" onClick={() => restore.mutate()} loading={restore.isPending} className="px-6 shadow-lift">
                Restore
              </Button>
            ) : isIngested ? (
              <Button 
                variant="secondary" 
                onClick={() => {
                  if (confirm("Warning: Re-running will DELETE existing month folders and database entries for these records to run a fresh extraction. Continue?")) {
                    rerun.mutate();
                  }
                }} 
                loading={rerun.isPending}
                className="px-5 border-rose-100 text-rose-600 hover:bg-rose-50"
              >
                Re-run Extraction
              </Button>
            ) : (
              <>
                <Button 
                  variant="secondary" 
                  onClick={() => decide.mutate(false)} 
                  disabled={isLocked}
                  className="px-6"
                >
                  Reject
                </Button>
                <Button 
                  variant="primary" 
                  onClick={() => decide.mutate(true)} 
                  disabled={isLocked}
                  loading={decide.isPending}
                  className="px-8 shadow-lift"
                >
                  Run AI Ingestion
                </Button>
              </>
            )}
          </div>
        </div>

        {data.body_text && (
          <div className="p-6 bg-white rounded-3xl border border-slate-100 shadow-sm relative overflow-hidden group">
            <div className="absolute top-0 left-0 w-1 h-full bg-slate-100 group-hover:bg-petrol-200 transition-colors" />
            <pre className="whitespace-pre-wrap font-sans text-sm leading-relaxed text-slate-600 italic">
              “{data.body_text}”
            </pre>
          </div>
        )}
      </div>

      {/* Attachments Section */}
      <div className="flex-1 p-10 py-8 overflow-y-auto max-h-[600px] custom-scrollbar">
        <div className="flex items-center justify-between mb-6">
          <span className="text-[11px] font-bold text-slate-400 uppercase tracking-[0.15em]">
            Attached Documents ({data.attachments.length})
          </span>
          {data.attachments.length > 0 && <Badge tone="slate">Ready for Review</Badge>}
        </div>

        <div className="flex flex-wrap gap-3 mb-8">
          {data.attachments.map((a: Attachment) => (
            <button
              key={a.attachment_id}
              onClick={() => setPreview(a)}
              className={`flex items-center gap-3 rounded-2xl border px-4 py-3 text-sm font-bold transition-all duration-200 ${preview?.attachment_id === a.attachment_id
                  ? "border-petrol-500 bg-petrol-50 text-petrol-800 shadow-md ring-1 ring-petrol-200"
                  : "border-slate-200/60 bg-white text-slate-500 hover:border-slate-300 hover:text-ink shadow-sm"
                }`}
            >
              <FileIcon kind={a.kind} active={preview?.attachment_id === a.attachment_id} />
              <span className="max-w-[200px] truncate">{a.filename}</span>
              {a.kind === "approval_screenshot" && <Badge tone="petrol">Approval</Badge>}
            </button>
          ))}
        </div>

        {preview && (
          <div className="animate-in zoom-in-95 duration-200">
            <AttachmentPreview msgId={id} att={preview} />
          </div>
        )}
      </div>

      {/* Footer / Results Message */}
      <div className="px-10 py-10 bg-slate-50 border-t border-slate-100">
        {msg.text ? (
           <div className={`p-6 rounded-[2rem] border flex items-center justify-between gap-6 transition-all duration-500 shadow-sm ${
             msg.type === 'success' ? 'bg-emerald-50 border-emerald-100 text-emerald-800' : 
             msg.type === 'error' ? 'bg-rose-50 border-rose-100 text-rose-800' :
             'bg-slate-50 border-slate-200 text-slate-600'
           }`}>
             <div className="flex items-center gap-4">
                <div className={`h-10 w-10 shrink-0 rounded-2xl flex items-center justify-center bg-white shadow-sm ${
                  msg.type === 'success' ? 'text-emerald-500' : msg.type === 'error' ? 'text-rose-500' : 'text-slate-400'
                }`}>
                  {msg.type === 'success' ? <CheckIcon className="w-5 h-5" /> : msg.type === 'error' ? <AlertIcon className="w-5 h-5" /> : <InfoIcon className="w-5 h-5" />}
                </div>
                <span className="text-sm font-bold leading-relaxed">{msg.text}</span>
             </div>
             {isArchived && (
               <Button variant="secondary" size="sm" onClick={() => restore.mutate()} loading={restore.isPending}>
                 Restore to Inbox
               </Button>
             )}
           </div>
        ) : (
           <div className="flex items-center gap-3">
              <div className="h-2 w-2 rounded-full bg-petrol-500 animate-pulse" />
              <p className="text-xs font-bold text-slate-400 uppercase tracking-widest leading-loose">
                 {isIngested ? "Extraction record persisted in archive" : isArchived ? "Conversation is currently archived" : "AI Extraction ready to process"}
              </p>
           </div>
        )}
      </div>
    </div>
  );
}

function AttachmentPreview({ msgId, att }: { msgId: string; att: Attachment }) {
  const url = attachmentUrl(msgId, att.attachment_id);
  if (att.content_type.startsWith("image/")) {
    return (
      <div className="overflow-hidden rounded-[2rem] border border-slate-200 bg-slate-900 shadow-lift group relative">
        <div className="absolute inset-x-0 bottom-0 p-4 bg-gradient-to-t from-black/60 to-transparent opacity-0 group-hover:opacity-100 transition-opacity">
          <p className="text-white text-xs font-bold font-mono tracking-tighter uppercase">{att.filename}</p>
        </div>
        <img src={url} alt={att.filename} className="mx-auto max-h-[500px] w-full object-contain" />
      </div>
    );
  }
  if (att.content_type === "application/pdf") {
    return <object data={url} type="application/pdf" className="h-[600px] w-full rounded-[2rem] border border-slate-200 shadow-lift" />;
  }
  return (
    <div className="flex items-center justify-between rounded-[2rem] border border-slate-200 bg-slate-50 px-8 py-10 shadow-soft">
      <div className="flex items-center gap-6">
        <div className="h-14 w-14 rounded-2xl bg-white shadow-sm flex items-center justify-center ring-1 ring-slate-100">
          <FolderIcon className="w-7 h-7 text-slate-300" />
        </div>
        <div>
          <p className="text-sm font-bold text-ink mb-1">{att.filename}</p>
          <p className="text-xs font-medium text-slate-400 italic">Binary document — Direct download only</p>
        </div>
      </div>
      <a href={url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-2 rounded-2xl bg-white px-6 py-3 text-xs font-bold text-slate-700 shadow-sm border border-slate-200 hover:border-petrol-500 hover:text-petrol-700 transition-all">
        Download Original
      </a>
    </div>
  );
}

function StatusTag({ status }: { status: string }) {
  if (status === "ingested") return <Badge tone="emerald">Accepted</Badge>;
  if (status === "archived") return <Badge tone="rose">Archived</Badge>;
  return <Badge tone="amber">Pending</Badge>;
}

// Icons
function SearchIcon({ className }: { className?: string }) { return <svg className={className} width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><circle cx="11" cy="11" r="8" /><path d="m21 21-4.3-4.3" /></svg> }
function ClipIcon({ className }: { className?: string }) { return <svg className={className} width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="m21.4 11.05-9.19 9.19a5 5 0 0 1-7.07-7.07l9.19-9.19a3.5 3.5 0 0 1 4.95 4.95l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" /></svg> }
function MailIcon({ className }: { className?: string }) { return <svg className={className} width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><rect x="3" y="5" width="18" height="14" rx="2" /><path d="m3 7 9 6 9-6" /></svg> }
function FolderIcon({ className }: { className?: string }) { return <svg className={className} width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" /></svg> }
function CheckIcon({ className }: { className?: string }) { return <svg className={className} width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M20 6 9 17l-5-5" /></svg> }
function AlertIcon({ className }: { className?: string }) { return <svg className={className} width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><circle cx="12" cy="12" r="10" /><line x1="12" y1="8" x2="12" y2="12" /><line x1="12" y1="16" x2="12.01" y2="16" /></svg> }
function InfoIcon({ className }: { className?: string }) { return <svg className={className} width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><circle cx="12" cy="12" r="10" /><line x1="12" y1="16" x2="12" y2="12" /><line x1="12" y1="8" x2="12.01" y2="8" /></svg> }

function FileIcon({ kind, active }: { kind: string, active?: boolean }) {
  const color = active ? "text-petrol-600" : (kind === "approval_screenshot" ? "text-petrol-400" : "text-slate-300");
  return <svg className={color} width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6" /></svg>
}
