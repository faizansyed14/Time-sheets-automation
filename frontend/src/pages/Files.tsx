import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createFileEmployee, createFileMonth, deleteFolder, fileContentUrl,
  listFileEmployees, listFileItems, listFileMonths, renameFolder, type FileItem,
} from "../api/client";
import { Spinner } from "../components/ui";
import { ConfirmDialog, FilePreview, Modal } from "../components/Modal";

export default function Files() {
  const qc = useQueryClient();
  const [emp, setEmp] = useState<string | null>(null);
  const [month, setMonth] = useState<string | null>(null);
  const [preview, setPreview] = useState<FileItem | null>(null);
  const [prompt, setPrompt] = useState<{ kind: "emp" | "month" | "rename"; rel?: string } | null>(null);
  const [confirm, setConfirm] = useState<{ rel: string; label: string } | null>(null);

  const employees = useQuery({ queryKey: ["fEmployees"], queryFn: listFileEmployees });
  const months = useQuery({ queryKey: ["fMonths", emp], queryFn: () => listFileMonths(emp!), enabled: !!emp });
  const items = useQuery({ queryKey: ["fItems", emp, month], queryFn: () => listFileItems(emp!, month!), enabled: !!emp && !!month });

  const refresh = () => qc.invalidateQueries({ predicate: (q) => String(q.queryKey[0]).startsWith("f") });
  const delFolder = useMutation({ mutationFn: (rel: string) => deleteFolder(rel), onSuccess: () => { setMonth(null); refresh(); } });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-ink">Files</h1>
        <p className="mt-1 text-sm text-slate-500">
          Stored timesheets organised by employee → month. Folder changes here apply to the
          file store (local now; OneDrive once connected).
        </p>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        {/* Employees */}
        <Column
          title="Employees"
          onAdd={() => setPrompt({ kind: "emp" })}
          loading={employees.isLoading}
        >
          {(employees.data ?? []).map((e) => (
            <FolderRow
              key={e.rel_path}
              label={e.name}
              meta={`${e.month_count} month(s)`}
              active={emp === e.name}
              onClick={() => { setEmp(e.name); setMonth(null); }}
              onRename={() => setPrompt({ kind: "rename", rel: e.rel_path })}
              onDelete={() => setConfirm({ rel: e.rel_path, label: e.name })}
            />
          ))}
          {employees.data && employees.data.length === 0 && <Empty text="No employee folders yet." />}
        </Column>

        {/* Months */}
        <Column
          title={emp ? `${emp} — Months` : "Months"}
          onAdd={emp ? () => setPrompt({ kind: "month" }) : undefined}
          loading={!!emp && months.isLoading}
        >
          {!emp ? <Empty text="Select an employee." /> : (months.data ?? []).map((m) => (
            <FolderRow
              key={m.rel_path}
              label={m.name}
              meta={`${m.file_count} file(s)`}
              active={month === m.name}
              onClick={() => setMonth(m.name)}
              onRename={() => setPrompt({ kind: "rename", rel: m.rel_path })}
              onDelete={() => setConfirm({ rel: m.rel_path, label: `${emp}/${m.name}` })}
            />
          ))}
          {emp && months.data && months.data.length === 0 && <Empty text="No month folders." />}
        </Column>

        {/* Files */}
        <Column title={month ? `${month} — Files` : "Files"} loading={!!month && items.isLoading}>
          {!month ? <Empty text="Select a month." /> : (items.data ?? []).map((f) => (
            <button key={f.rel_path} onClick={() => setPreview(f)}
              className="flex w-full items-center justify-between rounded-lg border border-slate-200 px-3 py-2 text-left text-sm hover:border-petrol-300 hover:bg-petrol-50/40">
              <span className="truncate text-slate-700">{f.name}</span>
              <span className="ml-2 shrink-0 text-[11px] text-slate-400">{(f.size / 1024).toFixed(1)} KB</span>
            </button>
          ))}
          {month && items.data && items.data.length === 0 && <Empty text="No files in this folder." />}
        </Column>
      </div>

      {/* preview */}
      <Modal open={!!preview} title={preview?.name ?? ""} onClose={() => setPreview(null)} width="max-w-3xl">
        {preview && <FilePreview url={fileContentUrl(preview.rel_path)} name={preview.name} contentType={preview.content_type} />}
      </Modal>

      {/* name prompt */}
      <NamePrompt
        prompt={prompt}
        onClose={() => setPrompt(null)}
        onSubmit={async (value) => {
          if (!prompt) return;
          if (prompt.kind === "emp") await createFileEmployee(value);
          else if (prompt.kind === "month" && emp) await createFileMonth(emp, value);
          else if (prompt.kind === "rename" && prompt.rel) await renameFolder(prompt.rel, value);
          setPrompt(null);
          refresh();
        }}
      />

      <ConfirmDialog
        open={!!confirm}
        title="Delete folder?"
        danger
        confirmLabel="Delete"
        message={`Permanently delete "${confirm?.label}" and everything inside it?`}
        onConfirm={() => confirm && delFolder.mutate(confirm.rel)}
        onClose={() => setConfirm(null)}
      />
    </div>
  );
}

