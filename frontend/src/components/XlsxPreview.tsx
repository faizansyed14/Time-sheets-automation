/**
 * In-browser XLSX preview — a real spreadsheet grid (not a page image).
 *
 * Renders with ExcelJS (reads cell fill/font/border, not just values — the
 * SheetJS community build drops all of that, and fill colour is exactly
 * what these timesheets use to encode leave-type legends). One continuous
 * scrollable table per sheet, with a tab bar when a workbook has more than
 * one sheet — no click-through pagination.
 */
import { useEffect, useState } from "react";
import { cn } from "../lib/utils";
import { Spinner } from "./ui";

const MAX_ROWS = 500;
const MAX_COLS = 60;

interface CellModel {
  text: string;
  colSpan: number;
  rowSpan: number;
  style: React.CSSProperties;
}

interface SheetModel {
  name: string;
  colWidths: number[];
  rows: { heightPx: number; cells: (CellModel | null)[] }[];
  truncatedRows: boolean;
  truncatedCols: boolean;
}

function decodeAddress(addr: string): { row: number; col: number } {
  const m = /^([A-Z]+)(\d+)$/.exec(addr.trim());
  if (!m) return { row: 1, col: 1 };
  let col = 0;
  for (const ch of m[1]) col = col * 26 + (ch.charCodeAt(0) - 64);
  return { row: Number(m[2]), col };
}

type MergeRange = { top: number; left: number; bottom: number; right: number };

function parseMerges(raw: unknown): MergeRange[] {
  if (!Array.isArray(raw)) return [];
  const out: MergeRange[] = [];
  for (const m of raw) {
    if (typeof m === "string") {
      const [a, b] = m.split(":");
      if (!a || !b) continue;
      const A = decodeAddress(a), B = decodeAddress(b);
      out.push({
        top: Math.min(A.row, B.row), bottom: Math.max(A.row, B.row),
        left: Math.min(A.col, B.col), right: Math.max(A.col, B.col),
      });
    } else if (m && typeof m === "object" && "top" in (m as any)) {
      const mm = m as any;
      out.push({ top: mm.top, bottom: mm.bottom, left: mm.left, right: mm.right });
    }
  }
  return out;
}

function argbToCss(argb?: string): string | undefined {
  if (!argb || argb.length < 6) return undefined;
  const hex = argb.length >= 8 ? argb.slice(-6) : argb;
  return `#${hex}`;
}

function fillBg(fill: any): string | undefined {
  if (!fill || fill.type !== "pattern" || fill.pattern !== "solid") return undefined;
  return argbToCss(fill.fgColor?.argb);
}

function fontStyle(font: any): React.CSSProperties {
  if (!font) return {};
  const style: React.CSSProperties = {};
  if (font.bold) style.fontWeight = 700;
  if (font.italic) style.fontStyle = "italic";
  if (font.underline) style.textDecoration = "underline";
  if (font.size) style.fontSize = `${Math.max(10, Math.min(20, font.size))}px`;
  const c = argbToCss(font.color?.argb);
  if (c) style.color = c;
  return style;
}

function alignStyle(al: any): React.CSSProperties {
  if (!al) return {};
  const style: React.CSSProperties = {};
  if (al.horizontal) style.textAlign = al.horizontal;
  if (al.vertical) style.verticalAlign = al.vertical === "middle" ? "middle" : al.vertical;
  if (al.wrapText) style.whiteSpace = "pre-wrap";
  return style;
}

function borderSide(b: any): string | undefined {
  if (!b || !b.style) return undefined;
  const color = argbToCss(b.color?.argb) ?? "#cbd5e1";
  const width = b.style === "thick" ? 2 : b.style === "medium" ? 1.5 : 1;
  return `${width}px solid ${color}`;
}

function cellText(cell: any): string {
  const v = cell.value;
  if (v == null) return "";
  if (typeof v === "object") {
    if (v instanceof Date) return v.toLocaleDateString();
    if (Array.isArray(v.richText)) return v.richText.map((r: any) => r.text ?? "").join("");
    if ("result" in v) return v.result == null ? "" : String(v.result);
    if ("text" in v) return String(v.text);
    if ("error" in v) return String(v.error);
    return "";
  }
  return String(v);
}

const colWidthPx = (charWidth: number | undefined) => Math.round((charWidth ?? 8.43) * 7 + 5);
const rowHeightPx = (pt: number | undefined) => Math.round((pt ?? 15) * 1.333);

