import type { Employee } from "../api/client";
import { AVATAR_COLORS } from "./theme";

export function cn(...classes: (string | false | null | undefined)[]): string {
  return classes.filter(Boolean).join(" ");
}

/** Case-insensitive match across name, employee ID, location and account
 *  manager — the same fields Manual Entry and Compare & Fix both search. */
export function filterEmployees(employees: Employee[] | undefined, query: string): Employee[] {
  const list = employees ?? [];
  const q = query.toLowerCase().trim();
  if (!q) return list;
  return list.filter((e) =>
    e.name.toLowerCase().includes(q)
    || e.employee_id.toLowerCase().includes(q)
    || (e.location ?? "").toLowerCase().includes(q)
    || (e.account_manager ?? "").toLowerCase().includes(q));
}

export function formatBytes(n: number | null | undefined): string {
  if (n == null) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Always UAE local time (Asia/Dubai, UTC+4), regardless of the viewer's own
 *  timezone — used anywhere a reminder's sent/scheduled time is shown, since
 *  the automatic run's own schedule (28th, 9am) is defined in UAE time and a
 *  "sent at" timestamp reads as ambiguous/wrong if it silently switched to
 *  whoever happens to be looking at the page. */
export function formatUaeDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  const formatted = d.toLocaleString("en-GB", {
    timeZone: "Asia/Dubai",
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
  return `${formatted} UAE`;
}

/** Outlook reading-pane style: "Fri 6/12/2026 10:15 AM". */
export function formatOutlookDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  const weekday = d.toLocaleDateString(undefined, { weekday: "short" });
  const date = d.toLocaleDateString(undefined, {
    month: "numeric",
    day: "numeric",
    year: "numeric",
  });
  const time = d.toLocaleTimeString(undefined, {
    hour: "numeric",
    minute: "2-digit",
  });
  return `${weekday} ${date} ${time}`;
}

/** "Online now" / "5m ago" / "3h ago" / "12d ago" — for the last-seen dot on
 *  the Users & Access page. Falls back to a full date once it's old enough
 *  that a relative label stops being useful. Also the label for a user who
 *  has never logged in AND for one who just logged out (logout explicitly
 *  clears last_seen_at server-side — see auth.py's /logout — so the dot
 *  flips red immediately instead of still reading "online" for up to
 *  ONLINE_THRESHOLD after they signed out). */
export function formatRelativeTime(iso: string | null | undefined): string {
  if (!iso) return "Offline";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  const seconds = Math.max(0, (Date.now() - d.getTime()) / 1000);
  if (seconds < 60) return "Online now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return formatDateTime(iso);
}

export function initials(name: string | null | undefined): string {
  if (!name) return "?";
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((p) => p[0]!.toUpperCase())
    .join("");
}

/** First line of plain body for Outlook-style thread previews. */
export function emailSnippet(body: string | null | undefined, max = 120): string {
  const line = (body || "")
    .replace(/\r\n/g, "\n")
    .split("\n")
    .map((l) => l.trim())
    .find(Boolean);
  if (!line) return "(no message body)";
  return line.length > max ? `${line.slice(0, max)}…` : line;
}

export function avatarColor(name: string | null | undefined): string {
  const s = name || "?";
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return AVATAR_COLORS[h % AVATAR_COLORS.length]!;
}
