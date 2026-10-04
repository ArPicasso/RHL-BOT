"""Служба pages: будит сборку Pages с сервера (ADR-015, дополнение 03.10.2026)."""
import asyncio
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import admin
import pages_kick

TZ = ZoneInfo("Europe/Moscow")


class FakeResponse:
    def __init__(self, status, text=""):
        self.status = status
        self._text = text

    async def text(self):
        return self._text

    async def json(self):
        return json.loads(self._text)

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

    def get(self, url, headers=None):
        self.calls.append((url, None, headers))
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
        self.assertIsNone(asyncio.run(pages_kick.kick(s, "tok")))
        url, body, headers = s.calls[0]
        self.assertEqual(url, "https://api.github.com/repos/ArPicasso/RHL-BOT/actions/workflows/pages.yml/dispatches")
        self.assertEqual(body, {"ref": "main"})
        self.assertEqual(headers["Authorization"], "Bearer tok")

    def test_refusal_is_logged_without_token(self):
        s = FakeSession(403, "Resource not accessible by personal access token")
        with self.assertLogs(level="WARNING") as logs:
            err = asyncio.run(pages_kick.kick(s, "secret-token"))
        self.assertIn("HTTP 403", err)                  # причина — на пульт (ADR-021)
        self.assertIn("Actions: Read and write", err)
        self.assertNotIn("secret-token", err)
        text = "\n".join(logs.output)
        self.assertIn("403", text)
        self.assertIn("PAGES_TOKEN", text)
        self.assertNotIn("secret-token", text)


class WatchTest(unittest.TestCase):
    """Пульт админа (ADR-021): итоги заданий Actions → status/pages.json."""

    def test_runs_written_for_panel(self):
        runs = {"workflow_runs": [{"path": ".github/workflows/pages.yml", "status": "completed",
                                   "conclusion": "success", "created_at": "2026-10-03T17:00:00Z",
                                   "updated_at": "2026-10-03T17:03:00Z", "html_url": "https://github.com/x"}]}
        path = Path(tempfile.mkdtemp()) / "pages.json"
        track = admin.Tracker("pages", path)
        s = FakeSession(200, json.dumps(runs))
        asyncio.run(pages_kick.watch(s, "tok", "ArPicasso/RHL-BOT", track))
        self.assertEqual(s.calls[0][0], "https://api.github.com/repos/ArPicasso/RHL-BOT/actions/runs?per_page=100")
        got = json.loads(path.read_text(encoding="utf-8"))["info"]
        pages = next(r for r in got["runs"] if r["id"] == "pages")
        self.assertEqual((pages["conclusion"], pages["last_ok"]), ("success", "2026-10-03T20:03:00+03:00"))

    def test_refusal_keeps_old_runs_and_hides_token(self):
        path = Path(tempfile.mkdtemp()) / "pages.json"
        track = admin.Tracker("pages", path)
        track.info(runs=[{"id": "pages"}])
        with self.assertLogs(level="WARNING") as logs:
            asyncio.run(pages_kick.watch(FakeSession(403, "{}"), "secret-token", "ArPicasso/RHL-BOT", track))
        self.assertNotIn("secret-token", "\n".join(logs.output))
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["info"]["runs"], [{"id": "pages"}])


if __name__ == "__main__":
    unittest.main()
