"""Admin: who's signed up, invite link, removing users, new season."""

from __future__ import annotations

import os

from flask import Blueprint, flash, redirect, request, url_for

from .. import accounts
from ..errors import DomainError
from .support import admin_required, cfg, current_user, login_required, render, repo

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.get("")
@login_required
@admin_required
def overview():
    users, households = accounts.admin_overview(repo())
    invite_url = cfg().base_url + url_for("auth.site_invite", token=accounts.site_invite_token(repo()))
    return render("admin.html", users=users, households=households, invite_url=invite_url, tab=None)


@bp.post("/invite/reset")
@login_required
@admin_required
def reset_invite():
    accounts.reset_site_invite(repo())
    flash("New invite link made. The old one's dead. Share the new one.", "ok")
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
