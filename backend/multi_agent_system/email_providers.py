"""Pluggable email transports.

`send_email` does not know how mail leaves the machine. The transport is chosen
by EMAIL_PROVIDER, so the same tool works over SMTP or over a transactional HTTP
API without touching the agent layer:

    dry_run    validate and log only, never deliver (default, needs nothing)
    smtp       SMTP/SMTPS — fine for low volume, capped ~500/day on Gmail
    resend     Resend HTTP API      (RESEND_API_KEY)
    sendgrid   SendGrid v3 HTTP API (SENDGRID_API_KEY)
    brevo      Brevo v3 HTTP API    (BREVO_API_KEY)

The HTTP providers are the right choice for broadcasting to many recipients:
higher limits, proper bounce handling and deliverability reporting. All of them
send to any address — none is restricted to a contact list — though each
requires a verified sender address or domain before it will accept mail.

Every provider takes the same arguments and returns the same (ok, detail) pair,
so adding another is one function plus a registry entry.
"""

import base64
import json
import mimetypes
import smtplib
import urllib.error
import urllib.request
from email.message import EmailMessage
from pathlib import Path
from typing import List, Optional, Tuple

from .config import (
    BREVO_API_KEY,
    EMAIL_FROM,
    EMAIL_PROVIDER,
    RESEND_API_KEY,
    SENDGRID_API_KEY,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_USE_SSL,
    SMTP_USER,
)

TIMEOUT = 30


