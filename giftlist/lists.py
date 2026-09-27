"""Wishlists: adding and arranging items, and the Everyone view."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from . import access, activity, claims, clock
from .errors import DomainError, NotAllowed, NotFound
from .models import ActivityKind, HouseholdView, Item, Person, PersonView, User
from .money import MAX_MINOR
from .repository import Repository

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def _clean(text: str | None, limit: int = 300) -> str | None:
    text = _CONTROL_CHARS.sub("", (text or "")).strip()
    return text[:limit] or None


def _title(text: str) -> str:
    text = _CONTROL_CHARS.sub("", (text or "")).strip()
    if not text:
        raise DomainError("It needs a name. Even 'surprise me' counts.")
    return text[:160]


def _url(url: str | None) -> str | None:
    url = _clean(url, 2000)
    if not url:
        return None
    candidate = url if url.lower().startswith(("http://", "https://")) else "https://" + url
    if any(c.isspace() for c in candidate):
        return None
    try:
        parsed = urlparse(candidate)
        host = parsed.hostname
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not host or "." not in host:
        return None
    return candidate


def _price(price_minor: int) -> int:
    if price_minor <= 0:
        raise DomainError("Price has to be more than zero. Nothing's free, not even at Christmas.")
    if price_minor > MAX_MINOR:
        raise DomainError("That price is too large. Split it into a few items instead.")
    return price_minor


def editable_people(repo: Repository, user: User) -> list[Person]:
    """The My list switcher: you first, then your household's dependents."""
    me = access.own_person(repo, user)
    deps = [p for p in repo.people_in_household(me.household_id) if p.is_dependent]
    return [me, *deps]


def items_for_editing(repo: Repository, user: User, person_id: int) -> list[Item]:
    person = access.require_editable(repo, user, person_id)
    return repo.items_for_person(person.id)


def add_item(repo: Repository, user: User, person_id: int, title: str, url: str | None, image: str | None,
             price_minor: int, note: str | None, really_want: bool, is_voucher: bool = False) -> Item:
    with repo.write():
        person = access.require_editable(repo, user, person_id)
        item = repo.add_item(person.id, _title(title), _url(url), image, _price(price_minor), _clean(note),
                             really_want, clock.now(), is_voucher)
        activity.log(repo, ActivityKind.ITEM_ADDED, household_id=person.household_id, person_id=person.id,
                    item=item.title, person_name=person.name)
        return item


def _editable_item(repo: Repository, user: User, item_id: int) -> tuple[Item, Person]:
    item = repo.item(item_id)
    if item is None:
        raise NotFound("That item's already gone.")
    if not access.can_edit_item(repo, user, item):
        raise NotAllowed("That's not your list to fiddle with.")
    return item, repo.person(item.person_id)


def edit_item(repo: Repository, user: User, item_id: int, title: str, url: str | None, image: str | None,
              price_minor: int, note: str | None, really_want: bool, is_voucher: bool = False) -> Item:
    """Price may drop below what's claimed: blocking it would reveal claims.
    Claimers see the item flagged as over-claimed instead."""
    with repo.write():
        _, person = _editable_item(repo, user, item_id)
        new_title = _title(title)
        repo.update_item(item_id, new_title, _url(url), image, _price(price_minor), _clean(note),
                         really_want, is_voucher)
        activity.log(repo, ActivityKind.ITEM_EDITED, household_id=person.household_id, person_id=person.id,
                    item=new_title, person_name=person.name)
        return repo.item(item_id)


def delete_item(repo: Repository, user: User, item_id: int) -> Item:
    """A bought item can't be taken down -- whoever bought it gets told you tried,
    instead of the item just vanishing on them."""
    with repo.write():
        item, person = _editable_item(repo, user, item_id)
        if item.is_bought:
            claims.notify_removal_blocked(repo, item, person)
        else:
            claims.remove_item_with_notices(repo, item, person)
            activity.log(repo, ActivityKind.ITEM_REMOVED, household_id=person.household_id, person_id=person.id,
                        item=item.title, person_name=person.name)
    if item.is_bought:
        raise DomainError("Can't take that off the list now -- it's already bought. "
                          "Whoever's got it has been told you tried; sort it out with them directly.")
    return item


