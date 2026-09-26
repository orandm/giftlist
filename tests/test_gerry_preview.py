import random
import tempfile
import unittest
from datetime import date

from giftlist import gerry, linkpreview
from giftlist.gerry import Trigger


class AlwaysRoll(random.Random):
    """rng.random() -> 0.0, so every chance passes."""

    def random(self):
        return 0.0


class NeverRoll(random.Random):
    def random(self):
        return 0.999


class TestTriggers(unittest.TestCase):
    D = date(2026, 12, 10)

    def test_claim_priority(self):
        self.assertEqual(gerry.claim_trigger(8000, 200, 8000, 200, False, self.D), Trigger.TINY_CHIP_IN)
        self.assertEqual(gerry.claim_trigger(3000, 1500, 3000, 1500, False, self.D), Trigger.SPLIT_CHEAP)
        self.assertEqual(gerry.claim_trigger(1500, 1500, 1500, 1500, False, self.D), Trigger.CHEAP_CLAIM)
        self.assertEqual(gerry.claim_trigger(8000, 8000, 8000, 8000, True, self.D), Trigger.SULK_AVERTED)
        self.assertEqual(gerry.claim_trigger(8000, 8000, 8000, 8000, False, date(2026, 12, 22)), Trigger.LATE_CLAIM)
        self.assertIsNone(gerry.claim_trigger(8000, 8000, 8000, 8000, False, self.D))

    def test_other_triggers(self):
        self.assertEqual(gerry.share_change_trigger(4000, 0), Trigger.BACKING_OUT)
        self.assertEqual(gerry.share_change_trigger(4000, 1000), Trigger.LOWERED_SHARE)
        self.assertIsNone(gerry.share_change_trigger(1000, 4000))
        self.assertEqual(gerry.add_item_trigger(15001, 1), Trigger.PRICEY_WISH)
        self.assertEqual(gerry.add_item_trigger(1000, 8), Trigger.LONG_LIST)
        self.assertEqual(gerry.visit_trigger(date(2026, 11, 3), 0), Trigger.EARLY_VISIT)
        self.assertEqual(gerry.visit_trigger(date(2026, 12, 3), 3), Trigger.BROWSING)
        self.assertIsNone(gerry.visit_trigger(date(2026, 12, 3), 1))


class TestDecide(unittest.TestCase):
    ctx = gerry.context(owner="Máire", item="coat", amount_minor=200, price_minor=8000)

    def d(self, trigger=Trigger.TINY_CHIP_IN, **kw):
        args = dict(ghost=False, last_line=None, rng=AlwaysRoll())
        args.update(kw)
        return gerry.decide(trigger, self.ctx, **args)

    def test_shows_and_fills_placeholders(self):
        p = self.d()
        self.assertFalse(p.ghost)
        self.assertNotIn("{", p.text)

    def test_no_cap_on_repeat_appearances(self):
        for _ in range(20):
            self.assertIsNotNone(self.d())

    def test_chance(self):
        self.assertIsNone(self.d(rng=NeverRoll()))

    def test_ghost_speaks_ghost(self):
        p = self.d(ghost=True)
        self.assertTrue(p.ghost)
        self.assertIn(p.template, gerry.GHOST_LINES)

    def test_ghost_always_lands(self):
        self.assertIsNotNone(self.d(ghost=True, rng=NeverRoll()))

    def test_banish_and_apology_always_land(self):
        self.assertTrue(self.d(Trigger.BANISHED, rng=NeverRoll()).ghost)
        self.assertFalse(self.d(Trigger.APOLOGY, rng=NeverRoll()).ghost)

    def test_no_repeat_and_skips_unfillable(self):
        rng = AlwaysRoll(1)  # passes every chance; choice() still varies with the seed
        seen, last = set(), None
        for _ in range(30):
            p = gerry.decide(Trigger.TINY_CHIP_IN, self.ctx, ghost=False,
                             last_line=last, rng=rng)
            self.assertNotEqual(p.template, last)
            last = p.template
            seen.add(p.template)
        self.assertGreater(len(seen), 1)
        # a line needing {count} is never used without a count
        p = gerry.decide(Trigger.LONG_LIST, {}, ghost=False, last_line=None, rng=AlwaysRoll())
        self.assertEqual(p.text, "Leave some Christmas for the rest of us.")

    def test_every_line_formats(self):
        full = gerry.context(owner="A", item="B", amount_minor=1, price_minor=2, count=3)
        for lines in [*gerry.LINES.values(), gerry.GHOST_LINES]:
            for line in lines:
                line.format_map(full)


