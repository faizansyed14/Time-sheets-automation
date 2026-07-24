/**
 * Agentic Chat — a timesheet-scoped assistant.
 *
 * Natural-language questions and edits over the timesheet database: check
 * submissions, count leaves, find who is missing, and add / replace / clear
 * leave dates. It is a READ/UPDATE agent only — sheets enter the system
 * through Extract Email, Upload or Manual Entry, never through the chat.
 */
import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  Send, Sparkles, BookOpen, User, Loader2, AlertTriangle,
  ArrowRight, Plus, Minus, RotateCcw, Trash2, FileText, UserCheck, CalendarDays,
  Check, X, Mail, Copy,
} from "lucide-react";
import {
  fetchChatSuggestions, sendChatStream,
  type ChatMessage, type ChatChange,
  type ChatCard, type ChatStreamEvent,
} from "../api/client";
import { Badge, Button, Card, Modal, PageHeader, Spinner } from "../components/ui";
import { useToast } from "../components/toast";
import { cn } from "../lib/utils";

// A live tool-activity chip shown while the agent works.
interface Activity { name: string; label: string; write?: boolean; done?: boolean; ok?: boolean }

interface Turn extends ChatMessage {
  changes?: ChatChange[];
  error?: string | null;
  cards?: ChatCard[];            // structured result cards streamed by the agent
  activity?: Activity[];         // live tool-activity chips
  suggestions?: string[];        // proactive follow-up chips
  streaming?: boolean;           // true while tokens are still arriving
}

const ACTION_META: Record<ChatChange["action"], { label: string; icon: typeof Plus; tone: string }> = {
  add: { label: "Added", icon: Plus, tone: "text-emerald-600" },
  set: { label: "Replaced", icon: RotateCcw, tone: "text-brand-600" },
  clear: { label: "Cleared", icon: Trash2, tone: "text-rose-600" },
};

function ChangeCard({ c }: { c: ChatChange }) {
  const meta = ACTION_META[c.action] ?? ACTION_META.add;
  const Icon = meta.icon;
  return (
    <div className="mt-2 rounded-lg border border-slate-200 bg-white p-3 text-xs">
      <div className="flex items-center gap-2 font-semibold text-slate-700">
        <Icon className={cn("h-3.5 w-3.5", meta.tone)} />
        {meta.label} {c.leave_type}
        <span className="text-slate-400">·</span>
        <span className="text-slate-500">{c.employee_name}</span>
        <span className="text-slate-400">·</span>
        <span className="text-slate-500">{c.month_name} {c.year}</span>
        <Link to={`/records/${c.record_id}`} className="ml-auto inline-flex items-center gap-0.5 text-brand-600 hover:underline">
          <FileText className="h-3 w-3" /> Record
        </Link>
      </div>
      {c.added.length > 0 && (
        <p className="mt-1.5 flex flex-wrap items-center gap-1 text-emerald-700">
          <Plus className="h-3 w-3" />
          {c.added.map((d) => <span key={d} className="rounded bg-emerald-50 px-1.5 py-0.5">{d}</span>)}
        </p>
      )}
      {c.removed.length > 0 && (
        <p className="mt-1.5 flex flex-wrap items-center gap-1 text-rose-700">
          <Minus className="h-3 w-3" />
          {c.removed.map((d) => <span key={d} className="rounded bg-rose-50 px-1.5 py-0.5 line-through">{d}</span>)}
        </p>
      )}
      <p className="mt-1.5 text-[11px] text-slate-400">
        {c.before.length} → {c.after.length} date{c.after.length === 1 ? "" : "s"} on this leave type
      </p>
    </div>
  );
}

// Typewriter reveal — types out assistant text on first mount with a blinking
// caret. History bubbles don't re-mount (stable keys) so they never re-type.
function Typewriter({ text, onTick }: { text: string; onTick?: () => void }) {
  const [shown, setShown] = useState(0);
  useEffect(() => {
    setShown(0);
    if (!text) return;
    let i = 0;
    const step = Math.max(1, Math.round(text.length / 220)); // ~quick even for long replies
    const id = setInterval(() => {
      i += step;
      setShown(Math.min(i, text.length));
      onTick?.();
      if (i >= text.length) clearInterval(id);
    }, 16);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text]);
  const done = shown >= text.length;
  return (
    <p className="whitespace-pre-wrap">
      {text.slice(0, shown)}
      {!done && <span className="ml-0.5 inline-block h-[1.05em] w-[2px] translate-y-[2px] animate-blink rounded-full bg-brand-500 align-middle" />}
    </p>
  );
}

