"""The site-wide activity feed: everyone's comings and goings, minus whatever
would spoil your own household's surprises."""

from __future__ import annotations

from datetime import datetime

from . import access, clock
from .models import Activity, ActivityKind, User
from .repository import Repository

# Kinds tied to one household's list -- hidden from that household's own
# members, same as the Everyone page, unless the person's been revealed.
_HOUSEHOLD_SCOPED = {
    ActivityKind.ITEM_ADDED, ActivityKind.ITEM_EDITED, ActivityKind.ITEM_REMOVED,
    ActivityKind.ITEM_CLAIMED, ActivityKind.ITEM_BOUGHT, ActivityKind.ITEM_WITHDRAWN,
}


def log(repo: Repository, kind: ActivityKind, *, household_id: int | None = None,
        person_id: int | None = None, **params) -> None:
    """Call from inside an already-open repo.write() block."""
    repo.add_activity(kind, household_id, person_id, params, clock.now())


def _visible(repo: Repository, user: User, a: Activity) -> bool:
    if a.kind not in _HOUSEHOLD_SCOPED:
        return True
    mine = access.household_id_of(repo, user)
    if a.household_id != mine:
        return True
    return a.person_id is not None and repo.is_revealed(user.id, a.person_id)


def feed(repo: Repository, user: User, limit: int = 60) -> list[Activity]:
    """Most recent activity this user is allowed to see."""
    out = []
    for a in repo.recent_activity(limit * 4):  # over-fetch to allow for filtering
        if _visible(repo, user, a):
            out.append(a)
            if len(out) >= limit:
                break
    return out


def feed_since(repo: Repository, user: User, since: datetime) -> list[Activity]:
    """Everything visible to this user since a given time -- oldest first, for a digest."""
    items = [a for a in repo.activity_since(since.isoformat()) if _visible(repo, user, a)]
    return list(reversed(items))