class TestCountdown(unittest.TestCase):
    def test_days_remaining_and_bands(self):
        rng = AlwaysRoll()
        days, line = gerry.countdown(date(2026, 12, 1), rng)
        self.assertEqual(days, 24)
        self.assertIn(line, gerry.COUNTDOWN_LINES["chill"])

        days, line = gerry.countdown(date(2026, 12, 20), rng)
        self.assertEqual(days, 5)
        self.assertIn(line, gerry.COUNTDOWN_LINES["urgent"])

        days, line = gerry.countdown(date(2026, 12, 24), rng)
        self.assertEqual(days, 1)
        self.assertIn(line, gerry.COUNTDOWN_LINES["panic"])

        days, line = gerry.countdown(date(2026, 12, 25), rng)
        self.assertEqual(days, 0)
        self.assertIn(line, gerry.COUNTDOWN_LINES["today"])

        days, line = gerry.countdown(date(2026, 12, 27), rng)
        self.assertEqual(days, -2)
        self.assertIn(line, gerry.COUNTDOWN_LINES["past"])


class TestMood(unittest.TestCase):
    def test_baseline_is_grumpy_not_neutral(self):
        self.assertEqual(gerry.mood_score([]), 50)
        self.assertEqual(gerry.mood_label(50), "Grumpy")

    def test_annoying_events_raise_it_calming_lowers_it(self):
        up = gerry.mood_score(["tiny_chip_in", "tiny_chip_in", "backing_out"])
        self.assertGreater(up, 50)
        down = gerry.mood_score(["marked_bought", "apology"])
        self.assertLess(down, 50)

    def test_clamped_to_0_100(self):
        self.assertEqual(gerry.mood_score(["tiny_chip_in"] * 20), 100)
        self.assertEqual(gerry.mood_score(["marked_bought"] * 20), 0)


PAGE = """<html><head><title> Shop | Flask </title>
<meta property="og:title" content="Insulated flask, 600ml">
<meta property="og:image" content="/img/flask.jpg">
<script type="application/ld+json">{"@type":"Product","offers":{"price":"24.99"}}</script>
</head></html>"""


class TestPreview(unittest.TestCase):
    def test_parse(self):
        title, image, price = linkpreview.parse_html(PAGE, "https://shop.ie/p/1")
        self.assertEqual(title, "Insulated flask, 600ml")
        self.assertEqual(image, "https://shop.ie/img/flask.jpg")
        self.assertEqual(price, 2499)

    def test_blocks_private_addresses(self):
        for host in ["localhost", "127.0.0.1", "10.0.0.5", "169.254.169.254", "192.168.1.1"]:
            self.assertFalse(linkpreview.is_public_host(host), host)
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(linkpreview.preview("http://127.0.0.1:8000/admin", d), linkpreview.Preview())
            self.assertEqual(linkpreview.preview("file:///etc/passwd", d), linkpreview.Preview())

    def test_save_image_reencodes(self):
        import io
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGBA", (1200, 800), (200, 30, 40, 255)).save(buf, "PNG")
        with tempfile.TemporaryDirectory() as d:
            name = linkpreview.save_image(buf.getvalue(), d)
            self.assertEqual(linkpreview.valid_image_name(name), name)
            with Image.open(f"{d}/{name}") as im:
                self.assertLessEqual(max(im.size), 600)
        self.assertIsNone(linkpreview.valid_image_name("../../etc/passwd"))


if __name__ == "__main__":
    unittest.main()
