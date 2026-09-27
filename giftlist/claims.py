"""Claiming items: alone or split, marking bought, and the notices that follow."""

from __future__ import annotations

from dataclasses import dataclass

from . import access, activity, clock
from .errors import DomainError, NotAllowed, NotFound, OverClaimed
from .models import ActivityKind, Claim, Contribution, Item, ItemView, MyClaim, Notice, NoticeKind, Person, User
from .repository import Repository


@dataclass(slots=True, frozen=True)
class ClaimOutcome:
    """What just happened, for Gerry to have an opinion about."""

    item: Item
    owner: Person
    amount_minor: int          # the user's share after the action (0 if withdrawn)
    previous_minor: int        # their share before (0 if new)
    remaining_before_minor: int
    claimed_after_minor: int


def item_view(repo: Repository, item: Item) -> ItemView:
    contributions = []
    for c in repo.claims_for_item(item.id):
        claimer = repo.user(c.user_id)
        contributions.append(Contribution(c.user_id, claimer.name if claimer else "Someone", c.amount_minor))
    return ItemView(item, tuple(contributions))


def _claimable_item(repo: Repository, user: User, item_id: int) -> tuple[Item, Person]:
    item = repo.item(item_id)
    if item is None:
        raise NotFound("That item's gone. Someone removed it.")
    owner = access.require_claimable(repo, user, item.person_id)
    return item, owner


def _positive(amount_minor: int) -> None:
    if amount_minor <= 0:
        raise DomainError("Nice try. It has to be more than zero.")


def _notify_co_claimers(repo: Repository, item: Item, owner: Person, actor: User,
                        kind: NoticeKind, extra: dict) -> None:
    claimed = sum(c.amount_minor for c in repo.claims_for_item(item.id))
    for c in repo.claims_for_item(item.id):
        if c.user_id == actor.id:
            continue
        params = {"who": actor.name, "item": item.title, "owner": owner.name, "item_id": item.id,
                  "owner_id": owner.id, "remaining_minor": max(0, item.price_minor - claimed),
                  "bought": item.is_bought, **extra}
        repo.add_notice(c.user_id, kind, params, clock.now())


def claim(repo: Repository, user: User, item_id: int, amount_minor: int) -> ClaimOutcome:
    _positive(amount_minor)
    with repo.write():
        item, owner = _claimable_item(repo, user, item_id)
        claims = repo.claims_for_item(item_id)
        if any(c.user_id == user.id for c in claims):
            raise DomainError("You're already in on this one. Change your share instead.")
        claimed = sum(c.amount_minor for c in claims)
        remaining = item.price_minor - claimed
        if not item.is_voucher:
            if remaining <= 0:
                raise OverClaimed("Too slow. That's already covered.")
            if amount_minor > remaining:
                raise OverClaimed("That's more than what's left. Generous, but no.")
        repo.add_claim(item_id, user.id, amount_minor, clock.now())
        repo.reset_visits_since_claim(user.id)
        activity.log(repo, ActivityKind.ITEM_CLAIMED, household_id=owner.household_id, person_id=owner.id,
                    item=item.title, owner_name=owner.name, claimer_name=user.name, amount_minor=amount_minor)
        return ClaimOutcome(item, owner, amount_minor, 0, max(0, remaining), claimed + amount_minor)


def update_claim(repo: Repository, user: User, item_id: int, amount_minor: int) -> ClaimOutcome:
    """Increases must fit the remaining balance; decreases are always allowed
    (including after the item is marked bought, and to fix an over-claimed item)."""
    _positive(amount_minor)
    with repo.write():
        item, owner = _claimable_item(repo, user, item_id)
        claims = repo.claims_for_item(item_id)
        mine = next((c for c in claims if c.user_id == user.id), None)
        if mine is None:
            raise NotFound("You haven't claimed any of this one.")
        others = sum(c.amount_minor for c in claims if c.user_id != user.id)
        if not item.is_voucher and amount_minor > mine.amount_minor and others + amount_minor > item.price_minor:
            raise OverClaimed("That's more than what's left. Generous, but no.")
        remaining_before = max(0, item.price_minor - others - mine.amount_minor)
        if amount_minor != mine.amount_minor:
            repo.set_claim_amount(item_id, user.id, amount_minor)
            _notify_co_claimers(repo, item, owner, user, NoticeKind.SHARE_CHANGED,
                                {"old_minor": mine.amount_minor, "new_minor": amount_minor})
        return ClaimOutcome(item, owner, amount_minor, mine.amount_minor, remaining_before, others + amount_minor)