// --------------------------------------------------------------------------- //
// Structured result cards streamed by the agent
// --------------------------------------------------------------------------- //
function Stat({ label, value, tone }: { label: string; value: number | string; tone?: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-center">
      <div className={cn("text-base font-bold", tone ?? "text-slate-800")}>{value}</div>
      <div className="text-[10px] font-medium uppercase tracking-wide text-slate-400">{label}</div>
    </div>
  );
}

function ApprovalChangeCard({ c }: { c: any }) {
  const approved = c.after === "approved";
  return (
    <div className={cn("mt-1 rounded-lg border p-3 text-xs",
      approved ? "border-emerald-200 bg-emerald-50" : "border-rose-200 bg-rose-50")}>
      <div className="flex items-center gap-2 font-semibold">
        <UserCheck className={cn("h-3.5 w-3.5", approved ? "text-emerald-600" : "text-rose-600")} />
        <span className={approved ? "text-emerald-700" : "text-rose-700"}>
          {approved ? "Approved" : "Marked not approved"}
        </span>
        <span className="text-slate-400">·</span>
        <span className="text-slate-600">{c.employee_name}</span>
        <span className="text-slate-400">·</span>
        <span className="text-slate-500">{c.month_name} {c.year}</span>
        <Link to={`/records/${c.record_id}`} className="ml-auto inline-flex items-center gap-0.5 text-brand-600 hover:underline">
          <FileText className="h-3 w-3" /> Record
        </Link>
      </div>
      <p className="mt-1 text-[11px] text-slate-500">Manager approval: {c.before} → {c.after}</p>
    </div>
  );
}

function DraftEmailCard({ c }: { c: any }) {
  const { toast } = useToast();
  const copy = () => {
    navigator.clipboard?.writeText(`Subject: ${c.subject}\n\n${c.body}`);
    toast("success", "Copied", "Draft copied to clipboard — paste it into your mail client.");
  };
  return (
    <div className="mt-1 w-full max-w-lg rounded-lg border border-slate-200 bg-white p-3 text-xs">
      <div className="mb-2 flex items-center gap-2 font-semibold text-slate-700">
        <Mail className="h-3.5 w-3.5 text-brand-500" />
        Draft {c.kind === "approval" ? "approval request" : "reminder"} · {c.count} recipient{c.count === 1 ? "" : "s"}
        <button onClick={copy} className="ml-auto inline-flex items-center gap-1 rounded-md border border-slate-200 px-1.5 py-0.5 font-semibold text-brand-600 hover:bg-brand-50">
          <Copy className="h-3 w-3" /> Copy
        </button>
      </div>
      <div className="rounded-md bg-slate-50 p-2.5">
        <p className="font-semibold text-slate-700">Subject: {c.subject}</p>
        <p className="mt-1.5 whitespace-pre-wrap text-slate-600">{c.body}</p>
      </div>
      {(c.recipients ?? []).length > 0 && (
        <p className="mt-2 flex flex-wrap gap-1 text-[11px] text-slate-500">
          {(c.recipients as any[]).slice(0, 12).map((r, i) => (
            <span key={i} className="rounded bg-slate-100 px-1.5 py-0.5">{r.name}{r.email ? ` · ${r.email}` : ""}</span>
          ))}
        </p>
      )}
      <p className="mt-1.5 text-[10px] text-slate-400">This is a draft — nothing was sent. Copy it into your mail client to send.</p>
    </div>
  );
}

