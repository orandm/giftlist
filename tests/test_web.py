import random
import re
import shutil
import tempfile
import unittest
from urllib.parse import parse_qs, urlparse

from giftlist.auth import AuthError, Identity
from giftlist.config import Config
from giftlist.web import create_app


class FakeGoogle:
    """Stands in for Google: the 'code' is the email to sign in as."""

    def authorize_url(self, redirect_uri, state):
        return f"https://accounts.example/auth?state={state}&redirect_uri={redirect_uri}"

    def identity(self, code, redirect_uri):
        if code == "bad":
            raise AuthError("nope")
        return Identity(sub="g-" + code, email=code, name=code.split("@")[0].title())


class AlwaysRoll(random.Random):
    def random(self):
        return 0.0


def make_app(dev_login=False, admin="boss@x.ie"):
    d = tempfile.mkdtemp()
    cfg = Config(secret_key="s" * 40, base_url="http://localhost", google_client_id="id", google_client_secret="sec",
                 admin_emails=frozenset({admin}), data_dir=d, dev_login=dev_login, secure_cookies=False)
    app = create_app(cfg, provider=FakeGoogle())
    app.config["GIFTLIST_RNG"] = AlwaysRoll(3)
    return app, d


class Browser:
    """A test client that knows how to sign in and post forms with CSRF."""

    def __init__(self, app):
        self.c = app.test_client()

    def sign_in(self, email, invite_path=None):
        if invite_path:
            self.c.get(invite_path)
        r = self.c.get("/auth/google")
        state = parse_qs(urlparse(r.headers["Location"]).query)["state"][0]
        return self.c.get(f"/auth/callback?state={state}&code={email}")

    def csrf(self):
        with self.c.session_transaction() as s:
            return s.get("csrf", "")

    def post(self, path, data=None, **kw):
        data = dict(data or {})
        data.setdefault("csrf", self.csrf())
        return self.c.post(path, data=data, **kw)

    def get(self, path):
        return self.c.get(path)

    def text(self, path):
        return self.c.get(path).get_data(as_text=True)