function buildSheetModel(ws: any): SheetModel {
  const lastRow = Math.min(ws.rowCount || 0, MAX_ROWS);
  const lastCol = Math.min(ws.columnCount || 0, MAX_COLS);
  const merges = parseMerges(ws.model?.merges);
  const mergeSpan = new Map<string, { rowSpan: number; colSpan: number }>();
  const covered = new Set<string>();
  for (const m of merges) {
    mergeSpan.set(`${m.top}:${m.left}`, { rowSpan: m.bottom - m.top + 1, colSpan: m.right - m.left + 1 });
    for (let r = m.top; r <= m.bottom; r++) {
      for (let c = m.left; c <= m.right; c++) {
        if (r === m.top && c === m.left) continue;
        covered.add(`${r}:${c}`);
      }
    }
  }

  const colWidths: number[] = [];
  for (let c = 1; c <= lastCol; c++) colWidths.push(colWidthPx(ws.getColumn(c).width));

  const rows: SheetModel["rows"] = [];
  for (let r = 1; r <= lastRow; r++) {
    const row = ws.getRow(r);
    const cells: (CellModel | null)[] = [];
    for (let c = 1; c <= lastCol; c++) {
      const key = `${r}:${c}`;
      if (covered.has(key)) { cells.push(null); continue; }
      const cell = row.getCell(c);
      const span = mergeSpan.get(key);
      const style: React.CSSProperties = { ...alignStyle(cell.alignment), ...fontStyle(cell.font) };
      const bg = fillBg(cell.fill);
      if (bg) style.backgroundColor = bg;
      const bt = borderSide(cell.border?.top);
      const bb = borderSide(cell.border?.bottom);
      const bl = borderSide(cell.border?.left);
      const br = borderSide(cell.border?.right);
      if (bt) style.borderTop = bt;
      if (bb) style.borderBottom = bb;
      if (bl) style.borderLeft = bl;
      if (br) style.borderRight = br;
      cells.push({
        text: cellText(cell),
        colSpan: span?.colSpan ?? 1,
        rowSpan: span?.rowSpan ?? 1,
        style,
      });
    }
    rows.push({ heightPx: rowHeightPx(row.height), cells });
  }

  return {
    name: ws.name || "Sheet",
    colWidths,
    rows,
    truncatedRows: (ws.rowCount || 0) > MAX_ROWS,
    truncatedCols: (ws.columnCount || 0) > MAX_COLS,
  };
}

// Rollup wraps this CJS bundle in its own commonjs-interop namespace, and
// that wrapper's shape (which key holds the real module — `.default`,
// `.e.__moduleExports`, etc.) is an internal Rollup implementation detail
// that already changed across a rebuild here. Scan for the constructor by
// shape instead of hardcoding a key path that the next bundler version is
// free to rename.
function findWorkbookCtor(node: unknown, depth = 0): any {
  if (!node || typeof node !== "object" || depth > 4) return undefined;
  const o = node as Record<string, unknown>;
  if (typeof o.Workbook === "function") return o.Workbook;
  for (const key of Object.keys(o)) {
    const found = findWorkbookCtor(o[key], depth + 1);
    if (found) return found;
  }
  return undefined;
}

async function loadWorkbook(url: string) {
  // Lazy-loaded — only users who actually open an XLSX preview pay for the
  // parser. The browser build (package.json "browser" field) is imported by
  // subpath directly so the choice isn't left to bundler field-resolution.
  const mod: unknown = await import("exceljs/dist/exceljs.min.js");
  const Workbook = findWorkbookCtor(mod);
  if (!Workbook) throw new Error("exceljs browser bundle did not expose Workbook");
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const buf = await res.arrayBuffer();
  const wb = new Workbook();
  await wb.xlsx.load(buf);
  return wb;
}

export function XlsxPreviewPane({ url }: { url: string }) {
  const [sheets, setSheets] = useState<SheetModel[] | null>(null);
  const [active, setActive] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setSheets(null);
    setError(null);
    setActive(0);
    loadWorkbook(url)
      .then((wb) => {
        if (!alive) return;
        const models = wb.worksheets
          .filter((ws: any) => ws.state !== "hidden" && ws.state !== "veryHidden")
          .map(buildSheetModel);
        setSheets(models.length ? models : [{
          name: "Sheet1", colWidths: [], rows: [], truncatedRows: false, truncatedCols: false,
        }]);
      })
      .catch((e) => {
        if (!alive) return;
        console.error("XLSX preview failed:", e);
        setError("Could not read this spreadsheet.");
      });
    return () => { alive = false; };
  }, [url]);

  if (error) {
    return <div className="flex h-full items-center justify-center text-sm text-rose-500">{error}</div>;
  }
  if (!sheets) {
    return <div className="flex h-full items-center justify-center"><Spinner className="h-6 w-6" /></div>;
  }

  const sheet = sheets[active];

  return (
    <div className="flex h-full flex-col">
      {sheets.length > 1 && (
        <div className="flex shrink-0 gap-1 overflow-x-auto border-b border-slate-200 bg-slate-50 px-2 py-1.5">
          {sheets.map((s, i) => (
            <button
              key={s.name + i}
              type="button"
              onClick={() => setActive(i)}
              className={cn(
                "shrink-0 rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
                i === active
                  ? "bg-white text-brand-700 shadow-xs ring-1 ring-slate-200"
                  : "text-slate-500 hover:text-slate-700"
              )}
            >
              {s.name}
            </button>
          ))}
        </div>
      )}
      {(sheet.truncatedRows || sheet.truncatedCols) && (
        <div className="shrink-0 border-b border-amber-200 bg-amber-50 px-3 py-1 text-[11px] text-amber-700">
          Showing the first {MAX_ROWS} rows / {MAX_COLS} columns of a larger sheet.
        </div>
      )}
      <div className="min-h-0 flex-1 overflow-auto bg-white">
        {sheet.rows.length === 0 ? (
          <div className="flex h-full items-center justify-center p-8 text-sm text-slate-400">
            (empty spreadsheet)
          </div>
        ) : (
          <table className="border-collapse text-[12px] leading-tight text-slate-800" style={{ tableLayout: "fixed" }}>
            <colgroup>
              {sheet.colWidths.map((w, i) => <col key={i} style={{ width: w }} />)}
            </colgroup>
            <tbody>
              {sheet.rows.map((row, ri) => (
                <tr key={ri} style={{ height: row.heightPx }}>
                  {row.cells.map((cell, ci) =>
                    cell === null ? null : (
                      <td
                        key={ci}
                        colSpan={cell.colSpan}
                        rowSpan={cell.rowSpan}
                        className="overflow-hidden truncate border border-slate-200 px-1.5 py-0.5"
                        style={cell.style}
                      >
                        {cell.text}
                      </td>
                    )
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
