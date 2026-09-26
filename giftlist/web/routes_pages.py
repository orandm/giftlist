"""The app's pages and actions."""

from __future__ import annotations

from flask import Blueprint, abort, flash, g, jsonify, redirect, request, send_from_directory, url_for

from .. import access, accounts, badges, claims, clock, gerry, linkpreview, lists
from ..errors import DomainError
from ..money import InvalidAmount, parse_amount
from .support import cfg, current_user, domain_action, fail, gerry_react, login_required, next_url, render, repo

bp = Blueprint("pages", __name__)


def _amount(field: str = "amount") -> int:
    return parse_amount(request.form.get(field, ""))


def _everyone_anchor(item_id: int) -> str:
    return url_for("pages.everyone") + f"#item-{item_id}"


# --- Everyone ----------------------------------------------------------------------

@bp.get("/")
@login_required
def everyone():
    return render("everyone.html", households=lists.everyone(repo(), current_user()),
                  badges=badges.leaderboard(repo()), tab="everyone")


@bp.get("/welcome")
@login_required
def welcome():
    me = access.own_person(repo(), current_user())
    return render("welcome.html", person=me, tab=None)


# --- claiming ------------------------------------------------------------------------

@bp.get("/items/<int:item_id>/buy")
@login_required
def buy(item_id: int):
    try:
        view, owner = lists.claimable_item(repo(), current_user(), item_id)
    except DomainError as e:
        return fail(str(e), url_for("pages.everyone"))
    mine = view.contribution_of(current_user().id)
    half = (view.remaining_minor + 1) // 2 // 50 * 50 or view.remaining_minor  # round to 50c
    return render("buy.html", view=view, owner=owner, mine=mine, half=half,
                  back=next_url(_everyone_anchor(item_id)), tab="everyone")


def _react_to_claim(outcome) -> None:
    trig = gerry.claim_trigger(outcome.item.price_minor, outcome.amount_minor, outcome.remaining_before_minor,
                               outcome.claimed_after_minor, outcome.item.really_want, clock.local_today())
    gerry_react(trig, gerry.context(cfg().currency, owner=outcome.owner.name, item=outcome.item.title,
                                    amount_minor=outcome.amount_minor, price_minor=outcome.item.price_minor))


@bp.post("/items/<int:item_id>/claim")
@login_required
def claim(item_id: int):
    def act():
        choice = request.form.get("quick") or request.form.get("amount", "")
        if choice == "rest":
            view, _ = lists.claimable_item(repo(), current_user(), item_id)
            amount = view.remaining_minor
        else:
            amount = parse_amount(choice)
        outcome = claims.claim(repo(), current_user(), item_id, amount)
        _react_to_claim(outcome)
        return redirect(next_url(_everyone_anchor(item_id)))
    return _guard(act, _everyone_anchor(item_id))


@bp.post("/items/<int:item_id>/share")
@login_required
def change_share(item_id: int):
    def act():
        outcome = claims.update_claim(repo(), current_user(), item_id, _amount())
        gerry_react(gerry.share_change_trigger(outcome.previous_minor, outcome.amount_minor),
                    gerry.context(cfg().currency, owner=outcome.owner.name, item=outcome.item.title))
        return redirect(next_url(_everyone_anchor(item_id)))
    return _guard(act, _everyone_anchor(item_id))


@bp.post("/items/<int:item_id>/withdraw")
@login_required
def withdraw(item_id: int):
    def act():
        outcome = claims.withdraw(repo(), current_user(), item_id)
        gerry_react(gerry.Trigger.BACKING_OUT, gerry.context(cfg().currency, owner=outcome.owner.name))
        return redirect(next_url(_everyone_anchor(item_id)))
    return _guard(act, _everyone_anchor(item_id))


@bp.post("/items/<int:item_id>/bought")
@login_required
def bought(item_id: int):
    def act():
        claims.mark_bought(repo(), current_user(), item_id)
        gerry_react(gerry.Trigger.MARKED_BOUGHT)
        return redirect(next_url(url_for("pages.my_buys")))
    return _guard(act, url_for("pages.my_buys"))


