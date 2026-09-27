import random
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from giftlist import accounts, claims, email_digest, lists
from giftlist.accounts import Invite
from giftlist.config import Config
from giftlist.sqlite_repo import SqliteRepository, connect, init_db
from giftlist.web import create_app


def make_repo():
    conn = connect(":memory:")
    init_db(conn)
    return SqliteRepository(conn)


def dublin_local_time(hour: int, minute: int = 0) -> datetime:
    """A moment, expressed in UTC, that is exactly this hour:minute in Dublin
    today -- handles DST correctly instead of assuming a fixed UTC offset."""
    local = datetime.now(ZoneInfo("Europe/Dublin")).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return local.astimezone(UTC)


class DigestWorld(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo()
        site = Invite(site_token=accounts.site_invite_token(self.repo))
        self.maire = accounts.sign_in(self.repo, "g-maire", "maire@x.ie", "Máire", site, False)
        self.ciaran = accounts.sign_in(self.repo, "g-ciaran", "c@x.ie", "Ciarán", site, False)
        p_maire = self.repo.person_for_user(self.maire.id)
        self.coat = lists.add_item(self.repo, self.maire, p_maire.id, "Wool coat", None, None, 8000, None, False)
        claims.claim(self.repo, self.ciaran, self.coat.id, 3000)
        accounts.set_email_subscription(self.repo, self.ciaran, True, "Europe/Dublin")
        self.ciaran = self.repo.user(self.ciaran.id)

        self.dir = tempfile.mkdtemp()
        self.cfg = Config(secret_key="s" * 40, base_url="https://gifts.example.ie", google_client_id="id",
                          google_client_secret="sec", admin_emails=frozenset(), data_dir=self.dir,
                          secure_cookies=False)
        self.app = create_app(self.cfg, provider=None)


class TestBuildEmail(DigestWorld):
    def test_includes_activity_names_amounts_and_sprite(self):
        with self.app.app_context():
            items = self.repo.activity_since((datetime.now(UTC) - timedelta(hours=24)).isoformat())
            subject, html, text = email_digest.build_email(items, "EUR", self.cfg.base_url, random.Random(1))
        self.assertIn("Wool coat", text)
        self.assertIn("Wool coat", html)
        self.assertIn("Ciarán", text)
        self.assertIn("€30", text)
        self.assertIn("https://gifts.example.ie/static/gerry-", html)
        self.assertIn(".svg", html)
        self.assertEqual(subject, "Gerry's daily gossip")

    def test_empty_activity_still_produces_a_valid_email(self):
        with self.app.app_context():
            subject, html, text = email_digest.build_email([], "EUR", self.cfg.base_url, random.Random(1))
        self.assertEqual(subject, "Gerry has nothing to say")
        self.assertIn("<html", html)

    def test_item_titles_are_html_escaped(self):
        """A title containing markup must not break out of the <li>."""
        evil_item = lists.add_item(self.repo, self.maire, self.repo.person_for_user(self.maire.id).id,
                                   "<script>alert(1)</script>", None, None, 500, None, False)
        claims.claim(self.repo, self.ciaran, evil_item.id, 500)
        with self.app.app_context():
            items = self.repo.activity_since((datetime.now(UTC) - timedelta(hours=24)).isoformat())
            _, html, _ = email_digest.build_email(items, "EUR", self.cfg.base_url, random.Random(1))
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)


class TestRun(DigestWorld):
    def test_sends_nothing_outside_the_digest_hour(self):
        with self.app.app_context(), patch("giftlist.email_digest.mail.send_digest") as sent:
            n = email_digest.run(self.repo, self.cfg, now_utc=dublin_local_time(12))
        self.assertEqual(n, 0)
        sent.assert_not_called()

    def test_sends_once_at_seven_pm_local_and_marks_the_date(self):
        now7 = dublin_local_time(19, 5)
        with self.app.app_context(), patch("giftlist.email_digest.mail.send_digest") as sent:
            n = email_digest.run(self.repo, self.cfg, now_utc=now7)
        self.assertEqual(n, 1)
        sent.assert_called_once()
        self.assertEqual(sent.call_args.kwargs["to"], "c@x.ie")
        self.assertEqual(self.repo.user(self.ciaran.id).last_digest_sent_date, now7.astimezone(ZoneInfo("Europe/Dublin")).date().isoformat())

    def test_does_not_send_twice_the_same_day(self):
        now7 = dublin_local_time(19, 5)
        with self.app.app_context(), patch("giftlist.email_digest.mail.send_digest") as sent:
            email_digest.run(self.repo, self.cfg, now_utc=now7)
            n2 = email_digest.run(self.repo, self.cfg, now_utc=now7 + timedelta(minutes=20))
        self.assertEqual(n2, 0)
        sent.assert_called_once()

    def test_send_failure_does_not_mark_as_sent_so_it_retries(self):
        now7 = dublin_local_time(19, 5)
        with self.app.app_context(), patch("giftlist.email_digest.mail.send_digest", side_effect=RuntimeError("boom")):
            n = email_digest.run(self.repo, self.cfg, now_utc=now7)
        self.assertEqual(n, 0)
        self.assertIsNone(self.repo.user(self.ciaran.id).last_digest_sent_date)

    def test_unsubscribed_users_are_skipped(self):
        accounts.set_email_subscription(self.repo, self.ciaran, False, "Europe/Dublin")
        with self.app.app_context(), patch("giftlist.email_digest.mail.send_digest") as sent:
            email_digest.run(self.repo, self.cfg, now_utc=dublin_local_time(19, 5))
        sent.assert_not_called()


class TestSendTest(DigestWorld):
    def test_bypasses_the_hour_and_date_guard(self):
        with self.app.app_context(), patch("giftlist.email_digest.mail.send_digest") as sent:
            had_activity = email_digest.send_test(self.repo, self.cfg, self.ciaran)
        self.assertTrue(had_activity)
        sent.assert_called_once()
        self.assertTrue(sent.call_args.kwargs["subject"].startswith("[test]"))


if __name__ == "__main__":
    unittest.main()
