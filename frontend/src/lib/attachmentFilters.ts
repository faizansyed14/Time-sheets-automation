import type { Attachment } from "../api/client";

// Images under this are signature logos/icons in practice — real screenshots
// of timesheets are far larger. Keep in sync with the backend's
// MIN_IMAGE_ATTACHMENT_KB (default 70).
const MIN_IMAGE_KB = 70;

export function isImageAttachment(a: Attachment): boolean {
  return (a.content_type || "").toLowerCase().startsWith("image/");
}

/** True when an image is smaller than the logo threshold. Applies ONLY to
 *  images — documents of any size are never filtered by this. */
export function isTinyImage(contentType: string | null | undefined, size: number | null | undefined): boolean {
  if (!(contentType || "").toLowerCase().startsWith("image/")) return false;
  return typeof size === "number" && size > 0 && size < MIN_IMAGE_KB * 1024;
}

/** Signature/logo images that live in the body, never a real screenshot or a
 *  document worth cross-checking against. Checked in order of trust: the
 *  size threshold (logos/icons), Graph's own `is_inline` flag (authoritative,
 *  set at sync time by the provider), then the backend's inline-resolution
 *  result, then a filename pattern for rows synced before `is_inline`
 *  existed. Real screenshots attached as files stay visible. */
const _BODY_JUNK_RE = /^(image\d{2,3}\.(png|jpe?g|gif)|outlook-.+\.(png|jpe?g|gif|bmp)|c2_signature_.+\.(png|jpe?g|gif))$/i;

export function isBodyJunkImage(a: Attachment, inlineIds: string[]): boolean {
  if (!isImageAttachment(a)) return false;
  if (isTinyImage(a.content_type, a.size)) return true;
  if (a.is_inline) return true;
  if (inlineIds.includes(a.attachment_id)) return true;
  return _BODY_JUNK_RE.test(a.filename || "");
}