@bp.post("/items/<int:item_id>/unbought")
@login_required
def unbought(item_id: int):
    def act():
        claims.unmark_bought(repo(), current_user(), item_id)
        return redirect(next_url(url_for("pages.my_buys")))
    return _guard(act, url_for("pages.my_buys"))


def _guard(act, default_next: str):
    try:
        return act()
    except (DomainError, InvalidAmount) as e:
        return fail(str(e), default_next)


# --- My list -----------------------------------------------------------------------

@bp.get("/my-list")
@login_required
def my_list():
    user = current_user()
    people = lists.editable_people(repo(), user)
    wanted = request.args.get("person", type=int)
    person = next((p for p in people if p.id == wanted), people[0])
    items = lists.items_for_editing(repo(), user, person.id)
    return render("my_list.html", people=people, person=person, items=items, tab="list")


def _list_url(person_id: int) -> str:
    return url_for("pages.my_list", person=person_id)


def _item_fields():
    f = request.form
    image = linkpreview.valid_image_name(f.get("image"))
    if f.get("remove_image"):
        image = None
    return dict(title=f.get("title", ""), url=f.get("url"), image=image, price_minor=_amount("price"),
                note=f.get("note"), really_want=bool(f.get("really_want")))


@bp.route("/my-list/<int:person_id>/add", methods=["GET", "POST"])
@login_required
def add_item(person_id: int):
    user = current_user()
    try:
        person = access.require_editable(repo(), user, person_id)
    except DomainError:
        abort(404)
    if request.method == "GET":
        return render("item_form.html", person=person, item=None, tab="list")
    try:
        item = lists.add_item(repo(), user, person_id, **_item_fields())
    except (DomainError, InvalidAmount) as e:
        return fail(str(e), url_for("pages.add_item", person_id=person_id))
    count = len(repo().items_for_person(person_id))
    gerry_react(gerry.add_item_trigger(item.price_minor, count),
                gerry.context(cfg().currency, price_minor=item.price_minor, count=count))
    return redirect(_list_url(person_id))


@bp.route("/items/<int:item_id>/edit", methods=["GET", "POST"])
@login_required
def edit_item(item_id: int):
    user = current_user()
    item = repo().item(item_id)
    if item is None:
        abort(404)
    try:
        person = access.require_editable(repo(), user, item.person_id)
    except DomainError:
        abort(404)
    if request.method == "GET":
        return render("item_form.html", person=person, item=item, tab="list")
    try:
        lists.edit_item(repo(), user, item_id, **_item_fields())
    except (DomainError, InvalidAmount) as e:
        return fail(str(e), url_for("pages.edit_item", item_id=item_id))
    return redirect(_list_url(person.id))


@bp.post("/items/<int:item_id>/delete")
@login_required
def delete_item(item_id: int):
    def act():
        item = lists.delete_item(repo(), current_user(), item_id)
        return redirect(_list_url(item.person_id))
    return domain_action(url_for("pages.my_list"), act)


@bp.post("/items/<int:item_id>/move")
@login_required
def move_item(item_id: int):
    item = repo().item(item_id)
    if item is None:
        abort(404)
    delta = -1 if request.form.get("dir") == "up" else 1

    def act():
        lists.move(repo(), current_user(), item_id, delta)
        return redirect(_list_url(item.person_id) + f"#row-{item_id}")
    return domain_action(_list_url(item.person_id), act)


@bp.post("/people/<int:person_id>/reorder")
@login_required
def reorder(person_id: int):
    data = request.get_json(silent=True) or {}
    ids = data.get("ids")
    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
        abort(400)
    try:
        lists.reorder(repo(), current_user(), person_id, ids)
    except DomainError as e:
        return jsonify(ok=False, error=str(e)), 409
    return jsonify(ok=True)


