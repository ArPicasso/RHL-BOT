"""Служба pages: будит сборку Pages с сервера (ADR-015, дополнение 03.10.2026)."""
import asyncio
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

import pages_kick

TZ = ZoneInfo("Europe/Moscow")


class FakeResponse:
    def __init__(self, status, text=""):
        self.status = status
        self._text = text

    async def text(self):
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, status, text=""):
        self.status, self.text = status, text
        self.calls = []

    def post(self, url, json=None, headers=None):
        self.calls.append((url, json, headers))
        return FakeResponse(self.status, self.text)


class AwakeTest(unittest.TestCase):
    def test_night_is_quiet(self):
        self.assertFalse(pages_kick.awake(datetime(2026, 10, 4, 2, 0, tzinfo=TZ)))
        self.assertFalse(pages_kick.awake(datetime(2026, 10, 4, 6, 59, tzinfo=TZ)))

    def test_day_and_late_evening_kick(self):
        self.assertTrue(pages_kick.awake(datetime(2026, 10, 4, 7, 0, tzinfo=TZ)))
        self.assertTrue(pages_kick.awake(datetime(2026, 10, 4, 1, 59, tzinfo=TZ)))
        self.assertTrue(pages_kick.awake(datetime(2026, 10, 3, 19, 30, tzinfo=TZ)))

    def test_other_zone_is_read_in_moscow_time(self):
        # 23:30 UTC — это 02:30 МСК: ночь
        self.assertFalse(pages_kick.awake(datetime(2026, 10, 3, 23, 30, tzinfo=ZoneInfo("UTC"))))


class KickTest(unittest.TestCase):
    def test_dispatches_pages_workflow_on_main(self):
        s = FakeSession(204)
        self.assertTrue(asyncio.run(pages_kick.kick(s, "tok")))
        url, body, headers = s.calls[0]
        self.assertEqual(url, "https://api.github.com/repos/ArPicasso/bogdanov/actions/workflows/pages.yml/dispatches")
        self.assertEqual(body, {"ref": "main"})
        self.assertEqual(headers["Authorization"], "Bearer tok")

    def test_refusal_is_logged_without_token(self):
        s = FakeSession(403, "Resource not accessible by personal access token")
        with self.assertLogs(level="WARNING") as logs:
            self.assertFalse(asyncio.run(pages_kick.kick(s, "secret-token")))
        text = "\n".join(logs.output)
        self.assertIn("403", text)
        self.assertIn("PAGES_TOKEN", text)
        self.assertNotIn("secret-token", text)


if __name__ == "__main__":
    unittest.main()