def _split(value: Optional[str]) -> List[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def _post_json(url: str, payload: dict, headers: dict) -> Tuple[bool, str]:
    """POST JSON and normalise the outcome into (ok, detail).

    A real User-Agent is sent because provider APIs sit behind Cloudflare, which
    blocks urllib's default "Python-urllib/x.y" signature with a 403 (error 1010).
    """
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "TaseersAgent/1.0 (+https://github.com/Gardezi-Enterprises/Multi_Agent_Demo)",
            **headers,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return True, f"HTTP {response.status}"
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        return False, f"HTTP {exc.code}: {detail}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _encoded_attachments(files: List[Path]) -> List[dict]:
    out = []
    for file in files:
        out.append({
            "filename": file.name,
            "content": base64.b64encode(file.read_bytes()).decode("ascii"),
            "type": mimetypes.guess_type(str(file))[0] or "application/octet-stream",
        })
    return out


# --- transports --------------------------------------------------------------


def send_via_smtp(
    recipients: List[str], subject: str, body: str, files: List[Path],
    cc: Optional[str], bcc: Optional[str], reply_to: Optional[str],
) -> Tuple[bool, str]:
    if not (SMTP_HOST and SMTP_USER and SMTP_PASSWORD):
        return False, "SMTP_HOST/SMTP_USER/SMTP_PASSWORD are not all set."

    message = EmailMessage()
    message["From"] = EMAIL_FROM
    message["To"] = ", ".join(recipients)
    if cc:
        message["Cc"] = cc
    if bcc:
        message["Bcc"] = bcc
    if reply_to:
        message["Reply-To"] = reply_to
    message["Subject"] = subject
    message.set_content(body)

    for file in files:
        ctype, encoding = mimetypes.guess_type(str(file))
        if ctype is None or encoding is not None:
            ctype = "application/octet-stream"
        maintype, _, subtype = ctype.partition("/")
        message.add_attachment(
            file.read_bytes(), maintype=maintype, subtype=subtype, filename=file.name
        )

    try:
        # Port 465 is TLS from the first byte; 587/25 connect plain and upgrade.
        opener = smtplib.SMTP_SSL if SMTP_USE_SSL else smtplib.SMTP
        with opener(SMTP_HOST, SMTP_PORT, timeout=TIMEOUT) as server:
            if not SMTP_USE_SSL:
                server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(message)
    except Exception as exc:
        return False, f"SMTP delivery failed: {type(exc).__name__}: {exc}"
    return True, f"SMTP {SMTP_HOST}:{SMTP_PORT}"


def send_via_resend(
    recipients: List[str], subject: str, body: str, files: List[Path],
    cc: Optional[str], bcc: Optional[str], reply_to: Optional[str],
) -> Tuple[bool, str]:
    if not RESEND_API_KEY:
        return False, "RESEND_API_KEY is not set."
    payload = {"from": EMAIL_FROM, "to": recipients, "subject": subject, "text": body}
    if cc:
        payload["cc"] = _split(cc)
    if bcc:
        payload["bcc"] = _split(bcc)
    if reply_to:
        payload["reply_to"] = reply_to
    if files:
        payload["attachments"] = [
            {"filename": a["filename"], "content": a["content"]}
            for a in _encoded_attachments(files)
        ]
    return _post_json(
        "https://api.resend.com/emails", payload,
        {"Authorization": f"Bearer {RESEND_API_KEY}"},
    )


def send_via_sendgrid(
    recipients: List[str], subject: str, body: str, files: List[Path],
    cc: Optional[str], bcc: Optional[str], reply_to: Optional[str],
) -> Tuple[bool, str]:
    if not SENDGRID_API_KEY:
        return False, "SENDGRID_API_KEY is not set."
    personalization = {"to": [{"email": r} for r in recipients]}
    if cc:
        personalization["cc"] = [{"email": e} for e in _split(cc)]
    if bcc:
        personalization["bcc"] = [{"email": e} for e in _split(bcc)]
    payload = {
        "personalizations": [personalization],
        "from": {"email": EMAIL_FROM},
        "subject": subject,
        "content": [{"type": "text/plain", "value": body}],
    }
    if reply_to:
        payload["reply_to"] = {"email": reply_to}
    if files:
        payload["attachments"] = [
            {"filename": a["filename"], "content": a["content"], "type": a["type"],
             "disposition": "attachment"}
            for a in _encoded_attachments(files)
        ]
    return _post_json(
        "https://api.sendgrid.com/v3/mail/send", payload,
        {"Authorization": f"Bearer {SENDGRID_API_KEY}"},
    )


def send_via_brevo(
    recipients: List[str], subject: str, body: str, files: List[Path],
    cc: Optional[str], bcc: Optional[str], reply_to: Optional[str],
) -> Tuple[bool, str]:
    if not BREVO_API_KEY:
        return False, "BREVO_API_KEY is not set."
    payload = {
        "sender": {"email": EMAIL_FROM},
        "to": [{"email": r} for r in recipients],
        "subject": subject,
        "textContent": body,
    }
    if cc:
        payload["cc"] = [{"email": e} for e in _split(cc)]
    if bcc:
        payload["bcc"] = [{"email": e} for e in _split(bcc)]
    if reply_to:
        payload["replyTo"] = {"email": reply_to}
    if files:
        payload["attachment"] = [
            {"name": a["filename"], "content": a["content"]}
            for a in _encoded_attachments(files)
        ]
    return _post_json(
        "https://api.brevo.com/v3/smtp/email", payload, {"api-key": BREVO_API_KEY}
    )


PROVIDERS = {
    "smtp": send_via_smtp,
    "resend": send_via_resend,
    "sendgrid": send_via_sendgrid,
    "brevo": send_via_brevo,
}


def deliver(
    recipients: List[str], subject: str, body: str, files: List[Path],
    cc: Optional[str] = None, bcc: Optional[str] = None, reply_to: Optional[str] = None,
) -> Tuple[bool, str]:
    """Deliver through the configured provider. Returns (ok, detail)."""
    provider = PROVIDERS.get(EMAIL_PROVIDER)
    if provider is None:
        return False, (
            f"Unknown EMAIL_PROVIDER '{EMAIL_PROVIDER}'. "
            f"Valid values: dry_run, {', '.join(PROVIDERS)}."
        )
    return provider(recipients, subject, body, files, cc, bcc, reply_to)