function DashboardCard({ c }: { c: any }) {
  return (
    <div className="mt-1 w-full max-w-lg rounded-lg border border-slate-200 bg-white p-3 text-xs">
      <div className="mb-2 flex items-center gap-2 font-semibold text-slate-700">
        <CalendarDays className="h-3.5 w-3.5 text-brand-500" /> {c.month_name} {c.year} — status
      </div>
      <div className="grid grid-cols-3 gap-1.5 sm:grid-cols-6">
        <Stat label="Total" value={c.total_employees} />
        <Stat label="Submitted" value={c.submitted} tone="text-emerald-600" />
        <Stat label="Missing" value={c.missing_count} tone={c.missing_count ? "text-rose-600" : "text-slate-800"} />
        <Stat label="Approved" value={c.approved_count} tone="text-emerald-600" />
        <Stat label="Awaiting" value={c.awaiting_approval_count} tone={c.awaiting_approval_count ? "text-amber-600" : "text-slate-800"} />
        <Stat label="Review" value={c.needs_review_count} tone={c.needs_review_count ? "text-amber-600" : "text-slate-800"} />
      </div>
    </div>
  );
}

function EmployeeListCard({ title, icon: Icon, tone, items }: {
  title: string; icon: typeof Mail; tone: string; items: { name: string; label?: string }[];
}) {
  return (
    <div className="mt-1 w-full max-w-lg rounded-lg border border-slate-200 bg-white p-3 text-xs">
      <div className="mb-2 flex items-center gap-2 font-semibold text-slate-700">
        <Icon className={cn("h-3.5 w-3.5", tone)} /> {title}
      </div>
      {items.length === 0 ? (
        <p className="text-slate-400">None 🎉</p>
      ) : (
        <div className="flex flex-wrap gap-1">
          {items.slice(0, 40).map((r, i) => (
            <span key={i} className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-600">
              {r.name}{r.label ? ` · ${r.label}` : ""}
            </span>
          ))}
          {items.length > 40 && <span className="text-slate-400">+{items.length - 40} more</span>}
        </div>
      )}
    </div>
  );
}

