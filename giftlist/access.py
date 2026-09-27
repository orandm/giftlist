"""Who may see and do what. Pure checks over repository reads."""

from __future__ import annotations

from .errors import NotAllowed, NotFound
from .models import Item, Person, User
from .repository import Repository


def own_person(repo: Repository, user: User) -> Person:
    person = repo.person_for_user(user.id)
    if person is None:
        raise NotFound("You don't seem to have a list. Sign out and back in.")
    return person


def household_id_of(repo: Repository, user: User) -> int:
    return own_person(repo, user).household_id


def is_in_my_household(repo: Repository, user: User, person: Person) -> bool:
    return person.household_id == household_id_of(repo, user)


def is_revealed(repo: Repository, user: User, person: Person) -> bool:
    """A household-mate (never a dependent) you've chosen to see and claim on."""
    return not person.is_dependent and repo.is_revealed(user.id, person.id)


def can_edit(repo: Repository, user: User, person: Person) -> bool:
    """Your own list, and dependents in your household. Not a co-manager's list."""
    if person.user_id == user.id:
        return True
    return person.is_dependent and is_in_my_household(repo, user, person)


def require_editable(repo: Repository, user: User, person_id: int) -> Person:
    person = repo.person(person_id)
    if person is None or not can_edit(repo, user, person):
        raise NotAllowed("That's not your list to fiddle with.")
    return person


def can_edit_item(repo: Repository, user: User, item: Item) -> bool:
    """Your own item, a dependent's in your household, or one someone shared with you."""
    person = repo.person(item.person_id)
    if person is not None and can_edit(repo, user, person):
        return True
    mine = repo.person_for_user(user.id)
    return mine is not None and repo.is_item_shared_with(item.id, mine.id)


def require_claimable(repo: Repository, user: User, item: Item) -> Person:
    """Claims are for other households only, unless you've revealed the owner to
    yourself -- and, for a gift shared between household-mates, unless you've
    revealed every one of them. One co-owner being revealed to you isn't the
    other's consent to spoil a gift that's just as much theirs."""
    owner = repo.person(item.person_id)
    if owner is None:
        raise NotFound("That person's gone.")
    if is_in_my_household(repo, user, owner):
        co_owners = [owner, *repo.people_sharing_item(item.id)]
        if not all(is_revealed(repo, user, p) for p in co_owners):
            raise NotAllowed("Nice try. You can't see what's happening with your own household's lists.")
    return owner
