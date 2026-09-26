"""Accounts: sign-in with invites, households, admin actions, new season."""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from . import access, claims, clock
from .errors import DomainError, NotAllowed, NotFound
from .models import Household, Person, User
from .repository import Repository

SITE_INVITE_KEY = "site_invite_token"


def _token() -> str:
    return secrets.token_urlsafe(16)


def _name(text: str, what: str = "Name") -> str:
    text = (text or "").strip()
    if not text:
        raise DomainError(f"{what} can't be empty. Even Gerry has a name.")
    return text[:60]


@dataclass(slots=True, frozen=True)
class Invite:
    """The invite link a visitor arrived with, if any."""

    site_token: str | None = None
    household_token: str | None = None


# --- site invite -----------------------------------------------------------

def site_invite_token(repo: Repository) -> str:
    token = repo.setting(SITE_INVITE_KEY)
    if token is None:
        with repo.write():
            token = repo.setting(SITE_INVITE_KEY) or _token()
            repo.set_setting(SITE_INVITE_KEY, token)
    return token


def reset_site_invite(repo: Repository) -> str:
    with repo.write():
        token = _token()
        repo.set_setting(SITE_INVITE_KEY, token)
        return token


def invite_is_valid(repo: Repository, invite: Invite) -> bool:
    site_ok = bool(invite.site_token) and secrets.compare_digest(invite.site_token, site_invite_token(repo))
    return site_ok or (bool(invite.household_token) and repo.household_by_invite(invite.household_token) is not None)


# --- sign in -----------------------------------------------------------------

def sign_in(repo: Repository, google_sub: str, email: str, name: str, invite: Invite, is_admin: bool) -> User:
    """Existing users just sign in. New users need an invite link (or to be the admin)."""
    existing = repo.user_by_sub(google_sub)
    if existing is not None:
        with repo.write():
            repo.touch_user(existing.id, clock.now())
        if invite.household_token:
            try:
                join_household(repo, existing, invite.household_token)
            except DomainError:
                pass  # shown again on the household page if they retry
        return repo.user(existing.id)

    if not (is_admin or invite_is_valid(repo, invite)):
        raise NotAllowed("You need an invite link to get in. Ask whoever runs the family group chat.")

    display = _name(name or email.split("@")[0])
    with repo.write():
        user = repo.add_user(google_sub, email, display, clock.now())
        household = repo.household_by_invite(invite.household_token) if invite.household_token else None
        if household is None:
            household = repo.add_household(f"{display}'s household", _token())
        repo.add_person(household.id, display, user.id)
    return user


def start_visit(repo: Repository, user: User) -> User:
    with repo.write():
        repo.start_visit(user.id)
        repo.touch_user(user.id, clock.now())
    return repo.user(user.id)


# --- households --------------------------------------------------------------

def my_household(repo: Repository, user: User) -> tuple[Household, list[Person]]:
    hh_id = access.household_id_of(repo, user)
    return repo.household(hh_id), repo.people_in_household(hh_id)


def rename_household(repo: Repository, user: User, name: str) -> None:
    with repo.write():
        repo.rename_household(access.household_id_of(repo, user), _name(name, "Household name"))


def add_dependent(repo: Repository, user: User, name: str) -> Person:
    with repo.write():
        return repo.add_person(access.household_id_of(repo, user), _name(name), None)


def rename_person(repo: Repository, user: User, person_id: int, name: str) -> None:
    with repo.write():
        person = access.require_editable(repo, user, person_id)
        repo.rename_person(person.id, _name(name))


def _remove_person_lists(repo: Repository, person: Person) -> None:
    for item in repo.items_for_person(person.id):
        claims.remove_item_with_notices(repo, item, person)


def remove_dependent(repo: Repository, user: User, person_id: int) -> None:
    with repo.write():
        person = access.require_editable(repo, user, person_id)
        if not person.is_dependent:
            raise NotAllowed("You can't remove yourself like that.")
        _remove_person_lists(repo, person)
        repo.delete_person(person.id)


def join_household(repo: Repository, user: User, invite_token: str) -> Household:
    """Merge your whole household (you, plus any dependents) into another via their invite link."""
    with repo.write():
        target = repo.household_by_invite(invite_token)
        if target is None:
            raise NotFound("That household link doesn't work any more.")
        me = access.own_person(repo, user)
        if me.household_id == target.id:
            return target
        old_household = me.household_id
        for person in repo.people_in_household(old_household):
            repo.move_person(person.id, target.id)
        repo.delete_household(old_household)
        _purge_in_household_claims(repo, target.id)
        return target


