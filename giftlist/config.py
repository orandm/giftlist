"""Settings from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _flag(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


@dataclass(slots=True, frozen=True)
class Config:
    secret_key: str
    base_url: str                  # e.g. https://gifts.example.ie (used for the OAuth callback)
    google_client_id: str
    google_client_secret: str
    admin_emails: frozenset[str]
    data_dir: str
    currency: str = "EUR"
    dev_login: bool = False        # local testing only: sign in without Google
    secure_cookies: bool = True
    smtp_host: str = ""            # empty = not configured; magic-link email falls back to on-screen display
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    mail_from: str = ""

    @property
    def db_path(self) -> str:
        return os.path.join(self.data_dir, "giftlist.db")

    @property
    def images_dir(self) -> str:
        return os.path.join(self.data_dir, "images")

    def is_admin(self, email: str) -> bool:
        return email.strip().lower() in self.admin_emails

    @classmethod
    def from_env(cls) -> "Config":
        secret = os.environ.get("SECRET_KEY", "")
        if len(secret) < 32:
            raise RuntimeError("SECRET_KEY must be set to at least 32 random characters.")
        dev = _flag("DEV_LOGIN")
        base_url = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
        if dev and base_url.startswith("https://"):
            raise RuntimeError("DEV_LOGIN lets anyone sign in as anyone. Never turn it on for the real site.")
        return cls(
            secret_key=secret,
            base_url=base_url,
            google_client_id=os.environ.get("GOOGLE_CLIENT_ID", ""),
            google_client_secret=os.environ.get("GOOGLE_CLIENT_SECRET", ""),
            admin_emails=frozenset(e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()),
            data_dir=os.environ.get("DATA_DIR", "./data"),
            currency=os.environ.get("CURRENCY", "EUR"),
            dev_login=dev,
            secure_cookies=_flag("SECURE_COOKIES", not dev),
            smtp_host=os.environ.get("SMTP_HOST", ""),
            smtp_port=int(os.environ.get("SMTP_PORT", "587")),
            smtp_user=os.environ.get("SMTP_USER", ""),
            smtp_password=os.environ.get("SMTP_PASSWORD", ""),
            mail_from=os.environ.get("MAIL_FROM", ""),
        )
