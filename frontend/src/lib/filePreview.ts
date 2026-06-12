export type PreviewFile = {
  url: string;
  filename: string;
  contentType?: string | null;
};

const IMAGE_EXTS = new Set(["jpg", "jpeg", "png", "gif", "webp", "bmp", "svg"]);
const PREVIEW_EXTS = new Set([...IMAGE_EXTS, "pdf"]);

function ext(filename: string) {
  return filename.split(".").pop()?.toLowerCase() ?? "";
}

export function isPdf(filename: string, contentType?: string | null) {
  const ct = contentType?.toLowerCase() ?? "";
  return ct === "application/pdf" || ext(filename) === "pdf";
}

export function isPreviewable(filename: string, contentType?: string | null) {
  const ct = contentType?.toLowerCase() ?? "";
  if (ct.startsWith("image/") || ct === "application/pdf") return true;
  return PREVIEW_EXTS.has(ext(filename));
}

export function downloadFile(url: string, filename: string) {
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.rel = "noreferrer";
  a.click();
}