def leave_household(repo: Repository, user: User) -> Household:
    """Split off into your own new household. Blocked while it has any dependents in it,
    so their lists never end up orphaned -- move or remove them first."""
    with repo.write():
        me = access.own_person(repo, user)
        others = repo.people_in_household(me.household_id)
        if any(p.is_dependent for p in others):
            raise NotAllowed("This household has dependents in it. Remove them, or ask the admin, before leaving.")
        if not any(p.id != me.id for p in others):
            raise NotAllowed("You're already the only one here.")
        new_household = repo.add_household(f"{me.name}'s household", _token())
        repo.move_person(me.id, new_household.id)
        return new_household


def set_reveal(repo: Repository, user: User, target_person_id: int, revealed: bool) -> None:
    """Opt to see and claim on a household-mate's list on Everyone, as if they were another household."""
    with repo.write():
        target = repo.person(target_person_id)
        if target is None or target.user_id == user.id or target.is_dependent \
                or not access.is_in_my_household(repo, user, target):
            raise NotAllowed("That's not someone you can reveal.")
        repo.set_reveal(user.id, target_person_id, revealed)
        if not revealed:
            for item in repo.items_for_person(target_person_id):
                for c in repo.claims_for_item(item.id):
                    if c.user_id == user.id:
                        claims.drop_claim_with_notices(repo, c, user)


def _purge_in_household_claims(repo: Repository, household_id: int) -> None:
    """After someone joins, nobody may hold claims on their own household's lists."""
    people = repo.people_in_household(household_id)
    member_users = {p.user_id for p in people if p.user_id is not None}
    for p in people:
        for item in repo.items_for_person(p.id):
            for c in repo.claims_for_item(item.id):
                if c.user_id in member_users:
                    claims.drop_claim_with_notices(repo, c, repo.user(c.user_id))


# --- admin ---------------------------------------------------------------------

@dataclass(slots=True, frozen=True)
class UserRow:
    user: User
    household_name: str


@dataclass(slots=True, frozen=True)
class HouseholdRow:
    household: Household
    users: int
    dependents: int


def admin_overview(repo: Repository) -> tuple[list[UserRow], list[HouseholdRow]]:
    users = []
    for u in repo.all_users():
        p = repo.person_for_user(u.id)
        hh = repo.household(p.household_id) if p else None
        users.append(UserRow(u, hh.name if hh else "None"))
    households = []
    for hh in repo.all_households():
        people = repo.people_in_household(hh.id)
        households.append(HouseholdRow(hh, sum(1 for p in people if not p.is_dependent),
                                       sum(1 for p in people if p.is_dependent)))
    return users, households


def remove_user(repo: Repository, user_id: int) -> None:
    """Delete a user's list and claims. If nobody's left to run their household,
    its dependents go too. Everyone affected gets a notice."""
    with repo.write():
        user = repo.user(user_id)
        if user is None:
            raise NotFound("That user's already gone.")
        for c in repo.claims_by_user(user.id):
            claims.drop_claim_with_notices(repo, c, user)
        person = repo.person_for_user(user.id)
        if person is not None:
            _remove_person_lists(repo, person)
            repo.delete_person(person.id)
            remaining = repo.people_in_household(person.household_id)
            if not any(not p.is_dependent for p in remaining):
                for dep in remaining:
                    _remove_person_lists(repo, dep)
                    repo.delete_person(dep.id)
                repo.delete_household(person.household_id)
        repo.delete_user(user.id)


def new_season(repo: Repository) -> None:
    """Clear every list, claim and notice. Accounts and households stay."""
    with repo.write():
        repo.delete_all_items()
        repo.delete_all_notices()


# --- Gerry's per-user state ---------------------------------------------------

def set_gerry_ghost(repo: Repository, user: User, ghost: bool) -> User:
    with repo.write():
        repo.set_gerry(user.id, ghost, user.gerry_last_line)
    return repo.user(user.id)


def remember_gerry_line(repo: Repository, user: User, template: str) -> None:
    with repo.write():
        current = repo.user(user.id)
        repo.set_gerry(user.id, current.gerry_ghost, template)
