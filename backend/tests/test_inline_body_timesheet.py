"""Inline attendance screenshots pasted into the email HTML body.

These cover the shared file_processor.py helpers (CID resolution, decorative
image filtering, signature stripping) that the collector and the vault/
preview rendering both depend on — independent of the extraction pipeline
that consumes them.
"""
import io
import random
from email.message import EmailMessage

from PIL import Image

from app.services.extraction import file_processor as fp


def _large_png_bytes(min_bytes: int = 70 * 1024) -> bytes:
    """Valid PNG large enough to pass the inline-timesheet byte threshold."""
    random.seed(42)
    for side in (600, 800, 1000, 1200, 1400, 1600):
        img = Image.new("RGB", (side, side))
        img.putdata([
            (random.randrange(256), random.randrange(256), random.randrange(256))
            for _ in range(side * side)
        ])
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        data = buf.getvalue()
        if len(data) >= min_bytes:
            return data
    raise AssertionError(f"could not build PNG >= {min_bytes} bytes")


def _inline_timesheet_eml(png_bytes: bytes, *, cid: str = "sheet001@01DAX") -> bytes:
    """Outlook-style: HTML body references a large inline CID image."""
    from email import policy

    msg = EmailMessage(policy=policy.SMTP)
    msg["Subject"] = "RE: TIMESHEET for June 2026 | Rinziya Rasheed | E2507086"
    html = (
        "<html><body>"
        "<p>Approved.</p><p>Kind regards, Mojca</p><hr/>"
        "<p>From: Rinziya</p>"
        "<p>For your kind approval on the June timesheet provided below.</p>"
        f'<img src="cid:{cid}"/>'
        "</body></html>"
    )
    msg.set_content("Approved.\n\nKind regards,\nMojca")
    msg.add_alternative(html, subtype="html")
    msg.add_attachment(
        png_bytes, maintype="image", subtype="png",
        filename="image003.png",
    )
    part = next(p for p in msg.walk() if p.get_filename() == "image003.png")
    part.add_header("Content-Id", f"<{cid}>")
    part.replace_header("Content-Disposition", 'inline; filename="image003.png"')
    return msg.as_bytes()


def test_eml_collect_keeps_medium_inline_body_timesheet():
    """Generic image003.png pasted in body (≥20 KB) must reach vision."""
    payload = _large_png_bytes()
    eml = _inline_timesheet_eml(payload)
    atts = fp.eml_all_attachments(eml)
    assert len(atts) == 1, atts
    assert atts[0][0] == "body_timesheet.png"
    assert atts[0][2] == "image"


def test_eml_collect_skips_tiny_inline_logo():
    payload = b"\x89PNG\r\n\x1a\n" + (b"\x00" * 4_000)
    eml = _inline_timesheet_eml(payload)
    assert fp.eml_all_attachments(eml) == []


def test_resolve_cid_embeds_image_in_html():
    payload = _large_png_bytes()
    eml = _inline_timesheet_eml(payload)
    _subj, html, _plain = fp._eml_subject_and_body_html(eml)
    resolved = fp._resolve_eml_html_cids(eml, html or "")
    assert "data:image/png;base64," in resolved
    assert "cid:sheet001" not in resolved.lower()


def test_resolve_cid_drops_logo_and_wide_banner():
    """Signature logos + wide brand strips must not become vision pixels."""
    logo = Image.new("RGB", (120, 40), (200, 0, 0))
    logo_buf = io.BytesIO()
    logo.save(logo_buf, format="PNG")
    logo_bytes = logo_buf.getvalue()

    banner = Image.new("RGB", (1200, 200))
    random.seed(99)
    banner.putdata([
        (random.randrange(256), random.randrange(256), random.randrange(256))
        for _ in range(1200 * 200)
    ])
    ban_buf = io.BytesIO()
    banner.save(ban_buf, format="PNG")
    banner_bytes = ban_buf.getvalue()
    assert len(banner_bytes) >= fp._INLINE_TIMESHEET_MIN_BYTES

    sheet = _large_png_bytes()
    msg = EmailMessage()
    msg["Subject"] = "TIMESHEET"
    html = (
        "<html><body><p>ATTENDANCE SHEET</p>"
        '<img src="cid:sheet@x"/>'
        "<p>EMPLOYEE SIGNATURE</p><p>MANAGER SIGNATURE</p>"
        '<img src="cid:logo@x"/>'
        '<img src="cid:banner@x"/>'
        "</body></html>"
    )
    msg.set_content("ATTENDANCE SHEET")
    msg.add_alternative(html, subtype="html")
    for payload, fn, cid in (
        (sheet, "sheet.png", "sheet@x"),
        (logo_bytes, "logo.png", "logo@x"),
        (banner_bytes, "banner.png", "banner@x"),
    ):
        msg.add_attachment(payload, maintype="image", subtype="png", filename=fn)
        part = next(p for p in msg.walk() if p.get_filename() == fn)
        part.add_header("Content-Id", f"<{cid}>")
        part.replace_header("Content-Disposition", f'inline; filename="{fn}"')

    eml = msg.as_bytes()
    resolved = fp._resolve_eml_html_cids(eml, html)
    assert "data:image/png;base64," in resolved
    assert resolved.count("data:image/png;base64,") == 1
    assert "cid:logo" not in resolved.lower()
    assert "cid:banner" not in resolved.lower()
    assert '<img src="cid:logo@x"/>' not in resolved
    assert '<img src="cid:banner@x"/>' not in resolved


def test_strip_html_after_sheet_signatures():
    html = (
        "<table><tr><td>ATTENDANCE</td></tr></table>"
        "<p>EMPLOYEE SIGNATURE</p><p>MANAGER SIGNATURE</p>"
        "<img src='cid:logo@x'/>"
        "<p>T: +971 4 777 8526</p>"
        "<p>DUBAI HOLDING REAL ESTATE</p>"
    )
    out = fp._strip_html_after_sheet_signatures(html)
    assert "ATTENDANCE" in out
    assert "MANAGER SIGNATURE" in out
    assert "DUBAI HOLDING" not in out
    assert "cid:logo" not in out
    assert "signature-redacted" in out


def test_dedup_prefers_real_name_over_generic_inline_duplicate():
    """Outlook/Graph often attach the SAME image twice: once inline (generic
    cid name) and once as a normal attachment (its real name) — these must
    collapse into ONE sheet, keeping the real name, not the generic one."""
    banner = _large_png_bytes()
    msg = EmailMessage()
    msg["Subject"] = "AI Platform brochure"
    msg.set_content("See below.")
    # Real, descriptively-named attachment.
    msg.add_attachment(banner, maintype="image", subtype="png", filename="ATT00008.png")
    # The SAME bytes again, inline, with a generic auto-generated name.
    msg.add_attachment(banner, maintype="image", subtype="png", filename="image005.png")
    part = next(p for p in msg.walk() if p.get_filename() == "image005.png")
    part.add_header("Content-Id", "<banner@x>")
    part.replace_header("Content-Disposition", 'inline; filename="image005.png"')
    eml = msg.as_bytes()

    atts = fp.eml_all_attachments(eml)
    names = [a[0] for a in atts]
    assert names == ["ATT00008.png"], names
