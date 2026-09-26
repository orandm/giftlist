"""Domain models: plain frozen dataclasses, no I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Funding(Enum):
    OPEN = "open"        # nobody has claimed
    PARTIAL = "partial"  # some claimed, room for more
    FULL = "full"        # claims exactly cover the price
    OVER = "over"        # owner lowered the price below what's claimed


def funding_state(price_minor: int, claimed_minor: int) -> Funding:
    if claimed_minor == 0:
        return Funding.OPEN
    if claimed_minor < price_minor:
        return Funding.PARTIAL
    if claimed_minor == price_minor:
        return Funding.FULL
    return Funding.OVER


@dataclass(slots=True, frozen=True)
class User:
    id: int
    google_sub: str
    email: str
    name: str
    created_at: datetime
    last_seen_at: datetime | None
    gerry_ghost: bool
    gerry_last_line: str | None
    visit_count: int
    visits_since_claim: int


@dataclass(slots=True, frozen=True)
class Household:
    id: int
    name: str
    invite_token: str


@dataclass(slots=True, frozen=True)
class Person:
    """Anyone with a wishlist. `user_id` is None for dependents who never sign in."""

    id: int
    household_id: int
    name: str
    user_id: int | None

    @property
    def is_dependent(self) -> bool:
        return self.user_id is None


@dataclass(slots=True, frozen=True)
class Item:
    id: int
    person_id: int
    title: str
    url: str | None
    image: str | None  # file name under the images dir
    price_minor: int
    note: str | None
    really_want: bool
    position: int
    bought_at: datetime | None
    created_at: datetime

    @property
    def is_bought(self) -> bool:
        return self.bought_at is not None


@dataclass(slots=True, frozen=True)
class Claim:
    item_id: int
    user_id: int
    amount_minor: int
    claimed_at: datetime


class NoticeKind(Enum):
    ITEM_REMOVED = "item_removed"      # owner removed an item you'd claimed
    SHARE_CHANGED = "share_changed"    # a co-splitter changed their share
    SHARE_WITHDRAWN = "share_withdrawn"  # a co-splitter withdrew


@dataclass(slots=True, frozen=True)
class Notice:
    id: int
    user_id: int
    kind: NoticeKind
    params: dict = field(hash=False)
    created_at: datetime


# --- Read models -----------------------------------------------------------
# What a viewer sees of an item on someone else's list. The owner's household
# never receives one of these, so claim data can't leak through a template.


@dataclass(slots=True, frozen=True)
class Contribution:
    user_id: int
    name: str
    amount_minor: int


@dataclass(slots=True, frozen=True)
class ItemView:
    item: Item
    contributions: tuple[Contribution, ...]

    @property
    def claimed_minor(self) -> int:
        return sum(c.amount_minor for c in self.contributions)

    @property
    def remaining_minor(self) -> int:
        return max(0, self.item.price_minor - self.claimed_minor)

    @property
    def funding(self) -> Funding:
        return funding_state(self.item.price_minor, self.claimed_minor)

    def contribution_of(self, user_id: int) -> Contribution | None:
        return next((c for c in self.contributions if c.user_id == user_id), None)


@dataclass(slots=True, frozen=True)
class PersonView:
    person: Person
    items: tuple[ItemView, ...]

    @property
    def still_needed(self) -> int:
        return sum(1 for v in self.items if v.remaining_minor > 0 and not v.item.is_bought)


@dataclass(slots=True, frozen=True)
class HouseholdView:
    household: Household
    people: tuple[PersonView, ...]


@dataclass(slots=True, frozen=True)
class MyClaim:
    view: ItemView
    owner: Person
    my_amount_minor: int
