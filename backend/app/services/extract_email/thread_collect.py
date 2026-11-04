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

from app.core.config import settings
from app.models.email_message import EmailMessage
from app.services.inbox.eml_export import eml_filename


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

    # policy=SMTP is not optional here — the default policy's line separator
    # is a bare "\n", not the "\r\n" MIME actually requires. That's fine for
    # Python's OWN (lenient) parser reading it back, which is why this looked
    # fine in-app — but it silently turns every quoted-printable soft line
    # break ("=\r\n") into "=\n", which Outlook's stricter parser does NOT
    # recognise as a soft break. It shows the literal "=" instead of joining
    # the line, corrupting exactly the words/entities that happened to wrap
    # mid-token (confirmed by reproducing this exact byte sequence directly).
    outer = _MimeMessage(policy=_email.policy.SMTP)
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


_FETCH_RETRY_DELAY = 1.5      # seconds x attempt between re-downloads of a message


class CollectNotes(list):
    """The human-readable notes list collect_thread_emls returns, plus
    `.problems`: the subset that means part of the conversation could NOT be
    read (a message that would not download, a history lookup that failed).
    Those are retriable — the run is marked incomplete and tried again —
    whereas a plain note (e.g. "older messages were reused") is informational."""

    def __init__(self) -> None:
        super().__init__()
        self.problems: list[str] = []

    def problem(self, text: str) -> None:
        self.append(text)
        self.problems.append(text)


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
    *, since=None, context_messages: int = 2, only_ids: set[str] | None = None,
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

    `since` (a datetime, typically sheet_cache.last_extraction_at()) trims the
    thread to only messages received AFTER it, plus `context_messages`
    immediately before the earliest new one — so a reply arriving into an
    already-extracted thread only costs a read of what's actually new, not the
    whole conversation again. Messages outside that window are never fetched
    (no raw-MIME round-trip spent on them at all); whatever was already
    extracted from them is carried forward separately (see
    sheet_cache.thread_cached_sheets / thread_extract.extract_thread_sheets).
    `since=None` (first-ever extraction on this thread, or a forced full
    re-read) sends the whole thread, unchanged from before this parameter
    existed.
    """
    from app.services.inbox.eml_export import build_full_eml

    import asyncio

    out: list[tuple[str, bytes]] = []
    seen_ids: set[str] = set()
    notes = CollectNotes()

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
        last_err = ""
        # A transient mailbox hiccup (throttling, a dropped connection) must not
        # silently cost a message — try a few times before giving up on it.
        for attempt in range(3):
            try:
                if hasattr(msg_like, "provider_message_id"):
                    data, _name = await build_full_eml(provider, msg_like, strict=True)
                elif mid:
                    getter = getattr(provider, "get_message_mime", None)
                    data = await getter(mid) if getter is not None else None
                else:
                    data = None
            except Exception as e:
                data = None
                last_err = str(e)[:120]
            if data:
                break
            if attempt < 2:
                await asyncio.sleep(_FETCH_RETRY_DELAY * (attempt + 1))
        if not data:
            if mid or hasattr(msg_like, "provider_message_id"):
                notes.problem(
                    f"Could not download the message '{(subject or '(no subject)')[:80]}'"
                    f"{' (' + last_err + ')' if last_err else ''} — it was NOT read this run.")
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
            notes.problem(
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

        if only_ids:
            # An explicit selection ("extract just these messages"): fetch exactly
            # those, nothing else - no incremental window, no context messages.
            known = {getattr(m, "message_id", None) for m in thread_msgs}
            missing = only_ids - known
            thread_msgs = [m for m in thread_msgs if getattr(m, "message_id", None) in only_ids]
            if missing:
                notes.problem(f"{len(missing)} selected message(s) were not found in this conversation.")
            since = None
        if since is not None:
            new_index = next(
                (i for i, m in enumerate(thread_msgs) if (_when(m) or since) > since), None)
            if new_index is not None:
                window_start = max(0, new_index - context_messages)
                if window_start > 0:
                    notes.append(
                        f"This thread was already extracted through an earlier message — "
                        f"{window_start} older message(s) were NOT re-read; anything already "
                        f"extracted from them is carried forward from the last run.")
                    thread_msgs = thread_msgs[window_start:]
            # else: nothing newer than `since` — a re-run with no new mail at
            # all (e.g. triggered by hand). Fall through to the full/oldest-N
            # behaviour below rather than silently sending nothing.

        # Keep the NEWEST slice when a thread is very long — that is where the
        # current period's sheets and approvals live. A conversation longer
        # than this is either a genuine long-running back-and-forth, or a
        # manager's reminder that many people replied to independently (one
        # employee per reply) — either way, the newest messages matter most
        # and an unbounded request isn't safe to send in one call.
        max_thread_messages = int(settings.max_thread_messages or 0)
        if max_thread_messages > 0 and len(thread_msgs) > max_thread_messages:
            dropped = len(thread_msgs) - max_thread_messages
            notes.append(
                f"This conversation has {len(thread_msgs)} messages — only the "
                f"newest {max_thread_messages} were sent for extraction; "
                f"{dropped} older message(s) (and any sheets attached only to "
                f"them) were NOT read.")
            thread_msgs = thread_msgs[-max_thread_messages:]
        for m in thread_msgs:
            await add(m, getattr(m, "subject", None), _when(m))

    # A provider without raw-MIME support (e.g. the mock) yields nothing above;
    # fall back to the DB rows rather than sending an empty thread.
    if not out:
        # No conversation support (or the lookup failed) — the clicked message,
        # plus the prior message when this is an approval-only reply.
        if only_ids and email.provider_message_id not in only_ids:
            notes.problem("None of the selected messages could be found in this conversation.")
        else:
            if prior_email is not None and not only_ids:
                await add(prior_email, prior_email.subject,
                          getattr(prior_email, "received_at", None))
            await add(email, email.subject, getattr(email, "received_at", None))

    return out, notes
