"""The site-wide activity feed: everyone's comings and goings, minus whatever
would spoil your own household's surprises."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from better_profanity import profanity

from . import access, clock
from .errors import DomainError
from .models import Activity, ActivityKind, User
from .repository import Repository

profanity.load_censor_words()

# Kinds tied to one household's list -- hidden from that household's own
# members, same as the Everyone page, unless the person's been revealed.
_HOUSEHOLD_SCOPED = {
    ActivityKind.ITEM_ADDED, ActivityKind.ITEM_EDITED, ActivityKind.ITEM_REMOVED,
    ActivityKind.ITEM_CLAIMED, ActivityKind.ITEM_BOUGHT, ActivityKind.ITEM_WITHDRAWN,
}

# --- talking back to Gerry ---------------------------------------------------------

REPLY_MAX_LEN = 240

_LINK_RE = re.compile(r"https?://|www\.|\b[a-z0-9-]+\.(?:com|net|org|ie|co|io|uk|ca|info|biz)\b", re.IGNORECASE)


def log(repo: Repository, kind: ActivityKind, *, household_id: int | None = None,
        person_id: int | None = None, **params) -> None:
    """Call from inside an already-open repo.write() block."""
    repo.add_activity(kind, household_id, person_id, params, clock.now())


def _visible(mine: int, revealed: set[int], a: Activity) -> bool:
    if a.kind not in _HOUSEHOLD_SCOPED:
        return True
    if a.household_id != mine:
        return True
    if a.person_id not in revealed:
        return False
    # A shared gift needs every co-owner revealed too -- one of them being
    # revealed to you isn't the other's consent to spoil what's just as much
    # theirs. Co-owners are frozen into the activity's params at log time, so
    # this still holds even if the item's since been unshared or deleted.
    co_owners = a.params.get("co_owner_ids") or ()
    return set(co_owners) <= revealed


def feed(repo: Repository, user: User, limit: int = 60) -> list[Activity]:
    """Most recent activity this user is allowed to see."""
    mine = access.household_id_of(repo, user)
    revealed = repo.revealed_person_ids(user.id)
    out = []
    for a in repo.recent_activity(limit * 4):  # over-fetch to allow for filtering
        if _visible(mine, revealed, a):
            out.append(a)
            if len(out) >= limit:
                break
    return out


def feed_since(repo: Repository, user: User, since: datetime) -> list[Activity]:
    """Everything visible to this user since a given time -- oldest first, for a digest."""
    mine = access.household_id_of(repo, user)
    revealed = repo.revealed_person_ids(user.id)
    items = [a for a in repo.activity_since(since.isoformat()) if _visible(mine, revealed, a)]
    return list(reversed(items))


def post_reply(repo: Repository, user: User, message: str) -> None:
    """A user talking back to Gerry on the activity feed. Text only, short,
    clean, no links, one a day, and only if there's something to reply to."""
    text = " ".join(message.split())
    if not text:
        raise DomainError("Say something, or don't bother.")
    if len(text) > REPLY_MAX_LEN:
        raise DomainError(f"Keep it under {REPLY_MAX_LEN} characters.")
    if _LINK_RE.search(text):
        raise DomainError("No links.")
    if profanity.contains_profanity(text):
        raise DomainError("Keep it clean.")
    person = access.own_person(repo, user)
    with repo.write():
        if not feed(repo, user, limit=1):
            raise DomainError("Nothing to reply to yet.")
        since = (clock.now() - timedelta(hours=24)).isoformat()
        if repo.has_replied_recently(person.id, since):
            raise DomainError("One a day. Save the rest for tomorrow.")
        repo.add_activity(ActivityKind.USER_REPLY, person.household_id, person.id,
                          {"person_name": person.name, "message": text}, clock.now())
