import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  attachmentUrl,
  decideEmail,
  fetchEmail,
  fetchInbox,
  type Attachment,
  type EmailListItem,
} from "../api/client";
import { Pill, Spinner } from "../components/ui";

export default function Inbox() {
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [selected, setSelected] = useState<string | null>(null);

  const list = useQuery({
    queryKey: ["inbox", q, statusFilter],
    queryFn: () => fetchInbox(q, statusFilter),
  });

  // auto-select first email
  useEffect(() => {
    if (!selected && list.data && list.data.length) setSelected(list.data[0].provider_message_id);
  }, [list.data, selected]);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-ink">Email Inbox</h1>
        <p className="mt-1 text-sm text-slate-500">
          Review incoming timesheet emails. Accept to run extraction, or reject to archive.
        </p>
      </div>

      {/* controls */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[220px] max-w-sm">
          <SearchIcon />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search subject, sender, body…"
            className="w-full rounded-lg border border-slate-200 bg-white py-2.5 pl-9 pr-3 text-sm shadow-panel focus:border-petrol-500 focus:outline-none"
          />
        </div>
        <div className="flex gap-1 rounded-lg border border-slate-200 bg-white p-1 shadow-panel">
          {[
            ["", "All"],
            ["new", "New"],
            ["ingested", "Accepted"],
            ["archived", "Archived"],
          ].map(([val, lbl]) => (
            <button
              key={val}
              onClick={() => setStatusFilter(val)}
              className={`rounded-md px-3 py-1.5 text-xs font-medium transition ${
                statusFilter === val ? "bg-ink text-white" : "text-slate-500 hover:bg-slate-100"
              }`}
            >
              {lbl}
            </button>
          ))}
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-[360px_1fr]">
        {/* list */}
        <div className="space-y-2">
          {list.isLoading ? (
            <Spinner />
          ) : (
            (list.data ?? []).map((m) => (
              <EmailCard key={m.provider_message_id} m={m} active={selected === m.provider_message_id} onClick={() => setSelected(m.provider_message_id)} />
            ))
          )}
          {list.data && list.data.length === 0 && (
            <div className="rounded-2xl border border-dashed border-slate-300 bg-white px-4 py-8 text-center text-sm text-slate-400">
              No emails match.
            </div>
          )}
        </div>

        {/* detail */}
        <div>
          {selected ? (
            <EmailPanel
              id={selected}
              onDecided={() => {
                qc.invalidateQueries({ queryKey: ["inbox"] });
                qc.invalidateQueries({ queryKey: ["dashboard"] });
              }}
            />
          ) : (
            <div className="grid h-full place-items-center rounded-2xl border border-slate-200 bg-white py-20 text-sm text-slate-400 shadow-panel">
              Select an email to preview.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function EmailCard({ m, active, onClick }: { m: EmailListItem; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={`w-full rounded-2xl border px-4 py-3 text-left shadow-panel transition ${
        active ? "border-petrol-300 bg-petrol-50/50 ring-1 ring-petrol-200" : "border-slate-200 bg-white hover:border-slate-300"
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-sm font-semibold text-ink">{m.sender_name ?? "Unknown"}</span>
        <StatusTag status={m.status} />
      </div>
      <div className="mt-0.5 truncate text-sm text-slate-600">{m.subject ?? "(no subject)"}</div>
      <div className="mt-1.5 flex items-center gap-2 text-[11px] text-slate-400">
        <span>{m.received_at ? new Date(m.received_at).toLocaleDateString() : ""}</span>
        <span>·</span>
        <span className="inline-flex items-center gap-1"><ClipIcon /> {m.attachment_count}</span>
        {m.has_approval_screenshot && <Pill tone="petrol">approval</Pill>}
      </div>
    </button>
  );
}

function EmailPanel({ id, onDecided }: { id: string; onDecided: () => void }) {
  const { data, isLoading } = useQuery({ queryKey: ["email", id], queryFn: () => fetchEmail(id) });
  const [preview, setPreview] = useState<Attachment | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    setPreview(data?.attachments?.[0] ?? null);
    setMsg(null);
  }, [data]);

  const decide = useMutation({
    mutationFn: (accepted: boolean) => decideEmail(id, accepted),
    onSuccess: (res: any) => {
      setMsg(res.status === "ingested" ? `Accepted — ${res.records_created} record(s) extracted and filed.` : "Email archived.");
      onDecided();
    },
  });

  if (isLoading || !data) return <Spinner />;
  const decided = data.status !== "new";

  return (
    <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-panel">
      {/* meta header */}
      <div className="border-b border-slate-200 px-5 py-4">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <div className="text-base font-semibold text-ink">{data.subject ?? "(no subject)"}</div>
            <div className="mt-1 text-sm text-slate-500">
              <span className="font-medium text-slate-700">{data.sender_name}</span>{" "}
              <span className="font-mono text-xs">&lt;{data.sender_email}&gt;</span>
            </div>
          </div>
          <div className="text-right text-xs text-slate-400">
            <StatusTag status={data.status} />
            <div className="mt-1">{data.received_at ? new Date(data.received_at).toLocaleString() : ""}</div>
          </div>
        </div>
      </div>

      {/* body */}
      {data.body_text && (
        <div className="border-b border-slate-100 px-5 py-4">
          <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">Message</div>
          <pre className="whitespace-pre-wrap font-sans text-sm leading-relaxed text-slate-700">{data.body_text}</pre>
        </div>
      )}

      {/* attachments */}
      <div className="px-5 py-4">
        <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
          Attachments ({data.attachments.length})
        </div>
        <div className="mb-3 flex flex-wrap gap-2">
          {data.attachments.map((a) => (
            <button
              key={a.attachment_id}
              onClick={() => setPreview(a)}
              className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-sm transition ${
                preview?.attachment_id === a.attachment_id
                  ? "border-petrol-300 bg-petrol-50 text-petrol-700"
                  : "border-slate-200 bg-white text-slate-600 hover:border-slate-300"
              }`}
            >
              <FileIcon kind={a.kind} />
              <span className="max-w-[180px] truncate">{a.filename}</span>
              {a.kind === "approval_screenshot" && <Pill tone="petrol">approval</Pill>}
            </button>
          ))}
        </div>

        {preview && <AttachmentPreview msgId={id} att={preview} />}
      </div>

      {/* decision bar */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 bg-slate-50/70 px-5 py-4">
        <div className="text-sm">
          {msg ? (
            <span className="font-medium text-emerald-700">{msg}</span>
          ) : decided ? (
            <span className="text-slate-500">Already {data.status === "ingested" ? "accepted" : "archived"}.</span>
          ) : (
            <span className="text-slate-500">Did the manager accept this timesheet?</span>
          )}
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => decide.mutate(false)}
            disabled={decide.isPending || decided}
            className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-700 transition hover:bg-slate-100 disabled:opacity-40"
          >
            No · Archive
          </button>
          <button
            onClick={() => decide.mutate(true)}
            disabled={decide.isPending || decided}
            className="rounded-lg bg-petrol-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-petrol-700 disabled:opacity-40"
          >
            {decide.isPending ? "Processing…" : "Yes · Run extraction"}
          </button>
        </div>
      </div>
    </div>
  );
}

function AttachmentPreview({ msgId, att }: { msgId: string; att: Attachment }) {
  const url = attachmentUrl(msgId, att.attachment_id);
  if (att.content_type.startsWith("image/")) {
    return (
      <div className="overflow-hidden rounded-xl border border-slate-200 bg-slate-50">
        <img src={url} alt={att.filename} className="mx-auto max-h-[420px] w-full object-contain" />
      </div>
    );
  }
  if (att.content_type === "application/pdf") {
    return <object data={url} type="application/pdf" className="h-[480px] w-full rounded-xl border border-slate-200" />;
  }
  // docx and others: no inline preview
  return (
    <div className="flex items-center justify-between rounded-xl border border-slate-200 bg-slate-50 px-4 py-5">
      <div className="flex items-center gap-3 text-sm text-slate-600">
        <FileIcon kind="timesheet" />
        <span>{att.filename} — no inline preview for this type.</span>
      </div>
      <a href={url} target="_blank" rel="noreferrer" className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-100">
        Open / download
      </a>
    </div>
  );
}

function StatusTag({ status }: { status: string }) {
  if (status === "ingested") return <Pill tone="emerald">Accepted</Pill>;
  if (status === "archived") return <Pill tone="rose">Archived</Pill>;
  return <Pill tone="amber">New</Pill>;
}

function SearchIcon() {
  return (
    <svg className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" />
    </svg>
  );
}
function ClipIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="m21.4 11.05-9.19 9.19a5 5 0 0 1-7.07-7.07l9.19-9.19a3.5 3.5 0 0 1 4.95 4.95l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
    </svg>
  );
}
function FileIcon({ kind }: { kind: string }) {
  const color = kind === "approval_screenshot" ? "text-petrol-600" : "text-slate-400";
  return (
    <svg className={color} width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6" />
    </svg>
  );
}
