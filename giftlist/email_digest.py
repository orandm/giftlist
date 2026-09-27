"""Gerry's daily activity digest. Run hourly: python -m giftlist.email_digest

Each subscriber gets one email a day, at 7pm in their own timezone, covering
the last 24 hours of activity they're allowed to see (same rules as the
Activity page). Run this hourly rather than once a day at a fixed UTC time,
since subscribers can be in different timezones; the per-user "already sent
today" guard keeps it to one email even though the hour-19 window is checked
on every run.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from flask import render_template

from . import activity, clock, gerry, mail
from .config import Config
from .models import Activity, ActivityKind, User
from .money import format_amount
from .repository import Repository
from .sqlite_repo import SqliteRepository, connect, init_db

DIGEST_HOUR = 19  # 7pm


def _line(a: Activity, currency: str) -> str:
    p = a.params
    if a.kind is ActivityKind.HOUSEHOLD_CREATED:
        return f"{p['household_name']} joined the site."
    if a.kind is ActivityKind.ITEM_ADDED:
        return f"{p['person_name']} added “{p['item']}” to their list."
    if a.kind is ActivityKind.ITEM_EDITED:
        return f"{p['person_name']} updated “{p['item']}” on their list."
    if a.kind is ActivityKind.ITEM_REMOVED:
        return f"{p['person_name']} took “{p['item']}” off their list."
    if a.kind is ActivityKind.ITEM_CLAIMED:
        amount = format_amount(p['amount_minor'], currency)
        return f"{p['claimer_name']} put {amount} towards “{p['item']}” for {p['owner_name']}."
    if a.kind is ActivityKind.ITEM_BOUGHT:
        return f"“{p['item']}” for {p['owner_name']} was marked bought."
    if a.kind is ActivityKind.ITEM_WITHDRAWN:
        return f"{p['claimer_name']} backed out of “{p['item']}” for {p['owner_name']}."
    return ""


def build_email(items: list[Activity], currency: str, base_url: str, rng: random.Random) -> tuple[str, str, str]:
    """Returns (subject, html_body, text_body). Assumes an active Flask app context
    (for render_template) -- deliberately avoids url_for, which needs a request
    context this standalone cron process never has."""
    lines = [l for l in (_line(a, currency) for a in items) if l]
    intro = rng.choice(gerry.DIGEST_INTROS)
    outro = rng.choice(gerry.DIGEST_OUTROS)
    sprite = gerry.digest_sprite(rng)
    subject = "Gerry's daily gossip" if lines else "Gerry has nothing to say"
    sprite_url = f"{base_url}/static/gerry-{sprite}.svg"
    manage_url = f"{base_url}/activity"
    html = render_template("email/digest.html", intro=intro, outro=outro, lines=lines,
                          sprite_url=sprite_url, manage_url=manage_url)
    text = intro + "\n\n" + "\n".join(f"- {l}" for l in lines) + "\n\n" + outro
    return subject, html, text


def _local_date(now_utc: datetime, tzname: str | None) -> tuple[datetime, str]:
    try:
        tz = ZoneInfo(tzname) if tzname else clock.LOCAL
    except Exception:
        tz = clock.LOCAL
    local = now_utc.astimezone(tz)
    return local, local.date().isoformat()


def run(repo: Repository, cfg: Config, now_utc: datetime | None = None) -> int:
    """Sends today's due digests. Returns how many were sent."""
    now_utc = now_utc or datetime.now(UTC)
    rng = random.Random()
    since = now_utc - timedelta(hours=24)
    sent = 0
    for user in repo.subscribed_users():
        local, today = _local_date(now_utc, user.timezone)
        if local.hour != DIGEST_HOUR or user.last_digest_sent_date == today:
            continue
        items = activity.feed_since(repo, user, since)
        if items:
            subject, html, text = build_email(items, cfg.currency, cfg.base_url, rng)
            try:
                mail.send_digest(cfg, to=user.email, subject=subject, text_body=text, html_body=html)
            except Exception as e:
                print(f"digest send failed for {user.email}: {e}")
                continue  # don't mark as sent -- retry next hour
            sent += 1
        with repo.write():
            repo.set_last_digest_sent(user.id, today)
    return sent


def send_test(repo: Repository, cfg: Config, user: User) -> bool:
    """Admin action: send one subscriber a real digest right now, ignoring the
    hour/date guard. Returns True if there was real activity to report."""
    since = datetime.now(UTC) - timedelta(hours=24)
    items = activity.feed_since(repo, user, since)
    subject, html, text = build_email(items, cfg.currency, cfg.base_url, random.Random())
    mail.send_digest(cfg, to=user.email, subject=f"[test] {subject}", text_body=text, html_body=html)
    return bool(items)


if __name__ == "__main__":
    from .web import create_app

    config = Config.from_env()
    app = create_app(config)
    with app.app_context():
        conn = connect(config.db_path)
        init_db(conn)
        n = run(SqliteRepository(conn), config)
        conn.close()
        print(f"sent {n} digest(s)")
