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
TINY_SHARE = 0.50          # chip-in under this fraction of the price
CHEAP_MINOR = 10_00        # claiming something under €10
SPLIT_CHEAP_MINOR = 30_00  # splitting something under €30
PRICEY_MINOR = 75_00       # adding a wish over €75
LONG_LIST = 5              # items on one list
BROWSE_VISITS = 2          # visits in a row without claiming anything

# frequency
NORMAL_CHANCE = 0.30
GHOST_CHANCE = 1.0
GHOST_HAUNT_CHANCE = 1 / 4  # plain page loads while banished


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
    GIFT_SHARED = "gift_shared"
    VOUCHER_TOPUP = "voucher_topup"


LINES: dict[Trigger, tuple[str, ...]] = {
    Trigger.TINY_CHIP_IN: (
        "{amount}? Did you find that down the back of the couch?",
        "That's not a contribution, that's a tip.",
        "{amount} towards {item}. Stop, you'll bankrupt yourself.",
        "Put that on your headstone: chipped in {amount}.",
        "Generous. If this were 1990s Monopoly money.",
        "{amount}? Gerry felt that in his knees, from laughing.",
        "The elves give more than that, and they work for free.",
        "Big spender energy: {amount}.",
        "{amount} towards {item}. A moment of silence, please.",
    ),
    Trigger.CHEAP_CLAIM: (
        "{amount}. Pushing the boat out, are we?",
        "It's the thought that counts. You didn't think much.",
        "Grand. {owner} will re-gift it by Easter.",
        "{amount}. {owner} is thrilled. Devastated, but thrilled.",
        "You call that a gift? I call it a formality.",
        "Cheap and cheerful. Mostly cheap.",
    ),
    Trigger.SPLIT_CHEAP: (
        "Halves on a {price} thing? Sound, big spender.",
        "You're splitting {price}. Unreal.",
        "Splitting {price} between you. Teamwork makes the tightwad dream work.",
        "Two of you, and it still came to {price}. Impressive, somehow.",
    ),
    Trigger.PRICEY_WISH: (
        "{price}? Is this a wish list or a mortgage application?",
        "Santa's not a bank. Neither is your family.",
        "{price}. Bold. Do they even like you that much?",
        "{price}? Who do you think you are, Santa's accountant?",
        "Put it on the good list. The bill's coming regardless.",
        "{price}? Somebody's been very good, or very delusional.",
    ),
    Trigger.LONG_LIST: (
        "Leave some Christmas for the rest of us.",
        "{count} items. Greedy's a strong word. Accurate, though.",
        "{count} items and counting. Make a wish, not an inventory.",
        "At this stage just send them the whole shop.",
    ),
    Trigger.BACKING_OUT: (
        "Backing out. Classic.",
        "Ah here. Commitment issues, is it?",
        "{owner} will be gutted. I won't, I never liked you.",
        "And there it goes. Commitment: not your strong suit.",
        "Pulled the plug on {item}. {owner} will never know. I'll tell them.",
        "Gerry's disappointed. Gerry's rarely surprised.",
    ),
    Trigger.LOWERED_SHARE: (
        "Shrinking your share. Very festive.",
        "Tightening the purse strings? In this economy? Fair.",
        "Downsizing the generosity on {item}. Bold strategy.",
        "Every cent counts, apparently. Every single one.",
    ),
    Trigger.MARKED_BOUGHT: (
        "You actually bought something? Mark the calendar.",
        "Keep the receipt. For when they hate it.",
        "Wonders never cease. You actually followed through.",
        "Gerry's shocked. Pleasantly. Briefly.",
    ),
    Trigger.SULK_AVERTED: (
        "Sulk averted. Hero. I suppose.",
        "Crisis averted. Nobody had to hear about it. This time.",
        "Tantrum cancelled. You're welcome, everyone.",
    ),
    Trigger.LATE_CLAIM: (
        "Cutting it fine, aren't we?",
        "Hope it's in stock. It won't be.",
        "Last minute is a lifestyle for you, isn't it?",
        "The elves are already exhausted. Now so am I.",
    ),
    Trigger.EARLY_VISIT: (
        "It's not even December. Get a hobby.",
        "Checking in already? Get a hobby. Or a therapist.",
        "Christmas isn't a sprint. Slow down, freak.",
    ),
    Trigger.BROWSING: (
        "Just looking? This isn't a museum.",
        "Three visits, zero gifts. Impressive, in a way.",
        "Window shopping again? The window's getting tired.",
        "You've seen every list twice. Buy something.",
        "Nobody's claimed a thing. Tragic, really.",
    ),
    Trigger.BANISHED: ("Banish me? Grand. Now I'm a ghost, and I'm everywhere.",),
    Trigger.APOLOGY: ("Apology accepted. Barely.",),
    Trigger.GIFT_SHARED: (
        "Sharing {item} with {owner}? Nothing says love like splitting the bill.",
        "{owner}'s in on {item} now. Efficient. Very married of you.",
        "Joint custody of {item}. How romantic.",
        "Two names on one gift. Gerry respects the economy of it.",
        "{owner} just got roped into {item}. Consent was implied, apparently.",
        "Sharing is caring, or so they tell me. Mostly it's just cheaper.",
        "One gift, two people, half the effort each. Maths.",
    ),
    Trigger.VOUCHER_TOPUP: (
        "{amount} into a voucher. The laziest gift dressed up as generosity.",
        "Cash by another name. {owner} will be thrilled. Or insulted. Toss a coin.",
        "{amount} chucked at {item}. No thought required, and it shows.",
        "A voucher. Bold move, showing your working.",
        "{amount} more into the pot. At least you're consistent.",
        "Congratulations, you've reinvented giving money in an envelope.",
        "{amount}? Generous, or just couldn't be bothered shopping. Six of one.",
    ),
}

