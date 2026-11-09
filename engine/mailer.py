"""Sends the set-password emails through your company's mail server (SMTP).

Settings in .env: SMTP_HOST, SMTP_PORT (587), SMTP_USER, SMTP_PASSWORD, SMTP_FROM, and SMTP_SECURITY
(starttls, ssl or none; default starttls). The password is never printed or logged.
"""
import os
import smtplib
import ssl
from email.message import EmailMessage


def configured():
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_FROM"))


def link_email_html(greeting, lead, link, hours):
    """The set-password email as a small branded page. Inline styles only, because mail apps ignore stylesheets."""
    from html import escape
    g, l, k = escape(greeting), escape(lead), escape(link, quote=True)
    return f"""<!doctype html><html><body style="margin:0;padding:0;background:#f3f6f2;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f6f2;padding:32px 12px;">
<tr><td align="center">
<table role="presentation" width="480" cellpadding="0" cellspacing="0" style="max-width:480px;width:100%;background:#ffffff;border-radius:12px;overflow:hidden;border:1px solid #dfe7dc;font-family:Segoe UI,Helvetica,Arial,sans-serif;color:#1f2a24;">
<tr><td style="background:#123524;padding:22px 28px;"><span style="font-size:20px;font-weight:600;color:#ffffff;letter-spacing:0.2px;">HeritageEdge</span><span style="display:inline-block;width:8px;height:8px;border-radius:4px;background:#e0a526;margin-left:8px;"></span></td></tr>
<tr><td style="padding:28px 28px 8px;">
<p style="margin:0 0 14px;font-size:16px;font-weight:600;">{g}</p>
<p style="margin:0 0 22px;font-size:15px;line-height:1.55;color:#3b4a41;">{l} Choose a password to start using it.</p>
<table role="presentation" cellpadding="0" cellspacing="0"><tr><td style="background:#259821;border-radius:8px;"><a href="{k}" style="display:inline-block;padding:12px 26px;font-size:15px;font-weight:600;color:#ffffff;text-decoration:none;">Set your password</a></td></tr></table>
<p style="margin:22px 0 6px;font-size:13px;line-height:1.5;color:#5d6b62;">The link works once and expires in {hours} hours.</p>
<p style="margin:0 0 6px;font-size:13px;line-height:1.5;color:#5d6b62;">If the button does not work, paste this address into your browser:</p>
<p style="margin:0 0 22px;font-size:12px;line-height:1.5;word-break:break-all;color:#2d6a9f;">{k}</p>
</td></tr>
<tr><td style="padding:16px 28px;background:#f3f6f2;font-size:12px;color:#7a877f;">If you did not expect this email, you can ignore it.</td></tr>
</table></td></tr></table></body></html>"""


def _clean(text):
    pw = os.environ.get("SMTP_PASSWORD", "")
    return str(text).replace(pw, "***") if pw else str(text)


def send(to, subject, body, html=None):
    """Returns (True, "") when sent, or (False, reason) when not. Never raises."""
    if not configured():
        return False, "The email could not be sent: email is not set up (SMTP_HOST and SMTP_FROM in .env)."
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = os.environ["SMTP_FROM"], to, subject
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT") or 587)
    mode = (os.environ.get("SMTP_SECURITY") or "starttls").lower()
    try:
        ctx = ssl.create_default_context()
        if mode == "ssl":
            server = smtplib.SMTP_SSL(host, port, timeout=20, context=ctx)
        else:
            server = smtplib.SMTP(host, port, timeout=20)
        with server:
            if mode == "starttls":
                server.starttls(context=ctx)
            if os.environ.get("SMTP_USER"):
                server.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
            server.send_message(msg)
    except (OSError, smtplib.SMTPException, ValueError) as exc:
        return False, "The email could not be sent: " + _clean(exc)
    return True, ""


def diagnose(out=print):
    """Walks the connection step by step and says where it stops. Never prints the password."""
    host, port = os.environ.get("SMTP_HOST"), int(os.environ.get("SMTP_PORT") or 587)
    mode = (os.environ.get("SMTP_SECURITY") or "starttls").lower()
    user = os.environ.get("SMTP_USER")
    out(f"Server {host}, port {port}, security {mode}, login user {user or '(none)'}, from {os.environ.get('SMTP_FROM')}")
    if not host:
        out("SMTP_HOST is empty in .env.")
        return False
    step = "connect to the server"
    try:
        ctx = ssl.create_default_context()
        server = smtplib.SMTP_SSL(host, port, timeout=20, context=ctx) if mode == "ssl" else smtplib.SMTP(host, port, timeout=20)
        out("1. Connected to the server: ok")
        with server:
            step = "say hello (EHLO)"
            server.ehlo()
            out("2. Server answered hello: ok")
            if mode == "starttls":
                step = "switch to an encrypted connection (STARTTLS)"
                server.starttls(context=ctx)
                server.ehlo()
                out("3. Encrypted connection: ok")
            if user:
                step = "log in"
                server.login(user, os.environ.get("SMTP_PASSWORD", ""))
                out("4. Login: ok")
    except (OSError, smtplib.SMTPException, ValueError) as exc:
        out(f"Stopped when trying to {step}: {_clean(exc)}")
        return False
    out("Everything worked. The server accepts the connection and the login.")
    return True
