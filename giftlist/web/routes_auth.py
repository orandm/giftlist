"""Sign-in: invite links, Google, and a dev-only shortcut."""

from __future__ import annotations

import secrets

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for

from .. import accounts
from ..accounts import Invite
from ..auth import AuthError, Identity
from ..errors import DomainError, NotAllowed
from .support import cfg, current_user, log_in, repo

bp = Blueprint("auth", __name__)


def _provider():
    return current_app.config["GIFTLIST_PROVIDER"]


def _callback_url() -> str:
    return cfg().base_url + url_for("auth.callback")


@bp.get("/login")
def login():
    if current_user() is not None:
        return redirect(url_for("pages.everyone"))
    invited = bool(session.get("invite_site") or session.get("invite_household"))
    return render_template("signin.html", invited=invited, dev_login=cfg().dev_login, me=None, gerry=None)


@bp.get("/invite/<token>")
def site_invite(token: str):
    if current_user() is not None:
        return redirect(url_for("pages.everyone"))
    session["invite_site"] = token
    return redirect(url_for("auth.login"))


@bp.get("/household/join/<token>")
def household_invite(token: str):
    user = current_user()
    if user is None:
        session["invite_household"] = token
        return redirect(url_for("auth.login"))
    try:
        hh = accounts.join_household(repo(), user, token)
        flash(f"You're in. Welcome to {hh.name}.", "ok")
    except DomainError as e:
        flash(str(e), "error")
    return redirect(url_for("pages.household"))


@bp.get("/auth/google")
def google():
    state = secrets.token_urlsafe(24)
    session["oauth_state"] = state
    return redirect(_provider().authorize_url(_callback_url(), state))


def _finish(identity: Identity):
    invite = Invite(site_token=session.get("invite_site"), household_token=session.get("invite_household"))
    try:
        user = accounts.sign_in(repo(), identity.sub, identity.email, identity.name, invite,
                                cfg().is_admin(identity.email))
    except NotAllowed as e:
        return render_template("message.html", title="Who invited you?", message=str(e), me=None, gerry=None), 403
    is_new = user.visit_count == 0
    log_in(user)
    return redirect(url_for("pages.welcome" if is_new else "pages.everyone"))


@bp.get("/auth/callback")
def callback():
    state = session.pop("oauth_state", None)
    if not state or not secrets.compare_digest(state, request.args.get("state", "")):
        return redirect(url_for("auth.login"))
    if "error" in request.args or "code" not in request.args:
        flash("Sign-in was cancelled. Chicken.", "error")
        return redirect(url_for("auth.login"))
    try:
        identity = _provider().identity(request.args["code"], _callback_url())
    except AuthError as e:
        flash(str(e), "error")
        return redirect(url_for("auth.login"))
    return _finish(identity)


@bp.route("/dev/login", methods=["GET", "POST"])
def dev_login():
    """Local testing only: pretend to be anyone. Disabled unless DEV_LOGIN=1."""
    if not cfg().dev_login:
        abort(404)
    if request.method == "GET":
        return render_template("dev_login.html", me=None, gerry=None)
    email = request.form.get("email", "").strip().lower()
    name = request.form.get("name", "").strip() or email.split("@")[0]
    if not email:
        abort(400)
    return _finish(Identity(sub="dev:" + email, email=email, name=name))


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