function Column({ title, onAdd, loading, children }: { title: string; onAdd?: () => void; loading?: boolean; children: React.ReactNode }) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white shadow-panel">
      <div className="flex items-center justify-between border-b border-slate-100 px-4 py-2.5">
        <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">{title}</span>
        {onAdd && (
          <button onClick={onAdd} className="rounded-md bg-petrol-600 px-2 py-1 text-xs font-semibold text-white hover:bg-petrol-700">+ Add</button>
        )}
      </div>
      <div className="max-h-[60vh] space-y-1.5 overflow-auto p-3">{loading ? <Spinner /> : children}</div>
    </div>
  );
}

function FolderRow({ label, meta, active, onClick, onRename, onDelete }: {
  label: string; meta: string; active: boolean; onClick: () => void; onRename: () => void; onDelete: () => void;
}) {
  return (
    <div className={`group flex items-center justify-between rounded-lg border px-3 py-2 ${active ? "border-petrol-300 bg-petrol-50/60" : "border-slate-200 hover:border-slate-300"}`}>
      <button onClick={onClick} className="flex min-w-0 items-center gap-2 text-left">
        <svg className="shrink-0 text-amber-500" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" /></svg>
        <span className="min-w-0">
          <span className="block truncate text-sm font-medium text-ink">{label}</span>
          <span className="block text-[11px] text-slate-400">{meta}</span>
        </span>
      </button>
      <div className="flex shrink-0 gap-1 opacity-0 transition group-hover:opacity-100">
        <button onClick={onRename} title="Rename" className="grid h-6 w-6 place-items-center rounded text-slate-400 hover:bg-white hover:text-petrol-700">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 20h9" /><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z" /></svg>
        </button>
        <button onClick={onDelete} title="Delete" className="grid h-6 w-6 place-items-center rounded text-slate-400 hover:bg-white hover:text-rose-600">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6" /></svg>
        </button>
      </div>
    </div>
  );
}

function NamePrompt({ prompt, onClose, onSubmit }: {
  prompt: { kind: "emp" | "month" | "rename" } | null; onClose: () => void; onSubmit: (v: string) => void;
}) {
  const [val, setVal] = useState("");
  const title = prompt?.kind === "emp" ? "New employee folder" : prompt?.kind === "month" ? "New month folder" : "Rename folder";
  const ph = prompt?.kind === "month" ? "e.g. March-2026" : "Name";
  return (
    <Modal open={!!prompt} title={title} onClose={() => { setVal(""); onClose(); }} width="max-w-sm">
      <input autoFocus value={val} onChange={(e) => setVal(e.target.value)} placeholder={ph}
        onKeyDown={(e) => { if (e.key === "Enter" && val.trim()) { onSubmit(val.trim()); setVal(""); } }}
        className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm focus:border-petrol-500 focus:outline-none" />
      <div className="mt-4 flex justify-end gap-2">
        <button onClick={() => { setVal(""); onClose(); }} className="rounded-lg border border-slate-200 px-4 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50">Cancel</button>
        <button onClick={() => { if (val.trim()) { onSubmit(val.trim()); setVal(""); } }}
          className="rounded-lg bg-petrol-600 px-4 py-2 text-sm font-semibold text-white hover:bg-petrol-700">Save</button>
      </div>
    </Modal>
  );
}

function Empty({ text }: { text: string }) {
  return <div className="px-2 py-6 text-center text-xs text-slate-400">{text}</div>;
}