# --- household gifts: sharing an item with someone else in your household ----

def shared_items_for_editing(repo: Repository, user: User, person_id: int) -> list[Item]:
    """Joint gifts someone else in the household shared with this person -- edited the
    same as their own."""
    person = access.require_editable(repo, user, person_id)
    return repo.items_shared_with_person(person.id)


def share_names(repo: Repository, item_id: int, exclude_person_id: int | None = None) -> list[str]:
    return [p.name for p in repo.people_sharing_item(item_id) if p.id != exclude_person_id]


def share_candidates(repo: Repository, user: User, item: Item) -> list[Person]:
    """Household-mates this item could be shared with: real users, not you, not the
    dependent whose list it might be on. Only the item's own owner may manage this."""
    owner = repo.person(item.person_id)
    if owner is None or owner.user_id != user.id:
        return []
    return [p for p in repo.people_in_household(owner.household_id) if p.user_id and p.user_id != user.id]


def toggle_share(repo: Repository, user: User, item_id: int, target_person_id: int) -> tuple[Item, Person, bool]:
    """Share (or un-share) one of your own gifts with a household-mate. Once shared,
    it shows on both your lists and either of you can manage it."""
    with repo.write():
        item = repo.item(item_id)
        if item is None:
            raise NotFound("That item's already gone.")
        owner = repo.person(item.person_id)
        if owner is None or owner.user_id != user.id:
            raise NotAllowed("Only the person who added it can share it.")
        target = repo.person(target_person_id)
        if target is None or target.id == owner.id or target.user_id is None \
                or target.household_id != owner.household_id:
            raise NotFound("Can't share it with that person.")
        sharing_now = not repo.is_item_shared_with(item_id, target.id)
        if sharing_now:
            repo.add_item_share(item_id, target.id)
        else:
            repo.remove_item_share(item_id, target.id)
        return item, target, sharing_now


def reorder(repo: Repository, user: User, person_id: int, ordered_ids: list[int]) -> None:
    with repo.write():
        person = access.require_editable(repo, user, person_id)
        current = [i.id for i in repo.items_for_person(person.id)]
        if sorted(current) != sorted(ordered_ids):
            raise DomainError("The list changed while you were dragging. Try again.")
        repo.set_positions(ordered_ids)


def move(repo: Repository, user: User, item_id: int, delta: int) -> None:
    """Keyboard and screen-reader fallback for drag: move one step up or down."""
    with repo.write():
        item, person = _editable_item(repo, user, item_id)
        ids = [i.id for i in repo.items_for_person(person.id)]
        i = ids.index(item.id)
        j = max(0, min(len(ids) - 1, i + delta))
        ids.insert(j, ids.pop(i))
        repo.set_positions(ids)


def everyone(repo: Repository, user: User) -> list[HouseholdView]:
    """Every other household, plus any household-mates you've chosen to reveal to yourself.

    A gift shared between household-mates only shows once *every* co-owner is
    revealed to you -- revealing one of them isn't the other's consent to
    spoil a gift that's just as much theirs, so it stays out until both are."""
    mine = access.household_id_of(repo, user)
    revealed = repo.revealed_person_ids(user.id)
    out = []
    for hh in repo.all_households():
        own_household = hh.id == mine
        candidates = [p for p in repo.people_in_household(hh.id) if p.id in revealed] \
            if own_household else repo.people_in_household(hh.id)
        people = []
        for p in candidates:
            items = repo.items_for_person(p.id)
            if own_household:
                items = [i for i in items
                        if {p.id, *(co.id for co in repo.people_sharing_item(i.id))} <= revealed]
            people.append(PersonView(p, tuple(claims.item_view(repo, i) for i in items)))
        people = tuple(people)
        if people:
            out.append(HouseholdView(hh, people))
    return out


def claimable_item(repo: Repository, user: User, item_id: int):
    """An item plus its claims, for the buy / split page. Refuses your own household's."""
    item = repo.item(item_id)
    if item is None:
        raise NotFound("That item's gone. Someone removed it.")
    owner = access.require_claimable(repo, user, item)
    return claims.item_view(repo, item), owner
