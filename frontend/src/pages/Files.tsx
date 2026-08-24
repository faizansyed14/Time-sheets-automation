import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  FolderOpen,
  Folder,
  FolderKanban,
  MapPin,
  FileText,
  Download,
  Plus,
  Trash2,
  Pencil,
  ChevronRight,
  Briefcase,
  User,
  Upload,
  Search,
} from "lucide-react";
import {
  createFileEmployee,
  createFileManager,
  createFileMonth,
  deleteFolder,
  deleteVaultFile,
  downloadScopedZipUrl,
  fileContentUrl,
  fileRenderUrl,
  listEmployeeVaultItems,
  listEmployeeVaultMonths,
  listFileEmployees,
  listFileItems,
  listFileLocations,
  listFileManagers,
  listFileMonths,
  listFileProjects,
  listLocationEmployees,
  listProjectEmployees,
  renameFolder,
  searchVaultEmployees,
  uploadFilesToMonth,
  type ProjectEmployee,
} from "../api/client";
import { cn, formatBytes, formatDateTime } from "../lib/utils";
import { FilePreviewModal, PreviewableFileRow } from "../components/FilePreview";
import { VaultDownload } from "../components/VaultDownload";
import { Button, Card, EmptyState, Input, Modal, PageHeader, Skeleton } from "../components/ui";
import { useToast } from "../components/toast";
import type { PreviewFile } from "../lib/filePreview";

type CreateTarget = "manager" | "employee" | "month";
type ViewMode = "manager" | "project" | "location";

/** Shared "jump straight to an employee's vault" search — works the same
 *  way in every view; selecting a result routes into whichever view can
 *  actually show that employee (their own project/location if the current
 *  view doesn't fit them). */
