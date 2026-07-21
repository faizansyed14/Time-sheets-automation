export type PreviewFile = {
  url: string;
  filename: string;
  contentType?: string | null;
  /** Server render-to-image endpoint (DOCX/XLSX) — the preview works in any
   *  browser while `url` stays the downloadable original. */
  renderUrl?: string | null;
  /** Raw embedded bytes for cases like attachments inside an .eml preview,
   *  where no standalone render URL exists yet. */
  renderUpload?: {
    filename: string;
    contentType: string;
    dataB64: string;
  } | null;
};

const IMAGE_EXTS = new Set(["jpg", "jpeg", "png", "gif", "webp", "bmp", "svg"]);
const PREVIEW_EXTS = new Set([...IMAGE_EXTS, "pdf", "eml", "docx", "xlsx"]);

const DOCX_CTS = new Set([
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/docx",
]);

const XLSX_CTS = new Set([
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "application/vnd.ms-excel",
]);

function ext(filename: string) {
  return filename.split(".").pop()?.toLowerCase() ?? "";
}

export function isXlsx(filename: string, contentType?: string | null) {
  const ct = contentType?.toLowerCase() ?? "";
  return ext(filename) === "xlsx" || XLSX_CTS.has(ct);
}

export function isPdf(filename: string, contentType?: string | null) {
  const ct = contentType?.toLowerCase() ?? "";
  return ct === "application/pdf" || ext(filename) === "pdf";
}

export function isEml(filename: string, contentType?: string | null) {
  const ct = contentType?.toLowerCase() ?? "";
  return ext(filename) === "eml" || ct === "message/rfc822";
}

export function isDocx(filename: string, contentType?: string | null) {
  const ct = contentType?.toLowerCase() ?? "";
  return ext(filename) === "docx" || DOCX_CTS.has(ct);
}

export function isPreviewable(filename: string, contentType?: string | null) {
  const ct = contentType?.toLowerCase() ?? "";
  if (ct.startsWith("image/") || ct === "application/pdf" || ct === "message/rfc822") return true;
  if (DOCX_CTS.has(ct)) return true;
  return PREVIEW_EXTS.has(ext(filename));
}

export function downloadFile(url: string, filename: string) {
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.rel = "noreferrer";
  a.click();
}

// Attributes that can carry a URL the browser may navigate/execute.
const URL_ATTRS = new Set(["href", "src", "action", "formaction", "xlink:href", "data", "background", "poster"]);

function isScriptUrl(value: string): boolean {
  // Browsers ignore control chars/whitespace inside the scheme ("java\nscript:"),
  // so strip them before testing — a plain /^javascript:/ test can be evaded.
  const v = (value || "").replace(/[\u0000-\u0020]/g, "").toLowerCase();
  return v.startsWith("javascript:") || v.startsWith("vbscript:");
}

/** Strip scripts / event handlers / executable URLs from email HTML before the
 * sandboxed iframe render. The iframe sandbox (no allow-scripts) remains the
 * hard boundary; this keeps the document clean so the browser has nothing to
 * block (and logs no "Blocked script execution" console noise). */
export function sanitizeEmailHtml(html: string): string {
  // Fast path for non-browser contexts (shouldn't happen in this app, but safe).
  if (typeof window === "undefined" || typeof DOMParser === "undefined") {
    return html
      .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, "")
      .replace(/<script\b[^>]*\/>/gi, "")
      .replace(/\s+on\w+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)/gi, "");
  }

  try {
    const doc = new DOMParser().parseFromString(html, "text/html");

    // Remove script-like / active / navigation-hijacking nodes completely.
    doc.querySelectorAll(
      "script,noscript,iframe,frame,object,embed,base,template," +
      "link[rel='preload'][as='script'],meta[http-equiv='refresh' i]"
    ).forEach((n) => n.remove());

    // Strip inline event handlers + javascript:/vbscript: URLs anywhere.
    doc.querySelectorAll("*").forEach((el) => {
      // Clone list because we'll mutate attributes while iterating.
      Array.from(el.attributes).forEach((a) => {
        const name = a.name.toLowerCase();
        if (name.startsWith("on")) {
          el.removeAttribute(a.name);
          return;
        }
        if (URL_ATTRS.has(name) && isScriptUrl(a.value)) el.removeAttribute(a.name);
      });
    });

    return doc.documentElement.outerHTML;
  } catch {
    // Fallback to regex if parsing fails.
    return html
      .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, "")
      .replace(/<script\b[^>]*\/>/gi, "")
      .replace(/\s+on\w+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)/gi, "");
  }
}
