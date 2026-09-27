import os
import tempfile
import threading
import unittest
from datetime import UTC, datetime
from unittest.mock import patch

from giftlist import accounts, activity, badges, claims, clock, gerry, lists
from giftlist.accounts import Invite
from giftlist.errors import DomainError, NotAllowed, NotFound, OverClaimed
from giftlist.models import ActivityKind, Funding, NoticeKind
from giftlist.money import MAX_MINOR, InvalidAmount, parse_amount
from giftlist.sqlite_repo import SqliteRepository, _ensure_column, connect, init_db


def make_repo(path=":memory:"):
    conn = connect(path)
    init_db(conn)
    return SqliteRepository(conn)


class World(unittest.TestCase):
    """Mam & Dad's household (Máire, Seán) and Ciarán's (Ciarán, Aoife + Liam)."""

    def setUp(self):
        self.repo = make_repo()
        r = self.repo
        self.site = Invite(site_token=accounts.site_invite_token(r))
        self.maire = accounts.sign_in(r, "g-maire", "maire@x.ie", "Máire", self.site, False)
        mam_hh, _ = accounts.my_household(r, self.maire)
        self.sean = accounts.sign_in(r, "g-sean", "sean@x.ie", "Seán", Invite(household_token=mam_hh.invite_token), False)
        self.ciaran = accounts.sign_in(r, "g-ciaran", "c@x.ie", "Ciarán", self.site, False)
        c_hh, _ = accounts.my_household(r, self.ciaran)
        self.aoife = accounts.sign_in(r, "g-aoife", "a@x.ie", "Aoife", Invite(household_token=c_hh.invite_token), False)
        self.liam = accounts.add_dependent(r, self.ciaran, "Liam")
        self.p_maire = r.person_for_user(self.maire.id)
        self.p_aoife = r.person_for_user(self.aoife.id)
        self.coat = lists.add_item(r, self.maire, self.p_maire.id, "Wool coat", "x.ie/coat", None, 8000, None, True)
        self.lego = lists.add_item(r, self.ciaran, self.liam.id, "Lego", None, None, 6000, None, False)

    def view(self, viewer, item_id):
        for hh in lists.everyone(self.repo, viewer):
            for pv in hh.people:
                for v in pv.items:
                    if v.item.id == item_id:
                        return v
            for sg in hh.shared:
                for v in sg.items:
                    if v.item.id == item_id:
                        return v
        return None


class TestSignIn(World):
    def test_needs_invite(self):
        with self.assertRaises(NotAllowed):
            accounts.sign_in(self.repo, "g-x", "x@x.ie", "X", Invite(), False)
        with self.assertRaises(NotAllowed):
            accounts.sign_in(self.repo, "g-x", "x@x.ie", "X", Invite(site_token="wrong"), False)

    def test_admin_needs_no_invite(self):
        u = accounts.sign_in(self.repo, "g-admin", "boss@x.ie", "Boss", Invite(), True)
        self.assertIsNotNone(self.repo.person_for_user(u.id))

    def test_existing_user_signs_in_without_invite(self):
        again = accounts.sign_in(self.repo, "g-maire", "maire@x.ie", "Máire", Invite(), False)
        self.assertEqual(again.id, self.maire.id)

    def test_household_link_makes_co_manager(self):
        self.assertEqual(self.p_aoife.household_id, self.repo.person_for_user(self.ciaran.id).household_id)

    def test_reset_invite_kills_old_link(self):
        old = self.site
        accounts.reset_site_invite(self.repo)
        with self.assertRaises(NotAllowed):
            accounts.sign_in(self.repo, "g-y", "y@x.ie", "Y", old, False)


class TestMagicLink(World):
    def test_enroll_creates_account_without_punishment_by_default(self):
        user, token = accounts.enroll_magic(self.repo, "Kodi", "kodi@x.ie", self.site.site_token)
        self.assertEqual(user.email, "kodi@x.ie")
        self.assertFalse(user.punishment_mode)
        self.assertIsNotNone(self.repo.person_for_user(user.id))
        self.assertEqual(accounts.magic_login(self.repo, token).id, user.id)

    def test_enroll_rejects_a_bad_invite_token(self):
        with self.assertRaises(NotAllowed):
            accounts.enroll_magic(self.repo, "Kodi", "kodi@x.ie", "not-a-real-token")

    def test_enroll_rejects_a_bad_email(self):
        with self.assertRaises(DomainError):
            accounts.enroll_magic(self.repo, "Kodi", "not-an-email", self.site.site_token)

    def test_magic_login_returns_none_for_a_bogus_token(self):
        self.assertIsNone(accounts.magic_login(self.repo, "nonsense"))

    def test_regenerating_kills_the_old_token(self):
        user, old_token = accounts.enroll_magic(self.repo, "Kodi", "kodi@x.ie", self.site.site_token)
        new_token = accounts.regenerate_magic_link(self.repo, user.id)
        self.assertIsNone(accounts.magic_login(self.repo, old_token))
        self.assertEqual(accounts.magic_login(self.repo, new_token).id, user.id)

    def test_admin_can_toggle_punishment_mode_on_any_account(self):
        accounts.set_punishment_mode(self.repo, self.maire.id, True)
        self.assertTrue(self.repo.user(self.maire.id).punishment_mode)
        accounts.set_punishment_mode(self.repo, self.maire.id, False)
        self.assertFalse(self.repo.user(self.maire.id).punishment_mode)


