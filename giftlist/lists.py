"""Wishlists: adding and arranging items, and the Everyone view."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from . import access, claims, clock
from .errors import DomainError, NotFound
from .models import HouseholdView, Item, Person, PersonView, User
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
             price_minor: int, note: str | None, really_want: bool) -> Item:
    with repo.write():
        person = access.require_editable(repo, user, person_id)
        return repo.add_item(person.id, _title(title), _url(url), image, _price(price_minor), _clean(note),
                             really_want, clock.now())


def _editable_item(repo: Repository, user: User, item_id: int) -> tuple[Item, Person]:
    item = repo.item(item_id)
    if item is None:
        raise NotFound("That item's already gone.")
    person = access.require_editable(repo, user, item.person_id)
    return item, person


def edit_item(repo: Repository, user: User, item_id: int, title: str, url: str | None, image: str | None,
              price_minor: int, note: str | None, really_want: bool) -> Item:
    """Price may drop below what's claimed: blocking it would reveal claims.
    Claimers see the item flagged as over-claimed instead."""
    with repo.write():
        _editable_item(repo, user, item_id)
        repo.update_item(item_id, _title(title), _url(url), image, _price(price_minor), _clean(note), really_want)
        return repo.item(item_id)


def delete_item(repo: Repository, user: User, item_id: int) -> Item:
    with repo.write():
        item, person = _editable_item(repo, user, item_id)
        claims.remove_item_with_notices(repo, item, person)
        return item


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
    """Every other household, plus any household-mates you've chosen to reveal to yourself."""
    mine = access.household_id_of(repo, user)
    revealed = repo.revealed_person_ids(user.id)
    out = []
    for hh in repo.all_households():
        candidates = [p for p in repo.people_in_household(hh.id) if p.id in revealed] \
            if hh.id == mine else repo.people_in_household(hh.id)
        people = tuple(
            PersonView(p, tuple(claims.item_view(repo, i) for i in repo.items_for_person(p.id)))
            for p in candidates)
        if people:
            out.append(HouseholdView(hh, people))
    return out


def claimable_item(repo: Repository, user: User, item_id: int):
    """An item plus its claims, for the buy / split page. Refuses your own household's."""
    item = repo.item(item_id)
    if item is None:
        raise NotFound("That item's gone. Someone removed it.")
    owner = access.require_claimable(repo, user, item.person_id)
    return claims.item_view(repo, item), owner
