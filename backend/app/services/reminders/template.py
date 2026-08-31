"""The reminder email itself — one HTML string, table-based layout (email
clients, Outlook especially, don't reliably support flex/grid), inline styles
throughout since most clients strip <style> blocks. Colors match the app's own
brand teal (see frontend/tailwind.config: brand-600 #0f766e, brand-50 #ccfbf1)
so the email reads as the same product, not a generic notification.
"""
from __future__ import annotations

from html import escape

_TEAL = "#0f766e"
_TEAL_DARK = "#115e59"
_MINT = "#ccfbf1"
_INK = "#0f172a"
_SLATE = "#64748b"
_BORDER = "#e2e8f0"
_BG = "#f1f5f9"


def render_reminder_html(*, employee_name: str, month_label: str) -> str:
    """month_label e.g. "August 2026". employee_name is escaped — it comes
    from the employee matcher, not a trusted template author."""
    name = escape(employee_name or "there")
    period = escape(month_label)
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
              <td style="background:{_TEAL};padding:18px 28px;">
                <span style="color:#ffffff;font-size:14px;font-weight:700;letter-spacing:0.02em;">TIMESHEET PORTAL</span>
              </td>
            </tr>
            <tr>
              <td style="padding:32px 28px 28px 28px;">
                <p style="margin:0 0 8px 0;color:{_TEAL};font-size:11px;font-weight:700;letter-spacing:0.12em;text-transform:uppercase;">
                  Timesheet reminder
                </p>
                <h1 style="margin:0 0 20px 0;color:{_INK};font-size:22px;line-height:1.35;font-weight:700;">
                  Your {period} timesheet hasn't arrived yet
                </h1>
                <p style="margin:0 0 14px 0;color:{_INK};font-size:14px;line-height:1.6;">
                  Dear {name},
                </p>
                <p style="margin:0 0 22px 0;color:{_INK};font-size:14px;line-height:1.6;">
                  We haven't received your timesheet for the period below. Please submit it at
                  your earliest convenience so it can be reviewed and processed on time.
                </p>
                <table role="presentation" cellpadding="0" cellspacing="0" width="100%"
                       style="background:{_MINT};border-radius:8px;">
                  <tr>
                    <td style="padding:14px 18px;">
                      <span style="color:{_TEAL_DARK};font-size:12px;font-weight:600;">Period due</span>
                      <span style="float:right;color:{_TEAL_DARK};font-size:14px;font-weight:700;">{period}</span>
                    </td>
                  </tr>
                </table>
                <hr style="border:none;border-top:1px solid {_BORDER};margin:28px 0 16px 0;" />
                <p style="margin:0;color:{_SLATE};font-size:12px;line-height:1.6;">
                  This is an automated reminder — please do not reply to this email.
                </p>
              </td>
            </tr>
          </table>
          <p style="margin:16px 0 0 0;color:{_SLATE};font-size:11px;">
            Sent by the Timesheet Portal automated reminder service.
          </p>
        </td>
      </tr>
    </table>
  </body>
</html>"""


def reminder_subject(month_label: str) -> str:
    return f"Reminder: {month_label} timesheet not yet received"
