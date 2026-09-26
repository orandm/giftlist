"""Who may see and do what. Pure checks over repository reads."""

from __future__ import annotations

from .errors import NotAllowed, NotFound
from .models import Person, User
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


def require_claimable(repo: Repository, user: User, person_id: int) -> Person:
    """Claims are for other households only, unless you've revealed that person to yourself."""
    person = repo.person(person_id)
    if person is None:
        raise NotFound("That person's gone.")
    if is_in_my_household(repo, user, person) and not is_revealed(repo, user, person):
        raise NotAllowed("Nice try. You can't see what's happening with your own household's lists.")
    return person