def withdraw(repo: Repository, user: User, item_id: int) -> ClaimOutcome:
    """Back out entirely. If that leaves nobody claiming it, it can't stay marked
    bought -- there'd be nobody left for a shortfall to belong to."""
    with repo.write():
        item, owner = _claimable_item(repo, user, item_id)
        claims = repo.claims_for_item(item_id)
        mine = next((c for c in claims if c.user_id == user.id), None)
        if mine is None:
            raise NotFound("You weren't in on that one anyway.")
        repo.delete_claim(item_id, user.id)
        others = sum(c.amount_minor for c in claims if c.user_id != user.id)
        if others == 0 and item.is_bought:
            repo.set_bought(item_id, None)
        _notify_co_claimers(repo, item, owner, user, NoticeKind.SHARE_WITHDRAWN, {"old_minor": mine.amount_minor})
        activity.log(repo, ActivityKind.ITEM_WITHDRAWN, household_id=owner.household_id, person_id=owner.id,
                    item=item.title, owner_name=owner.name, claimer_name=user.name, amount_minor=mine.amount_minor)
        return ClaimOutcome(item, owner, 0, mine.amount_minor, max(0, item.price_minor - others - mine.amount_minor), others)


def _require_claimer(repo: Repository, user: User, item_id: int) -> tuple[Item, Person, list[Claim]]:
    item, owner = _claimable_item(repo, user, item_id)
    claims = repo.claims_for_item(item_id)
    if not any(c.user_id == user.id for c in claims):
        raise NotAllowed("Only people who claimed it can say it's bought.")
    return item, owner, claims


def mark_bought(repo: Repository, user: User, item_id: int) -> ClaimOutcome:
    with repo.write():
        item, owner, claims = _require_claimer(repo, user, item_id)
        claimed = sum(c.amount_minor for c in claims)
        if not item.is_voucher and claimed < item.price_minor:
            raise DomainError("Can't mark it bought until it's fully covered. Someone needs to cough up.")
        repo.set_bought(item_id, clock.now())
        mine = next(c.amount_minor for c in claims if c.user_id == user.id)
        activity.log(repo, ActivityKind.ITEM_BOUGHT, household_id=owner.household_id, person_id=owner.id,
                    item=item.title, owner_name=owner.name, buyer_name=user.name)
        return ClaimOutcome(item, owner, mine, mine, 0, claimed)


def unmark_bought(repo: Repository, user: User, item_id: int) -> None:
    with repo.write():
        _require_claimer(repo, user, item_id)
        repo.set_bought(item_id, None)


def my_claims(repo: Repository, user: User) -> list[MyClaim]:
    out = []
    for c in repo.claims_by_user(user.id):
        item = repo.item(c.item_id)
        owner = repo.person(item.person_id)
        out.append(MyClaim(item_view(repo, item), owner, c.amount_minor))
    return out


def notices(repo: Repository, user: User) -> list[Notice]:
    return repo.notices_for_user(user.id)


def dismiss_notice(repo: Repository, user: User, notice_id: int) -> None:
    with repo.write():
        repo.delete_notice(notice_id, user.id)


# --- used when items or people disappear -----------------------------------

def remove_item_with_notices(repo: Repository, item: Item, owner: Person) -> None:
    """Delete an item; everyone who'd claimed it gets told (and whether it was bought)."""
    for c in repo.claims_for_item(item.id):
        repo.add_notice(c.user_id, NoticeKind.ITEM_REMOVED,
                        {"item": item.title, "owner": owner.name, "amount_minor": c.amount_minor,
                         "bought": item.is_bought}, clock.now())
    repo.delete_item(item.id)


def notify_removal_blocked(repo: Repository, item: Item, owner: Person) -> None:
    """The owner tried to take down an item that's already bought -- whoever bought
    it gets told, since the owner can't be shown who that is. The item's still there,
    so the notice links straight to it."""
    for c in repo.claims_for_item(item.id):
        repo.add_notice(c.user_id, NoticeKind.REMOVE_BLOCKED,
                        {"item": item.title, "owner": owner.name, "amount_minor": c.amount_minor,
                         "item_id": item.id}, clock.now())


def drop_claim_with_notices(repo: Repository, claim_: Claim, actor: User) -> None:
    """Remove someone's claim (they left, or moved household); co-claimers are told."""
    item = repo.item(claim_.item_id)
    owner = repo.person(item.person_id)
    repo.delete_claim(item.id, actor.id)
    _notify_co_claimers(repo, item, owner, actor, NoticeKind.SHARE_WITHDRAWN, {"old_minor": claim_.amount_minor})
