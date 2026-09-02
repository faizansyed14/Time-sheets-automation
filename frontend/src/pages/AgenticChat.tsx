/**
 * Ask AI — a plain, general-purpose chat assistant (no database access, no
 * app-specific tools). The same shape as ChatGPT/Claude: type anything, or
 * attach a file (PDF, DOCX, XLSX, TXT/CSV, or an image) and ask about it.
 */
import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Send, Sparkles, User, AlertTriangle, Paperclip, X, FileText } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  fetchChatAccess, fetchChatSuggestions, sendChatStream, type ChatMessage, type ChatStreamEvent,
} from "../api/client";
import { Badge, Card, PageHeader, Spinner } from "../components/ui";
import { useAuth } from "../lib/auth";
import { cn } from "../lib/utils";

// Renders the assistant's markdown as actual formatting (bold, lists, code,
// tables) instead of showing raw "**"/"-"/"#" characters — sized to sit
// inside a compact chat bubble, not a full document.
function MarkdownText({ text }: { text: string }) {
  return (
    <div className="prose-chat">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          p: ({ children }) => <p className="whitespace-pre-wrap [&:not(:first-child)]:mt-2">{children}</p>,
          ul: ({ children }) => <ul className="mt-1 list-disc space-y-0.5 pl-5">{children}</ul>,
          ol: ({ children }) => <ol className="mt-1 list-decimal space-y-0.5 pl-5">{children}</ol>,
          li: ({ children }) => <li>{children}</li>,
          strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
          a: ({ children, href }) => (
            <a href={href} target="_blank" rel="noreferrer" className="text-brand-600 underline underline-offset-2">
              {children}
            </a>
          ),
          code: ({ children }) => (
            <code className="rounded bg-slate-100 px-1 py-0.5 font-mono text-[0.85em]">{children}</code>
          ),
          pre: ({ children }) => (
            <pre className="mt-1.5 overflow-x-auto rounded-lg bg-slate-900 p-3 text-[0.85em] text-slate-100">{children}</pre>
          ),
          h1: ({ children }) => <p className="mt-2 font-semibold">{children}</p>,
          h2: ({ children }) => <p className="mt-2 font-semibold">{children}</p>,
          h3: ({ children }) => <p className="mt-2 font-semibold">{children}</p>,
          table: ({ children }) => (
            <div className="mt-1.5 overflow-x-auto"><table className="w-full border-collapse text-left">{children}</table></div>
          ),
          th: ({ children }) => <th className="border-b border-slate-200 px-2 py-1 font-semibold">{children}</th>,
          td: ({ children }) => <td className="border-b border-slate-100 px-2 py-1">{children}</td>,
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}

interface Turn extends ChatMessage {
  error?: string | null;
  attachmentName?: string;
  streaming?: boolean;   // true while tokens are still arriving
  streamed?: boolean;    // true once tokens arrived; prevents double typing animation
}

const EXAMPLE_PROMPTS = [
  "Summarize a document I attach",
  "Explain a concept in simple terms",
  "Help me write and refine something",
  "Pull the key numbers out of a spreadsheet I attach",
];

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
    <>
      <MarkdownText text={text.slice(0, shown)} />
      {!done && <span className="ml-0.5 inline-block h-[1.05em] w-[2px] translate-y-[2px] animate-blink rounded-full bg-brand-500 align-middle" />}
    </>
  );
}