class WebTests(unittest.TestCase):
    def setUp(self):
        self.app, self.dir = make_app()
        self.boss = Browser(self.app)
        r = self.boss.sign_in("boss@x.ie")  # admin needs no invite
        self.assertIn("/welcome", r.headers["Location"])
        admin = self.boss.text("/admin")
        self.invite = re.search(r"/invite/[\w-]+", admin).group(0)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def person_id(self, browser):
        return int(re.search(r"/my-list/(\d+)/add", browser.text("/my-list")).group(1))

    def add_item(self, browser, title, price, person_id=None):
        pid = person_id or self.person_id(browser)
        return browser.post(f"/my-list/{pid}/add", {"title": title, "price": price, "url": "", "note": ""})

    def item_id(self, browser, title):
        page = browser.text("/")
        m = re.search(r'id="item-(\d+)"[^§]*?' + re.escape(title), page)
        return int(m.group(1))

    # --- sign in ------------------------------------------------------------------

    def test_stranger_without_invite_is_refused(self):
        r = Browser(self.app).sign_in("stranger@x.ie")
        self.assertEqual(r.status_code, 403)
        self.assertIn("invite link", r.get_data(as_text=True))

    def test_invite_link_lets_you_in(self):
        r = Browser(self.app).sign_in("mam@x.ie", self.invite)
        self.assertIn("/welcome", r.headers["Location"])

    def test_bad_state_rejected(self):
        b = Browser(self.app)
        b.get(self.invite)
        b.get("/auth/google")
        r = b.get("/auth/callback?state=forged&code=mam@x.ie")
        self.assertIn("/login", r.headers["Location"])
        self.assertIn("/login", b.get("/").headers["Location"])

    def test_dev_login_off_by_default(self):
        self.assertEqual(Browser(self.app).get("/dev/login").status_code, 404)

    def test_csrf_required(self):
        pid = self.person_id(self.boss)
        r = self.boss.c.post(f"/my-list/{pid}/add", data={"title": "X", "price": "5"})
        self.assertEqual(r.status_code, 400)

    # --- the main flow ----------------------------------------------------------

    def test_household_hidden_and_claim_flow(self):
        mam = Browser(self.app)
        mam.sign_in("mam@x.ie", self.invite)
        self.add_item(mam, "Wool coat", "80")

        # boss's partner joins boss's household via the household link
        join = re.search(r"/household/join/[\w-]+", self.boss.text("/household")).group(0)
        partner = Browser(self.app)
        partner.sign_in("partner@x.ie", join)
        self.assertNotIn("Boss", partner.text("/"))  # own household hidden

        coat = self.item_id(self.boss, "Wool coat")
        self.boss.post(f"/items/{coat}/claim", {"amount": "30"})
        page = partner.text("/")
        self.assertIn("€30 of €80 covered", page)
        partner.post(f"/items/{coat}/claim", {"amount": "rest"})
        self.assertIn("Mark as bought", partner.text("/my-buys"))

        # the owner never sees any of it
        mine = mam.text("/my-list")
        self.assertIn("Wool coat", mine)
        self.assertNotIn("covered", mine)
        self.assertNotIn("Boss", mine)
        self.assertNotIn("Wool coat", mam.text("/"))

        # over-claiming is refused with a message
        r = mam.text(f"/items/{coat}/buy")  # can't open your own item's buy page
        self.assertNotIn("Chip in", r)

    def test_last_claimer_backing_out_after_bought_reopens_the_item(self):
        mam = Browser(self.app)
        mam.sign_in("mam@x.ie", self.invite)
        self.add_item(mam, "Chicken Treats", "3.79")
        item = self.item_id(self.boss, "Chicken Treats")
        self.boss.post(f"/items/{item}/claim", {"amount": "rest"})
        self.boss.post(f"/items/{item}/bought")
        self.boss.post(f"/items/{item}/withdraw")
        page = self.boss.text("/")
        self.assertNotIn("Bought", page)
        self.assertIn("I'll get this", page)

    def test_partial_withdraw_after_bought_shows_shortfall_not_unclaimed(self):
        mam = Browser(self.app)
        mam.sign_in("mam@x.ie", self.invite)
        self.add_item(mam, "Chicken Treats", "3.79")
        item = self.item_id(self.boss, "Chicken Treats")
        self.boss.post(f"/items/{item}/claim", {"amount": "2.00"})
        partner = Browser(self.app)
        join = re.search(r"/household/join/[\w-]+", self.boss.text("/household")).group(0)
        partner.sign_in("partner@x.ie", join)
        partner.post(f"/items/{item}/claim", {"amount": "rest"})
        self.boss.post(f"/items/{item}/bought")
        self.boss.post(f"/items/{item}/withdraw")
        page = partner.text("/")
        self.assertIn("Bought, but", page)
        self.assertIn("short", page)
        self.assertNotIn("Tragic", page)

    def test_owner_deleting_claimed_item_notifies(self):
        mam = Browser(self.app)
        mam.sign_in("mam@x.ie", self.invite)
        self.add_item(mam, "Scarf", "30")
        scarf = self.item_id(self.boss, "Scarf")
        self.boss.post(f"/items/{scarf}/claim", {"amount": "rest"})
        self.boss.post(f"/items/{scarf}/bought")
        mam.post(f"/items/{scarf}/delete")
        buys = self.boss.text("/my-buys")
        self.assertIn("Awkward.", buys)
        self.assertIn("already bought", buys)

    def test_dependents_and_their_lists(self):
        self.boss.post("/household/people", {"name": "Liam"})
        page = self.boss.text("/my-list")
        liam = int(re.search(r'person=(\d+)"[^>]*>Liam', page).group(1))
        self.add_item(self.boss, "Lego", "60", person_id=liam)
        self.assertIn("Lego", self.boss.text(f"/my-list?person={liam}"))
        mam = Browser(self.app)
        mam.sign_in("mam@x.ie", self.invite)
        self.assertIn("Lego", mam.text("/"))
        self.assertNotIn("Lego", self.boss.text("/"))

    def test_reorder_json(self):
        pid = self.person_id(self.boss)
        for t in ("A", "B", "C"):
            self.add_item(self.boss, t, "5")
        ids = [int(i) for i in re.findall(r'data-id="(\d+)"', self.boss.text("/my-list"))]
        r = self.boss.c.post(f"/people/{pid}/reorder", json={"ids": ids[::-1]},
                             headers={"X-CSRF-Token": self.boss.csrf()})
        self.assertEqual(r.get_json(), {"ok": True})
        page = self.boss.text("/my-list")
        self.assertLess(page.index(">C<"), page.index(">A<"))

    # --- Gerry ----------------------------------------------------------------------

    def test_gerry_reacts_and_can_be_banished_and_forgiven(self):
        mam = Browser(self.app)
        mam.sign_in("mam@x.ie", self.invite)
        self.add_item(mam, "Wool coat", "80")
        coat = self.item_id(self.boss, "Wool coat")
        # a new browser session = a second visit, so Gerry is allowed out
        again = Browser(self.app)
        again.sign_in("boss@x.ie")
        again.get("/")  # visit 2 starts
        again.c.set_cookie("gl_visit", "0")
        again.post(f"/items/{coat}/claim", {"amount": "2"})
        page = again.text("/")
        self.assertIn('class="gerry"', page)
        self.assertIn("Banish Gerry", page)
        again.post("/gerry/banish", {"next": "/"})
        page = again.text("/")
        self.assertIn("Ghost of Gerry", page)
        self.assertIn("a ghost, and", page)
        again.post("/gerry/sorry", {"next": "/"})
        self.assertIn("Apology accepted", again.text("/"))

    # --- magic link + punishment mode --------------------------------------------------

    def magic_token(self, url_or_path):
        return re.search(r"/join/magic/([\w-]+)", url_or_path).group(1)

    def test_magic_signup_flow_creates_account_in_punishment_mode(self):
        admin = self.boss.text("/admin")
        token = self.magic_token(admin)
        anon = Browser(self.app)
        self.assertEqual(anon.get(f"/join/magic/{token}").status_code, 200)
        r = anon.post(f"/join/magic/{token}", {"name": "Kodi", "email": "kodi@x.ie"})
        page = r.get_data(as_text=True)
        # no SMTP configured in tests, so the link is shown on screen instead of emailed
        link = re.search(r'value="(http://[^"]+/magic/[\w-]+)"', page).group(1)
        r = anon.get(link.replace("http://localhost", ""))
        self.assertIn("/welcome", r.headers["Location"])
        page = anon.text("/")
        self.assertIn('class="gerry cursed"', page)
        self.assertIn("Google", page)
        self.assertNotIn("data-close", page)

    def test_magic_signup_rejects_a_dead_token(self):
        r = Browser(self.app).get("/join/magic/not-a-real-token")
        self.assertIn("doesn&#39;t work any more", r.get_data(as_text=True))

    def test_admin_can_toggle_punishment_mode_on_a_normal_account(self):
        mam = Browser(self.app)
        mam.sign_in("mam@x.ie", self.invite)
        admin = self.boss.text("/admin")
        row = re.search(r"mam@x\.ie.*?/admin/users/(\d+)/punishment", admin, re.S)
        uid = int(row.group(1))
        self.boss.post(f"/admin/users/{uid}/punishment", {"enabled": "1"})
        self.assertIn("Google", mam.text("/"))
        self.boss.post(f"/admin/users/{uid}/punishment", {"enabled": "0"})
        self.assertNotIn("Google", mam.text("/"))

    def test_banishing_while_punished_spawns_four_echo_ghosts(self):
        admin = self.boss.text("/admin")
        token = self.magic_token(admin)
        riley = Browser(self.app)
        riley.get(f"/join/magic/{token}")
        r = riley.post(f"/join/magic/{token}", {"name": "Riley", "email": "riley@x.ie"})
        link = re.search(r'value="(http://[^"]+/magic/[\w-]+)"', r.get_data(as_text=True)).group(1)
        riley.get(link.replace("http://localhost", ""))
        self.assertNotIn("gerry-echo", riley.text("/"))
        riley.post("/gerry/banish", {"next": "/"})
        page = riley.text("/")
        self.assertNotIn("data-gerry", page)  # the big central popup is gone once banished
        self.assertEqual(page.count("gerry-echo tl"), 1)
        self.assertEqual(page.count("gerry-echo tr"), 1)
        self.assertEqual(page.count("gerry-echo bl"), 1)
        self.assertEqual(page.count("gerry-echo br"), 1)
        echo_lines = re.findall(r'<div class="gerry-echo[^"]*">.*?<p class="says">([^<]+)</p>', page, re.S)
        self.assertEqual(len(set(echo_lines)), 4)  # all 4 corner lines are distinct
        self.assertEqual(page.count(">Say sorry<"), 4)  # each corner can un-banish him
        riley.post("/gerry/sorry", {"next": "/"})
        page = riley.text("/")
        self.assertNotIn("gerry-echo", page)
        self.assertIn("data-gerry", page)

    # --- admin ------------------------------------------------------------------------

    def test_admin_is_hidden_from_others(self):
        mam = Browser(self.app)
        mam.sign_in("mam@x.ie", self.invite)
        self.assertEqual(mam.get("/admin").status_code, 404)

    def test_admin_remove_user_and_reset_invite_and_season(self):
        mam = Browser(self.app)
        mam.sign_in("mam@x.ie", self.invite)
        self.add_item(mam, "Coat", "80")
        admin = self.boss.text("/admin")
        uid = int(re.search(r'/admin/users/(\d+)/remove', admin).group(1))
        self.boss.post(f"/admin/users/{uid}/remove")
        self.assertIn("/login", mam.get("/").headers["Location"])  # signed out, gone

        self.boss.post("/admin/invite/reset")
        r = Browser(self.app).sign_in("late@x.ie", self.invite)
        self.assertEqual(r.status_code, 403)

        self.add_item(self.boss, "Socks", "10")
        self.boss.post("/admin/season", {"confirm": "nope"})
        self.assertIn("Socks", self.boss.text("/my-list"))
        self.boss.post("/admin/season", {"confirm": "CLEAR"})
        self.assertNotIn("Socks", self.boss.text("/my-list"))

    # --- odds and ends -------------------------------------------------------------

    def test_image_path_traversal_blocked(self):
        self.assertEqual(self.boss.get("/img/..%2F..%2Fgiftlist.db").status_code, 404)

    def test_preview_refuses_local_urls(self):
        r = self.boss.c.post("/preview", json={"url": "http://127.0.0.1:8000/admin"},
                             headers={"X-CSRF-Token": self.boss.csrf()})
        self.assertEqual(r.get_json()["title"], None)

    def test_pages_render(self):
        for path in ["/", "/my-list", "/my-buys", "/household", "/welcome", "/admin"]:
            r = self.boss.get(path)
            self.assertEqual(r.status_code, 200, path)
        pid = self.person_id(self.boss)
        self.assertEqual(self.boss.get(f"/my-list/{pid}/add").status_code, 200)


if __name__ == "__main__":
    unittest.main()
