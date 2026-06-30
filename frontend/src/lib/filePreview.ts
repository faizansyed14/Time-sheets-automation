export type PreviewFile = {
  url: string;
  filename: string;
  contentType?: string | null;
};

const IMAGE_EXTS = new Set(["jpg", "jpeg", "png", "gif", "webp", "bmp", "svg"]);
const PREVIEW_EXTS = new Set([...IMAGE_EXTS, "pdf", "eml", "docx"]);

const DOCX_CTS = new Set([
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/docx",
]);

function ext(filename: string) {
  return filename.split(".").pop()?.toLowerCase() ?? "";
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

/** Strip scripts/event handlers from email HTML before sandboxed iframe render. */
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

    // Remove script-like / active content nodes completely.
    doc.querySelectorAll("script,noscript,iframe,object,embed,link[rel='preload'][as='script']").forEach((n) => n.remove());

    // Strip inline event handlers + javascript: URLs.
    doc.querySelectorAll("*").forEach((el) => {
      // Clone list because we'll mutate attributes while iterating.
      Array.from(el.attributes).forEach((a) => {
        const name = a.name.toLowerCase();
        const value = (a.value || "").trim();
        if (name.startsWith("on")) el.removeAttribute(a.name);
        if ((name === "href" || name === "src") && /^javascript:/i.test(value)) el.removeAttribute(a.name);
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
