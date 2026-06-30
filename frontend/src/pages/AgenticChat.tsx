/**
 * Agentic Chat — a timesheet-scoped assistant.
 *
 * Natural-language questions and edits over the timesheet database: check
 * submissions, count leaves, find who is missing, and add / replace / clear
 * leave dates. You can also drop a sheet (PDF/DOCX/XLSX/EML) into the chat —
 * it is extracted by the SAME validated pipeline the rest of the app uses
 * (so the leaves/dates are grounded, not guessed), previewable inline, and the
 * assistant can update that employee directly from the extracted values.
 * The uploaded file is held in memory only (never saved).
 */
import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  Send, Sparkles, BookOpen, User, Loader2, AlertTriangle, Paperclip,
  ArrowRight, Plus, Minus, RotateCcw, Trash2, FileText, Eye, UserCheck, CalendarDays,
} from "lucide-react";
import {
  fetchChatSuggestions, sendChat, extractChatSheet, chatAttachmentUrl,
  type ChatMessage, type ChatChange, type ChatResponse, type ChatExtraction,
} from "../api/client";
import { Badge, Button, Card, Modal, PageHeader, Spinner } from "../components/ui";
import { FilePreviewModal } from "../components/FilePreview";
import type { PreviewFile } from "../lib/filePreview";
import { useToast } from "../components/toast";
import { cn } from "../lib/utils";

interface Turn extends ChatMessage {
  changes?: ChatChange[];
  error?: string | null;
  extraction?: ChatExtraction;   // a user turn that carried an uploaded sheet
}

