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
