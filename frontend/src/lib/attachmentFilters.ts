import type { Attachment } from "../api/client";

export function isImageAttachment(a: Attachment): boolean {
  return (a.content_type || "").toLowerCase().startsWith("image/");
}

/** Signature/logo images that live in the body, never a real screenshot or a
 *  document worth cross-checking against. Checked in order of trust: Graph's
 *  own `is_inline` flag (authoritative, set at sync time by the provider),
 *  then the backend's inline-resolution result, then a filename pattern for
 *  rows synced before `is_inline` existed. Real screenshots attached as files
 *  keep their real names and stay visible. */
const _BODY_JUNK_RE = /^(image\d{2,3}\.(png|jpe?g|gif)|outlook-.+\.(png|jpe?g|gif|bmp)|c2_signature_.+\.(png|jpe?g|gif))$/i;

export function isBodyJunkImage(a: Attachment, inlineIds: string[]): boolean {
  if (!isImageAttachment(a)) return false;
  if (a.is_inline) return true;
  if (inlineIds.includes(a.attachment_id)) return true;
  return _BODY_JUNK_RE.test(a.filename || "");
}