class TestVisibility(World):
    def test_everyone_hides_own_household(self):
        names = [hh.household.name for hh in lists.everyone(self.repo, self.ciaran)]
        self.assertEqual(names, ["Máire's household"])
        people = [p.person.name for hh in lists.everyone(self.repo, self.aoife) for p in hh.people]
        self.assertNotIn("Liam", people)
        self.assertNotIn("Ciarán", people)

    def test_cannot_claim_in_own_household(self):
        with self.assertRaises(NotAllowed):
            claims.claim(self.repo, self.aoife, self.lego.id, 1000)
        with self.assertRaises(NotAllowed):
            lists.claimable_item(self.repo, self.ciaran, self.lego.id)

    def test_co_manager_cannot_edit_partner_list(self):
        mine = lists.add_item(self.repo, self.aoife, self.p_aoife.id, "Scarf", None, None, 3000, None, False)
        with self.assertRaises(NotAllowed):
            lists.edit_item(self.repo, self.ciaran, mine.id, "X", None, None, 100, None, False)
        # both co-managers can edit a dependent's list
        lists.edit_item(self.repo, self.aoife, self.lego.id, "Lego set", None, None, 6500, None, False)

    def test_other_household_sees_claims(self):
        claims.claim(self.repo, self.ciaran, self.coat.id, 3000)
        v = self.view(self.aoife, self.coat.id)
        self.assertEqual([(c.name, c.amount_minor) for c in v.contributions], [("Ciarán", 3000)])
        self.assertEqual(v.funding, Funding.PARTIAL)

    def test_reveal_lets_you_see_and_claim_a_housemates_list(self):
        scarf = lists.add_item(self.repo, self.aoife, self.p_aoife.id, "Scarf", None, None, 3000, None, False)
        self.assertNotIn("Aoife", [p.person.name for hh in lists.everyone(self.repo, self.ciaran) for p in hh.people])
        with self.assertRaises(NotAllowed):
            claims.claim(self.repo, self.ciaran, scarf.id, 3000)

        accounts.set_reveal(self.repo, self.ciaran, self.p_aoife.id, True)
        self.assertIn("Aoife", [p.person.name for hh in lists.everyone(self.repo, self.ciaran) for p in hh.people])
        claims.claim(self.repo, self.ciaran, scarf.id, 3000)  # no longer raises

    def test_unrevealing_drops_your_claim(self):
        scarf = lists.add_item(self.repo, self.aoife, self.p_aoife.id, "Scarf", None, None, 3000, None, False)
        accounts.set_reveal(self.repo, self.ciaran, self.p_aoife.id, True)
        claims.claim(self.repo, self.ciaran, scarf.id, 3000)
        accounts.set_reveal(self.repo, self.ciaran, self.p_aoife.id, False)
        self.assertEqual(self.repo.claims_for_item(scarf.id), [])

    def test_cannot_reveal_self_or_dependent(self):
        p_ciaran = self.repo.person_for_user(self.ciaran.id)
        with self.assertRaises(NotAllowed):
            accounts.set_reveal(self.repo, self.ciaran, p_ciaran.id, True)
        with self.assertRaises(NotAllowed):
            accounts.set_reveal(self.repo, self.ciaran, self.liam.id, True)