GHOST_LINES: tuple[str, ...] = (
    "Ooooh. Still here. Still judging.",
    "You can't banish Christmas spirit. Literally.",
    "Boo. That one's for the {amount}.",
    "Haunting you is the most fun I've had in years.",
    "I've seen your chip-ins from the other side. Tragic.",
    "Say sorry and I might go back to normal. Might.",
    "Still nothing? Shocking. Absolutely shocking.",
    "I'm not haunting you. I'm just always here. Forever.",
    "Boo. Also: buy something.",
    "Death hasn't improved your taste, {owner}.",
    "{price}? I'm dead, not blind.",
)

PUNISHMENT_LINES: tuple[str, ...] = (
    "Oh, you're back. Google Analytics says hello.",
    "Every click here goes straight to Google. They send me a Christmas card.",
    "I've been selling your wish list to Google Analytics all morning. Cha-ching.",
    "Google Analytics called. They said thanks for the data.",
    "Don't mind me, just forwarding your browsing habits to Google.",
    "Fun fact: Google Analytics knows what you want for Christmas now. From me.",
    "Google Analytics just texted. They want more detail on your wish list.",
    "I'm basically a Google Analytics intern at this point. Unpaid. Thriving.",
    "Your every click is a data point. I'm the one cashing the cheque.",
    "Congratulations, you're a very engaged user. Google Analytics loves that.",
    "I sold your browsing history for a candy cane. Worth it.",
    "Somewhere, a Google server just got slightly happier because of you.",
    "I've got a dashboard. It's just you, all day, every day.",
    "Google Analytics knows you're reading this right now. So do I. Small world.",
)

UNCLAIMED_LINES: tuple[str, ...] = (
    "Nobody's claimed this. Tragic.",
    "Sitting here unloved. Same as every year.",
    "Zero claims. Gerry's not surprised.",
    "Still up for grabs. Nobody's grabbing.",
    "Not a single taker. Ouch.",
    "This one's gathering dust.",
    "Crickets. Actual crickets.",
    "Unclaimed and unloved. Do something about it.",
    "Nobody wants the responsibility, apparently.",
    "Waiting. Still waiting.",
)


def unclaimed_line(rng: random.Random) -> str:
    """A rude aside shown on an item nobody's claimed yet."""
    return rng.choice(UNCLAIMED_LINES)


DIGEST_INTROS: tuple[str, ...] = (
    "Right, here's what you missed while you weren't obsessively refreshing the page.",
    "Gerry's nightly report. Try to contain your excitement.",
    "Another day, another pile of Christmas nonsense. Here's the damage.",
    "Settle in. Here's what everyone's been up to.",
    "Your daily dose of other people's business, delivered by yours truly.",
    "I've been watching. Here's today's gossip.",
)

DIGEST_OUTROS: tuple[str, ...] = (
    "That's your lot. Go do something useful with it.",
    "Same time tomorrow, assuming anyone does anything worth reporting.",
    "Gerry out. Try to keep up.",
    "Don't say I never tell you anything.",
    "Right, I'm off. Some of us have judging to do.",
)


