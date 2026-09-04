"""
System health checks — LLM provider key/credits + Microsoft Graph credential.

Why this exists: an expired API key, exhausted LLM credits, or a Graph client
secret that's expired/rotated currently fails SILENTLY from the app's point of
view — extraction just starts returning llm_failed on every file, OTP/
reminder emails just stop sending, one at a time, with nothing that actively
tells anyone. This runs on a schedule (see core/celery_app.py's beat entry,
gated by settings.system_health_check_enabled) and emails an admin the moment
either breaks — and again when it recovers.

Deliberately isolated from reminders and OTP: separate config flags
(system_health_*, never reminder_sending_enabled / otp_sending_enabled),
separate mailer (alert_mailer.py, its own Graph token-fetch, not shared code),
separate DB table (SystemHealthCheck, not reminder_config / chat_access).
Flipping any reminder/OTP setting has zero effect on this feature, and this
feature reading/writing its own table has zero effect on theirs — checked by
reading every touch point before writing a line of this, not assumed.

What "checked" means per component (both are read-only, side-effect-free
against the real providers — no message sent, no completion generated), and
exactly how "key invalid" is told apart from "credits ran out" — deliberately
only ever reporting what's objectively true, never a guess:

  llm   -> OpenRouter: GET /auth/key returns the account's real, exact
           {limit, usage} in USD — the SAME numbers that determine whether a
           real completion call succeeds or gets rejected. "usage >= limit"
           IS credits exhausted, not an inference from it — it's the exact
           condition under which OpenRouter would reject a real extraction
           call with HTTP 402 "Insufficient credits". A 401 here is a
           DIFFERENT, distinct fact: the key itself is invalid/revoked,
           independent of balance (a bad key still has "some" balance
           number attached to the account, it's just not YOUR key anymore).
           These two are never conflated into one vague "down" — the alert
           email always names which one actually happened.

           There is deliberately NO "running low" early warning here: this
           app has no way to know how many tokens (=$) a given prompt will
           actually cost ahead of time (varies by model, image size, prompt
           length, thinking-token usage...), so a fixed dollar floor would be
           a guess about runway, not a fact — removed rather than shipped as
           a false-precision number. What's reported is only ever the
           OBJECTIVE, exact state: still funded, or exhausted right now.

           OpenAI (or any other OpenAI-compatible base_url, non-OpenRouter):
           GET /models — this only validates that the key itself is still
           accepted at all. Credit/quota exhaustion has no equivalent check
           here: a standard OpenAI API key has no
           balance-reading endpoint (unlike a real completion call, /models
           is not billing-gated, so it would return 200 even at $0 balance) —
           that failure mode will only ever surface as a real failed
           extraction (llm_failed in Pipeline), same as before this feature
           existed. Not worth faking a number for.

  graph -> the same client-credentials OAuth2 token exchange the OTP/
           reminder/inbox mailers already perform — success means the
           tenant/client id AND the client secret are BOTH still valid and
           unexpired RIGHT NOW; failure (invalid_client, AADSTS7000215
           wrong secret / 7000222 expired secret, etc.) means exactly the
           credential problem this feature exists to catch. This can only
           ever tell you "already broken", never "about to break" — Graph's
           token endpoint doesn't expose the secret's own expiry date, and
           reading it for real (Application.Read.All + admin consent) is a
           permission this app doesn't request. So an advance warning is
           layered on top from settings.graph_client_secret_expires_on — a
           date YOU enter (copied from Azure Portal when the secret was
           created/rotated), purely a manually-tracked calendar reminder,
           never a substitute for the live check above.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.openai_url import openai_urls
from app.models.system_health import HealthComponent, HealthStatus, SystemHealthCheck
from app.services.system_health import alert_mailer
from app.services.system_health.template import alert_subject, render_alert_html

log = logging.getLogger("system_health.monitor")


class CheckResult:
    def __init__(self, status: str, detail: str, applicable: bool = True):
        self.status = status
        self.detail = detail
        self.applicable = applicable  # False = nothing configured to check, skip entirely


# --------------------------------------------------------------- LLM check
def _check_llm_sync() -> CheckResult:
    api_key = (settings.openai_api_key or "").strip()
    if not api_key or api_key.lower() in ("change-me", "missing"):
        return CheckResult(HealthStatus.UNKNOWN, "No LLM API key configured.", applicable=False)

    provider = (settings.llm_provider or "openai").strip().lower()
    api_root, _langchain_base = openai_urls(settings.openai_base_url)
    headers = {"Authorization": f"Bearer {api_key}"}

    if provider == "openrouter":
        try:
            r = httpx.get(f"{api_root}/v1/auth/key", headers=headers, timeout=20)
        except Exception as exc:
            return CheckResult(HealthStatus.DOWN, f"Could not reach OpenRouter to check the key: {exc}")
        if r.status_code == 401:
            return CheckResult(HealthStatus.DOWN, "OpenRouter rejected the API key (401 — invalid or revoked).")
        if r.status_code != 200:
            return CheckResult(HealthStatus.DOWN, f"OpenRouter key check failed (HTTP {r.status_code}).")
        data = (r.json() or {}).get("data") or {}
        limit = data.get("limit")
        usage = data.get("usage") or 0.0
        if limit is None:
            return CheckResult(HealthStatus.OK, "OpenRouter key is valid (no hard spend limit set).")
        remaining = float(limit) - float(usage)
        # Exact, not estimated: this IS the same balance a real completion
        # call is checked against — usage >= limit is precisely the
        # condition OpenRouter itself would reject a request for (HTTP 402
        # "Insufficient credits"), not a prediction of it.
        if remaining <= 0:
            return CheckResult(HealthStatus.DOWN, f"OpenRouter credits exhausted (used ${usage:.2f} of ${limit:.2f}).")
        return CheckResult(HealthStatus.OK, f"OpenRouter key valid, ${remaining:.2f} credit remaining.")

    # Generic OpenAI-compatible: validate the key against a free, no-cost
    # endpoint. Deliberately reports ONLY what this endpoint can actually
    # prove — key accepted or not. A 429 here is NOT reported as "credits
    # exhausted": /models isn't billing-gated the way a real completion call
    # is, so a 429 here is far more likely a plain rate limit (too many
    # requests/min) than a quota signal — asserting "credits" from it would
    # be exactly the kind of guess this feature exists to avoid. It still
    # surfaces as a down/anomalous result (something needs a look), just
    # without claiming to know why.
    try:
        r = httpx.get(f"{api_root}/v1/models", headers=headers, timeout=20)
    except Exception as exc:
        return CheckResult(HealthStatus.DOWN, f"Could not reach the LLM provider to check the key: {exc}")
    if r.status_code == 401:
        return CheckResult(HealthStatus.DOWN, "The LLM provider rejected the API key (401 — invalid or revoked).")
    if r.status_code != 200:
        return CheckResult(
            HealthStatus.DOWN,
            f"LLM provider key check failed (HTTP {r.status_code}). Credit/quota exhaustion has no dedicated "
            "signal for a plain OpenAI-compatible key — check the provider's own dashboard if this persists.",
        )
    return CheckResult(HealthStatus.OK, "LLM API key is valid.")


# ------------------------------------------------------------- Graph check
def _check_graph_sync() -> CheckResult:
    if not (settings.graph_tenant_id and settings.graph_client_id and settings.graph_client_secret):
        return CheckResult(HealthStatus.UNKNOWN, "Microsoft Graph is not configured.", applicable=False)
    try:
        r = httpx.post(
            f"https://login.microsoftonline.com/{settings.graph_tenant_id}/oauth2/v2.0/token",
            data={
                "client_id": settings.graph_client_id,
                "client_secret": settings.graph_client_secret,
                "scope": "https://graph.microsoft.com/.default",
                "grant_type": "client_credentials",
            },
            timeout=20,
        )
    except Exception as exc:
        return CheckResult(HealthStatus.DOWN, f"Could not reach Microsoft's token endpoint: {exc}")
    if r.status_code == 200 and "access_token" in (r.json() or {}):
        return _with_expiry_warning(CheckResult(HealthStatus.OK, "Microsoft Graph credential is valid."))
    try:
        err = (r.json() or {}).get("error_description", r.text[:300])
    except Exception:
        err = r.text[:300]
    return CheckResult(HealthStatus.DOWN, f"Microsoft Graph token request failed (HTTP {r.status_code}): {err}")


def _with_expiry_warning(result: CheckResult) -> CheckResult:
    """Layered ONLY on top of an already-OK live check (never instead of
    it) — settings.graph_client_secret_expires_on is a manually-entered
    date, not something read from Azure, so it can only ever add an advance
    warning here, never override what the live check just proved is true
    right now."""
    raw = (settings.graph_client_secret_expires_on or "").strip()
    if not raw:
        return result
    try:
        expires_on = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        log.warning("graph_client_secret_expires_on=%r is not a YYYY-MM-DD date — ignoring.", raw)
        return result
    days_left = (expires_on - datetime.now(timezone.utc).date()).days
    if days_left > settings.system_health_graph_expiry_warn_days:
        return result
    if days_left < 0:
        note = f"its configured expiry ({raw}) passed {abs(days_left)} day(s) ago"
    elif days_left == 0:
        note = f"its configured expiry ({raw}) is today"
    else:
        note = f"its configured expiry ({raw}) is in {days_left} day(s)"
    return CheckResult(
        HealthStatus.DEGRADED,
        f"{result.detail} Still working, but {note} — rotate the client secret in Azure Portal "
        "(App registrations -> Certificates & secrets) before then.",
    )


_CHECKS = {HealthComponent.LLM: _check_llm_sync, HealthComponent.GRAPH: _check_graph_sync}


async def _get_or_create(db: AsyncSession, component: str) -> SystemHealthCheck:
    row = await db.get(SystemHealthCheck, component)
    if row is None:
        row = SystemHealthCheck(component=component, status=HealthStatus.UNKNOWN)
        db.add(row)
        await db.flush()
    return row


def _should_alert(row: SystemHealthCheck, new_status: str, now: datetime) -> bool:
    """Alert on a transition INTO a bad state, on transition back to ok
    (recovery), or if still bad and the resend cooldown has elapsed. Never
    alerts twice for the same already-alerted bad streak within the cooldown."""
    was_bad = row.status in (HealthStatus.DOWN, HealthStatus.DEGRADED)
    is_bad = new_status in (HealthStatus.DOWN, HealthStatus.DEGRADED)
    if is_bad and not was_bad:
        return True                                    # just broke
    if not is_bad and was_bad:
        return True                                    # just recovered
    if is_bad and was_bad:
        if not row.last_alert_sent_at:
            return True
        elapsed = now - row.last_alert_sent_at
        return elapsed >= timedelta(hours=settings.system_health_alert_resend_hours)
    return False                                        # ok -> ok, nothing to say


async def _check_one(db: AsyncSession, component: str, result: CheckResult, now: datetime) -> dict:
    row = await _get_or_create(db, component)
    prior_status = row.status
    alert_sent = False
    alert_error: str | None = None

    if _should_alert(row, result.status, now):
        try:
            alert_mailer.send_health_alert_email(
                alert_subject(component=component, status=result.status),
                render_alert_html(component=component, status=result.status, detail=result.detail),
            )
            alert_sent = True
            row.last_alert_sent_at = now
        except Exception as exc:
            alert_error = str(exc)[:500]
            log.warning("System health alert email failed for %s: %s", component, exc)

    row.status = result.status
    row.detail = result.detail
    row.last_checked_at = now
    if result.status == HealthStatus.OK:
        row.last_ok_at = now
    await db.commit()

    return {
        "component": component, "prior_status": prior_status, "status": result.status,
        "detail": result.detail, "alert_sent": alert_sent, "alert_error": alert_error,
    }


async def run_health_check(db: AsyncSession) -> dict:
    """Run both checks and return a per-component summary. Never raises —
    a check that can't even complete (network error, unexpected response
    shape) is recorded as DOWN with the error as detail, not left unhandled;
    this is itself a scheduled background task and must never crash silently
    the way the very failures it's meant to catch already do."""
    now = datetime.now(timezone.utc)
    out: dict[str, dict] = {}
    for component, check_fn in _CHECKS.items():
        try:
            result = check_fn()
        except Exception as exc:
            result = CheckResult(HealthStatus.DOWN, f"Health check itself raised: {exc}")
        if not result.applicable:
            # Nothing configured for this component — leave any existing row
            # untouched (don't manufacture a false "down" for a feature that
            # was never turned on) and skip alerting entirely.
            out[component] = {"component": component, "status": result.status,
                              "detail": result.detail, "applicable": False}
            continue
        out[component] = await _check_one(db, component, result, now)
    return out


async def list_status(db: AsyncSession) -> list[SystemHealthCheck]:
    rows = (await db.execute(select(SystemHealthCheck))).scalars().all()
    return list(rows)