class TestClaims(World):
    def test_split_and_full(self):
        claims.claim(self.repo, self.ciaran, self.coat.id, 5000)
        claims.claim(self.repo, self.aoife, self.coat.id, 3000)
        self.assertEqual(self.view(self.ciaran, self.coat.id).funding, Funding.FULL)

    def test_over_remaining_rejected(self):
        claims.claim(self.repo, self.ciaran, self.coat.id, 5000)
        with self.assertRaises(OverClaimed):
            claims.claim(self.repo, self.aoife, self.coat.id, 3001)

    def test_no_double_claim(self):
        claims.claim(self.repo, self.ciaran, self.coat.id, 1000)
        with self.assertRaises(DomainError):
            claims.claim(self.repo, self.ciaran, self.coat.id, 1000)

    def test_bought_needs_full_cover(self):
        claims.claim(self.repo, self.ciaran, self.coat.id, 5000)
        with self.assertRaises(DomainError):
            claims.mark_bought(self.repo, self.ciaran, self.coat.id)
        claims.claim(self.repo, self.aoife, self.coat.id, 3000)
        claims.mark_bought(self.repo, self.aoife, self.coat.id)
        self.assertTrue(self.repo.item(self.coat.id).is_bought)

    def test_only_claimers_mark_bought(self):
        claims.claim(self.repo, self.ciaran, self.coat.id, 8000)
        with self.assertRaises(NotAllowed):
            claims.mark_bought(self.repo, self.aoife, self.coat.id)

    def test_change_after_bought_leaves_shortfall_others_can_top_up(self):
        claims.claim(self.repo, self.ciaran, self.coat.id, 5000)
        claims.claim(self.repo, self.aoife, self.coat.id, 3000)
        claims.mark_bought(self.repo, self.ciaran, self.coat.id)
        claims.update_claim(self.repo, self.aoife, self.coat.id, 1000)
        v = self.view(self.ciaran, self.coat.id)
        self.assertTrue(v.item.is_bought)
        self.assertEqual(v.remaining_minor, 2000)
        claims.update_claim(self.repo, self.ciaran, self.coat.id, 7000)  # top up
        self.assertEqual(self.view(self.ciaran, self.coat.id).remaining_minor, 0)

    def test_last_claimer_withdrawing_after_bought_un_marks_it(self):
        claims.claim(self.repo, self.ciaran, self.coat.id, 8000)
        claims.mark_bought(self.repo, self.ciaran, self.coat.id)
        claims.withdraw(self.repo, self.ciaran, self.coat.id)
        v = self.view(self.aoife, self.coat.id)
        self.assertFalse(v.item.is_bought)
        self.assertEqual(v.contributions, ())

    def test_one_of_several_claimers_withdrawing_after_bought_stays_bought(self):
        claims.claim(self.repo, self.ciaran, self.coat.id, 5000)
        claims.claim(self.repo, self.aoife, self.coat.id, 3000)
        claims.mark_bought(self.repo, self.ciaran, self.coat.id)
        claims.withdraw(self.repo, self.aoife, self.coat.id)
        v = self.view(self.ciaran, self.coat.id)
        self.assertTrue(v.item.is_bought)
        self.assertEqual(v.remaining_minor, 3000)

    def test_co_claimers_notified_on_change_and_withdraw(self):
        claims.claim(self.repo, self.ciaran, self.coat.id, 4000)
        claims.claim(self.repo, self.aoife, self.coat.id, 4000)
        claims.update_claim(self.repo, self.aoife, self.coat.id, 2000)
        claims.withdraw(self.repo, self.aoife, self.coat.id)
        kinds = [n.kind for n in claims.notices(self.repo, self.ciaran)]
        self.assertEqual(kinds, [NoticeKind.SHARE_WITHDRAWN, NoticeKind.SHARE_CHANGED])
        self.assertEqual(claims.notices(self.repo, self.aoife), [])
        n = claims.notices(self.repo, self.ciaran)[0]
        self.assertEqual(n.params["remaining_minor"], 4000)

    def test_price_drop_over_claimed_and_reducible(self):
        claims.claim(self.repo, self.ciaran, self.coat.id, 4000)
        claims.claim(self.repo, self.aoife, self.coat.id, 4000)
        lists.edit_item(self.repo, self.maire, self.coat.id, "Wool coat", None, None, 5000, None, True)
        self.assertEqual(self.view(self.ciaran, self.coat.id).funding, Funding.OVER)
        claims.update_claim(self.repo, self.ciaran, self.coat.id, 3000)  # still over, allowed
        claims.update_claim(self.repo, self.aoife, self.coat.id, 2000)
        self.assertEqual(self.view(self.ciaran, self.coat.id).funding, Funding.FULL)

    def test_concurrent_last_claim_one_wins(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.db")
            repo = make_repo(path)
            site = Invite(site_token=accounts.site_invite_token(repo))
            owner = accounts.sign_in(repo, "o", "o@x", "O", site, False)
            b = accounts.sign_in(repo, "b", "b@x", "B", site, False)
            c = accounts.sign_in(repo, "c", "c@x", "C", site, False)
            item = lists.add_item(repo, owner, repo.person_for_user(owner.id).id, "Lamp", None, None, 2000, None, False)
            barrier, results = threading.Barrier(2), []

            def go(u):
                r = make_repo(path)
                barrier.wait()
                try:
                    claims.claim(r, u, item.id, 2000)
                    results.append("ok")
                except OverClaimed:
                    results.append("no")

            ts = [threading.Thread(target=go, args=(u,)) for u in (b, c)]
            [t.start() for t in ts]
            [t.join() for t in ts]
            self.assertEqual(sorted(results), ["no", "ok"])


class TestRemoval(World):
    def test_owner_removes_claimed_item(self):
        claims.claim(self.repo, self.ciaran, self.coat.id, 4000)
        lists.delete_item(self.repo, self.maire, self.coat.id)
        [n] = claims.notices(self.repo, self.ciaran)
        self.assertEqual(n.kind, NoticeKind.ITEM_REMOVED)
        self.assertFalse(n.params["bought"])
        self.assertEqual(claims.my_claims(self.repo, self.ciaran), [])

    def test_owner_cannot_remove_an_already_bought_item(self):
        claims.claim(self.repo, self.ciaran, self.coat.id, 8000)
        claims.mark_bought(self.repo, self.ciaran, self.coat.id)
        with self.assertRaises(DomainError):
            lists.delete_item(self.repo, self.maire, self.coat.id)
        self.assertIsNotNone(self.repo.item(self.coat.id))
        [n] = claims.notices(self.repo, self.ciaran)
        self.assertEqual(n.kind, NoticeKind.REMOVE_BLOCKED)
        self.assertEqual(n.params["item"], "Wool coat")
        self.assertEqual(n.params["item_id"], self.coat.id)

    def test_admin_renames_any_household(self):
        hh, _ = accounts.my_household(self.repo, self.ciaran)
        accounts.admin_rename_household(self.repo, hh.id, "The Ciarán Clan")
        renamed, _ = accounts.my_household(self.repo, self.aoife)
        self.assertEqual(renamed.name, "The Ciarán Clan")

    def test_admin_rename_household_rejects_missing(self):
        with self.assertRaises(NotFound):
            accounts.admin_rename_household(self.repo, 999999, "Ghosts")

    def test_admin_renames_a_user_and_their_person_together(self):
        accounts.admin_rename_user(self.repo, self.ciaran.id, "CJ")
        self.assertEqual(self.repo.user(self.ciaran.id).name, "CJ")
        self.assertEqual(self.repo.person_for_user(self.ciaran.id).name, "CJ")

    def test_admin_rename_user_rejects_missing(self):
        with self.assertRaises(NotFound):
            accounts.admin_rename_user(self.repo, 999999, "Ghost")

    def test_admin_removes_user_frees_their_claims_and_notifies(self):
        claims.claim(self.repo, self.ciaran, self.coat.id, 4000)
        claims.claim(self.repo, self.aoife, self.coat.id, 4000)
        accounts.remove_user(self.repo, self.ciaran.id)
        self.assertIsNone(self.repo.user(self.ciaran.id))
        self.assertEqual(self.view(self.maire, self.lego.id).item.title, "Lego")  # Aoife still runs the household
        self.assertEqual([n.kind for n in claims.notices(self.repo, self.aoife)], [NoticeKind.SHARE_WITHDRAWN])

    def test_last_manager_removed_takes_dependents(self):
        claims.claim(self.repo, self.maire, self.lego.id, 1000)
        accounts.remove_user(self.repo, self.aoife.id)
        accounts.remove_user(self.repo, self.ciaran.id)
        self.assertIsNone(self.repo.person(self.liam.id))
        [n] = claims.notices(self.repo, self.maire)
        self.assertEqual(n.params["item"], "Lego")

    def test_leave_household_splits_off_solo(self):
        accounts.leave_household(self.repo, self.sean)
        _, new_people = accounts.my_household(self.repo, self.sean)
        self.assertEqual([p.name for p in new_people], ["Seán"])
        _, old_people = accounts.my_household(self.repo, self.maire)
        self.assertEqual([p.name for p in old_people], ["Máire"])

    def test_cannot_leave_if_sole_manager(self):
        boss = accounts.sign_in(self.repo, "g-boss", "boss@x.ie", "Boss", Invite(), True)
        with self.assertRaises(NotAllowed):
            accounts.leave_household(self.repo, boss)  # already the only one there

    def test_cannot_leave_household_with_dependents_even_with_a_co_manager(self):
        # Ciarán's household has Aoife as a co-manager, but also Liam, a dependent
        with self.assertRaises(NotAllowed):
            accounts.leave_household(self.repo, self.ciaran)

    def test_cannot_leave_if_sole_manager_with_a_dependent(self):
        boss = accounts.sign_in(self.repo, "g-boss", "boss@x.ie", "Boss", Invite(), True)
        accounts.add_dependent(self.repo, boss, "Kid")
        with self.assertRaises(NotAllowed):
            accounts.leave_household(self.repo, boss)

    def test_joining_household_purges_claims_inside_it(self):
        site = self.site
        niamh = accounts.sign_in(self.repo, "g-n", "n@x", "Niamh", site, False)
        claims.claim(self.repo, niamh, self.lego.id, 1000)
        claims.claim(self.repo, self.maire, self.lego.id, 1000)
        hh, _ = accounts.my_household(self.repo, self.ciaran)
        accounts.join_household(self.repo, niamh, hh.invite_token)
        self.assertEqual([c.user_id for c in self.repo.claims_for_item(self.lego.id)], [self.maire.id])

    def test_joining_brings_your_whole_household_with_you(self):
        mam_hh, _ = accounts.my_household(self.repo, self.maire)
        c_hh, _ = accounts.my_household(self.repo, self.ciaran)
        accounts.join_household(self.repo, self.ciaran, mam_hh.invite_token)
        merged, people = accounts.my_household(self.repo, self.ciaran)
        self.assertEqual(merged.id, mam_hh.id)
        self.assertEqual({p.name for p in people}, {"Máire", "Seán", "Ciarán", "Aoife", "Liam"})
        self.assertIsNone(self.repo.household(c_hh.id))

    def test_new_season_clears_lists_keeps_people(self):
        claims.claim(self.repo, self.ciaran, self.coat.id, 1000)
        accounts.new_season(self.repo)
        self.assertEqual(self.repo.items_for_person(self.p_maire.id), [])
        self.assertIsNotNone(self.repo.person(self.liam.id))
        self.assertEqual(claims.my_claims(self.repo, self.ciaran), [])


class TestHouseholdGifts(World):
    def test_sharing_makes_it_editable_by_both_and_shows_on_both_lists(self):
        p_sean = self.repo.person_for_user(self.sean.id)
        item, target, sharing_now = lists.toggle_share(self.repo, self.maire, self.coat.id, p_sean.id)
        self.assertTrue(sharing_now)
        self.assertEqual(target.id, p_sean.id)
        self.assertIn(self.coat.id, [i.id for i in lists.shared_items_for_editing(self.repo, self.sean, p_sean.id)])
        lists.edit_item(self.repo, self.sean, self.coat.id, "Wool coat", None, None, 9000, None, True)
        self.assertEqual(self.repo.item(self.coat.id).price_minor, 9000)

    def test_unsharing_removes_it_from_the_other_list(self):
        p_sean = self.repo.person_for_user(self.sean.id)
        lists.toggle_share(self.repo, self.maire, self.coat.id, p_sean.id)
        _, _, sharing_now = lists.toggle_share(self.repo, self.maire, self.coat.id, p_sean.id)
        self.assertFalse(sharing_now)
        self.assertEqual(lists.shared_items_for_editing(self.repo, self.sean, p_sean.id), [])
        with self.assertRaises(NotAllowed):
            lists.edit_item(self.repo, self.sean, self.coat.id, "X", None, None, 100, None, False)

    def test_cannot_share_outside_your_own_household(self):
        with self.assertRaises(NotFound):
            lists.toggle_share(self.repo, self.maire, self.coat.id, self.p_aoife.id)

    def test_only_the_owner_can_manage_sharing(self):
        p_sean = self.repo.person_for_user(self.sean.id)
        with self.assertRaises(NotAllowed):
            lists.toggle_share(self.repo, self.sean, self.coat.id, p_sean.id)

    def test_leaving_household_drops_a_now_cross_household_share(self):
        p_sean = self.repo.person_for_user(self.sean.id)
        lists.toggle_share(self.repo, self.maire, self.coat.id, p_sean.id)
        accounts.leave_household(self.repo, self.sean)
        self.assertEqual(self.repo.people_sharing_item(self.coat.id), [])
        with self.assertRaises(NotAllowed):
            lists.edit_item(self.repo, self.sean, self.coat.id, "X", None, None, 100, None, False)


class TestSharedGiftReveal(World):
    """A third household-mate revealing just one co-owner of a shared gift
    shouldn't be able to see or claim on it -- the other co-owner never
    consented to being spoiled to that viewer."""

    def setUp(self):
        super().setUp()
        mam_hh, _ = accounts.my_household(self.repo, self.maire)
        self.nora = accounts.sign_in(self.repo, "g-nora", "nora@x.ie", "Nóra",
                                     Invite(household_token=mam_hh.invite_token), False)
        self.p_sean = self.repo.person_for_user(self.sean.id)
        lists.toggle_share(self.repo, self.maire, self.coat.id, self.p_sean.id)

    def _maire_items_seen_by_nora(self):
        views = lists.everyone(self.repo, self.nora)
        hh = next(v for v in views if v.household.id == self.p_maire.household_id)
        maire_view = next((pv for pv in hh.people if pv.person.id == self.p_maire.id), None)
        ids = [v.item.id for v in maire_view.items] if maire_view else []
        for sg in hh.shared:
            if any(p.id == self.p_maire.id for p in sg.people):
                ids.extend(v.item.id for v in sg.items)
        return ids

    def test_revealing_only_one_co_owner_still_hides_the_shared_gift(self):
        accounts.set_reveal(self.repo, self.nora, self.p_maire.id, True)
        self.assertNotIn(self.coat.id, self._maire_items_seen_by_nora())

    def test_revealing_both_co_owners_shows_the_shared_gift(self):
        accounts.set_reveal(self.repo, self.nora, self.p_maire.id, True)
        accounts.set_reveal(self.repo, self.nora, self.p_sean.id, True)
        self.assertIn(self.coat.id, self._maire_items_seen_by_nora())

    def test_cannot_claim_shared_gift_with_only_one_co_owner_revealed(self):
        accounts.set_reveal(self.repo, self.nora, self.p_maire.id, True)
        with self.assertRaises(NotAllowed):
            claims.claim(self.repo, self.nora, self.coat.id, 1000)

    def test_can_claim_shared_gift_once_both_co_owners_revealed(self):
        accounts.set_reveal(self.repo, self.nora, self.p_maire.id, True)
        accounts.set_reveal(self.repo, self.nora, self.p_sean.id, True)
        outcome = claims.claim(self.repo, self.nora, self.coat.id, 1000)
        self.assertEqual(outcome.amount_minor, 1000)

    def test_unshared_item_is_unaffected_by_the_extra_check(self):
        scarf = lists.add_item(self.repo, self.maire, self.p_maire.id, "Scarf", None, None, 2000, None, False)
        accounts.set_reveal(self.repo, self.nora, self.p_maire.id, True)
        self.assertIn(scarf.id, self._maire_items_seen_by_nora())
        outcome = claims.claim(self.repo, self.nora, scarf.id, 500)
        self.assertEqual(outcome.amount_minor, 500)


class TestEveryoneSharedGrouping(World):
    """A shared gift shows once on the Everyone page, grouped under all its
    co-owners' names, separate from each person's own individual gifts."""

    def setUp(self):
        super().setUp()
        self.p_sean = self.repo.person_for_user(self.sean.id)
        lists.toggle_share(self.repo, self.maire, self.coat.id, self.p_sean.id)
        self.scarf = lists.add_item(self.repo, self.maire, self.p_maire.id, "Scarf", None, None, 2000, None, False)

    def _household_view(self):
        views = lists.everyone(self.repo, self.ciaran)
        return next(v for v in views if v.household.id == self.p_maire.household_id)

    def test_shared_gift_grouped_under_combined_name(self):
        hh = self._household_view()
        self.assertEqual(len(hh.shared), 1)
        group = hh.shared[0]
        self.assertEqual(group.name, "Máire and Seán")
        self.assertEqual([v.item.id for v in group.items], [self.coat.id])

    def test_shared_gift_not_duplicated_under_the_owner(self):
        hh = self._household_view()
        maire_view = next(pv for pv in hh.people if pv.person.id == self.p_maire.id)
        self.assertNotIn(self.coat.id, [v.item.id for v in maire_view.items])

    def test_individual_gifts_still_shown_per_person(self):
        hh = self._household_view()
        maire_view = next(pv for pv in hh.people if pv.person.id == self.p_maire.id)
        self.assertEqual([v.item.id for v in maire_view.items], [self.scarf.id])

    def test_co_owners_status_counts_their_shared_gifts_too(self):
        hh = self._household_view()
        sean_view = next(pv for pv in hh.people if pv.person.id == self.p_sean.id)
        self.assertEqual(sean_view.items, ())
        self.assertEqual(sean_view.still_needed, 1)


class TestVouchers(World):
    def test_contributions_are_not_capped_at_the_price(self):
        voucher = lists.add_item(self.repo, self.maire, self.p_maire.id, "Spa day", None, None, 5000, None, False, True)
        claims.claim(self.repo, self.ciaran, voucher.id, 3000)
        claims.claim(self.repo, self.aoife, voucher.id, 4000)  # 7000 total, over the 5000 "price"
        v = self.view(self.ciaran, voucher.id)
        self.assertEqual(v.claimed_minor, 7000)

    def test_mark_bought_needs_no_full_cover(self):
        voucher = lists.add_item(self.repo, self.maire, self.p_maire.id, "Spa day", None, None, 5000, None, False, True)
        claims.claim(self.repo, self.ciaran, voucher.id, 1000)
        claims.mark_bought(self.repo, self.ciaran, voucher.id)
        self.assertTrue(self.repo.item(voucher.id).is_bought)

    def test_non_voucher_item_still_caps_at_the_price(self):
        with self.assertRaises(OverClaimed):
            claims.claim(self.repo, self.ciaran, self.coat.id, 9000)  # coat's price is 8000


class TestActivity(World):
    def test_household_created_is_visible_to_everyone(self):
        names = [a.params.get("household_name") for a in activity.feed(self.repo, self.aoife)
                if a.kind is ActivityKind.HOUSEHOLD_CREATED]
        self.assertIn("Máire's household", names)

    def test_item_added_hidden_from_own_household_visible_to_others(self):
        self.assertTrue(any(a.kind is ActivityKind.ITEM_ADDED and a.params["item"] == "Wool coat"
                            for a in activity.feed(self.repo, self.ciaran)))
        self.assertFalse(any(a.kind is ActivityKind.ITEM_ADDED and a.params["item"] == "Wool coat"
                             for a in activity.feed(self.repo, self.maire)))
        self.assertFalse(any(a.kind is ActivityKind.ITEM_ADDED and a.params["item"] == "Wool coat"
                             for a in activity.feed(self.repo, self.sean)))

    def test_revealing_yourself_makes_your_activity_visible_to_that_viewer(self):
        scarf = lists.add_item(self.repo, self.aoife, self.p_aoife.id, "Scarf", None, None, 3000, None, False)
        self.assertFalse(any(a.params.get("item") == "Scarf" for a in activity.feed(self.repo, self.ciaran)))
        accounts.set_reveal(self.repo, self.ciaran, self.p_aoife.id, True)
        self.assertTrue(any(a.params.get("item") == "Scarf" for a in activity.feed(self.repo, self.ciaran)))

    def test_claim_bought_withdrawn_logged_and_hidden_from_owner(self):
        claims.claim(self.repo, self.ciaran, self.coat.id, 8000)
        claims.mark_bought(self.repo, self.ciaran, self.coat.id)
        claims.withdraw(self.repo, self.ciaran, self.coat.id)
        kinds = {a.kind for a in activity.feed(self.repo, self.aoife) if a.params.get("item") == "Wool coat"}
        self.assertEqual(kinds, {ActivityKind.ITEM_ADDED, ActivityKind.ITEM_CLAIMED,
                                 ActivityKind.ITEM_BOUGHT, ActivityKind.ITEM_WITHDRAWN})
        self.assertFalse(any(a.params.get("item") == "Wool coat" and a.kind is not ActivityKind.ITEM_ADDED
                             for a in activity.feed(self.repo, self.maire)))

    def test_edit_and_delete_logged(self):
        lists.edit_item(self.repo, self.maire, self.coat.id, "Wool coat", None, None, 9000, None, True)
        scarf = lists.add_item(self.repo, self.maire, self.p_maire.id, "Scarf", None, None, 1000, None, False)
        lists.delete_item(self.repo, self.maire, scarf.id)
        kinds = {a.kind for a in activity.feed(self.repo, self.ciaran) if a.params.get("item") in ("Wool coat", "Scarf")}
        self.assertIn(ActivityKind.ITEM_EDITED, kinds)
        self.assertIn(ActivityKind.ITEM_REMOVED, kinds)

    def test_new_season_clears_activity(self):
        self.assertTrue(self.repo.recent_activity(100))
        accounts.new_season(self.repo)
        self.assertEqual(self.repo.recent_activity(100), [])

    def test_feed_since_is_chronological_and_time_bounded(self):
        from datetime import timedelta
        items = activity.feed_since(self.repo, self.aoife, clock.now() - timedelta(hours=1))
        self.assertTrue(items)
        self.assertEqual([a.id for a in items], sorted(a.id for a in items))


class TestActivitySharedGiftReveal(World):
    """A shared gift's claim/bought/withdrawn activity is spoiler-gated the same
    way seeing or claiming it is: revealing just one co-owner to yourself isn't
    the other's consent, so it stays hidden until every co-owner is revealed."""

    def setUp(self):
        super().setUp()
        mam_hh, _ = accounts.my_household(self.repo, self.maire)
        self.nora = accounts.sign_in(self.repo, "g-nora", "nora@x.ie", "Nóra",
                                     Invite(household_token=mam_hh.invite_token), False)
        self.p_sean = self.repo.person_for_user(self.sean.id)
        lists.toggle_share(self.repo, self.maire, self.coat.id, self.p_sean.id)
        claims.claim(self.repo, self.ciaran, self.coat.id, 8000)
        claims.mark_bought(self.repo, self.ciaran, self.coat.id)

    def _kinds_for_coat(self, viewer):
        return {a.kind for a in activity.feed(self.repo, viewer) if a.params.get("item") == "Wool coat"}

    def test_hidden_with_only_one_co_owner_revealed(self):
        accounts.set_reveal(self.repo, self.nora, self.p_maire.id, True)
        kinds = self._kinds_for_coat(self.nora)
        self.assertNotIn(ActivityKind.ITEM_CLAIMED, kinds)
        self.assertNotIn(ActivityKind.ITEM_BOUGHT, kinds)

    def test_visible_once_both_co_owners_revealed(self):
        accounts.set_reveal(self.repo, self.nora, self.p_maire.id, True)
        accounts.set_reveal(self.repo, self.nora, self.p_sean.id, True)
        kinds = self._kinds_for_coat(self.nora)
        self.assertIn(ActivityKind.ITEM_CLAIMED, kinds)
        self.assertIn(ActivityKind.ITEM_BOUGHT, kinds)

    def test_unshared_items_activity_unaffected(self):
        scarf = lists.add_item(self.repo, self.maire, self.p_maire.id, "Scarf", None, None, 2000, None, False)
        accounts.set_reveal(self.repo, self.nora, self.p_maire.id, True)
        self.assertTrue(any(a.params.get("item") == "Scarf" for a in activity.feed(self.repo, self.nora)))


class TestActivityReplies(World):
    def test_reply_appears_in_feed(self):
        activity.post_reply(self.repo, self.ciaran, "  well I never  ")
        replies = [a for a in activity.feed(self.repo, self.aoife) if a.kind is ActivityKind.USER_REPLY]
        self.assertEqual(replies[-1].params["message"], "well I never")
        self.assertEqual(replies[-1].params["person_name"], "Ciarán")

    def test_rejects_too_long(self):
        with self.assertRaises(DomainError):
            activity.post_reply(self.repo, self.ciaran, "x" * (activity.REPLY_MAX_LEN + 1))

    def test_rejects_links(self):
        with self.assertRaises(DomainError):
            activity.post_reply(self.repo, self.ciaran, "check www.example.com")
        with self.assertRaises(DomainError):
            activity.post_reply(self.repo, self.ciaran, "https://x.ie")

    def test_rejects_profanity(self):
        with self.assertRaises(DomainError):
            activity.post_reply(self.repo, self.ciaran, "this is shit")

    def test_rejects_empty_message(self):
        with self.assertRaises(DomainError):
            activity.post_reply(self.repo, self.ciaran, "   ")

    def test_one_reply_per_day(self):
        activity.post_reply(self.repo, self.ciaran, "first")
        with self.assertRaises(DomainError):
            activity.post_reply(self.repo, self.ciaran, "second")

    def test_cannot_reply_with_no_activity(self):
        self.repo.delete_all_activity()
        with self.assertRaises(DomainError):
            activity.post_reply(self.repo, self.ciaran, "hello?")


class TestOrdering(World):
    def test_reorder_and_move(self):
        a = lists.add_item(self.repo, self.maire, self.p_maire.id, "A", None, None, 100, None, False)
        b = lists.add_item(self.repo, self.maire, self.p_maire.id, "B", None, None, 100, None, False)
        lists.reorder(self.repo, self.maire, self.p_maire.id, [b.id, self.coat.id, a.id])
        self.assertEqual([i.title for i in lists.items_for_editing(self.repo, self.maire, self.p_maire.id)],
                         ["B", "Wool coat", "A"])
        lists.move(self.repo, self.maire, a.id, -1)
        self.assertEqual([i.title for i in lists.items_for_editing(self.repo, self.maire, self.p_maire.id)],
                         ["B", "A", "Wool coat"])
        with self.assertRaises(DomainError):
            lists.reorder(self.repo, self.maire, self.p_maire.id, [a.id])


class TestBadges(World):
    def test_event_badges_pick_the_highest_count(self):
        accounts.record_gerry_event(self.repo, self.ciaran, gerry.Trigger.TINY_CHIP_IN)
        accounts.record_gerry_event(self.repo, self.ciaran, gerry.Trigger.TINY_CHIP_IN)
        accounts.record_gerry_event(self.repo, self.aoife, gerry.Trigger.TINY_CHIP_IN)
        accounts.record_gerry_event(self.repo, self.maire, gerry.Trigger.BANISHED)

        board = {b.key: b for b in badges.leaderboard(self.repo)}
        self.assertEqual(board["cheapskate"].subject_name, "Ciarán")
        self.assertEqual(board["cheapskate"].detail, "2 times")
        self.assertEqual(board["ghost_whisperer"].subject_name, "Máire")
        self.assertNotIn("big_spender", board)  # nobody's triggered it

    def test_window_shopper_picks_highest_visits_since_claim(self):
        for _ in range(3):
            accounts.start_visit(self.repo, self.maire)
        board = {b.key: b for b in badges.leaderboard(self.repo)}
        self.assertEqual(board["window_shopper"].subject_name, "Máire")
        self.assertEqual(board["window_shopper"].detail, "3 visits")

    def test_item_based_badges(self):
        lists.add_item(self.repo, self.aoife, self.p_aoife.id, "Sticker", None, None, 100, None, False)
        lists.add_item(self.repo, self.aoife, self.p_aoife.id, "Pencil", None, None, 100, None, False)
        board = {b.key: b for b in badges.leaderboard(self.repo)}
        self.assertEqual(board["wish_list_hoarder"].subject_name, "Aoife")
        self.assertEqual(board["wish_list_hoarder"].detail, "2 items")
        self.assertEqual(board["easy_to_please"].subject_name, "Aoife")  # cheapest total (coat/lego cost more)
        self.assertEqual(board["serial_sulker"].subject_name, "Máire")  # only the coat has really_want set

    def test_lone_entrant_gets_neither_hoarder_nor_easy_to_please(self):
        # Only Máire and Liam have items from World's setUp. Wipe Liam's so only one
        # person has any -- "highest" and "lowest" would otherwise be the same lone total.
        lists.delete_item(self.repo, self.ciaran, self.lego.id)
        board = {b.key: b for b in badges.leaderboard(self.repo)}
        self.assertNotIn("wish_list_hoarder", board)
        self.assertNotIn("easy_to_please", board)

    def test_extreme_item_badges_pick_cheapest_and_priciest(self):
        board = {b.key: b for b in badges.leaderboard(self.repo)}
        self.assertEqual(board["bargain_bin"].subject_name, "Liam")
        self.assertEqual(board["bargain_bin"].detail, "Lego, €60")
        self.assertEqual(board["big_ask"].subject_name, "Máire")
        self.assertEqual(board["big_ask"].detail, "Wool coat, €80")

    def test_extreme_item_badges_need_at_least_two_items(self):
        lists.delete_item(self.repo, self.ciaran, self.lego.id)
        board = {b.key: b for b in badges.leaderboard(self.repo)}
        self.assertNotIn("bargain_bin", board)
        self.assertNotIn("big_ask", board)

    def test_night_owl_picks_most_items_added_between_midnight_and_5am(self):
        kid = accounts.add_dependent(self.repo, self.ciaran, "Owlet")
        with patch("giftlist.clock.now", return_value=datetime(2026, 1, 15, 2, 30, tzinfo=UTC)):
            lists.add_item(self.repo, self.ciaran, kid.id, "Torch", None, None, 500, None, False)
            lists.add_item(self.repo, self.ciaran, kid.id, "Batteries", None, None, 500, None, False)
        with patch("giftlist.clock.now", return_value=datetime(2026, 1, 15, 14, 0, tzinfo=UTC)):
            lists.add_item(self.repo, self.ciaran, kid.id, "Daytime thing", None, None, 500, None, False)
        board = {b.key: b for b in badges.leaderboard(self.repo)}
        self.assertEqual(board["night_owl"].subject_name, "Owlet")
        self.assertEqual(board["night_owl"].detail, "2 items")

    def test_frequent_flyer_survives_a_claim_that_resets_the_window_shopper_streak(self):
        for _ in range(3):
            accounts.start_visit(self.repo, self.maire)
        claims.claim(self.repo, self.maire, self.lego.id, 1000)  # resets visits_since_claim, not visit_count
        board = {b.key: b for b in badges.leaderboard(self.repo)}
        self.assertNotIn("window_shopper", board)
        self.assertEqual(board["frequent_flyer"].subject_name, "Máire")
        self.assertEqual(board["frequent_flyer"].detail, "3 visits")

    def test_big_family_energy_picks_the_household_with_more_dependents(self):
        board = {b.key: b for b in badges.leaderboard(self.repo)}
        self.assertEqual(board["big_family_energy"].subject_name, "Ciarán's household")
        self.assertEqual(board["big_family_energy"].detail, "1 dependent")

    def test_empty_leaderboard_on_a_bare_repo(self):
        self.assertEqual(badges.leaderboard(make_repo()), [])

    def test_clear_badges_wipes_events_and_visit_streaks_not_lists(self):
        accounts.record_gerry_event(self.repo, self.ciaran, gerry.Trigger.TINY_CHIP_IN)
        accounts.start_visit(self.repo, self.maire)
        accounts.clear_badges(self.repo)
        board = {b.key: b for b in badges.leaderboard(self.repo)}
        self.assertNotIn("cheapskate", board)
        self.assertNotIn("window_shopper", board)
        self.assertIn("big_family_energy", board)  # untouched -- comes from live household data


class TestInputHardening(World):
    def test_price_over_the_ceiling_is_rejected(self):
        with self.assertRaises(DomainError):
            lists.add_item(self.repo, self.maire, self.p_maire.id, "Yacht", None, None, MAX_MINOR + 1, None, False)

    def test_price_at_the_ceiling_is_allowed(self):
        item = lists.add_item(self.repo, self.maire, self.p_maire.id, "Yacht", None, None, MAX_MINOR, None, False)
        self.assertEqual(item.price_minor, MAX_MINOR)

    def test_control_characters_stripped_from_title_note_and_name(self):
        item = lists.add_item(self.repo, self.maire, self.p_maire.id, "Sock\x00s", None, None, 100, "Warm\x07 ones", False)
        self.assertEqual(item.title, "Socks")
        self.assertEqual(item.note, "Warm ones")
        accounts.rename_person(self.repo, self.maire, self.p_maire.id, "M\x1baire")
        self.assertEqual(self.repo.person(self.p_maire.id).name, "Maire")

    def test_url_without_a_host_is_dropped_rather_than_saved(self):
        item = lists.add_item(self.repo, self.maire, self.p_maire.id, "Mystery", "javascript:alert(1)", None, 100, None, False)
        self.assertIsNone(item.url)

    def test_url_with_a_space_is_dropped(self):
        item = lists.add_item(self.repo, self.maire, self.p_maire.id, "Mystery", "x.ie/a b", None, 100, None, False)
        self.assertIsNone(item.url)

    def test_hostless_url_is_dropped(self):
        item = lists.add_item(self.repo, self.maire, self.p_maire.id, "Mystery", "http://", None, 100, None, False)
        self.assertIsNone(item.url)

    def test_normal_url_still_gets_a_scheme(self):
        item = lists.add_item(self.repo, self.maire, self.p_maire.id, "Book", "x.ie/book", None, 100, None, False)
        self.assertEqual(item.url, "https://x.ie/book")


class TestSqlHardening(World):
    def test_sql_injection_style_input_is_stored_literally(self):
        payload = "Robert'); DROP TABLE items; --"
        item = lists.add_item(self.repo, self.maire, self.p_maire.id, payload, None, None, 100, None, False)
        self.assertEqual(self.repo.item(item.id).title, payload)
        self.assertIsNotNone(self.repo.item(self.coat.id))  # table's still there -- nothing was dropped

    def test_ensure_column_rejects_unsafe_table_and_column_names(self):
        conn = connect(":memory:")
        init_db(conn)
        with self.assertRaises(ValueError):
            _ensure_column(conn, "users; DROP TABLE users; --", "x", "TEXT")
        with self.assertRaises(ValueError):
            _ensure_column(conn, "users", "x; DROP TABLE users; --", "TEXT")
        conn.close()


class TestParseAmount(unittest.TestCase):
    def test_rejects_over_the_ceiling(self):
        with self.assertRaises(InvalidAmount):
            parse_amount(f"{MAX_MINOR // 100 + 1}")

    def test_allows_the_ceiling(self):
        self.assertEqual(parse_amount(f"{MAX_MINOR // 100}"), MAX_MINOR)

    def test_rejects_absurdly_long_input(self):
        with self.assertRaises(InvalidAmount):
            parse_amount("9" * 30)


if __name__ == "__main__":
    unittest.main()
