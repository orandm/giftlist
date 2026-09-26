import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from giftlist.backup import backup
from giftlist.config import Config


class TestOps(unittest.TestCase):
    def test_backup_keeps_newest(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "giftlist.db")
            sqlite3.connect(db).execute("CREATE TABLE t (x)").connection.commit()
            out = os.path.join(d, "backups")
            for i in range(4):
                with mock.patch("giftlist.backup.datetime") as dt:
                    dt.now.return_value.__format__ = lambda self, f, i=i: f"2026120{i}-000000"
                    backup(db, out, keep=2)
            self.assertEqual(sorted(os.listdir(out)), ["giftlist-20261202-000000.db", "giftlist-20261203-000000.db"])

    def test_config_refuses_weak_secret_and_dev_login_on_https(self):
        with mock.patch.dict(os.environ, {"SECRET_KEY": "short"}, clear=True):
            with self.assertRaises(RuntimeError):
                Config.from_env()
        env = {"SECRET_KEY": "x" * 40, "DEV_LOGIN": "1", "BASE_URL": "https://gifts.example.ie"}
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(RuntimeError):
                Config.from_env()
        env = {"SECRET_KEY": "x" * 40, "BASE_URL": "https://gifts.example.ie", "ADMIN_EMAILS": "Me@X.ie, b@x.ie"}
        with mock.patch.dict(os.environ, env, clear=True):
            c = Config.from_env()
            self.assertTrue(c.secure_cookies)
            self.assertTrue(c.is_admin("me@x.ie"))


if __name__ == "__main__":
    unittest.main()