@bp.post("/preview")
@login_required
def preview():
    url = ((request.get_json(silent=True) or {}).get("url") or "").strip()
    if url and not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    p = linkpreview.preview(url, cfg().images_dir) if url else linkpreview.Preview()
    return jsonify(title=p.title, image=p.image,
                   image_url=url_for("pages.image", name=p.image) if p.image else None,
                   price=f"{p.price_minor // 100}.{p.price_minor % 100:02d}" if p.price_minor else None)


@bp.get("/img/<name>")
@login_required
def image(name: str):
    if not linkpreview.valid_image_name(name):
        abort(404)
    return send_from_directory(cfg().images_dir, name, max_age=86400 * 30)


# --- My buys ----------------------------------------------------------------------

@bp.get("/my-buys")
@login_required
def my_buys():
    user = current_user()
    mine = claims.my_claims(repo(), user)
    return render("my_buys.html", notices=claims.notices(repo(), user), mine=mine,
                  total=sum(c.my_amount_minor for c in mine), tab="buys")


@bp.post("/notices/<int:notice_id>/dismiss")
@login_required
def dismiss(notice_id: int):
    claims.dismiss_notice(repo(), current_user(), notice_id)
    return redirect(next_url(url_for("pages.my_buys")))


# --- Household ------------------------------------------------------------------

@bp.get("/household")
@login_required
def household():
    user = current_user()
    hh, people = accounts.my_household(repo(), user)
    join_url = cfg().base_url + url_for("auth.household_invite", token=hh.invite_token)
    revealed = repo().revealed_person_ids(user.id)
    return render("household.html", household=hh, people=people, join_url=join_url, revealed=revealed, tab="list")


@bp.post("/household/rename")
@login_required
def rename_household():
    def act():
        accounts.rename_household(repo(), current_user(), request.form.get("name", ""))
        return redirect(url_for("pages.household"))
    return domain_action(url_for("pages.household"), act)


@bp.post("/household/leave")
@login_required
def leave_household():
    def act():
        accounts.leave_household(repo(), current_user())
        return redirect(url_for("pages.household"))
    return domain_action(url_for("pages.household"), act)


@bp.post("/people/<int:person_id>/reveal")
@login_required
def toggle_reveal(person_id: int):
    def act():
        user = current_user()
        currently = person_id in repo().revealed_person_ids(user.id)
        accounts.set_reveal(repo(), user, person_id, not currently)
        return redirect(next_url(url_for("pages.household")))
    return domain_action(url_for("pages.household"), act)


@bp.post("/household/people")
@login_required
def add_person():
    def act():
        p = accounts.add_dependent(repo(), current_user(), request.form.get("name", ""))
        flash(f"{p.name} added. Their list is under My list.", "ok")
        return redirect(url_for("pages.household"))
    return domain_action(url_for("pages.household"), act)


@bp.post("/people/<int:person_id>/rename")
@login_required
def rename_person(person_id: int):
    def act():
        accounts.rename_person(repo(), current_user(), person_id, request.form.get("name", ""))
        return redirect(url_for("pages.household"))
    return domain_action(url_for("pages.household"), act)


@bp.post("/people/<int:person_id>/remove")
@login_required
def remove_person(person_id: int):
    def act():
        accounts.remove_dependent(repo(), current_user(), person_id)
        return redirect(url_for("pages.household"))
    return domain_action(url_for("pages.household"), act)


# --- Gerry ----------------------------------------------------------------------

@bp.post("/gerry/banish")
@login_required
def banish():
    g.user = accounts.set_gerry_ghost(repo(), current_user(), True)
    gerry_react(gerry.Trigger.BANISHED)
    return redirect(next_url(url_for("pages.everyone")))


@bp.post("/gerry/sorry")
@login_required
def sorry():
    g.user = accounts.set_gerry_ghost(repo(), current_user(), False)
    gerry_react(gerry.Trigger.APOLOGY)
    return redirect(next_url(url_for("pages.everyone")))
