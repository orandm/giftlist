"""Silly badges: whoever's racked up the most of a given gerry_event wins it."""

from __future__ import annotations

from dataclasses import dataclass

from .gerry import Trigger
from .repository import Repository


@dataclass(slots=True, frozen=True)
class Badge:
    key: str
    label: str
    trigger: Trigger
    blurb: str


BADGES: tuple[Badge, ...] = (
    Badge("cheapskate", "Cheapskate", Trigger.TINY_CHIP_IN, "most laughably tiny chip-ins"),
    Badge("last_minute_larry", "Last Minute Larry", Trigger.LATE_CLAIM, "most claims made in the final days"),
    Badge("big_spender", "Big Spender", Trigger.PRICEY_WISH, "most extravagant wishes"),
    Badge("ghost_whisperer", "Ghost Whisperer", Trigger.BANISHED, "banished Gerry the most times"),
)


@dataclass(slots=True, frozen=True)
class BadgeHolder:
    badge: Badge
    user_name: str
    count: int


def leaderboard(repo: Repository) -> list[BadgeHolder]:
    """One holder per badge: whoever has the most of that trigger, ties broken by whoever got there first."""
    by_trigger: dict[str, list[tuple[int, int]]] = {}
    for user_id, trigger, count in repo.gerry_event_totals():
        by_trigger.setdefault(trigger, []).append((user_id, count))
    out = []
    for badge in BADGES:
        candidates = [(uid, n) for uid, n in by_trigger.get(badge.trigger.value, []) if n > 0]
        if not candidates:
            continue
        user_id, count = max(candidates, key=lambda t: t[1])
        user = repo.user(user_id)
        if user is not None:
            out.append(BadgeHolder(badge, user.name, count))
    return out
