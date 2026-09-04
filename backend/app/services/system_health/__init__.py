"""System health monitoring — checks the LLM provider key/credits and the
Microsoft Graph credential on a schedule and emails an admin the moment
either breaks, instead of extraction/OTP/reminder sends silently failing one
by one with nothing surfacing why. See monitor.py for the checks themselves,
alert_mailer.py for delivery."""
