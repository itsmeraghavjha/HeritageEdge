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


def _clean(text):
    pw = os.environ.get("SMTP_PASSWORD", "")
    return str(text).replace(pw, "***") if pw else str(text)


def send(to, subject, body):
    """Returns (True, "") when sent, or (False, reason) when not. Never raises."""
    if not configured():
        return False, "The email could not be sent: email is not set up (SMTP_HOST and SMTP_FROM in .env)."
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = os.environ["SMTP_FROM"], to, subject
    msg.set_content(body)
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
