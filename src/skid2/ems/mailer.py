"""Authorization and password emails for invited EMS users."""

from __future__ import annotations

import logging
import smtplib
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from html import escape
from pathlib import Path

from skid2.settings import Settings

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MailResult:
    """Outcome of trying to deliver one authorization email."""

    sent: bool
    stored_path: str = ""
    detail: str = ""


def invite_url(settings: Settings, token: str) -> str:
    base = (settings.public_app_url or settings.browser_origin).rstrip("/")
    return f"{base}/login?invite={token}"


def send_authorization_email(
    settings: Settings,
    *,
    to_address: str,
    display_name: str,
    token: str,
    purpose: str = "invite",
) -> MailResult:
    """Email a login link to confirm access or reset a password."""

    link = invite_url(settings, token)
    name = display_name or to_address
    reset = purpose == "password_change"
    subject = (
        "Reset your Carbon DC Skid 2 password"
        if reset
        else "You are authorized for Carbon DC Skid 2 EMS"
    )
    intro = (
        "We received a request to reset your Skid 2 EMS password."
        if reset
        else "An administrator has authorized your Skid 2 EMS user login."
    )
    action = (
        "Open this login link to set a new password and sign in with User login:"
        if reset
        else "Open this login link to confirm access, set or change your password, and sign in with User login:"
    )
    ignore = (
        "If you did not request this, ignore the message."
        if reset
        else "If you did not expect this, ignore the message."
    )
    body = (
        f"Hello {name},\n\n"
        f"{intro}\n\n"
        f"{action}\n"
        f"{link}\n\n"
        f"The link expires in 48 hours. {ignore}\n\n"
        "Carbon DC\n"
    )
    safe_name = escape(name)
    safe_link = escape(link, quote=True)
    html_action = (
        "That link opens the login page so you can set a new password and sign in with User login."
        if reset
        else "That link opens the login page so you can set or change your password and sign in with User login."
    )
    html = (
        f"<p>Hello {safe_name},</p>"
        f"<p>{escape(intro)}</p>"
        f'<p><a href="{safe_link}">Open Skid 2 login</a></p>'
        f"<p>{html_action}</p>"
        f"<p>The link expires in 48 hours. {escape(ignore)}</p>"
        "<p>Carbon DC</p>"
    )
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.smtp_from
    message["To"] = to_address
    message.set_content(body)
    message.add_alternative(html, subtype="html")
    stored = _store_mail(settings, message)
    if not settings.smtp_host:
        return MailResult(
            sent=False,
            stored_path=stored,
            detail="Invitation saved locally until outbound email is configured.",
        )
    try:
        _deliver(settings, message)
    except (OSError, smtplib.SMTPException) as error:
        LOGGER.warning("authorization email was not delivered to %s", to_address)
        return MailResult(
            sent=False,
            stored_path=stored,
            detail=f"Could not send email ({error}). The invitation link is still valid.",
        )
    return MailResult(sent=True, stored_path=stored, detail=f"Invitation sent to {to_address}.")


def _store_mail(settings: Settings, message: EmailMessage) -> str:
    folder = Path(settings.mail_dir)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    safe_to = "".join(character if character.isalnum() else "-" for character in message["To"])
    path = folder / f"{stamp}-{safe_to[:80]}.eml"
    path.write_bytes(message.as_bytes())
    return str(path)


def _deliver(settings: Settings, message: EmailMessage) -> None:
    username = settings.smtp_username
    password = settings.smtp_password.get_secret_value()
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
        if settings.smtp_starttls:
            smtp.starttls()
        if username:
            smtp.login(username, password)
        smtp.send_message(message)