function TeamCard({ c }: { c: any }) {
  return (
    <div className="mt-1 w-full max-w-lg overflow-hidden rounded-lg border border-slate-200 bg-white text-xs">
      <div className="flex items-center gap-2 border-b border-slate-100 px-3 py-2 font-semibold text-slate-700">
        <CalendarDays className="h-3.5 w-3.5 text-brand-500" /> {c.month_name ?? `${c.month}/${c.year}`} by {c.group_by === "location" ? "location" : "manager"}
      </div>
      <table className="w-full">
        <thead className="text-[10px] uppercase tracking-wide text-slate-400">
          <tr><th className="px-3 py-1 text-left">Team</th><th className="px-2 py-1">Sub</th><th className="px-2 py-1">Appr</th><th className="px-2 py-1">Pend</th><th className="px-2 py-1">Miss</th></tr>
        </thead>
        <tbody>
          {(c.groups as any[]).slice(0, 20).map((g, i) => (
            <tr key={i} className="border-t border-slate-50">
              <td className="px-3 py-1 text-slate-700">{g.group}</td>
              <td className="px-2 py-1 text-center text-slate-600">{g.submitted}/{g.total}</td>
              <td className="px-2 py-1 text-center text-emerald-600">{g.approved}</td>
              <td className="px-2 py-1 text-center text-amber-600">{g.pending}</td>
              <td className={cn("px-2 py-1 text-center", g.missing ? "text-rose-600 font-semibold" : "text-slate-400")}>{g.missing}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function AnomaliesCard({ c }: { c: any }) {
  return (
    <div className="mt-1 w-full max-w-lg rounded-lg border border-amber-200 bg-amber-50/60 p-3 text-xs">
      <div className="mb-2 flex items-center gap-2 font-semibold text-amber-800">
        <AlertTriangle className="h-3.5 w-3.5" /> {c.count} record{c.count === 1 ? "" : "s"} to review — {c.month_name} {c.year}
      </div>
      <div className="space-y-1">
        {(c.anomalies as any[]).slice(0, 12).map((a, i) => (
          <div key={i} className="flex items-center gap-2">
            <Link to={`/records/${a.record_id}`} className="font-medium text-slate-700 hover:underline">{a.employee_name}</Link>
            <span className="text-slate-400">·</span>
            <span className="text-slate-500">{(a.reasons ?? []).join(", ")}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function CompareCard({ c }: { c: any }) {
  const a = c.period_a, b = c.period_b;
  const deltas = Object.entries(c.deltas ?? {}) as [string, number][];
  return (
    <div className="mt-1 w-full max-w-lg rounded-lg border border-slate-200 bg-white p-3 text-xs">
      <div className="mb-2 flex items-center gap-2 font-semibold text-slate-700">
        <CalendarDays className="h-3.5 w-3.5 text-brand-500" /> {c.employee?.name} — {a.month_name} vs {b.month_name}
      </div>
      {deltas.length === 0 ? (
        <p className="text-slate-500">No difference in leave totals between the two months.</p>
      ) : (
        <div className="flex flex-wrap gap-1.5">
          {deltas.map(([label, d]) => (
            <span key={label} className={cn("rounded px-1.5 py-0.5",
              d > 0 ? "bg-rose-50 text-rose-700" : "bg-emerald-50 text-emerald-700")}>
              {label}: {d > 0 ? `+${d}` : d}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function CardRenderer({ card }: { card: ChatCard }) {
  const c = card as any;
  switch (card.type) {
    case "leave_change": return <ChangeCard c={c as ChatChange} />;
    case "approval_change": return <ApprovalChangeCard c={c} />;
    case "draft_email": return <DraftEmailCard c={c} />;
    case "dashboard": return <DashboardCard c={c} />;
    case "missing":
      return <EmployeeListCard title={`Missing — ${c.month_name} ${c.year} (${c.missing_count})`}
        icon={AlertTriangle} tone="text-rose-500"
        items={(c.missing ?? []).map((e: any) => ({ name: e.name, label: e.employee_id }))} />;
    case "submitted":
      return <EmployeeListCard title={`Submitted — ${c.month_name} ${c.year} (${c.count})`}
        icon={Check} tone="text-emerald-500"
        items={(c.submitted ?? []).map((r: any) => ({
          name: r.employee_name,
          label: r.approval_status === "approved" ? "approved" : "pending approval",
        }))} />;
    case "pending":
      return <EmployeeListCard title={`Awaiting approval (${c.count})`} icon={UserCheck} tone="text-amber-500"
        items={(c.records ?? []).map((r: any) => ({
          name: r.employee_name,
          label: `${r.month_name} ${r.year} · ${r.approval_status === "not_approved" ? "not approved" : "pending"}`,
        }))} />;
    case "team": return <TeamCard c={c} />;
    case "anomalies": return <AnomaliesCard c={c} />;
    case "compare": return <CompareCard c={c} />;
    default: return null;
  }
}

function Bubble({
  turn, onTick, onSuggestion,
}: {
  turn: Turn; onTick?: () => void;
  onSuggestion: (s: string) => void;
}) {
  const isUser = turn.role === "user";
  const streaming = !!turn.streaming;
  const activity = turn.activity ?? [];
  const thinking = !isUser && streaming && !turn.content && activity.length === 0;
  return (
    <div className={cn("flex animate-bubble-in gap-3", isUser && "flex-row-reverse")}>
      <span className={cn(
        "mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full shadow-sm",
        isUser
          ? "bg-slate-200 text-slate-600"
          : "bg-brand-600 text-white ring-2 ring-white")}>
        {isUser ? <User className="h-4 w-4" /> : <Sparkles className="h-4 w-4" />}
      </span>
      <div className={cn("flex min-w-0 max-w-[82%] flex-col gap-1.5", isUser && "items-end")}>
        {/* live tool-activity chips */}
        {!isUser && activity.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {activity.map((a, i) => (
              <span key={a.name + i} className={cn(
                "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium",
                a.done
                  ? (a.ok === false ? "border-rose-200 bg-rose-50 text-rose-600" : "border-emerald-200 bg-emerald-50 text-emerald-700")
                  : "border-brand-200 bg-brand-50 text-brand-700")}>
                {a.done
                  ? (a.ok === false ? <X className="h-3 w-3" /> : <Check className="h-3 w-3" />)
                  : <Loader2 className="h-3 w-3 animate-spin" />}
                {a.label}
              </span>
            ))}
          </div>
        )}

        {thinking && (
          <div className="flex items-center gap-2 rounded-2xl rounded-tl-sm bg-white px-4 py-3 text-xs text-slate-400 shadow-xs ring-1 ring-slate-200/80">
            <span className="typing-dots flex items-center gap-1"><span /><span /><span /></span>
            Thinking…
          </div>
        )}

        {turn.content && (
          <div className={cn(
            "rounded-2xl px-4 py-2.5 text-sm leading-6 shadow-xs",
            isUser
              ? "rounded-tr-sm bg-gradient-to-br from-brand-600 to-brand-700 text-white"
              : "rounded-tl-sm bg-white text-slate-800 ring-1 ring-slate-200/80")}>
            {isUser ? (
              <p className="whitespace-pre-wrap">{turn.content}</p>
            ) : streaming ? (
              <p className="whitespace-pre-wrap">
                {turn.content}
                <span className="ml-0.5 inline-block h-[1.05em] w-[2px] translate-y-[2px] animate-blink rounded-full bg-brand-500 align-middle" />
              </p>
            ) : (
              <Typewriter text={turn.content} onTick={onTick} />
            )}
          </div>
        )}

        {/* structured result cards — held until the text finishes so they
            appear after the answer, not mid-stream */}
        {!streaming && (turn.cards ?? []).map((c, i) => <CardRenderer key={i} card={c} />)}

        {turn.changes?.map((c) => <ChangeCard key={c.record_id + c.leave_type + c.action} c={c} />)}

        {/* proactive follow-up chips */}
        {!streaming && (turn.suggestions ?? []).length > 0 && (
          <div className="mt-1 flex flex-wrap gap-1.5">
            {turn.suggestions!.map((s) => (
              <button key={s} onClick={() => onSuggestion(s)}
                className="inline-flex items-center gap-1 rounded-full border border-brand-200 bg-brand-50/70 px-2.5 py-1 text-[11px] font-semibold text-brand-700 transition hover:bg-brand-100">
                <Sparkles className="h-3 w-3" /> {s}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default function AgenticChatPage() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [bookOpen, setBookOpen] = useState(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const { data: suggest } = useQuery({ queryKey: ["chat-suggestions"], queryFn: fetchChatSuggestions });

  const scrollToBottom = () =>
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });

  useEffect(() => {
    scrollToBottom();
  }, [turns, sending]);

  const send = async (text: string) => {
    const msg = text.trim();
    if (!msg || sending) return;
    const history: Turn[] = [...turns, { role: "user", content: msg }];
    // Add the user turn + an empty streaming assistant turn we fill as events arrive.
    setTurns([...history, { role: "assistant", content: "", streaming: true, activity: [], cards: [] }]);
    setInput("");
    setSending(true);

    // Mutate the LAST turn (the streaming assistant) in place.
    const patch = (fn: (t: Turn) => Turn) =>
      setTurns((prev) => prev.map((t, i) => (i === prev.length - 1 ? fn(t) : t)));

    try {
      await sendChatStream(
        history.filter((t) => t.content).map((t) => ({ role: t.role, content: t.content })),
        (ev: ChatStreamEvent) => {
          if (ev.type === "token") {
            patch((t) => ({ ...t, content: t.content + ev.text }));
          } else if (ev.type === "tool" && ev.phase === "start") {
            patch((t) => ({
              ...t,
              activity: [...(t.activity ?? []), { name: ev.name, label: ev.label ?? "Working", write: ev.write }],
            }));
          } else if (ev.type === "tool" && ev.phase === "end") {
            patch((t) => ({
              ...t,
              activity: (t.activity ?? []).map((a, i, arr) =>
                i === arr.map((x) => x.name).lastIndexOf(ev.name) && !a.done
                  ? { ...a, done: true, ok: ev.ok } : a),
            }));
          } else if (ev.type === "card") {
            patch((t) => ({ ...t, cards: [...(t.cards ?? []), ev.card] }));
          } else if (ev.type === "suggestions") {
            patch((t) => ({ ...t, suggestions: ev.items }));
          } else if (ev.type === "done") {
            patch((t) => ({ ...t, streaming: false, changes: ev.changes ?? t.changes, error: ev.error ?? null }));
          }
          scrollToBottom();
        },
      );
    } catch {
      patch((t) => ({
        ...t, streaming: false,
        content: t.content || "Sorry — I couldn't reach the assistant. Please try again.",
        error: "network",
      }));
    } finally {
      patch((t) => ({ ...t, streaming: false }));
      setSending(false);
    }
  };

  const onSubmit = (ev: React.FormEvent) => {
    ev.preventDefault();
    send(input);
  };

  const empty = turns.length === 0;

  return (
    <div className="flex h-full animate-fade-up flex-col">
      <PageHeader
        title="Ask AI"
        subtitle="Ask about timesheets and leaves, or make edits — the assistant only works on this database."
        actions={
          <div className="flex items-center gap-2">
            {suggest?.enabled && suggest.model && <Badge tone="slate"><Sparkles className="h-3 w-3" />{suggest.model}</Badge>}
            <Button variant="secondary" onClick={() => setBookOpen(true)}>
              <BookOpen className="h-4 w-4" /> Prompt book
            </Button>
          </div>
        }
      />

      {suggest && !suggest.enabled && (
        <div className="mb-4 flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>No AI provider is configured. The chat will return a setup message until an admin adds a key under <span className="font-semibold">AI Settings</span>. The prompt book still works.</span>
        </div>
      )}

      <Card className="flex min-h-0 flex-1 flex-col">
        <div ref={scrollRef} className="min-h-0 flex-1 space-y-4 overflow-y-auto p-5">
          {empty ? (
            <div className="mx-auto max-w-xl py-8 text-center">
              <div className="mx-auto mb-4 flex h-14 w-14 animate-scale-in items-center justify-center rounded-2xl bg-brand-600 text-white shadow-card">
                <Sparkles className="h-7 w-7" />
              </div>
              <h3 className="text-xl font-bold">
                <span className="text-gradient">How can I help with timesheets?</span>
              </h3>
              <p className="mx-auto mt-2 max-w-md text-sm text-slate-500">
                Check submissions, count leaves, find who's missing, and add / clear leave dates.
                To bring in a sheet, use <span className="font-medium text-slate-600">Upload</span> or
                <span className="font-medium text-slate-600"> Extract Email</span>.
              </p>
              <div className="mt-6 grid gap-2.5 sm:grid-cols-2">
                {(suggest?.suggestions ?? []).map((s, i) => (
                  <button
                    key={s}
                    onClick={() => send(s)}
                    style={{ animationDelay: `${i * 70}ms` }}
                    className="group animate-fade-up rounded-xl border border-slate-200 bg-white px-3.5 py-3 text-left text-sm text-slate-700 shadow-xs transition-all hover:-translate-y-0.5 hover:border-brand-300 hover:shadow-card-hover"
                  >
                    <Sparkles className="mr-1.5 inline h-3.5 w-3.5 text-brand-500 transition-transform group-hover:scale-110" />
                    {s}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            turns.map((t, i) => (
              <Bubble key={i} turn={t} onTick={scrollToBottom} onSuggestion={send} />
            ))
          )}
        </div>

        <form onSubmit={onSubmit} className="flex items-end gap-2 border-t border-slate-100 p-3">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send(input);
              }
            }}
            rows={1}
            placeholder="Ask about timesheets, or e.g. “Add sick leave for Faizan on 26-May-2026”…"
            className="max-h-32 min-h-[42px] flex-1 resize-none rounded-xl border border-slate-300 bg-white px-3.5 py-2.5 text-sm text-slate-800 placeholder:text-slate-400 focus:border-brand-500 focus:outline-none focus:ring-4 focus:ring-brand-500/10"
          />
          <Button type="submit" disabled={sending || !input.trim()}>
            {sending ? <Spinner className="h-4 w-4" /> : <Send className="h-4 w-4" />}
            Send
          </Button>
        </form>
      </Card>

      <Modal open={bookOpen} onClose={() => setBookOpen(false)} title="Prompt book" wide
        subtitle="Examples you can ask. Replace the {placeholders} with real names, months and dates.">
        <div className="space-y-5">
          {(suggest?.prompt_book ?? []).map((g) => (
            <div key={g.group}>
              <p className="mb-2 text-[11px] font-bold uppercase tracking-wide text-slate-400">{g.group}</p>
              <div className="space-y-1.5">
                {g.prompts.map((p) => (
                  <button
                    key={p}
                    onClick={() => { setInput(p); setBookOpen(false); }}
                    className="flex w-full items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-left text-sm text-slate-700 transition-colors hover:border-brand-300 hover:bg-brand-50/40"
                  >
                    <ArrowRight className="h-3.5 w-3.5 shrink-0 text-slate-300" />
                    {p}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      </Modal>
    </div>
  );
}
