import os
import tempfile
import threading
import unittest

from giftlist import accounts, claims, lists
from giftlist.accounts import Invite
from giftlist.errors import DomainError, NotAllowed, OverClaimed
from giftlist.models import Funding, NoticeKind
from giftlist.sqlite_repo import SqliteRepository, connect, init_db


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
        claims.claim(self.repo, self.ciaran, self.coat.id, 8000)
        claims.mark_bought(self.repo, self.ciaran, self.coat.id)
        lists.delete_item(self.repo, self.maire, self.coat.id)
        [n] = claims.notices(self.repo, self.ciaran)
        self.assertEqual(n.kind, NoticeKind.ITEM_REMOVED)
        self.assertTrue(n.params["bought"])
        self.assertEqual(claims.my_claims(self.repo, self.ciaran), [])

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

    def test_joining_household_purges_claims_inside_it(self):
        site = self.site
        niamh = accounts.sign_in(self.repo, "g-n", "n@x", "Niamh", site, False)
        claims.claim(self.repo, niamh, self.lego.id, 1000)
        claims.claim(self.repo, self.maire, self.lego.id, 1000)
        hh, _ = accounts.my_household(self.repo, self.ciaran)
        accounts.join_household(self.repo, niamh, hh.invite_token)
        self.assertEqual([c.user_id for c in self.repo.claims_for_item(self.lego.id)], [self.maire.id])

    def test_cannot_join_if_household_not_empty(self):
        mam_hh, _ = accounts.my_household(self.repo, self.maire)
        with self.assertRaises(NotAllowed):
            accounts.join_household(self.repo, self.ciaran, mam_hh.invite_token)

    def test_new_season_clears_lists_keeps_people(self):
        claims.claim(self.repo, self.ciaran, self.coat.id, 1000)
        accounts.new_season(self.repo)
        self.assertEqual(self.repo.items_for_person(self.p_maire.id), [])
        self.assertIsNotNone(self.repo.person(self.liam.id))
        self.assertEqual(claims.my_claims(self.repo, self.ciaran), [])


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


if __name__ == "__main__":
    unittest.main()
