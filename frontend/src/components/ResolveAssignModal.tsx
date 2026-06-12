import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { BadgeCheck, Search, User } from "lucide-react";
import { fetchEmployeeMatcher, MONTHS_LONG, type Employee, type PipelineFile } from "../api/client";
import { cn } from "../lib/utils";
import { Button, Field, Input, Modal, Select, Spinner } from "./ui";

export default function ResolveAssignModal({
  file,
  onClose,
  onProceed,
  pending,
}: {
  file: PipelineFile | null;
  onClose: () => void;
  onProceed: (body: { employee_pk: string; month: number; year: number }) => void;
  pending: boolean;
}) {
  const [employeeQ, setEmployeeQ] = useState("");
  const [employeePk, setEmployeePk] = useState("");
  const [month, setMonth] = useState(1);
  const [year, setYear] = useState(new Date().getFullYear());
  const [openList, setOpenList] = useState(false);

  const { data: employees, isLoading } = useQuery({
    queryKey: ["employee-matcher"],
    queryFn: fetchEmployeeMatcher,
    enabled: !!file,
  });

  useEffect(() => {
    if (!file) return;
    setEmployeeQ(file.employee_id ?? file.employee_name ?? "");
    setEmployeePk("");
    setMonth(file.month ?? 1);
    setYear(file.year ?? new Date().getFullYear());
    setOpenList(true);
  }, [file]);

  const selected = useMemo(
    () => employees?.find((e) => e.id === employeePk) ?? null,
    [employees, employeePk]
  );

  const matches = useMemo(() => {
    const q = employeeQ.toLowerCase().trim();
    const list = employees ?? [];
    const filtered = q
      ? list.filter(
          (e) =>
            e.name.toLowerCase().includes(q) ||
            e.employee_id.toLowerCase().includes(q) ||
            (e.location ?? "").toLowerCase().includes(q) ||
            (e.account_manager ?? "").toLowerCase().includes(q)
        )
      : list;
    return filtered.slice(0, 25);
  }, [employees, employeeQ]);

  const pick = (e: Employee) => {
    setEmployeePk(e.id);
    setEmployeeQ(`${e.employee_id} · ${e.name}${e.location ? ` [${e.location}]` : ""}`);
    setOpenList(false);
  };

  return (
    <Modal
      open={!!file}
      onClose={onClose}
      title="Assign employee & complete"
      subtitle={file ? `${file.filename} — ${file.failure_label ?? ""}` : undefined}
      wide
    >
      {file && (
        <>
          <p className="mb-4 rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm leading-6 text-rose-800">
            {file.failure_detail}
          </p>
          <p className="mb-4 text-sm text-slate-600">
            Sheet extracted{" "}
            <span className="font-medium text-slate-800">
              {file.employee_name ?? "—"} ({file.employee_id ?? "no ID"})
            </span>{" "}
            for{" "}
            <span className="font-medium text-slate-800">
              {file.month ? MONTHS_LONG[file.month] : "—"} {file.year ?? "—"}
            </span>
            . Pick the correct matcher entry and period, then proceed to file it.
          </p>

          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <div className="md:col-span-2">
              <label htmlFor="resolve-employee" className="block">
                <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">
                  Employee
                </span>
                <div className="relative">
                  <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-slate-400" />
                  <Input
                    id="resolve-employee"
                    name="resolve-employee"
                    value={employeeQ}
                    onChange={(e) => {
                      setEmployeeQ(e.target.value);
                      setEmployeePk("");
                      setOpenList(true);
                    }}
                    onFocus={() => setOpenList(true)}
                    onBlur={() => setTimeout(() => setOpenList(false), 150)}
                    placeholder="Search by name or employee ID…"
                    className="pl-9"
                    autoComplete="off"
                  />
                  {openList && (
                    <div className="absolute z-10 mt-1 max-h-52 w-full overflow-y-auto rounded-lg border border-slate-200 bg-white shadow-pop">
                      {isLoading ? (
                        <div className="flex justify-center py-6">
                          <Spinner />
                        </div>
                      ) : matches.length === 0 ? (
                        <p className="px-3 py-4 text-sm text-slate-400">No employees match.</p>
                      ) : (
                        matches.map((e) => (
                          <button
                            key={e.id}
                            type="button"
                            onMouseDown={(ev) => ev.preventDefault()}
                            onClick={() => pick(e)}
                            className={cn(
                              "flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-brand-50",
                              employeePk === e.id && "bg-brand-50"
                            )}
                          >
                            <User className="h-4 w-4 shrink-0 text-slate-400" />
                            <span className="min-w-0 flex-1">
                              <span className="block truncate font-medium text-slate-800">{e.name}</span>
                              <span className="block truncate text-xs text-slate-500">
                                {e.employee_id}
                                {e.location ? ` · ${e.location}` : ""}
                                {e.account_manager ? ` · ${e.account_manager}` : ""}
                              </span>
                            </span>
                          </button>
                        ))
                      )}
                    </div>
                  )}
                </div>
              </label>
              {selected && (
                <p className="mt-1.5 text-xs text-emerald-700">
                  Selected: {selected.name} ({selected.employee_id}
                  {selected.location ? ` · ${selected.location}` : ""})
                </p>
              )}
            </div>

            <Field label="Month" name="resolve-month">
              <Select value={String(month)} onChange={(e) => setMonth(Number(e.target.value))}>
                {MONTHS_LONG.map((m, i) =>
                  i === 0 ? null : (
                    <option key={i} value={i}>
                      {m}
                    </option>
                  )
                )}
              </Select>
            </Field>

            <Field label="Year" name="resolve-year">
              <Input
                type="number"
                min={2000}
                max={2100}
                value={year}
                onChange={(e) => setYear(Number(e.target.value))}
              />
            </Field>
          </div>

          <div className="mt-4 flex justify-end gap-2">
            <Button variant="secondary" onClick={onClose}>
              Cancel
            </Button>
            <Button
              disabled={!employeePk || !month || !year || pending}
              onClick={() => onProceed({ employee_pk: employeePk, month, year })}
            >
              {pending ? <Spinner className="border-white/40 border-t-white" /> : <BadgeCheck className="h-4 w-4" />}
              Proceed
            </Button>
          </div>
        </>
      )}
    </Modal>
  );
}
