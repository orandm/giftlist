"""Silly badges: whoever's racked up the most of something wins it."""

from __future__ import annotations

from dataclasses import dataclass

from .clock import LOCAL
from .gerry import Trigger
from .money import format_amount
from .repository import Repository

NIGHT_OWL_HOURS = range(0, 5)  # local midnight to 5am -- should really be asleep


@dataclass(slots=True, frozen=True)
class BadgeHolder:
    key: str
    label: str
    icon: str
    blurb: str
    subject_name: str
    detail: str


# (key, label, icon, blurb, trigger) -- the "most of this gerry_event" badges
_EVENT_BADGES = (
    ("cheapskate", "Cheapskate", "\U0001FA99", "most laughably tiny chip-ins", Trigger.TINY_CHIP_IN),
    ("last_minute_larry", "Last Minute Larry", "⏰", "most claims made in the final days", Trigger.LATE_CLAIM),
    ("big_spender", "Big Spender", "\U0001F4B8", "most extravagant wishes", Trigger.PRICEY_WISH),
    ("ghost_whisperer", "Ghost Whisperer", "\U0001F47B", "banished Gerry the most times", Trigger.BANISHED),
    ("commitment_issues", "Commitment Issues", "\U0001F3C3", "backed out of the most claims", Trigger.BACKING_OUT),
)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _event_badges(repo: Repository) -> list[BadgeHolder]:
    by_trigger: dict[str, list[tuple[int, int]]] = {}
    for user_id, trigger, count in repo.gerry_event_totals():
        by_trigger.setdefault(trigger, []).append((user_id, count))
    out = []
    for key, label, icon, blurb, trigger in _EVENT_BADGES:
        candidates = [(uid, n) for uid, n in by_trigger.get(trigger.value, []) if n > 0]
        if not candidates:
            continue
        user_id, count = max(candidates, key=lambda t: t[1])
        user = repo.user(user_id)
        if user is not None:
            out.append(BadgeHolder(key, label, icon, blurb, user.name, _plural(count, "time")))
    return out


def _window_shopper(repo: Repository) -> BadgeHolder | None:
    candidates = [u for u in repo.all_users() if u.visits_since_claim > 0]
    if not candidates:
        return None
    winner = max(candidates, key=lambda u: u.visits_since_claim)
    return BadgeHolder("window_shopper", "Window Shopper", "\U0001F440", "most visits since their last claim",
                       winner.name, _plural(winner.visits_since_claim, "visit"))


def _person_item_badges(repo: Repository) -> list[BadgeHolder]:
    stats = repo.item_stats_by_person()  # (person_id, count, total_minor, sulk_count)
    out = []

    # Need at least two people in the running, or "highest" and "lowest" are the same
    # lone entrant -- which reads as a bug ("lowest total" showing the biggest number).
    with_items = [s for s in stats if s[1] > 0]
    if len(with_items) >= 2:
        person_id, count, _, _ = max(with_items, key=lambda s: s[1])
        person = repo.person(person_id)
        if person is not None:
            out.append(BadgeHolder("wish_list_hoarder", "Wish List Hoarder", "\U0001F4DD",
                                   "most items asked for", person.name, _plural(count, "item")))

        person_id, _, total, _ = min(with_items, key=lambda s: s[2])
        person = repo.person(person_id)
        if person is not None:
            out.append(BadgeHolder("easy_to_please", "Easy to Please", "\U0001F381",
                                   "lowest total wish list value", person.name, format_amount(total, "EUR")))

    sulky = [s for s in stats if s[3] > 0]
    if sulky:
        person_id, _, _, sulk = max(sulky, key=lambda s: s[3])
        person = repo.person(person_id)
        if person is not None:
            out.append(BadgeHolder("serial_sulker", "Serial Sulker", "\U0001F624",
                                   "most \"will sulk without it\" items", person.name, _plural(sulk, "item")))
    return out


def _extreme_item_badges(repo: Repository) -> list[BadgeHolder]:
    """The single cheapest and priciest items anyone's asked for, site-wide."""
    items = repo.all_items()
    if len(items) < 2:  # need something to actually compare against
        return []
    out = []

    cheapest = min(items, key=lambda i: i.price_minor)
    person = repo.person(cheapest.person_id)
    if person is not None:
        out.append(BadgeHolder("bargain_bin", "Bargain Bin", "\U0001F3F7", "cheapest single item on any wish list",
                               person.name, f"{cheapest.title}, {format_amount(cheapest.price_minor, 'EUR')}"))

    priciest = max(items, key=lambda i: i.price_minor)
    person = repo.person(priciest.person_id)
    if person is not None:
        out.append(BadgeHolder("big_ask", "Big Ask", "\U0001F48E", "priciest single item on any wish list",
                               person.name, f"{priciest.title}, {format_amount(priciest.price_minor, 'EUR')}"))
    return out


def _night_owl(repo: Repository) -> BadgeHolder | None:
    """Whoever adds the most wishes while everyone sane is asleep."""
    counts: dict[int, int] = {}
    for item in repo.all_items():
        if item.created_at.astimezone(LOCAL).hour in NIGHT_OWL_HOURS:
            counts[item.person_id] = counts.get(item.person_id, 0) + 1
    if not counts:
        return None
    person_id, n = max(counts.items(), key=lambda t: t[1])
    person = repo.person(person_id)
    if person is None:
        return None
    return BadgeHolder("night_owl", "Night Owl", "\U0001F989", "most wishes added between midnight and 5am",
                       person.name, _plural(n, "item"))


def _frequent_flyer(repo: Repository) -> BadgeHolder | None:
    """Most visits to the site, ever -- unlike Window Shopper, this one never resets."""
    candidates = [u for u in repo.all_users() if u.visit_count > 0]
    if not candidates:
        return None
    winner = max(candidates, key=lambda u: u.visit_count)
    return BadgeHolder("frequent_flyer", "Frequent Flyer", "✈️", "most visits to the site, ever",
                       winner.name, _plural(winner.visit_count, "visit"))


def _big_family_energy(repo: Repository) -> BadgeHolder | None:
    candidates = [(hh_id, n) for hh_id, n in repo.dependents_count_by_household() if n > 0]
    if not candidates:
        return None
    hh_id, n = max(candidates, key=lambda t: t[1])
    household = repo.household(hh_id)
    if household is None:
        return None
    return BadgeHolder("big_family_energy", "Big Family Energy", "\U0001F46A",
                       "most dependents in one household", household.name, _plural(n, "dependent"))


def leaderboard(repo: Repository) -> list[BadgeHolder]:
    """One holder per badge category: whoever's out in front of it."""
    out = _event_badges(repo)
    shopper = _window_shopper(repo)
    if shopper is not None:
        out.append(shopper)
    out.extend(_person_item_badges(repo))
    out.extend(_extreme_item_badges(repo))
    owl = _night_owl(repo)
    if owl is not None:
        out.append(owl)
    flyer = _frequent_flyer(repo)
    if flyer is not None:
        out.append(flyer)
    family = _big_family_energy(repo)
    if family is not None:
        out.append(family)
    return out
