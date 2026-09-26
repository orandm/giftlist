"""Gerry the elf. Purely decorative, reliably rude.

Pure functions only: callers pass in what happened, the visit state and a
random source; Gerry returns a popup or None.
"""

from __future__ import annotations

import random
import string
from dataclasses import dataclass
from datetime import date
from enum import Enum

from .money import format_amount

# thresholds
TINY_SHARE = 0.25          # chip-in under this fraction of the price
CHEAP_MINOR = 20_00        # claiming something under €20
SPLIT_CHEAP_MINOR = 40_00  # splitting something under €40
PRICEY_MINOR = 75_00       # adding a wish over €75
LONG_LIST = 8              # items on one list
BROWSE_VISITS = 3          # visits in a row without claiming anything

# frequency
NORMAL_CHANCE = 1 / 2
GHOST_CHANCE = 2 / 3
GHOST_HAUNT_CHANCE = 1 / 4  # plain page loads while banished
NORMAL_PER_VISIT = 3
GHOST_PER_VISIT = 9


class Trigger(Enum):
    TINY_CHIP_IN = "tiny_chip_in"
    CHEAP_CLAIM = "cheap_claim"
    SPLIT_CHEAP = "split_cheap"
    PRICEY_WISH = "pricey_wish"
    LONG_LIST = "long_list"
    BACKING_OUT = "backing_out"
    LOWERED_SHARE = "lowered_share"
    MARKED_BOUGHT = "marked_bought"
    SULK_AVERTED = "sulk_averted"
    LATE_CLAIM = "late_claim"
    EARLY_VISIT = "early_visit"
    BROWSING = "browsing"
    GHOST_HAUNT = "ghost_haunt"
    BANISHED = "banished"
    APOLOGY = "apology"


LINES: dict[Trigger, tuple[str, ...]] = {
    Trigger.TINY_CHIP_IN: (
        "{amount}? Did you find that down the back of the couch?",
        "That's not a contribution, that's a tip.",
        "{amount} towards {item}. Stop, you'll bankrupt yourself.",
        "Put that on your headstone: chipped in {amount}.",
    ),
    Trigger.CHEAP_CLAIM: (
        "{amount}. Pushing the boat out, are we?",
        "It's the thought that counts. You didn't think much.",
        "Grand. {owner} will re-gift it by Easter.",
    ),
    Trigger.SPLIT_CHEAP: (
        "Halves on a {price} thing? Sound, big spender.",
        "You're splitting {price}. Unreal.",
    ),
    Trigger.PRICEY_WISH: (
        "{price}? Is this a wish list or a mortgage application?",
        "Santa's not a bank. Neither is your family.",
        "{price}. Bold. Do they even like you that much?",
    ),
    Trigger.LONG_LIST: (
        "Leave some Christmas for the rest of us.",
        "{count} items. Greedy's a strong word. Accurate, though.",
    ),
    Trigger.BACKING_OUT: (
        "Backing out. Classic.",
        "Ah here. Commitment issues, is it?",
        "{owner} will be gutted. I won't, I never liked you.",
    ),
    Trigger.LOWERED_SHARE: (
        "Shrinking your share. Very festive.",
        "Tightening the purse strings? In this economy? Fair.",
    ),
    Trigger.MARKED_BOUGHT: (
        "You actually bought something? Mark the calendar.",
        "Keep the receipt. For when they hate it.",
    ),
    Trigger.SULK_AVERTED: ("Sulk averted. Hero. I suppose.",),
    Trigger.LATE_CLAIM: (
        "Cutting it fine, aren't we?",
        "Hope it's in stock. It won't be.",
    ),
    Trigger.EARLY_VISIT: ("It's not even December. Get a hobby.",),
    Trigger.BROWSING: (
        "Just looking? This isn't a museum.",
        "Three visits, zero gifts. Impressive, in a way.",
    ),
    Trigger.BANISHED: ("Banish me? Grand. Now I'm a ghost, and I'm everywhere.",),
    Trigger.APOLOGY: ("Apology accepted. Barely.",),
}

