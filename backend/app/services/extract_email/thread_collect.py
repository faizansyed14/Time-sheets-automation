"""Fetch every message of a conversation as raw .eml bytes.

Extract Email sends the whole thread in one model call, so it needs the whole
thread as bytes — not just the message that was clicked. An approval that
arrived three replies later, or the original timesheet attached weeks before a
short "Approved." reply, is only visible if the conversation is sent complete.

Provider-agnostic: it uses `list_thread_messages` when the provider supports
conversations, and degrades to the single message (plus any explicitly passed
prior message) when it does not.
"""
from __future__ import annotations

import email as _email
import email.policy
import re

from app.models.email_message import EmailMessage
from app.services.inbox.eml_export import eml_filename

# A conversation longer than this is almost certainly a mailing-list style
# thread; the newest messages carry the timesheet, so older ones are dropped
# rather than sending an unbounded request.
MAX_THREAD_MESSAGES = 15


def build_thread_bundle(
    messages: list[tuple[str, bytes]], subject: str | None,
) -> tuple[bytes, str]:
    """Package the whole conversation as ONE .eml for storage and review.

    Each thread message becomes a nested message/rfc822 part, so the stored
    evidence contains exactly what the model was shown — including the reply
    three messages later that carried the approval. Compare & Fix renders it
    with the normal EML viewer: the nested messages open individually, each
    with its own body and attachments.

    Storing the bundle (rather than the clicked message) closes an evidence
    gap: the extraction reasons over the whole thread, so the whole thread has
    to be what a reviewer and an auditor can see.
    """
    from email.message import EmailMessage as _MimeMessage
    from email.utils import formatdate

    if len(messages) == 1:
        # A single-message "thread" is already a complete .eml — wrapping it
        # would only add a layer for the reviewer to click through.
        return messages[0][1], eml_filename(subject)

    outer = _MimeMessage()
    outer["Subject"] = f"[Thread] {subject or '(no subject)'}"
    outer["Date"] = formatdate(localtime=True)
    outer["X-Timesheet-Thread-Messages"] = str(len(messages))
    outer.set_content(
        "Full conversation captured for timesheet extraction.\n\n"
        + "\n".join(f"{i}. {label}" for i, (label, _) in enumerate(messages, 1))
        + "\n\nEach message is attached below in full, oldest first."
    )
    for i, (label, data) in enumerate(messages, 1):
        safe = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", label).strip()[:80] or f"message {i}"
        try:
            nested = _email.message_from_bytes(data, policy=_email.policy.default)
            outer.add_attachment(nested, filename=f"{i:02d} — {safe}.eml")
        except Exception:
            outer.add_attachment(
                data, maintype="message", subtype="rfc822",
                filename=f"{i:02d} — {safe}.eml")
    return outer.as_bytes(), eml_filename(subject)


def _label(subject: str | None, received) -> str:
    when = ""
    try:
        when = received.strftime("%Y-%m-%d %H:%M") if received else ""
    except Exception:
        when = str(received or "")
    subj = (subject or "(no subject)").strip()
    return f"{when} — {subj}" if when else subj


async def collect_thread_emls(
    provider, email: EmailMessage, prior_email: EmailMessage | None = None,
) -> tuple[list[tuple[str, bytes]], list[str]]:
    """([(label, eml_bytes)], notes) for the conversation, OLDEST FIRST.

    Oldest-first matters: the model reads the timesheet before the reply that
    approves it, which is the order a human reads a thread in.

    `notes` is not decorative: when the mailbox fetch fails, or a long thread
    gets truncated to its newest messages, the run silently degrades to far
    fewer messages than the reviewer assumes was sent — a huge thread with
    sheets on early messages can end up reading almost nothing, with no sign
    anything went wrong. These notes are surfaced into the staged record so
    that degradation is visible instead of silent.
    """
    from app.services.inbox.eml_export import build_full_eml

    out: list[tuple[str, bytes]] = []
    seen_ids: set[str] = set()
    notes: list[str] = []

    async def add(msg_like, subject, received) -> None:
        """Accepts either a DB EmailMessage or a provider ProviderMessage.

        They are NOT interchangeable: ProviderMessage exposes `message_id`
        (not `provider_message_id`) and dataclass attachments (not dicts), so
        build_full_eml cannot take one. For those, raw MIME is fetched
        directly — which is the byte-exact original anyway, attachments and
        nested forwards included.
        """
        mid = (getattr(msg_like, "provider_message_id", None)
               or getattr(msg_like, "message_id", None))
        if mid and mid in seen_ids:
            return

        data: bytes | None = None
        if hasattr(msg_like, "provider_message_id"):
            try:
                data, _name = await build_full_eml(provider, msg_like)
            except Exception:
                data = None
        elif mid:
            getter = getattr(provider, "get_message_mime", None)
            if getter is not None:
                try:
                    data = await getter(mid)
                except Exception:
                    data = None
        if not data:
            return
        if mid:
            seen_ids.add(mid)
        out.append((_label(subject, received), data))

    conversation_id = getattr(email, "conversation_id", None)
    thread_msgs = []
    if conversation_id:
        try:
            thread_msgs = await provider.list_thread_messages(conversation_id) or []
        except Exception as e:
            thread_msgs = []
            # This is the failure mode that made a huge thread look like it was
            # sent whole when almost none of it was: the mailbox fetch broke
            # (one malformed message, a timeout, a permissions edge case) and
            # the code below silently fell back to 1-2 messages. Say so.
            notes.append(
                f"Could not fetch this conversation's full message history from "
                f"the mail server ({str(e)[:160]}) — only the currently open "
                f"message could be read, not the whole thread. Re-run once the "
                f"mailbox is reachable to pick up earlier attachments.")

    if thread_msgs:
        def _when(m):
            return getattr(m, "received_at", None) or getattr(m, "receivedDateTime", None)

        try:
            thread_msgs = sorted(thread_msgs, key=lambda m: (_when(m) is None, _when(m)))
        except Exception:
            pass
        # Keep the NEWEST slice when a thread is very long — that is where the
        # current period's sheets and approvals live.
        if len(thread_msgs) > MAX_THREAD_MESSAGES:
            dropped = len(thread_msgs) - MAX_THREAD_MESSAGES
            notes.append(
                f"This conversation has {len(thread_msgs)} messages — only the "
                f"newest {MAX_THREAD_MESSAGES} were sent for extraction; "
                f"{dropped} older message(s) (and any sheets attached only to "
                f"them) were NOT read.")
            thread_msgs = thread_msgs[-MAX_THREAD_MESSAGES:]
        for m in thread_msgs:
            await add(m, getattr(m, "subject", None), _when(m))

    # A provider without raw-MIME support (e.g. the mock) yields nothing above;
    # fall back to the DB rows rather than sending an empty thread.
    if not out:
        # No conversation support (or the lookup failed) — the clicked message,
        # plus the prior message when this is an approval-only reply.
        if prior_email is not None:
            await add(prior_email, prior_email.subject,
                      getattr(prior_email, "received_at", None))
        await add(email, email.subject, getattr(email, "received_at", None))

    return out, notes