function Bubble({ turn, onTick }: { turn: Turn; onTick?: () => void }) {
  const isUser = turn.role === "user";
  const streaming = !!turn.streaming;
  const thinking = !isUser && streaming && !turn.content;
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
        {isUser && turn.attachmentName && (
          <span className="inline-flex items-center gap-1 rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-[11px] font-medium text-slate-500">
            <FileText className="h-3 w-3" /> {turn.attachmentName}
          </span>
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
              <>
                <MarkdownText text={turn.content} />
                <span className="ml-0.5 inline-block h-[1.05em] w-[2px] translate-y-[2px] animate-blink rounded-full bg-brand-500 align-middle" />
              </>
            ) : turn.streamed ? (
              <MarkdownText text={turn.content} />
            ) : (
              <Typewriter text={turn.content} onTick={onTick} />
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export default function AgenticChatPage() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [sending, setSending] = useState(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const { isAdmin, canWrite } = useAuth();
  const { data: access } = useQuery({ queryKey: ["chat-access"], queryFn: fetchChatAccess });
  // Admin always has access. Otherwise, locked unless an admin has turned
  // this on for "others" AND the role can actually use it — viewer stays
  // locked regardless of the toggle, since the chat's own POST endpoint
  // structurally rejects viewer's requests either way (see agentic_chat.py).
  const locked = !isAdmin && !(canWrite && access?.enabled_for_others);

  const { data: suggest } = useQuery({ queryKey: ["chat-suggestions"], queryFn: fetchChatSuggestions });

  const scrollToBottom = () =>
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });

  useEffect(() => {
    scrollToBottom();
  }, [turns, sending]);

  const send = async (text: string) => {
    if (locked) return;
    const msg = text.trim();
    const attached = file;
    if (!msg && !attached) return;
    if (sending) return;
    const history: Turn[] = [
      ...turns,
      { role: "user", content: msg, attachmentName: attached?.name },
    ];
    setTurns([...history, { role: "assistant", content: "", streaming: true }]);
    setInput("");
    setFile(null);
    setSending(true);

    // Mutate the LAST turn (the streaming assistant) in place.
    const patch = (fn: (t: Turn) => Turn) =>
      setTurns((prev) => prev.map((t, i) => (i === prev.length - 1 ? fn(t) : t)));

    try {
      await sendChatStream(
        history.filter((t) => t.content).map((t) => ({ role: t.role, content: t.content })),
        (ev: ChatStreamEvent) => {
          if (ev.type === "token") {
            patch((t) => ({ ...t, content: t.content + ev.text, streamed: true }));
          } else if (ev.type === "done") {
            patch((t) => ({ ...t, streaming: false, error: ev.error ?? null }));
          }
          scrollToBottom();
        },
        undefined,
        attached,
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

  const onPickFile = (ev: React.ChangeEvent<HTMLInputElement>) => {
    const f = ev.target.files?.[0];
    if (f) setFile(f);
    ev.target.value = "";   // allow re-picking the same file later
  };

  const empty = turns.length === 0;

  return (
    <div className="flex h-full animate-fade-up flex-col">
      <PageHeader
        title="Ask AI"
        subtitle="We're using a smaller AI model, which can make mistakes, please cross-check important answers."
        actions={
          !locked && suggest?.enabled && suggest.model ? (
            <Badge tone="slate"><Sparkles className="h-3 w-3" />{suggest.model}</Badge>
          ) : null
        }
      />

      {!locked && suggest && !suggest.enabled && (
        <div className="mb-4 flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>No AI provider is configured. The chat will return a setup message until an admin adds a key under <span className="font-semibold">AI Settings</span>.</span>
        </div>
      )}

      <Card className="relative flex min-h-0 flex-1 overflow-hidden">
        <div className={cn("flex min-h-0 flex-1 flex-col", locked && "pointer-events-none select-none opacity-25 blur-[2px]")}>
          <div ref={scrollRef} className="min-h-0 flex-1 space-y-4 overflow-y-auto p-5">
            {empty ? (
              <div className="mx-auto max-w-xl py-8 text-center">
                <div className="mx-auto mb-4 flex h-14 w-14 animate-scale-in items-center justify-center rounded-2xl bg-brand-600 text-white shadow-card">
                  <Sparkles className="h-7 w-7" />
                </div>
                <h3 className="text-xl font-bold">
                  <span className="text-gradient">How can I help you today?</span>
                </h3>
                <p className="mx-auto mt-2 max-w-md text-sm text-slate-500">
                  Ask anything, or attach a file — PDF, DOCX, XLSX, TXT/CSV, or an image.
                </p>
                <div className="mt-6 grid gap-2.5 sm:grid-cols-2">
                  {EXAMPLE_PROMPTS.map((s, i) => (
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
              turns.map((t, i) => <Bubble key={i} turn={t} onTick={scrollToBottom} />)
            )}
          </div>

          <form onSubmit={onSubmit} className="border-t border-slate-100 p-3">
            {file && (
              <div className="mb-2 inline-flex items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-xs text-slate-600">
                <FileText className="h-3.5 w-3.5 text-slate-400" />
                {file.name}
                <button type="button" onClick={() => setFile(null)} className="text-slate-400 hover:text-slate-600">
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            )}
            <div className="flex items-end gap-2">
              <input ref={fileInputRef} type="file" className="hidden" onChange={onPickFile} />
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                title="Attach a file"
                className="flex h-[42px] w-[42px] shrink-0 items-center justify-center rounded-xl border border-slate-300 text-slate-500 transition-colors hover:border-brand-300 hover:text-brand-600"
              >
                <Paperclip className="h-4 w-4" />
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
                placeholder="Message Ask AI, or attach a file…"
                className="max-h-32 min-h-[42px] flex-1 resize-none rounded-xl border border-slate-300 bg-white px-3.5 py-2.5 text-sm text-slate-800 placeholder:text-slate-400 focus:border-brand-500 focus:outline-none focus:ring-4 focus:ring-brand-500/10"
              />
              <button
                type="submit"
                disabled={sending || (!input.trim() && !file)}
                className="flex h-[42px] items-center gap-1.5 rounded-xl bg-brand-600 px-4 text-sm font-semibold text-white shadow-xs transition-colors hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {sending ? <Spinner className="h-4 w-4" /> : <Send className="h-4 w-4" />}
                Send
              </button>
            </div>
          </form>
        </div>

        {locked && (
          <div className="absolute inset-0 z-10 flex items-center justify-center bg-white/80 p-6 backdrop-blur-sm">
            <div className="max-w-md text-center">
              <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-gradient-to-br from-brand-500 to-brand-700 text-white shadow-card">
                <Sparkles className="h-8 w-8" />
              </div>
              <p className="text-xs font-bold uppercase tracking-[0.24em] text-brand-600">Not available yet</p>
              <h3 className="mt-2 text-2xl font-bold text-slate-900">Ask AI isn't open to your account</h3>
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}
