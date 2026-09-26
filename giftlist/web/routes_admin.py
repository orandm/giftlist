"""Admin: who's signed up, invite link, removing users, new season."""

from __future__ import annotations

import os

from flask import Blueprint, flash, redirect, request, url_for

from .. import accounts, mail
from ..errors import DomainError
from .support import admin_required, cfg, current_user, login_required, render, repo

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.get("")
@login_required
@admin_required
def overview():
    users, households = accounts.admin_overview(repo())
    token = accounts.site_invite_token(repo())
    invite_url = cfg().base_url + url_for("auth.site_invite", token=token)
    magic_join_url = cfg().base_url + url_for("auth.join_magic", token=token)
    return render("admin.html", users=users, households=households,
                  invite_url=invite_url, magic_join_url=magic_join_url, tab=None)


@bp.post("/invite/reset")
@login_required
@admin_required
def reset_invite():
    accounts.reset_site_invite(repo())
    flash("New invite link made. The old one's dead. Share the new one.", "ok")
    return redirect(url_for("admin.overview"))


@bp.post("/households/<int:household_id>/rename")
@login_required
@admin_required
def rename_household(household_id: int):
    try:
        accounts.admin_rename_household(repo(), household_id, request.form.get("name", ""))
    except DomainError as e:
        flash(str(e), "error")
    return redirect(url_for("admin.overview"))


@bp.post("/users/<int:user_id>/remove")
@login_required
@admin_required
def remove_user(user_id: int):
    if user_id == current_user().id:
        flash("You can't remove yourself. Nice try.", "error")
        return redirect(url_for("admin.overview"))
    try:
        accounts.remove_user(repo(), user_id)
        flash("Gone. Their list and claims went with them.", "ok")
    except DomainError as e:
        flash(str(e), "error")
    return redirect(url_for("admin.overview"))


@bp.post("/users/<int:user_id>/punishment")
@login_required
@admin_required
def toggle_punishment(user_id: int):
    on = request.form.get("enabled") == "1"
    accounts.set_punishment_mode(repo(), user_id, on)
    flash("Punishment mode on. Gerry says thanks." if on else "Punishment mode off. Mercy.", "ok")
    return redirect(url_for("admin.overview"))


@bp.post("/users/<int:user_id>/magic/regenerate")
@login_required
@admin_required
def regenerate_magic(user_id: int):
    user = repo().user(user_id)
    if user is None:
        flash("That user's already gone.", "error")
        return redirect(url_for("admin.overview"))
    token = accounts.regenerate_magic_link(repo(), user_id)
    link = cfg().base_url + url_for("auth.magic_login", token=token)
    try:
        mail.send_magic_link(cfg(), to=user.email, name=user.name, link=link)
        flash(f"New link emailed to {user.email}. The old one's dead.", "ok")
    except Exception:
        flash(f"Couldn't email it -- here's the new link to send yourself: {link}", "ok")
    return redirect(url_for("admin.overview"))


@bp.post("/badges/clear")
@login_required
@admin_required
def clear_badges():
    accounts.clear_badges(repo())
    flash("Badge leaderboard wiped. Fresh start.", "ok")
    return redirect(url_for("admin.overview"))


@bp.post("/season")
@login_required
@admin_required
def new_season():
    if request.form.get("confirm", "").strip().upper() != "CLEAR":
        flash("Type CLEAR to confirm. This wipes every list.", "error")
        return redirect(url_for("admin.overview"))
    accounts.new_season(repo())
    images = cfg().images_dir
    if os.path.isdir(images):
        for name in os.listdir(images):
            os.remove(os.path.join(images, name))
    flash("All lists cleared. Happy new season.", "ok")
    return redirect(url_for("admin.overview"))