const ACTION_META: Record<ChatChange["action"], { label: string; icon: typeof Plus; tone: string }> = {
  add: { label: "Added", icon: Plus, tone: "text-emerald-600" },
  set: { label: "Replaced", icon: RotateCcw, tone: "text-sky-600" },
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

function ExtractionCard({ e, onPreview }: { e: ChatExtraction; onPreview: () => void }) {
  const ok = e.status === "ok";
  const buckets = Object.entries(e.leaves ?? {}).filter(([, d]) => d.length > 0);
  return (
    <div className="mt-2 w-full rounded-lg border border-slate-200 bg-white p-3 text-xs">
      <div className="flex items-center gap-2">
        <FileText className="h-4 w-4 shrink-0 text-brand-500" />
        <span className="truncate font-semibold text-slate-700">{e.filename}</span>
        {e.token && (
          <button onClick={onPreview} className="ml-auto inline-flex items-center gap-1 rounded-md border border-slate-200 px-1.5 py-0.5 font-semibold text-brand-600 hover:bg-brand-50">
            <Eye className="h-3 w-3" /> Preview
          </button>
        )}
      </div>
      {!ok ? (
        <p className="mt-2 text-rose-600">
          {e.message || e.error || "Could not extract this file."}
        </p>
      ) : (
        <>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {e.matched_employee ? (
              <Badge tone="green"><UserCheck className="h-3 w-3" />{e.matched_employee.name} · {e.matched_employee.employee_id}</Badge>
            ) : (
              <Badge tone="amber">{e.extracted_employee_name || "Unknown"} (no match)</Badge>
            )}
            {e.month_name && <Badge tone="sky"><CalendarDays className="h-3 w-3" />{e.month_name} {e.year}</Badge>}
            <Badge tone={e.validation_status === "verified" ? "slate" : "amber"}>{e.total_leaves ?? 0} leave day(s)</Badge>
          </div>
          {e.matched_employee?.email && (
            <p className="mt-1 text-[11px] text-slate-400">{e.matched_employee.email}</p>
          )}
          {buckets.length > 0 ? (
            <ul className="mt-2 space-y-1">
              {buckets.map(([label, dates]) => (
                <li key={label} className="flex flex-wrap items-center gap-1">
                  <span className="font-semibold text-slate-600">{label}:</span>
                  {dates.map((d) => <span key={d} className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-600">{d}</span>)}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-2 text-slate-500">No leave dates found on this sheet.</p>
          )}
          {!!e.flags?.length && (
            <p className="mt-2 flex items-start gap-1 text-amber-700">
              <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />{e.flags.join(" ")}
            </p>
          )}
        </>
      )}
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

function Bubble({ turn, onPreview, onTick }: { turn: Turn; onPreview: (e: ChatExtraction) => void; onTick?: () => void }) {
  const isUser = turn.role === "user";
  return (
    <div className={cn("flex animate-bubble-in gap-3", isUser && "flex-row-reverse")}>
      <span className={cn(
        "mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full shadow-sm",
        isUser
          ? "bg-slate-200 text-slate-600"
          : "bg-gradient-to-br from-brand-500 to-violet-600 text-white ring-2 ring-white")}>
        {isUser ? <User className="h-4 w-4" /> : <Sparkles className="h-4 w-4" />}
      </span>
      <div className={cn("flex min-w-0 max-w-[82%] flex-col", isUser && "items-end")}>
        {turn.content && (
          <div className={cn(
            "rounded-2xl px-4 py-2.5 text-sm leading-6 shadow-xs",
            isUser
              ? "rounded-tr-sm bg-gradient-to-br from-brand-600 to-brand-700 text-white"
              : "rounded-tl-sm bg-white text-slate-800 ring-1 ring-slate-200/80")}>
            {isUser ? (
              <p className="whitespace-pre-wrap">{turn.content}</p>
            ) : (
              <Typewriter text={turn.content} onTick={onTick} />
            )}
          </div>
        )}
        {turn.extraction && <ExtractionCard e={turn.extraction} onPreview={() => onPreview(turn.extraction!)} />}
        {turn.changes?.map((c) => <ChangeCard key={c.record_id + c.leave_type + c.action} c={c} />)}
      </div>
    </div>
  );
}

export default function AgenticChatPage() {
  const { toast } = useToast();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [extractions, setExtractions] = useState<ChatExtraction[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [bookOpen, setBookOpen] = useState(false);
  const [preview, setPreview] = useState<PreviewFile | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const { data: suggest } = useQuery({ queryKey: ["chat-suggestions"], queryFn: fetchChatSuggestions });

  const scrollToBottom = () =>
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });

  useEffect(() => {
    scrollToBottom();
  }, [turns, sending, uploading]);

  const send = async (text: string) => {
    const msg = text.trim();
    if (!msg || sending) return;
    const history: Turn[] = [...turns, { role: "user", content: msg }];
    setTurns(history);
    setInput("");
    setSending(true);
    try {
      const res: ChatResponse = await sendChat(
        history.filter((t) => t.content).map((t) => ({ role: t.role, content: t.content })),
        extractions,
      );
      setTurns((prev) => [...prev, {
        role: "assistant", content: res.answer, changes: res.changes, error: res.error,
      }]);
    } catch {
      setTurns((prev) => [...prev, {
        role: "assistant", content: "Sorry — I couldn't reach the assistant. Please try again.", error: "network",
      }]);
    } finally {
      setSending(false);
    }
  };

  const onPickFile = async (file: File | undefined) => {
    if (!file || uploading) return;
    setUploading(true);
    try {
      const e = await extractChatSheet(file);
      // Show the upload + its grounded extraction as a user turn.
      setTurns((prev) => [...prev, { role: "user", content: "", extraction: e }]);
      if (e.status === "ok" && e.token) {
        setExtractions((prev) => [...prev, e]);
        const who = e.matched_employee?.name || e.extracted_employee_name || "the employee";
        setTurns((prev) => [...prev, {
          role: "assistant",
          content:
            `I extracted ${e.total_leaves ?? 0} leave day(s) for ${who}` +
            (e.month_name ? ` (${e.month_name} ${e.year})` : "") +
            `. ${e.matched_employee ? "Tell me to apply these to their timesheet, or ask anything about the sheet." : "I couldn't match this to an employee in the matcher — who should I attach it to?"}`,
        }]);
      } else {
        toast("error", "Couldn't extract that file", e.message || e.error || "Unsupported or unreadable file.");
      }
    } catch {
      toast("error", "Upload failed", "Could not extract that file. Please try again.");
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const openPreview = (e: ChatExtraction) => {
    if (!e.token) return;
    setPreview({ url: chatAttachmentUrl(e.token), filename: e.filename, contentType: e.content_type });
  };

  const onSubmit = (ev: React.FormEvent) => {
    ev.preventDefault();
    send(input);
  };

  const empty = turns.length === 0;

  return (
    <div className="flex h-full animate-fade-up flex-col">
      <PageHeader
        title="Agentic Chat"
        subtitle="Ask about timesheets and leaves, upload a sheet to extract it, or make edits — the assistant only works on this database."
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
          <span>No AI provider is configured. The chat will return a setup message until an admin adds a key under <span className="font-semibold">AI Settings</span>. Sheet extraction and the prompt book still work.</span>
        </div>
      )}

      <Card className="flex min-h-0 flex-1 flex-col">
        <div ref={scrollRef} className="min-h-0 flex-1 space-y-4 overflow-y-auto p-5">
          {empty ? (
            <div className="mx-auto max-w-xl py-8 text-center">
              <div className="mx-auto mb-4 flex h-14 w-14 animate-pop-in items-center justify-center rounded-2xl bg-gradient-to-br from-brand-500 to-violet-600 text-white shadow-[0_8px_24px_-8px_rgb(99_102_241/0.7)]">
                <Sparkles className="h-7 w-7" />
              </div>
              <h3 className="text-xl font-bold">
                <span className="text-gradient">How can I help with timesheets?</span>
              </h3>
              <p className="mx-auto mt-2 max-w-md text-sm text-slate-500">
                Check submissions, count leaves, find who's missing, add / clear leave dates — or
                <span className="font-medium text-slate-600"> attach a sheet</span> to extract it.
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
            turns.map((t, i) => <Bubble key={i} turn={t} onPreview={openPreview} onTick={scrollToBottom} />)
          )}
          {(sending || uploading) && (
            <div className="flex animate-bubble-in items-center gap-3 text-sm text-slate-400">
              <span className="flex h-8 w-8 items-center justify-center rounded-full bg-gradient-to-br from-brand-500 to-violet-600 text-white shadow-sm ring-2 ring-white">
                <Sparkles className="h-4 w-4" />
              </span>
              <span className="flex items-center gap-2 rounded-2xl rounded-tl-sm bg-white px-4 py-3 shadow-xs ring-1 ring-slate-200/80">
                <span className="typing-dots flex items-center gap-1">
                  <span /><span /><span />
                </span>
                <span className="text-xs font-medium text-slate-400">{uploading ? "Extracting sheet…" : "Thinking…"}</span>
              </span>
            </div>
          )}
        </div>

        <form onSubmit={onSubmit} className="flex items-end gap-2 border-t border-slate-100 p-3">
          <input
            ref={fileRef}
            type="file"
            accept=".pdf,.docx,.xlsx,.eml"
            className="hidden"
            onChange={(e) => onPickFile(e.target.files?.[0])}
          />
          <button
            type="button"
            title="Attach a timesheet (PDF, DOCX, XLSX, EML)"
            onClick={() => fileRef.current?.click()}
            disabled={uploading}
            className="flex h-[42px] w-[42px] shrink-0 items-center justify-center rounded-xl border border-slate-300 text-slate-500 transition-colors hover:border-brand-400 hover:text-brand-600 disabled:opacity-50"
          >
            {uploading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Paperclip className="h-4 w-4" />}
          </button>
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
            placeholder="Ask about timesheets, attach a sheet, or e.g. “Add sick leave for Faizan on 26-May-2026”…"
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

      <FilePreviewModal file={preview} onClose={() => setPreview(null)} />
    </div>
  );
}
