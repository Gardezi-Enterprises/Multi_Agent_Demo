"""Email tools — owned by the Communication Agent.

Delivery is handled by a pluggable transport (see email_providers): SMTP, or a
transactional HTTP API such as Resend/SendGrid/Brevo, selected with
EMAIL_PROVIDER. With EMAIL_ENABLED=false everything runs in dry-run mode — the
message is fully validated and appended to output/sent_emails.log, but nothing
leaves the machine — so the system stays demonstrable with no credentials.

Recipients are resolved by the orchestrator before this tool is called: the
Communication Agent deliberately has no database access, so a broadcast is
"User Management Agent lists the users, then this tool sends to them".
"""

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from ..config import EMAIL_ENABLED, EMAIL_FROM, EMAIL_PROVIDER, OUTPUT_DIR
from ..email_providers import deliver

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")
EMAIL_LOG = OUTPUT_DIR / "sent_emails.log"

# Guardrail: a broadcast this large is almost certainly a mistake or an abuse of
# the tool, so it is refused rather than attempted.
MAX_RECIPIENTS = 200


def _log_email(
    to: str,
    subject: str,
    body: str,
    mode: str,
    cc: Optional[str] = None,
    bcc: Optional[str] = None,
    reply_to: Optional[str] = None,
    attachments: Optional[List[str]] = None,
) -> None:
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    headers = [f"From: {EMAIL_FROM or '(unset)'}", f"To: {to}"]
    for label, value in (("Cc", cc), ("Bcc", bcc), ("Reply-To", reply_to)):
        if value:
            headers.append(f"{label}: {value}")
    if attachments:
        headers.append("Attachments: " + ", ".join(attachments))
    headers.append(f"Subject: {subject}")
    entry = (
        f"\n{'=' * 70}\n[{stamp}] mode={mode}\n"
        + "\n".join(headers)
        + f"\n{'-' * 70}\n{body}\n"
    )
    with open(EMAIL_LOG, "a", encoding="utf-8") as fh:
        fh.write(entry)


def send_email(
    recipient: str,
    subject: str,
    body: str,
    cc: Optional[str] = None,
    bcc: Optional[str] = None,
    signature: Optional[str] = None,
    reply_to: Optional[str] = None,
    attachments: Optional[List[str]] = None,
    send_individually: bool = False,
) -> dict:
    """Send an email message to one or more recipients.

    Args:
        recipient: Destination email address. For several recipients, pass them
            comma-separated, e.g. "a@x.com, b@y.com".
        subject: Subject line of the email.
        body: Plain-text body of the email.
        cc: Optional comma-separated list of addresses to copy.
        bcc: Optional comma-separated list of addresses to blind-copy.
        signature: Optional sign-off appended to the body. Only pass a value
            that was actually supplied; never invent a sender identity.
        reply_to: Optional Reply-To address.
        attachments: Optional list of file paths to attach.
        send_individually: When True and there are several recipients, send a
            separate copy to each so no recipient can see the others. Use this
            for any broadcast to a list of users. When False, one message is
            sent with every address visible in the To field.

    Returns:
        A dict with status "success" and the delivery mode ("sent" for real
        delivery, "dry_run" when sending is disabled), the provider used, and
        per-recipient results for an individual broadcast; or status "error"
        with a message.
    """
    recipients = [r.strip() for r in (recipient or "").split(",") if r.strip()]
    if not recipients:
        return {"status": "error", "message": "recipient is required."}

    # Preserve order but drop duplicates, so a broadcast never double-sends.
    seen, unique = set(), []
    for address in recipients:
        key = address.lower()
        if key not in seen:
            seen.add(key)
            unique.append(address)
    recipients = unique

    invalid = [r for r in recipients if not EMAIL_RE.match(r)]
    if invalid:
        return {
            "status": "error",
            "message": f"Not a valid email address: {', '.join(invalid)}.",
        }
    if len(recipients) > MAX_RECIPIENTS:
        return {
            "status": "error",
            "message": (
                f"Refusing to send to {len(recipients)} recipients; the limit is "
                f"{MAX_RECIPIENTS}. Narrow the recipient list."
            ),
        }
    if not subject or not subject.strip():
        return {"status": "error", "message": "subject is required."}
    if not body or not body.strip():
        return {"status": "error", "message": "body is required."}

    body = body.rstrip()
    if signature and signature.strip():
        body = f"{body}\n\n{signature.strip()}"

    # Resolve attachments up front so a missing file is reported before any
    # delivery attempt, in both dry-run and live mode.
    files: List[Path] = []
    for raw in attachments or []:
        if not raw or not str(raw).strip():
            continue
        candidate = Path(str(raw).strip())
        if not candidate.exists():
            candidate = OUTPUT_DIR / candidate.name
        if not candidate.exists():
            return {"status": "error", "message": f"Attachment not found: {raw}"}
        files.append(candidate)
    attached_names = [f.name for f in files]

    # One message per recipient, or a single message addressed to all of them.
    batches = [[r] for r in recipients] if send_individually else [recipients]

    if not EMAIL_ENABLED:
        for batch in batches:
            _log_email(", ".join(batch), subject, body, "dry_run",
                       cc, bcc, reply_to, attached_names)
        return {
            "status": "success",
            "mode": "dry_run",
            "provider": "dry_run",
            "recipients": recipients,
            "recipient_count": len(recipients),
            "sent_individually": send_individually,
            "subject": subject,
            "attachments": attached_names,
            "message": (
                f"{len(batches)} email(s) to {len(recipients)} recipient(s) were "
                "composed and logged, but NOT delivered because email sending is "
                f"disabled (EMAIL_ENABLED=false). Copies are in {EMAIL_LOG.name}."
            ),
        }

    results, failures = [], []
    for batch in batches:
        ok, detail = deliver(batch, subject, body, files, cc, bcc, reply_to)
        target = ", ".join(batch)
        results.append({"recipient": target, "delivered": ok, "detail": detail})
        if ok:
            _log_email(target, subject, body, "sent", cc, bcc, reply_to, attached_names)
        else:
            failures.append(f"{target}: {detail}")

    delivered = [r for r in results if r["delivered"]]

    if not delivered:
        return {
            "status": "error",
            "provider": EMAIL_PROVIDER,
            "results": results,
            "message": "Delivery failed for every recipient. " + " | ".join(failures),
        }

    payload = {
        "status": "success",
        "mode": "sent",
        "provider": EMAIL_PROVIDER,
        "recipients": recipients,
        "recipient_count": len(recipients),
        "delivered_count": len(delivered),
        "sent_individually": send_individually,
        "subject": subject,
        "attachments": attached_names,
        "results": results,
    }
    if failures:
        # Partial success must be reported as such, never as a clean send.
        payload["status"] = "partial"
        payload["failed_count"] = len(failures)
        payload["message"] = (
            f"Delivered to {len(delivered)} of {len(results)} target(s) via "
            f"{EMAIL_PROVIDER}. Failures: " + " | ".join(failures)
        )
    else:
        payload["message"] = (
            f"Delivered to {len(recipients)} recipient(s) via {EMAIL_PROVIDER}"
            + (f" with {len(attached_names)} attachment(s)." if attached_names else ".")
        )
    return payload