function EmployeeSearchBox({ onSelect }: { onSelect: (r: ProjectEmployee) => void }) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const query = q.trim();
  const { data: results, isFetching } = useQuery({
    queryKey: ["files", "search-employees", query],
    queryFn: () => searchVaultEmployees(query),
    enabled: query.length >= 2,
  });

  return (
    <div className="relative w-full max-w-sm" ref={ref}>
      <div className="relative">
        <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
        <input
          value={q}
          onChange={(e) => { setQ(e.target.value); setOpen(true); }}
          onFocus={() => setOpen(true)}
          placeholder="Search employee name, ID, or project…"
          className="w-full rounded-lg border border-slate-200 bg-white py-1.5 pl-8 pr-2 text-sm placeholder:text-slate-400 focus:border-brand-400 focus:outline-none"
        />
      </div>
      {open && query.length >= 2 && (
        <div className="absolute left-0 top-full z-20 mt-1 max-h-72 w-full min-w-[20rem] overflow-y-auto rounded-lg border border-slate-200 bg-white shadow-pop">
          {isFetching ? (
            <p className="p-3 text-center text-xs text-slate-400">Searching…</p>
          ) : !results?.length ? (
            <p className="p-3 text-center text-xs text-slate-400">No match.</p>
          ) : (
            results.map((r) => (
              <button
                key={r.employee_pk}
                type="button"
                onClick={() => {
                  onSelect(r);
                  setQ("");
                  setOpen(false);
                }}
                className="flex w-full flex-col items-start gap-0.5 px-3 py-2 text-left hover:bg-slate-50"
              >
                <span className="text-sm font-medium text-slate-800">{r.name}</span>
                <span className="text-[11px] text-slate-400">
                  {r.employee_id} · {r.account_manager}
                  {r.project ? ` · ${r.project}` : ""}
                  {r.location ? ` · ${r.location}` : ""}
                </span>
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}

export default function FilesPage() {
  const qc = useQueryClient();
  const { toast } = useToast();

  // ---- Manager-wise view (the vault's real physical layout) ----
  const [manager, setManager] = useState<string | null>(null);
  const [employee, setEmployee] = useState<string | null>(null);
  const [month, setMonth] = useState<string | null>(null);

  // ---- Project-wise / location-wise views — alternate READ lenses onto the
  // SAME files, grouped by the Employee Matcher's own `project`/`location`
  // field instead of the account manager. Resolve back down to the
  // identical (manager, employee_folder, month) triple, so file-level
  // actions below are shared with the manager view unchanged. ----
  const [project, setProject] = useState<string | null>(null);
  const [projEmployee, setProjEmployee] = useState<ProjectEmployee | null>(null);
  const [projMonth, setProjMonth] = useState<string | null>(null);

  const [location, setLocationName] = useState<string | null>(null);
  const [locEmployee, setLocEmployee] = useState<ProjectEmployee | null>(null);
  const [locMonth, setLocMonth] = useState<string | null>(null);

  const [viewMode, setViewMode] = useState<ViewMode>("manager");
  const switchView = (v: ViewMode) => {
    if (v === viewMode) return;
    setViewMode(v);
    setManager(null);
    setEmployee(null);
    setMonth(null);
    setProject(null);
    setProjEmployee(null);
    setProjMonth(null);
    setLocationName(null);
    setLocEmployee(null);
    setLocMonth(null);
  };

  // A search result routes into whichever view can actually show that
  // employee — their own project/location if the CURRENT view doesn't fit
  // them (e.g. picked from Project view but this employee has no project).
  const selectSearchResult = (r: ProjectEmployee) => {
    if (viewMode === "manager") {
      setManager(r.account_manager);
      setEmployee(r.employee_folder);
      setMonth(null);
      return;
    }
    if (viewMode === "project" && r.project) {
      setProject(r.project);
      setProjEmployee(r);
      setProjMonth(null);
      return;
    }
    if (viewMode === "location" && r.location) {
      setLocationName(r.location);
      setLocEmployee(r);
      setLocMonth(null);
      return;
    }
    if (r.project) {
      setViewMode("project");
      setProject(r.project);
      setProjEmployee(r);
      setProjMonth(null);
    } else if (r.location) {
      setViewMode("location");
      setLocationName(r.location);
      setLocEmployee(r);
      setLocMonth(null);
    } else {
      setViewMode("manager");
      setManager(r.account_manager);
      setEmployee(r.employee_folder);
      setMonth(null);
    }
  };

  const [creating, setCreating] = useState<CreateTarget | null>(null);
  const [renaming, setRenaming] = useState<{ rel: string; name: string } | null>(null);
  const [name, setName] = useState("");
  const [preview, setPreview] = useState<PreviewFile | null>(null);

  useEffect(() => setPreview(null), [
    manager, employee, month, project, projEmployee, projMonth, location, locEmployee, locMonth,
  ]);

  // Resolved (manager folder, employee folder, month) regardless of which
  // view got us here — every file-level action below reads from this.
  const effManager =
    viewMode === "manager" ? manager
    : viewMode === "project" ? projEmployee?.account_manager ?? null
    : locEmployee?.account_manager ?? null;
  const effEmployeeFolder =
    viewMode === "manager" ? employee
    : viewMode === "project" ? projEmployee?.employee_folder ?? null
    : locEmployee?.employee_folder ?? null;
  const effMonth = viewMode === "manager" ? month : viewMode === "project" ? projMonth : locMonth;

  const { data: managers, isLoading: lm } = useQuery({
    queryKey: ["files", "managers"],
    queryFn: listFileManagers,
    enabled: viewMode === "manager",
  });
  const { data: employees, isLoading: le } = useQuery({
    queryKey: ["files", "employees", manager],
    queryFn: () => listFileEmployees(manager!),
    enabled: viewMode === "manager" && !!manager,
  });
  const { data: months, isLoading: lmo } = useQuery({
    queryKey: ["files", "months", manager, employee],
    queryFn: () => listFileMonths(manager!, employee!),
    enabled: viewMode === "manager" && !!manager && !!employee,
  });
  const { data: items, isLoading: li } = useQuery({
    queryKey: ["files", "items", manager, employee, month],
    queryFn: () => listFileItems(manager!, employee!, month!),
    enabled: viewMode === "manager" && !!manager && !!employee && !!month,
  });

  const { data: projects, isLoading: lp } = useQuery({
    queryKey: ["files", "projects"],
    queryFn: listFileProjects,
    enabled: viewMode === "project",
  });
  const { data: projEmployees, isLoading: lpe } = useQuery({
    queryKey: ["files", "project-employees", project],
    queryFn: () => listProjectEmployees(project!),
    enabled: viewMode === "project" && !!project,
  });
  const { data: projMonths, isLoading: lpm } = useQuery({
    queryKey: ["files", "employee-vault-months", projEmployee?.employee_pk],
    queryFn: () => listEmployeeVaultMonths(projEmployee!.employee_pk),
    enabled: viewMode === "project" && !!projEmployee,
  });
  const { data: projItems, isLoading: lpi } = useQuery({
    queryKey: ["files", "employee-vault-items", projEmployee?.employee_pk, projMonth],
    queryFn: () => listEmployeeVaultItems(projEmployee!.employee_pk, projMonth!),
    enabled: viewMode === "project" && !!projEmployee && !!projMonth,
  });

  const { data: locations, isLoading: ll } = useQuery({
    queryKey: ["files", "locations"],
    queryFn: listFileLocations,
    enabled: viewMode === "location",
  });
  const { data: locEmployees, isLoading: lle } = useQuery({
    queryKey: ["files", "location-employees", location],
    queryFn: () => listLocationEmployees(location!),
    enabled: viewMode === "location" && !!location,
  });
  const { data: locMonths, isLoading: llm } = useQuery({
    queryKey: ["files", "employee-vault-months", locEmployee?.employee_pk],
    queryFn: () => listEmployeeVaultMonths(locEmployee!.employee_pk),
    enabled: viewMode === "location" && !!locEmployee,
  });
  const { data: locItems, isLoading: lli } = useQuery({
    queryKey: ["files", "employee-vault-items", locEmployee?.employee_pk, locMonth],
    queryFn: () => listEmployeeVaultItems(locEmployee!.employee_pk, locMonth!),
    enabled: viewMode === "location" && !!locEmployee && !!locMonth,
  });

  // The 3rd column renders identically across all three views — same
  // FileItem/MonthFolder shape, just sourced from a different query.
  const visibleMonths = viewMode === "manager" ? months : viewMode === "project" ? projMonths : locMonths;
  const monthsLoading = viewMode === "manager" ? lmo : viewMode === "project" ? lpm : llm;
  const visibleItems = viewMode === "manager" ? items : viewMode === "project" ? projItems : locItems;
  const itemsLoading = viewMode === "manager" ? li : viewMode === "project" ? lpi : lli;
  const col3Employee =
    viewMode === "manager" ? employee
    : viewMode === "project" ? projEmployee?.name ?? null
    : locEmployee?.name ?? null;
  const col3Month = viewMode === "manager" ? month : viewMode === "project" ? projMonth : locMonth;

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
      uploadFilesToMonth(effManager!, effEmployeeFolder!, effMonth!, chosen),
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
        actions={<VaultDownload manager={viewMode === "manager" ? manager : null} />}
      />

      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-1">
          {viewMode === "manager" ? (
            <>
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
            </>
          ) : viewMode === "project" ? (
            <>
              {crumb("Projects", () => {
                setProject(null);
                setProjEmployee(null);
                setProjMonth(null);
              }, !project)}
              {project && (
                <>
                  <ChevronRight className="h-4 w-4 text-slate-300" />
                  {crumb(project, () => {
                    setProjEmployee(null);
                    setProjMonth(null);
                  }, !projEmployee)}
                </>
              )}
              {projEmployee && (
                <>
                  <ChevronRight className="h-4 w-4 text-slate-300" />
                  {crumb(projEmployee.name, () => setProjMonth(null), !projMonth)}
                </>
              )}
              {projMonth && (
                <>
                  <ChevronRight className="h-4 w-4 text-slate-300" />
                  {crumb(projMonth, undefined, true)}
                </>
              )}
            </>
          ) : (
            <>
              {crumb("Locations", () => {
                setLocationName(null);
                setLocEmployee(null);
                setLocMonth(null);
              }, !location)}
              {location && (
                <>
                  <ChevronRight className="h-4 w-4 text-slate-300" />
                  {crumb(location, () => {
                    setLocEmployee(null);
                    setLocMonth(null);
                  }, !locEmployee)}
                </>
              )}
              {locEmployee && (
                <>
                  <ChevronRight className="h-4 w-4 text-slate-300" />
                  {crumb(locEmployee.name, () => setLocMonth(null), !locMonth)}
                </>
              )}
              {locMonth && (
                <>
                  <ChevronRight className="h-4 w-4 text-slate-300" />
                  {crumb(locMonth, undefined, true)}
                </>
              )}
            </>
          )}
        </div>

        <div className="flex gap-1 rounded-lg border border-slate-200 bg-white p-1 text-sm">
          <button
            type="button"
            onClick={() => switchView("manager")}
            className={cn(
              "flex items-center gap-1.5 rounded-md px-3 py-1.5 font-medium transition-colors",
              viewMode === "manager" ? "bg-brand-50 text-brand-700" : "text-slate-500 hover:bg-slate-50"
            )}
          >
            <Briefcase className="h-3.5 w-3.5" /> By manager
          </button>
          <button
            type="button"
            onClick={() => switchView("project")}
            className={cn(
              "flex items-center gap-1.5 rounded-md px-3 py-1.5 font-medium transition-colors",
              viewMode === "project" ? "bg-brand-50 text-brand-700" : "text-slate-500 hover:bg-slate-50"
            )}
          >
            <FolderKanban className="h-3.5 w-3.5" /> By project
          </button>
          <button
            type="button"
            onClick={() => switchView("location")}
            className={cn(
              "flex items-center gap-1.5 rounded-md px-3 py-1.5 font-medium transition-colors",
              viewMode === "location" ? "bg-brand-50 text-brand-700" : "text-slate-500 hover:bg-slate-50"
            )}
          >
            <MapPin className="h-3.5 w-3.5" /> By location
          </button>
        </div>
      </div>

      <div className="mb-4">
        <EmployeeSearchBox onSelect={selectSearchResult} />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        {/* ------------ managers / projects / locations ------------ */}
        <Card className="flex min-h-[420px] flex-col">
          <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
            <h3 className="flex items-center gap-2 text-xs font-bold uppercase tracking-wide text-slate-500">
              {viewMode === "manager" ? (
                <>
                  <Briefcase className="h-4 w-4" /> Managers
                </>
              ) : viewMode === "project" ? (
                <>
                  <FolderKanban className="h-4 w-4" /> Projects
                </>
              ) : (
                <>
                  <MapPin className="h-4 w-4" /> Locations
                </>
              )}
            </h3>
            {viewMode === "manager" && (
              <Button size="sm" variant="ghost" onClick={() => { setCreating("manager"); setName(""); }}>
                <Plus className="h-4 w-4" />
              </Button>
            )}
          </div>
          <div className="flex-1 overflow-y-auto p-2">
            {viewMode === "manager" ? (
              lm ? (
                <Skeleton className="m-2 h-24" />
              ) : !managers?.length ? (
                <EmptyState title="No folders yet" detail="Folders appear when the pipeline files a timesheet." />
              ) : (
                managers.map((m) => (
                  <FolderRow
                    key={m.rel_path}
                    active={manager === m.name}
                    icon={<Folder className="h-4 w-4" />}
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
              )
            ) : viewMode === "project" ? (
              lp ? (
                <Skeleton className="m-2 h-24" />
              ) : !projects?.length ? (
                <EmptyState title="No projects yet" detail="Set a Project on employees in the Employee Matcher." />
              ) : (
                projects.map((p) => (
                  <FolderRow
                    key={p.name}
                    active={project === p.name}
                    icon={<Folder className="h-4 w-4" />}
                    label={p.name}
                    meta={`${p.employee_count} employee${p.employee_count !== 1 ? "s" : ""}`}
                    onClick={() => {
                      setProject(p.name);
                      setProjEmployee(null);
                      setProjMonth(null);
                    }}
                  />
                ))
              )
            ) : ll ? (
              <Skeleton className="m-2 h-24" />
            ) : !locations?.length ? (
              <EmptyState title="No locations yet" detail="Set a Location on employees in the Employee Matcher." />
            ) : (
              locations.map((loc) => (
                <FolderRow
                  key={loc.name}
                  active={location === loc.name}
                  icon={<Folder className="h-4 w-4" />}
                  label={loc.name}
                  meta={`${loc.employee_count} employee${loc.employee_count !== 1 ? "s" : ""}`}
                  onClick={() => {
                    setLocationName(loc.name);
                    setLocEmployee(null);
                    setLocMonth(null);
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
            {viewMode === "manager" && manager && (
              <Button size="sm" variant="ghost" onClick={() => { setCreating("employee"); setName(""); }}>
                <Plus className="h-4 w-4" />
              </Button>
            )}
          </div>
          <div className="flex-1 overflow-y-auto p-2">
            {viewMode === "manager" ? (
              !manager ? (
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
              )
            ) : viewMode === "project" ? (
              !project ? (
                <EmptyState title="Pick a project" />
              ) : lpe ? (
                <Skeleton className="m-2 h-24" />
              ) : !projEmployees?.length ? (
                <EmptyState title="No employees in this project" />
              ) : (
                projEmployees.map((e) => (
                  <FolderRow
                    key={e.employee_pk}
                    active={projEmployee?.employee_pk === e.employee_pk}
                    icon={<Folder className="h-4 w-4" />}
                    label={e.name}
                    meta={`${e.month_count} month${e.month_count !== 1 ? "s" : ""} · ${e.account_manager}`}
                    onClick={() => {
                      setProjEmployee(e);
                      setProjMonth(null);
                    }}
                  />
                ))
              )
            ) : !location ? (
              <EmptyState title="Pick a location" />
            ) : lle ? (
              <Skeleton className="m-2 h-24" />
            ) : !locEmployees?.length ? (
              <EmptyState title="No employees in this location" />
            ) : (
              locEmployees.map((e) => (
                <FolderRow
                  key={e.employee_pk}
                  active={locEmployee?.employee_pk === e.employee_pk}
                  icon={<Folder className="h-4 w-4" />}
                  label={e.name}
                  meta={`${e.month_count} month${e.month_count !== 1 ? "s" : ""} · ${e.account_manager}`}
                  onClick={() => {
                    setLocEmployee(e);
                    setLocMonth(null);
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
              <FolderOpen className="h-4 w-4" /> {col3Month ? `Files — ${col3Month}` : "Months"}
            </h3>
            {viewMode === "manager" && employee && !month && (
              <Button size="sm" variant="ghost" onClick={() => { setCreating("month"); setName(""); }}>
                <Plus className="h-4 w-4" />
              </Button>
            )}
            {col3Employee && col3Month && (
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
                <a href={downloadScopedZipUrl(`${effManager}/${effEmployeeFolder}/${effMonth}`)}>
                  <Button size="sm" variant="ghost" title="Download this month as ZIP">
                    <Download className="h-4 w-4" />
                  </Button>
                </a>
              </div>
            )}
          </div>
          <div className="flex-1 overflow-y-auto p-2">
            {!col3Employee ? (
              <EmptyState title="Pick an employee" />
            ) : col3Month ? (
              itemsLoading ? (
                <Skeleton className="m-2 h-24" />
              ) : !visibleItems?.length ? (
                <EmptyState title="Empty month folder" />
              ) : (
                <>
                  {visibleItems.map((f) => {
                    const file: PreviewFile = {
                      url: fileContentUrl(f.rel_path),
                      filename: f.name,
                      contentType: f.content_type,
                      renderUrl: fileRenderUrl(f.rel_path),
                    };
                    return (
                      <div key={f.rel_path} className="flex items-center gap-1">
                        <div className="min-w-0 flex-1">
                          <PreviewableFileRow
                            file={file}
                            onPreview={setPreview}
                            icon={<FileText className="h-4 w-4 shrink-0 text-slate-400" />}
                            subtitle={f.stored_at ? `Stored ${formatDateTime(f.stored_at)}` : undefined}
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
            ) : monthsLoading ? (
              <Skeleton className="m-2 h-24" />
            ) : !visibleMonths?.length ? (
              <EmptyState title="No month folders" />
            ) : (
              visibleMonths.map((mo) =>
                viewMode === "manager" ? (
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
                ) : viewMode === "project" ? (
                  <FolderRow
                    key={mo.rel_path}
                    active={false}
                    icon={<Folder className="h-4 w-4" />}
                    label={mo.name}
                    meta={`${mo.file_count} file${mo.file_count !== 1 ? "s" : ""}`}
                    onClick={() => setProjMonth(mo.name)}
                  />
                ) : (
                  <FolderRow
                    key={mo.rel_path}
                    active={false}
                    icon={<Folder className="h-4 w-4" />}
                    label={mo.name}
                    meta={`${mo.file_count} file${mo.file_count !== 1 ? "s" : ""}`}
                    onClick={() => setLocMonth(mo.name)}
                  />
                )
              )
            )}
          </div>
        </Card>
      </div>

      {/* create + rename modals — Manager view only */}
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
  onRename?: () => void;
  onDelete?: () => void;
}) {
  return (
    <div
      className={cn(
        "group flex items-center gap-2.5 rounded-lg px-3 py-2 transition-colors",
        active ? "bg-amber-50 text-amber-800" : "text-slate-600 hover:bg-slate-50"
      )}
    >
      <button onClick={onClick} className="flex min-w-0 flex-1 items-center gap-2.5 text-left">
        <span className={cn("shrink-0", active ? "text-amber-500" : "text-amber-400")}>{icon}</span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium">{label}</span>
          <span className="block text-[11px] text-slate-400">{meta}</span>
        </span>
      </button>
      {(onRename || onDelete) && (
        <span className="hidden shrink-0 gap-0.5 group-hover:flex">
          {onRename && (
            <button onClick={onRename} className="rounded p-1 text-slate-400 hover:text-brand-600">
              <Pencil className="h-3.5 w-3.5" />
            </button>
          )}
          {onDelete && (
            <button onClick={onDelete} className="rounded p-1 text-slate-400 hover:text-rose-500">
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          )}
        </span>
      )}
    </div>
  );
}
