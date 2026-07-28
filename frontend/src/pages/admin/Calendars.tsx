import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarDays, Plus, Trash2, Pencil, X } from "lucide-react";
import {
  adminDeleteCalendar,
  adminListCalendars,
  adminUpsertCalendar,
  MONTHS_LONG,
  type MonthCalendar,
  type PublicHoliday,
} from "../../api/client";
import { Badge, Button, Card, EmptyState, Field, Input, Modal, PageHeader, Select, Skeleton } from "../../components/ui";
import { useToast } from "../../components/toast";

const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

type Form = { month: number; year: number; weekend_weekdays: string[]; public_holidays: PublicHoliday[] };
const EMPTY: Form = {
  month: new Date().getMonth() + 1, year: new Date().getFullYear(),
  weekend_weekdays: ["Friday", "Saturday"], public_holidays: [],
};

export default function AdminCalendars() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const { data: calendars, isLoading } = useQuery({ queryKey: ["admin-calendars"], queryFn: adminListCalendars });
  const [modal, setModal] = useState<{ mode: "create" } | { mode: "edit"; row: MonthCalendar } | null>(null);
  const [form, setForm] = useState<Form>(EMPTY);
  const [newHolidayDate, setNewHolidayDate] = useState("");
  const [newHolidayName, setNewHolidayName] = useState("");

  const invalidate = () => qc.invalidateQueries({ queryKey: ["admin-calendars"] });

  const saveMut = useMutation({
    mutationFn: () => adminUpsertCalendar(form),
    onSuccess: () => { toast("success", "Calendar saved"); setModal(null); invalidate(); },
    onError: (e: any) => toast("error", "Could not save calendar", e?.response?.data?.detail ?? String(e)),
  });

  const deleteMut = useMutation({
    mutationFn: adminDeleteCalendar,
    onSuccess: () => { toast("info", "Calendar removed"); invalidate(); },
    onError: (e: any) => toast("error", "Delete failed", e?.response?.data?.detail ?? String(e)),
  });

  const openCreate = () => { setForm({ ...EMPTY }); setNewHolidayDate(""); setNewHolidayName(""); setModal({ mode: "create" }); };
  const openEdit = (row: MonthCalendar) => {
    setForm({
      month: row.month, year: row.year,
      weekend_weekdays: [...row.weekend_weekdays],
      public_holidays: row.public_holidays.map((h) => ({ ...h })),
    });
    setNewHolidayDate(""); setNewHolidayName("");
    setModal({ mode: "edit", row });
  };
  const isEdit = modal?.mode === "edit";

  const toggleWeekday = (day: string) => {
    setForm((f) => ({
      ...f,
      weekend_weekdays: f.weekend_weekdays.includes(day)
        ? f.weekend_weekdays.filter((d) => d !== day)
        : [...f.weekend_weekdays, day],
    }));
  };

  const addHoliday = () => {
    if (!newHolidayDate) return;
    setForm((f) => ({ ...f, public_holidays: [...f.public_holidays, { date: newHolidayDate, name: newHolidayName }] }));
    setNewHolidayDate(""); setNewHolidayName("");
  };
  const removeHoliday = (date: string) => {
    setForm((f) => ({ ...f, public_holidays: f.public_holidays.filter((h) => h.date !== date) }));
  };

  return (
    <div className="animate-fade-up">
      <PageHeader
        title="Month calendars"
        subtitle="Weekends and public holidays per month — handed to Extract Email as ground truth for that month's sheets, instead of the model having to guess."
        actions={<Button onClick={openCreate}><Plus className="h-4 w-4" /> Add month</Button>}
      />

      <Card>
        {isLoading ? (
          <div className="space-y-2 p-6"><Skeleton className="h-12" /><Skeleton className="h-12" /></div>
        ) : !calendars?.length ? (
          <EmptyState icon={<CalendarDays className="h-6 w-6" />} title="No months configured"
            detail="Extract Email falls back to inferring weekends/holidays itself until a month is configured here." />
        ) : (
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-slate-100 text-[11px] uppercase tracking-wide text-slate-400">
                <th className="px-5 py-2.5 font-semibold">Month</th>
                <th className="px-3 py-2.5 font-semibold">Weekend</th>
                <th className="px-3 py-2.5 font-semibold">Public holidays</th>
                <th className="px-3 py-2.5" />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {calendars.map((row) => (
                <tr key={row.id} className="hover:bg-slate-50">
                  <td className="px-5 py-2.5 font-semibold text-slate-800">
                    {MONTHS_LONG[row.month]} {row.year}
                  </td>
                  <td className="px-3 py-2.5 text-slate-600">
                    {row.weekend_weekdays.length ? row.weekend_weekdays.join(", ") : "—"}
                  </td>
                  <td className="px-3 py-2.5">
                    {row.public_holidays.length ? (
                      <div className="flex flex-wrap gap-1">
                        {row.public_holidays.map((h) => (
                          <Badge key={h.date} tone="brand">{h.date}{h.name ? ` · ${h.name}` : ""}</Badge>
                        ))}
                      </div>
                    ) : <span className="text-slate-300">none</span>}
                  </td>
                  <td className="px-3 py-2.5">
                    <div className="flex justify-end gap-1">
                      <button onClick={() => openEdit(row)} className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-brand-600" title="Edit">
                        <Pencil className="h-4 w-4" />
                      </button>
                      <button
                        onClick={() => { if (confirm(`Delete the calendar for ${MONTHS_LONG[row.month]} ${row.year}?`)) deleteMut.mutate(row.id); }}
                        className="rounded-lg p-1.5 text-slate-400 hover:bg-rose-50 hover:text-rose-500" title="Delete"
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Modal
        open={!!modal}
        onClose={() => setModal(null)}
        title={isEdit ? `Edit ${MONTHS_LONG[form.month]} ${form.year}` : "Add month"}
        subtitle="Saving replaces any existing calendar for this month + year."
      >
        <div className="grid grid-cols-2 gap-3">
          <Field label="Month">
            <Select className="w-full" value={form.month} disabled={isEdit}
                    onChange={(e) => setForm({ ...form, month: Number(e.target.value) })}>
              {MONTHS_LONG.slice(1).map((m, i) => <option key={m} value={i + 1}>{m}</option>)}
            </Select>
          </Field>
          <Field label="Year">
            <Input type="number" value={form.year} disabled={isEdit}
                   onChange={(e) => setForm({ ...form, year: Number(e.target.value) })} />
          </Field>
        </div>

        <div className="mt-4">
          <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">Weekend days</p>
          <div className="flex flex-wrap gap-1.5">
            {WEEKDAYS.map((day) => (
              <button
                key={day}
                type="button"
                onClick={() => toggleWeekday(day)}
                className={
                  form.weekend_weekdays.includes(day)
                    ? "rounded-md bg-brand-600 px-2.5 py-1 text-xs font-semibold text-white shadow-sm"
                    : "rounded-md border border-slate-300 px-2.5 py-1 text-xs font-medium text-slate-600 hover:bg-slate-50"
                }
              >
                {day}
              </button>
            ))}
          </div>
        </div>

        <div className="mt-4">
          <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">Public holidays</p>
          <div className="mb-2 flex flex-wrap gap-1.5">
            {form.public_holidays.length === 0 && <span className="text-xs text-slate-300">none yet</span>}
            {form.public_holidays.map((h) => (
              <span key={h.date} className="inline-flex items-center gap-1 rounded-md bg-brand-50 px-2 py-1 font-mono text-[11px] font-medium text-brand-800 ring-1 ring-inset ring-brand-200">
                {h.date}{h.name ? ` · ${h.name}` : ""}
                <button onClick={() => removeHoliday(h.date)} className="opacity-60 hover:opacity-100">
                  <X className="h-3 w-3" />
                </button>
              </span>
            ))}
          </div>
          <div className="flex gap-2">
            <Input type="date" value={newHolidayDate} onChange={(e) => setNewHolidayDate(e.target.value)} className="w-auto" />
            <Input placeholder="Name (optional)" value={newHolidayName} onChange={(e) => setNewHolidayName(e.target.value)} />
            <Button variant="secondary" onClick={addHoliday} disabled={!newHolidayDate}>Add</Button>
          </div>
        </div>

        <div className="mt-5 flex justify-end gap-2">
          <Button variant="secondary" onClick={() => setModal(null)}>Cancel</Button>
          <Button disabled={saveMut.isPending} onClick={() => saveMut.mutate()}>
            <CalendarDays className="h-4 w-4" /> Save calendar
          </Button>
        </div>
      </Modal>
    </div>
  );
}
