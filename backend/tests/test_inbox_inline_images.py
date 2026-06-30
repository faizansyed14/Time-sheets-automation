"""Inline cid: images in HTML email bodies render as self-contained data URIs."""
import pytest

from app.services.email_provider import get_email_provider
from app.services.inbox.inline_images import inline_cid_images
from tests.conftest import auth_headers


async def test_inline_cid_images_resolves_to_data_uri():
    provider = get_email_provider()
    msg = await provider.get_message("MSG-0001")
    assert msg.body_html and "cid:alphalogo" in msg.body_html
    attachments = [
        {"attachment_id": a.attachment_id, "filename": a.filename,
         "content_type": a.content_type, "kind": a.kind, "cid": a.cid}
        for a in msg.attachments
    ]
    html, inlined = await inline_cid_images(provider, "MSG-0001", msg.body_html, attachments)
    # cid: reference replaced with an embedded PNG data URI
    assert "cid:alphalogo" not in html
    assert "data:image/png;base64," in html
    # the logo attachment was reported as inlined
    logo = next(a for a in attachments if a["cid"] == "alphalogo")
    assert inlined == [logo["attachment_id"]]


async def test_no_html_or_no_cids_is_passthrough():
    provider = get_email_provider()
    html, inlined = await inline_cid_images(provider, "MSG-0001", None, [])
    assert html is None and inlined == []
    html2, inlined2 = await inline_cid_images(
        provider, "MSG-0001", "<p>plain, no images</p>", [])
    assert html2 == "<p>plain, no images</p>" and inlined2 == []


async def test_email_detail_endpoint_inlines_logo(client, admin_token):
    h = auth_headers(admin_token)
    r = await client.get("/api/v1/inbox/MSG-0001", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["body_html"] and "data:image/png;base64," in body["body_html"]
    assert "cid:alphalogo" not in body["body_html"]
    # The inline logo is hidden from the downloadable attachment list.
    assert len(body["inline_attachment_ids"]) == 1
    inlined = set(body["inline_attachment_ids"])
    logo = next(a for a in body["attachments"] if a.get("cid") == "alphalogo")
    assert logo["attachment_id"] in inlined


def test_attachment_count_excludes_images():
    """Part 1: inbox count is documents only (pdf/docx/xlsx/eml), not images/logos."""
    from app.api.routes.inbox import _doc_count, is_doc_attachment
    atts = [
        {"filename": "timesheet.pdf", "content_type": "application/pdf"},
        {"filename": "sheet.docx", "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
        {"filename": "alpha_data_footer_gradient1.png", "content_type": "image/png"},
        {"filename": "manager_approval.png", "content_type": "image/png"},
        {"filename": "nested.eml", "content_type": "message/rfc822"},
    ]
    assert _doc_count(atts) == 3
    assert is_doc_attachment(atts[0]) and not is_doc_attachment(atts[2])