def digest_sprite(rng: random.Random) -> str:
    """Which Gerry sprite fronts today's email."""
    return rng.choice(("smug", "eyeroll", "grumpy"))


@dataclass(slots=True, frozen=True)
class Popup:
    text: str
    ghost: bool
    template: str  # the unfilled line, remembered so it isn't repeated next time


# --- what counts as a trigger --------------------------------------------------

def _is_late(today: date) -> bool:
    return today.month == 12 and 10 <= today.day <= 24


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


def decide(trigger: Trigger | None, ctx: dict[str, str], *, ghost: bool,
           last_line: str | None, rng: random.Random,
           chance: float | None = None) -> Popup | None:
    """No cap on how often he shows up in a visit -- just a per-trigger chance,
    except the big ones (banish/apology replies), which always land."""
    if trigger is None:
        return None
    if trigger is Trigger.BANISHED:
        return Popup(LINES[trigger][0], True, LINES[trigger][0])
    if trigger is Trigger.APOLOGY:
        return Popup(LINES[trigger][0], False, LINES[trigger][0])
    if rng.random() >= (chance if chance is not None else (GHOST_CHANCE if ghost else NORMAL_CHANCE)):
        return None
    lines = GHOST_LINES if ghost else LINES.get(trigger, ())
    line = _pick(lines, ctx, last_line, rng)
    if line is None:
        return None
    return Popup(line.format_map(ctx), ghost, line)


def punishment_popup(last_line: str | None, rng: random.Random) -> Popup:
    """For punishment-mode accounts: fires on every page load, no chance roll,
    always from the fixed pool above -- never any other line."""
    choices = [line for line in PUNISHMENT_LINES if line != last_line] or list(PUNISHMENT_LINES)
    line = rng.choice(choices)
    return Popup(line, False, line)


def punishment_echoes(exclude: str, rng: random.Random, n: int = 4) -> list[str]:
    """Extra corner-ghost lines shown once a punishment-mode account banishes
    him -- distinct from the main popup's line and from each other."""
    pool = [line for line in PUNISHMENT_LINES if line != exclude]
    return rng.sample(pool, min(n, len(pool)))


# --- countdown ----------------------------------------------------------------

COUNTDOWN_LINES: dict[str, tuple[str, ...]] = {
    "chill": (
        "Plenty of time. Try not to ruin that by panicking early.",
        "Relax. For now.",
        "Loads of time left. Don't get complacent.",
    ),
    "getting_real": (
        "Getting closer. The smug 'I've plenty of time' window is closing.",
        "Two-ish weeks. Still time, less smugness advised.",
        "It's creeping up on you. I can see it in your face.",
    ),
    "urgent": (
        "Now it's urgent. Now you suddenly care.",
        "Days, not weeks. Move.",
        "This is the part where you panic. Right on schedule.",
    ),
    "panic": (
        "Tomorrow, basically. Good luck with that.",
        "This is now a crisis. Your crisis.",
    ),
    "today": (
        "It's today. It's actually today.",
        "Merry Christmas. Hope you sorted it in time.",
    ),
    "past": (
        "It's over. You're either a hero or a disgrace. No in between.",
        "Christmas happened without you rushing. Miracles do occur.",
    ),
}


def _countdown_band(days: int) -> str:
    if days > 14:
        return "chill"
    if days > 6:
        return "getting_real"
    if days > 1:
        return "urgent"
    if days == 1:
        return "panic"
    if days == 0:
        return "today"
    return "past"


# sprite (matches the gerry-*.svg files), per band -- angrier the closer it gets
COUNTDOWN_MOOD: dict[str, str] = {
    "chill": "smug",
    "getting_real": "eyeroll",
    "urgent": "grumpy",
    "panic": "grumpy",
    "today": "grumpy",
    "past": "smug",
}


def countdown(today: date, rng: random.Random) -> tuple[int, str, str]:
    """Days left until the 25th (negative once it's past), a line that gets ruder as it counts down,
    and the sprite to show him with -- he gets visibly angrier the closer Christmas gets."""
    days = (date(today.year, 12, 25) - today).days
    band = _countdown_band(days)
    return days, rng.choice(COUNTDOWN_LINES[band]), COUNTDOWN_MOOD[band]
