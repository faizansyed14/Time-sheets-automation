"""The health-alert email itself — plain HTML, inline styles (same reasoning
as reminders/template.py: most email clients strip <style> blocks). This one
is written FOR an admin/ops reader, not an employee, so it's more direct and
technical than the reminder email — it names the exact component and error.
"""
from __future__ import annotations

from html import escape

_RED = "#b91c1c"
_RED_BG = "#fee2e2"
_GREEN = "#0f766e"
_GREEN_BG = "#ccfbf1"
_INK = "#0f172a"
_SLATE = "#64748b"
_BORDER = "#e2e8f0"
_BG = "#f1f5f9"

_COMPONENT_LABEL = {"llm": "LLM API (extraction / chat)", "graph": "Microsoft Graph (email / OTP)"}


def render_alert_html(*, component: str, status: str, detail: str) -> str:
    label = escape(_COMPONENT_LABEL.get(component, component))
    is_recovery = status == "ok"
    accent, accent_bg = (_GREEN, _GREEN_BG) if is_recovery else (_RED, _RED_BG)
    headline = f"{label} is working again" if is_recovery else f"{label} needs attention"
    body_text = escape(detail)
    next_step = (
        "No action needed — this is confirmation the fix worked."
        if is_recovery else
        "Check AI Settings (LLM) or the Graph app registration in Azure (client secret expiry) "
        "as appropriate, then use \"Check now\" on the System Health card to confirm it's resolved."
    )
    return f"""\
<!DOCTYPE html>
<html>
  <body style="margin:0;padding:0;background:{_BG};font-family:Segoe UI,Arial,Helvetica,sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{_BG};padding:32px 16px;">
      <tr>
        <td align="center">
          <table role="presentation" width="560" cellpadding="0" cellspacing="0"
                 style="width:560px;max-width:100%;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 1px 3px rgba(15,23,42,0.08);">
            <tr>
              <td style="background:{accent};padding:18px 28px;">
                <span style="color:#ffffff;font-size:14px;font-weight:700;letter-spacing:0.02em;">TIMESHEET PORTAL — SYSTEM HEALTH</span>
              </td>
            </tr>
            <tr>
              <td style="padding:32px 28px 28px 28px;">
                <h1 style="margin:0 0 20px 0;color:{_INK};font-size:20px;line-height:1.35;font-weight:700;">
                  {headline}
                </h1>
                <table role="presentation" cellpadding="0" cellspacing="0" width="100%"
                       style="background:{accent_bg};border-radius:8px;">
                  <tr>
                    <td style="padding:14px 18px;color:{_INK};font-size:14px;line-height:1.6;">
                      {body_text}
                    </td>
                  </tr>
                </table>
                <p style="margin:22px 0 0 0;color:{_SLATE};font-size:13px;line-height:1.6;">
                  {next_step}
                </p>
                <hr style="border:none;border-top:1px solid {_BORDER};margin:24px 0 16px 0;" />
                <p style="margin:0;color:{_SLATE};font-size:12px;line-height:1.6;">
                  Automated system health check — please do not reply to this email.
                </p>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>"""


def alert_subject(*, component: str, status: str) -> str:
    label = _COMPONENT_LABEL.get(component, component)
    if status == "ok":
        return f"[Timesheet Portal] Resolved: {label} is working again"
    word = "DOWN" if status == "down" else "warning"
    return f"[Timesheet Portal] {word}: {label}"
