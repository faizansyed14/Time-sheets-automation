"""
Inline `cid:` images in an email's HTML body as self-contained data URIs.

Outlook / Microsoft Graph HTML bodies reference embedded images (logos,
signatures, pasted screenshots) by their MIME Content-ID, e.g.
``<img src="cid:image001.png@01DA...">``. A browser cannot resolve a `cid:`
URL, so those images render empty.

Rather than depend on fragile client-side cid→attachment matching plus
authenticated image sub-requests from a sandboxed iframe, we resolve each
referenced image to a base64 ``data:`` URI on the server (the same approach the
.eml parser already uses). The body then renders exactly like Outlook with no
extra network requests.
"""
from __future__ import annotations

import asyncio
import base64
import re

# Matches the cid token inside src="cid:..." / src='cid:...' / url(cid:...).
_CID_REF_RE = re.compile(r"cid:([^\"'\s>)]+)", re.IGNORECASE)
_IMG_TAG_CID_RE = re.compile(
    r"<img\b[^>]*\bsrc\s*=\s*[\"']cid:[^\"']+[\"'][^>]*/?>",
    re.IGNORECASE,
)
_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)


def _norm_cid(value: str | None) -> str:
    """Normalise a Content-ID for comparison: drop <> wrappers and case."""
    return (value or "").strip().strip("<>").lower()


def _uuids(value: str | None) -> set[str]:
    return {m.group(0).lower() for m in _UUID_RE.finditer(value or "")}


def cid_ref_matches(
    ref: str,
    *,
    cid: str | None,
    filename: str | None,
) -> bool:
    """True when a MIME part (cid / filename) satisfies an HTML ``cid:`` ref.

    Outlook / signature add-ins often disagree between the HTML token
    (``cid:facebook_32x32_<uuid>.png``) and the attachment filename
    (``C2_signature_facebook2_<uuid>.png``) — UUID overlap catches those.
    """
    ref_full = _norm_cid(ref)
    ref_name = ref_full.split("@")[0]
    ref_uuids = _uuids(ref_full)

    ac = _norm_cid(cid)
    if ac and ac in (ref_full, ref_name):
        return True
    if ac and (ref_name in ac or ac in ref_full or ref_full in ac):
        return True

    fn = (filename or "").lower()
    if fn:
        if fn == ref_name or ref_name in fn or fn in ref_full:
            return True
        fn_stem = fn.rsplit(".", 1)[0]
        if fn_stem and (fn_stem in ref_full or fn_stem in ref_name):
            return True

    for blob in (ac, fn, ref_full, ref_name):
        if ref_uuids & _uuids(blob):
            return True
    return False


def _find_attachment(ref: str, attachments: list[dict]) -> dict | None:
    """Resolve a `cid:` reference to its attachment."""
    for a in attachments:
        if cid_ref_matches(ref, cid=a.get("cid"), filename=a.get("filename")):
            return a
    return None


def strip_unresolved_cids(html: str) -> str:
    """Remove ``cid:`` image refs the browser cannot load (CSP blocks them)."""
    if not html or "cid:" not in html.lower():
        return html
    html = _IMG_TAG_CID_RE.sub("", html)
    return _CID_REF_RE.sub("", html)


async def inline_cid_images(
    provider,
    message_id: str,
    body_html: str | None,
    attachments: list[dict],
) -> tuple[str | None, list[str]]:
    """Return (html_with_data_uris, inlined_attachment_ids).

    `inlined_attachment_ids` are the attachments that were embedded in the body
    — the caller hides these from the separate attachment list so an inline
    logo is not also shown as a downloadable file (Outlook behaviour).
    """
    if not body_html or "cid:" not in body_html.lower():
        return body_html, []

    refs = {m.group(1) for m in _CID_REF_RE.finditer(body_html)}
    if not refs:
        return body_html, []

    # Resolve every cid: reference to its attachment first (no I/O), then fetch
    # ALL of them concurrently — a signature block alone can carry 5-10 inline
    # images, and fetching them one-by-one from Graph serialises that many
    # network round trips before the email can even render.
    pairs = [(ref, att) for ref in refs if (att := _find_attachment(ref, attachments))]

    async def _fetch(ref: str, att: dict) -> tuple[str, str | None]:
        try:
            data, _fn, ctype = await provider.get_attachment_bytes(
                message_id, att["attachment_id"])
        except Exception:
            return ref, None
        if not data:
            return ref, None
        ctype = (ctype or att.get("content_type") or "image/png").split(";")[0]
        if not ctype.startswith("image/"):
            return ref, None
        b64 = base64.b64encode(data).decode()
        return ref, f"data:{ctype};base64,{b64}"

    results = await asyncio.gather(*(_fetch(ref, att) for ref, att in pairs))

    data_uris: dict[str, str] = {}     # cid ref -> data: URI
    inlined_ids: set[str] = set()
    for (ref, att), (_ref2, uri) in zip(pairs, results):
        if uri is None:
            continue
        data_uris[ref] = uri
        inlined_ids.add(att["attachment_id"])

    def _sub(m: re.Match) -> str:
        return data_uris.get(m.group(1), m.group(0))

    out = _CID_REF_RE.sub(_sub, body_html) if data_uris else body_html
    return strip_unresolved_cids(out), sorted(inlined_ids)
