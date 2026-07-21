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
  "application/xlsx",
]);

function ext(filename: string) {
  return filename.split(".").pop()?.toLowerCase() ?? "";
}

export function isXlsx(filename: string, contentType?: string | null) {
  const ct = contentType?.toLowerCase() ?? "";
  return ext(filename) === "xlsx" || ext(filename) === "xls" || XLSX_CTS.has(ct);
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

/** Office docs previewed via server page-images (same UX path as inbox). */
export function isOfficeDoc(filename: string, contentType?: string | null) {
  return isDocx(filename, contentType) || isXlsx(filename, contentType);
}

/** Extensions accepted for manual file attachment (Upload / Compare & Fix). */
export const ATTACHABLE_FILE_RE = /\.(pdf|docx|xlsx|png|jpe?g|eml)$/i;

/** The server page-image renderer only supports office docs and PDFs — this
 *  is the one gate FilePreviewModal checks (`useServerPages`), so any other
 *  attachment type should get no renderUrl rather than one that's silently
 *  ignored downstream. */
export function attachmentRenderUrlIfSupported(
  filename: string, contentType: string | null | undefined, renderUrl: string,
): string | undefined {
  return (isOfficeDoc(filename, contentType) || isPdf(filename, contentType))
    ? renderUrl
    : undefined;
}

export function mimeFor(filename: string, contentType?: string | null): string {
  const ct = (contentType || "").toLowerCase();
  if (ct && ct !== "application/octet-stream" && ct !== "application/binary") {
    if (isPdf(filename, ct) || isDocx(filename, ct) || isXlsx(filename, ct)
        || isEml(filename, ct) || ct.startsWith("image/")) {
      return contentType!;
    }
  }
  if (isPdf(filename, ct)) return "application/pdf";
  if (isDocx(filename, ct)) {
    return "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
  }
  if (isXlsx(filename, ct)) {
    return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
  }
  if (isEml(filename, ct)) return "message/rfc822";
  const e = ext(filename);
  if (["jpg", "jpeg", "png", "gif", "webp", "bmp"].includes(e)) {
    return e === "jpg" ? "image/jpeg" : `image/${e}`;
  }
  return contentType || "application/octet-stream";
}

export function isPreviewable(filename: string, contentType?: string | null) {
  const ct = contentType?.toLowerCase() ?? "";
  if (ct.startsWith("image/") || ct === "application/pdf" || ct === "message/rfc822") return true;
  if (DOCX_CTS.has(ct) || XLSX_CTS.has(ct)) return true;
  return PREVIEW_EXTS.has(ext(filename));
}

/** Decode base64 (as delivered in EML preview payloads) to raw bytes.
 *  Return type is inferred as Uint8Array<ArrayBuffer> so it stays a valid
 *  BlobPart — annotating it as plain Uint8Array widens the buffer type. */
export function b64ToBytes(dataB64: string) {
  const binary = atob(dataB64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
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

const IMG_TAG_CID_RE =
  /<img\b[^>]*\bsrc\s*=\s*["']cid:[^"']+["'][^>]*\/?>/gi;
const CID_REF_RE = /cid:([^"'\s>)]+)/gi;

/** Browsers cannot load ``cid:`` URLs and CSP blocks them in sandboxed iframes. */
export function stripUnresolvedCids(html: string): string {
  if (!html || !/cid:/i.test(html)) return html;
  return html.replace(IMG_TAG_CID_RE, "").replace(CID_REF_RE, "");
}

/** Wrap sanitised email HTML for a sandboxed blob iframe (Inbox / EML preview). */
export function buildEmailHtmlDocument(html: string): string {
  const safe = sanitizeEmailHtml(html);
  return (
    `<!doctype html><html><head><meta charset="utf-8">` +
    `<base target="_blank">` +
    `<style>` +
    `html,body{margin:0;padding:12px 16px;word-wrap:break-word;overflow-wrap:break-word}` +
    `body{font-family:Calibri,Segoe UI,Arial,sans-serif;font-size:11pt;line-height:1.4;color:#000}` +
    `img{max-width:100%;height:auto}` +
    `table{border-collapse:collapse;max-width:100%}` +
    `a{color:#0563c1;text-decoration:underline}` +
    `</style></head>` +
    `<body>${safe}</body></html>`
  );
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
      "link[rel='preload'][as='script'],meta[http-equiv='refresh' i]," +
      "svg script"
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

    return stripUnresolvedCids(doc.body?.innerHTML || html);
  } catch {
    // Fallback to regex if parsing fails.
    return stripUnresolvedCids(
      html
      .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, "")
      .replace(/<script\b[^>]*\/>/gi, "")
      .replace(/\s+on\w+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)/gi, "")
    );
  }
}
