"""Request plumbing shared by the route modules."""

from __future__ import annotations

import random
import secrets
from functools import wraps

from flask import abort, current_app, flash, g, redirect, render_template, request, session, url_for

from .. import accounts, clock, gerry
from ..config import Config
from ..errors import DomainError
from ..models import User
from ..sqlite_repo import SqliteRepository, connect

VISIT_COOKIE = "gl_visit"  # browser-session cookie: this visit has already been counted

MOODS = {
    gerry.Trigger.MARKED_BOUGHT: "smug",
    gerry.Trigger.SULK_AVERTED: "smug",
    gerry.Trigger.APOLOGY: "smug",
    gerry.Trigger.TINY_CHIP_IN: "eyeroll",
    gerry.Trigger.SPLIT_CHEAP: "eyeroll",
    gerry.Trigger.BROWSING: "eyeroll",
    gerry.Trigger.LOWERED_SHARE: "eyeroll",
}


def cfg() -> Config:
    return current_app.config["GIFTLIST"]


def repo() -> SqliteRepository:
    if "repo" not in g:
        g.conn = connect(cfg().db_path)
        g.repo = SqliteRepository(g.conn)
    return g.repo


def close_repo(_exc=None) -> None:
    conn = g.pop("conn", None)
    if conn is not None:
        conn.close()


def rng() -> random.Random:
    return current_app.config.get("GIFTLIST_RNG") or random.SystemRandom()


# --- users ----------------------------------------------------------------------

def current_user() -> User | None:
    if "user" not in g:
        uid = session.get("uid")
        user = repo().user(uid) if uid else None
        if uid and user is None:  # removed by the admin
            session.clear()
        g.user = user
    return g.user


def is_admin(user: User | None) -> bool:
    return user is not None and cfg().is_admin(user.email)


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if current_user() is None:
            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)
    return wrapper


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not is_admin(current_user()):
            abort(404)
        return view(*args, **kwargs)
    return wrapper


def log_in(user: User) -> None:
    session.clear()
    session.permanent = True
    session["uid"] = user.id
    session["csrf"] = secrets.token_urlsafe(24)


# --- CSRF -----------------------------------------------------------------------

def csrf_token() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(24)
    return session["csrf"]


def check_csrf() -> None:
    if request.method != "POST":
        return
    sent = request.form.get("csrf") or request.headers.get("X-CSRF-Token") or ""
    if not sent or not secrets.compare_digest(sent, session.get("csrf", "")):
        abort(400, "That form went stale. Go back, refresh, and try again.")


# --- navigation helpers ---------------------------------------------------------

def next_url(default: str) -> str:
    target = request.form.get("next") or request.args.get("next") or ""
    return target if target.startswith("/") and not target.startswith("//") else default


def fail(message: str, default: str):
    flash(message, "error")
    return redirect(next_url(default))


def domain_action(default_next: str, action):
    """Run an action; on a broken rule, flash its message and go back."""
    try:
        return action()
    except DomainError as e:
        return fail(str(e), default_next)


# --- Gerry ------------------------------------------------------------------------

def start_request() -> None:
    user = current_user()
    g.new_visit = False
    if user is not None and VISIT_COOKIE not in request.cookies and request.method == "GET" \
            and not request.path.startswith(("/static", "/img")):
        g.user = accounts.start_visit(repo(), user)
        g.new_visit = True


def finish_request(resp):
    if g.get("new_visit") and current_user() is not None:
        resp.set_cookie(VISIT_COOKIE, "1", httponly=True, samesite="Lax",
                        secure=cfg().secure_cookies)  # no expiry: ends with the browser session
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["X-Robots-Tag"] = "noindex"
    return resp


def gerry_react(trigger: gerry.Trigger | None, ctx: dict[str, str] | None = None) -> None:
    """Called after an action. Logs it for badges/mood, and queues a popup for the next page, maybe."""
    user = current_user()
    if trigger is not None:
        accounts.record_gerry_event(repo(), user, trigger)
    popup = gerry.decide(trigger, ctx or {}, ghost=user.gerry_ghost, last_line=user.gerry_last_line, rng=rng())
    if popup is not None:
        accounts.remember_gerry_line(repo(), user, popup.template)
        mood = "ghost" if popup.ghost else MOODS.get(trigger, "grumpy")
        session["gerry"] = {"text": popup.text, "ghost": popup.ghost, "mood": mood}


def _page_load_popup() -> dict | None:
    user = current_user()
    if user is None:
        return None
    if user.punishment_mode:
        popup = gerry.punishment_popup(user.gerry_last_line, rng())
        accounts.remember_gerry_line(repo(), user, popup.template)
        return {"text": popup.text, "ghost": user.gerry_ghost, "mood": "ghost" if user.gerry_ghost else "grumpy"}
    queued = session.pop("gerry", None)
    if queued:
        return queued
    kw = dict(ghost=user.gerry_ghost, last_line=user.gerry_last_line, rng=rng())
    popup = None
    if g.new_visit:
        trigger = gerry.visit_trigger(clock.local_today(), user.visits_since_claim)
        popup = gerry.decide(trigger, {}, **kw)
    if popup is None and user.gerry_ghost:
        popup = gerry.decide(gerry.Trigger.GHOST_HAUNT, {}, chance=gerry.GHOST_HAUNT_CHANCE, **kw)
    if popup is None:
        return None
    accounts.remember_gerry_line(repo(), user, popup.template)
    return {"text": popup.text, "ghost": popup.ghost, "mood": "ghost" if popup.ghost else "grumpy"}


def render(template: str, **ctx):
    """render_template plus Gerry, the tab bar state and the current user."""
    popup = _page_load_popup()
    user = current_user()
    notice_count = len(repo().notices_for_user(user.id)) if user else 0
    return render_template(template, gerry=popup, me=user, is_admin=is_admin(user), notice_count=notice_count, **ctx)