GHOST_LINES: tuple[str, ...] = (
    "Ooooh. Still here. Still judging.",
    "You can't banish Christmas spirit. Literally.",
    "Boo. That one's for the {amount}.",
    "Haunting you is the most fun I've had in years.",
    "I've seen your chip-ins from the other side. Tragic.",
    "Say sorry and I might go back to normal. Might.",
)


@dataclass(slots=True, frozen=True)
class Popup:
    text: str
    ghost: bool
    template: str  # the unfilled line, remembered so it isn't repeated next time


# --- what counts as a trigger --------------------------------------------------

def _is_late(today: date) -> bool:
    return today.month == 12 and 20 <= today.day <= 24


def claim_trigger(price_minor: int, amount_minor: int, remaining_before_minor: int,
                  claimed_after_minor: int, really_want: bool, today: date) -> Trigger | None:
    """Most insulting applicable reason first."""
    if amount_minor < price_minor * TINY_SHARE:
        return Trigger.TINY_CHIP_IN
    if amount_minor < remaining_before_minor and price_minor < SPLIT_CHEAP_MINOR:
        return Trigger.SPLIT_CHEAP
    if amount_minor == price_minor and price_minor < CHEAP_MINOR:
        return Trigger.CHEAP_CLAIM
    if really_want and claimed_after_minor >= price_minor:
        return Trigger.SULK_AVERTED
    if _is_late(today):
        return Trigger.LATE_CLAIM
    return None


def share_change_trigger(previous_minor: int, new_minor: int) -> Trigger | None:
    if new_minor == 0:
        return Trigger.BACKING_OUT
    if new_minor < previous_minor:
        return Trigger.LOWERED_SHARE
    return None


def add_item_trigger(price_minor: int, items_on_list: int) -> Trigger | None:
    if price_minor > PRICEY_MINOR:
        return Trigger.PRICEY_WISH
    if items_on_list >= LONG_LIST:
        return Trigger.LONG_LIST
    return None


def visit_trigger(today: date, visits_since_claim: int) -> Trigger | None:
    if 9 <= today.month <= 11:
        return Trigger.EARLY_VISIT
    if visits_since_claim >= BROWSE_VISITS:
        return Trigger.BROWSING
    return None


# --- the decision ----------------------------------------------------------------

def _fillable(line: str, ctx: dict[str, str]) -> bool:
    names = {f for _, f, _, _ in string.Formatter().parse(line) if f}
    return names <= ctx.keys()


def _pick(lines: tuple[str, ...], ctx: dict[str, str], last: str | None, rng: random.Random) -> str | None:
    usable = [l for l in lines if _fillable(l, ctx)]
    fresh = [l for l in usable if l != last] or usable
    return rng.choice(fresh) if fresh else None


def context(currency: str = "EUR", *, owner: str | None = None, item: str | None = None,
            amount_minor: int | None = None, price_minor: int | None = None, count: int | None = None) -> dict[str, str]:
    ctx: dict[str, str] = {}
    if owner:
        ctx["owner"] = owner
    if item:
        ctx["item"] = item
    if amount_minor is not None:
        ctx["amount"] = format_amount(amount_minor, currency)
    if price_minor is not None:
        ctx["price"] = format_amount(price_minor, currency)
    if count is not None:
        ctx["count"] = str(count)
    return ctx


def decide(trigger: Trigger | None, ctx: dict[str, str], *, ghost: bool, shown_this_visit: int,
           last_line: str | None, rng: random.Random,
           chance: float | None = None) -> Popup | None:
    if trigger is None:
        return None
    # replies to pressing his buttons always land
    if trigger is Trigger.BANISHED:
        return Popup(LINES[trigger][0], True, LINES[trigger][0])
    if trigger is Trigger.APOLOGY:
        return Popup(LINES[trigger][0], False, LINES[trigger][0])
    if shown_this_visit >= (GHOST_PER_VISIT if ghost else NORMAL_PER_VISIT):
        return None
    if rng.random() >= (chance if chance is not None else (GHOST_CHANCE if ghost else NORMAL_CHANCE)):
        return None
    lines = GHOST_LINES if ghost else LINES.get(trigger, ())
    line = _pick(lines, ctx, last_line, rng)
    if line is None:
        return None
    return Popup(line.format_map(ctx), ghost, line)
