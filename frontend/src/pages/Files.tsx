import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  FolderOpen,
  Folder,
  FileText,
  Download,
  Plus,
  Trash2,
  Pencil,
  ChevronRight,
  Briefcase,
  User,
  Upload,
} from "lucide-react";
import {
  createFileEmployee,
  createFileManager,
  createFileMonth,
  deleteFolder,
  deleteVaultFile,
  downloadScopedZipUrl,
  fileContentUrl,
  listFileEmployees,
  listFileItems,
  listFileManagers,
  listFileMonths,
  renameFolder,
  uploadFilesToMonth,
} from "../api/client";
import { cn, formatBytes } from "../lib/utils";
import { FilePreviewModal, PreviewableFileRow } from "../components/FilePreview";
import { VaultDownload } from "../components/VaultDownload";
import { Button, Card, EmptyState, Input, Modal, PageHeader, Skeleton } from "../components/ui";
import { useToast } from "../components/toast";
import type { PreviewFile } from "../lib/filePreview";

type CreateTarget = "manager" | "employee" | "month";

export default function FilesPage() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const [manager, setManager] = useState<string | null>(null);
  const [employee, setEmployee] = useState<string | null>(null);
  const [month, setMonth] = useState<string | null>(null);
  const [creating, setCreating] = useState<CreateTarget | null>(null);
  const [renaming, setRenaming] = useState<{ rel: string; name: string } | null>(null);
  const [name, setName] = useState("");
  const [preview, setPreview] = useState<PreviewFile | null>(null);

  useEffect(() => setPreview(null), [manager, employee, month]);

  const { data: managers, isLoading: lm } = useQuery({
    queryKey: ["files", "managers"],
    queryFn: listFileManagers,
  });
  const { data: employees, isLoading: le } = useQuery({
    queryKey: ["files", "employees", manager],
    queryFn: () => listFileEmployees(manager!),
    enabled: !!manager,
  });
  const { data: months, isLoading: lmo } = useQuery({
    queryKey: ["files", "months", manager, employee],
    queryFn: () => listFileMonths(manager!, employee!),
    enabled: !!manager && !!employee,
  });
  const { data: items, isLoading: li } = useQuery({
    queryKey: ["files", "items", manager, employee, month],
    queryFn: () => listFileItems(manager!, employee!, month!),
    enabled: !!manager && !!employee && !!month,
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: ["files"] });

  const createMut = useMutation({
    mutationFn: async () => {
      if (creating === "manager") return createFileManager(name);
      if (creating === "employee") return createFileEmployee(manager!, name);
      return createFileMonth(manager!, employee!, name);
    },
    onSuccess: () => {
      toast("success", "Folder created");
      setCreating(null);
      setName("");
      invalidate();
    },
    onError: (e: any) => toast("error", "Could not create folder", e?.response?.data?.detail ?? String(e)),
  });

  const renameMut = useMutation({
    mutationFn: () => renameFolder(renaming!.rel, name),
    onSuccess: () => {
      toast("success", "Renamed");
      setRenaming(null);
      setName("");
      setManager(null);
      setEmployee(null);
      setMonth(null);
      invalidate();
    },
    onError: (e: any) => toast("error", "Rename failed", e?.response?.data?.detail ?? String(e)),
  });

  const deleteMut = useMutation({
    mutationFn: deleteFolder,
    onSuccess: () => {
      toast("info", "Folder deleted");
      setEmployee(null);
      setMonth(null);
      invalidate();
    },
    onError: (e: any) => toast("error", "Delete failed", e?.response?.data?.detail ?? String(e)),
  });

  const fileInputRef = useRef<HTMLInputElement>(null);

  const uploadMut = useMutation({
    mutationFn: (chosen: File[]) =>
      uploadFilesToMonth(manager!, employee!, month!, chosen),
    onSuccess: (res: any) => {
      toast("success", `Uploaded ${res?.saved?.length ?? 0} file(s)`);
      invalidate();
    },
    onError: (e: any) => toast("error", "Upload failed", e?.response?.data?.detail ?? String(e)),
  });

  const deleteFileMut = useMutation({
    mutationFn: (relPath: string) => deleteVaultFile(relPath),
    onSuccess: () => {
      toast("info", "File deleted");
      invalidate();
    },
    onError: (e: any) => toast("error", "Delete failed", e?.response?.data?.detail ?? String(e)),
  });

  const crumb = (label: string, onClick?: () => void, active?: boolean) => (
    <button
      onClick={onClick}
      disabled={!onClick}
      className={cn(
        "rounded px-1.5 py-0.5 text-sm font-medium",
        active ? "text-slate-800" : "text-brand-600 hover:bg-brand-50"
      )}
    >
      {label}
    </button>
  );

  return (
    <div className="animate-fade-up">
      <PageHeader
        title="File vault"
        subtitle="Everything the pipeline files on disk — Account Manager → Employee → Month."
        actions={<VaultDownload manager={manager} />}
      />

      <div className="mb-4 flex flex-wrap items-center gap-1">
        {crumb("Storage", () => {
          setManager(null);
          setEmployee(null);
          setMonth(null);
        }, !manager)}
        {manager && (
          <>
            <ChevronRight className="h-4 w-4 text-slate-300" />
            {crumb(manager, () => {
              setEmployee(null);
              setMonth(null);
            }, !employee)}
          </>
        )}
        {employee && (
          <>
            <ChevronRight className="h-4 w-4 text-slate-300" />
            {crumb(employee, () => setMonth(null), !month)}
          </>
        )}
        {month && (
          <>
            <ChevronRight className="h-4 w-4 text-slate-300" />
            {crumb(month, undefined, true)}
          </>
        )}
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        {/* ------------ managers ------------ */}
        <Card className="flex min-h-[420px] flex-col">
          <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
            <h3 className="flex items-center gap-2 text-xs font-bold uppercase tracking-wide text-slate-500">
              <Briefcase className="h-4 w-4" /> Managers
            </h3>
            <Button size="sm" variant="ghost" onClick={() => { setCreating("manager"); setName(""); }}>
              <Plus className="h-4 w-4" />
            </Button>
          </div>
          <div className="flex-1 overflow-y-auto p-2">
            {lm ? (
              <Skeleton className="m-2 h-24" />
            ) : !managers?.length ? (
              <EmptyState title="No folders yet" detail="Folders appear when the pipeline files a timesheet." />
            ) : (
              managers.map((m) => (
                <FolderRow
                  key={m.rel_path}
                  active={manager === m.name}
                  icon={<Briefcase className="h-4 w-4" />}
                  label={m.name}
                  meta={`${m.employee_count} employee${m.employee_count !== 1 ? "s" : ""}`}
                  onClick={() => {
                    setManager(m.name);
                    setEmployee(null);
                    setMonth(null);
                  }}
                  onRename={() => { setRenaming({ rel: m.rel_path, name: m.name }); setName(m.name); }}
                  onDelete={() => {
                    if (confirm(`Delete folder "${m.name}" and everything inside?`)) {
                      deleteMut.mutate(m.rel_path);
                      setManager(null);
                    }
                  }}
                />
              ))
            )}
          </div>
        </Card>

        {/* ------------ employees ------------ */}
        <Card className="flex min-h-[420px] flex-col">
          <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
            <h3 className="flex items-center gap-2 text-xs font-bold uppercase tracking-wide text-slate-500">
              <User className="h-4 w-4" /> Employees
            </h3>
            {manager && (
              <Button size="sm" variant="ghost" onClick={() => { setCreating("employee"); setName(""); }}>
                <Plus className="h-4 w-4" />
              </Button>
            )}
          </div>
          <div className="flex-1 overflow-y-auto p-2">
            {!manager ? (
              <EmptyState title="Pick a manager" />
            ) : le ? (
              <Skeleton className="m-2 h-24" />
            ) : !employees?.length ? (
              <EmptyState title="No employees in this folder" />
            ) : (
              employees.map((e) => (
                <FolderRow
                  key={e.rel_path}
                  active={employee === e.name}
                  icon={<Folder className="h-4 w-4" />}
                  label={e.name}
                  meta={`${e.month_count} month${e.month_count !== 1 ? "s" : ""}`}
                  onClick={() => {
                    setEmployee(e.name);
                    setMonth(null);
                  }}
                  onRename={() => { setRenaming({ rel: e.rel_path, name: e.name }); setName(e.name); }}
                  onDelete={() => {
                    if (confirm(`Delete folder "${e.name}"?`)) deleteMut.mutate(e.rel_path);
                  }}
                />
              ))
            )}
          </div>
        </Card>

        {/* ------------ months + files ------------ */}
        <Card className="flex min-h-[420px] flex-col">
          <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
            <h3 className="flex items-center gap-2 text-xs font-bold uppercase tracking-wide text-slate-500">
              <FolderOpen className="h-4 w-4" /> {month ? `Files — ${month}` : "Months"}
            </h3>
            {employee && !month && (
              <Button size="sm" variant="ghost" onClick={() => { setCreating("month"); setName(""); }}>
                <Plus className="h-4 w-4" />
              </Button>
            )}
            {employee && month && (
              <div className="flex items-center gap-1">
                <input
                  ref={fileInputRef}
                  type="file"
                  multiple
                  className="hidden"
                  onChange={(e) => {
                    const chosen = Array.from(e.target.files ?? []);
                    if (chosen.length) uploadMut.mutate(chosen);
                    e.target.value = "";
                  }}
                />
                <Button
                  size="sm"
                  variant="ghost"
                  title="Upload file(s) into this month"
                  disabled={uploadMut.isPending}
                  onClick={() => fileInputRef.current?.click()}
                >
                  <Upload className="h-4 w-4" /> Upload
                </Button>
                <a href={downloadScopedZipUrl(`${manager}/${employee}/${month}`)}>
                  <Button size="sm" variant="ghost" title="Download this month as ZIP">
                    <Download className="h-4 w-4" />
                  </Button>
                </a>
              </div>
            )}
          </div>
          <div className="flex-1 overflow-y-auto p-2">
            {!employee ? (
              <EmptyState title="Pick an employee" />
            ) : month ? (
              li ? (
                <Skeleton className="m-2 h-24" />
              ) : !items?.length ? (
                <EmptyState title="Empty month folder" />
              ) : (
                <>
                  {items.map((f) => {
                    const file: PreviewFile = {
                      url: fileContentUrl(f.rel_path),
                      filename: f.name,
                      contentType: f.content_type,
                    };
                    return (
                      <div key={f.rel_path} className="flex items-center gap-1">
                        <div className="min-w-0 flex-1">
                          <PreviewableFileRow
                            file={file}
                            onPreview={setPreview}
                            icon={<FileText className="h-4 w-4 shrink-0 text-slate-400" />}
                            meta={<span className="text-[11px] text-slate-400">{formatBytes(f.size)}</span>}
                            className="border-transparent px-3 py-2 hover:border-transparent"
                          />
                        </div>
                        <button
                          type="button"
                          title="Delete this file"
                          disabled={deleteFileMut.isPending}
                          onClick={() => {
                            if (confirm(`Delete "${f.name}"? This cannot be undone.`))
                              deleteFileMut.mutate(f.rel_path);
                          }}
                          className="shrink-0 rounded-lg p-2 text-slate-400 transition-colors hover:bg-rose-50 hover:text-rose-500"
                        >
                          <Trash2 className="h-4 w-4" />
                        </button>
                      </div>
                    );
                  })}
                </>
              )
            ) : lmo ? (
              <Skeleton className="m-2 h-24" />
            ) : !months?.length ? (
              <EmptyState title="No month folders" />
            ) : (
              months.map((mo) => (
                <FolderRow
                  key={mo.rel_path}
                  active={false}
                  icon={<Folder className="h-4 w-4" />}
                  label={mo.name}
                  meta={`${mo.file_count} file${mo.file_count !== 1 ? "s" : ""}`}
                  onClick={() => setMonth(mo.name)}
                  onRename={() => { setRenaming({ rel: mo.rel_path, name: mo.name }); setName(mo.name); }}
                  onDelete={() => {
                    if (confirm(`Delete folder "${mo.name}"?`)) deleteMut.mutate(mo.rel_path);
                  }}
                />
              ))
            )}
          </div>
        </Card>
      </div>

      {/* create + rename modals */}
      <Modal
        open={!!creating}
        onClose={() => setCreating(null)}
        title={`New ${creating ?? ""} folder`}
        subtitle={creating === "month" ? 'Use the "Month-Year" format, e.g. March-2026' : undefined}
      >
        <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Folder name" autoFocus />
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="secondary" onClick={() => setCreating(null)}>Cancel</Button>
          <Button disabled={!name.trim() || createMut.isPending} onClick={() => createMut.mutate()}>
            Create
          </Button>
        </div>
      </Modal>

      <Modal open={!!renaming} onClose={() => setRenaming(null)} title="Rename folder">
        <Input value={name} onChange={(e) => setName(e.target.value)} autoFocus />
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="secondary" onClick={() => setRenaming(null)}>Cancel</Button>
          <Button disabled={!name.trim() || renameMut.isPending} onClick={() => renameMut.mutate()}>
            Rename
          </Button>
        </div>
      </Modal>

      <FilePreviewModal file={preview} onClose={() => setPreview(null)} />
    </div>
  );
}

function FolderRow({
  active,
  icon,
  label,
  meta,
  onClick,
  onRename,
  onDelete,
}: {
  active: boolean;
  icon: React.ReactNode;
  label: string;
  meta: string;
  onClick: () => void;
  onRename: () => void;
  onDelete: () => void;
}) {
  return (
    <div
      className={cn(
        "group flex items-center gap-2.5 rounded-lg px-3 py-2 transition-colors",
        active ? "bg-brand-50 text-brand-700" : "text-slate-600 hover:bg-slate-50"
      )}
    >
      <button onClick={onClick} className="flex min-w-0 flex-1 items-center gap-2.5 text-left">
        <span className={cn("shrink-0", active ? "text-brand-500" : "text-slate-400")}>{icon}</span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium">{label}</span>
          <span className="block text-[11px] text-slate-400">{meta}</span>
        </span>
      </button>
      <span className="hidden shrink-0 gap-0.5 group-hover:flex">
        <button onClick={onRename} className="rounded p-1 text-slate-400 hover:text-brand-600">
          <Pencil className="h-3.5 w-3.5" />
        </button>
        <button onClick={onDelete} className="rounded p-1 text-slate-400 hover:text-rose-500">
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      </span>
    </div>
  );
}
