"""Sending the odd email. Stdlib only -- just the magic sign-in link so far."""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

from .config import Config


class MailNotConfigured(Exception):
    """cfg.smtp_host is empty. Caller falls back to showing the link on screen."""


def _send(cfg: Config, msg: EmailMessage) -> None:
    """Raises MailNotConfigured if no SMTP host is set. Any other failure
    (bad creds, network) raises from smtplib/socket -- callers must catch
    broadly and fall back gracefully, never strand the underlying action."""
    if not cfg.smtp_host:
        raise MailNotConfigured
    with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=10) as smtp:
        smtp.starttls()
        if cfg.smtp_user:
            smtp.login(cfg.smtp_user, cfg.smtp_password)
        smtp.send_message(msg)


def send_magic_link(cfg: Config, *, to: str, name: str, link: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = "Your sign-in link for The Family Christmas List"
    msg["From"] = cfg.mail_from or cfg.smtp_user
    msg["To"] = to
    msg.set_content(
        f"Hi {name},\n\n"
        f"Here's your personal sign-in link. Bookmark it -- it's how you get back in:\n\n"
        f"{link}\n\n"
        f"Don't share it; anyone with the link can sign in as you."
    )
    _send(cfg, msg)


def send_digest(cfg: Config, *, to: str, subject: str, text_body: str, html_body: str) -> None:
    """Gerry's daily activity digest: plain text, with an HTML alternative for the sprite."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.mail_from or cfg.smtp_user
    msg["To"] = to
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")
    _send(cfg, msg)
