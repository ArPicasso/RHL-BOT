"""«Смотреть» от лиги (rhl_media.py, ADR-019, раздел 7): вкладка «Видео» и «Трансляции» сайта rhl.fhr.ru на
настоящих страницах 03.10.2026, загрузка в rhl_site.update и первая кнопка «Смотреть» в league.json."""
import asyncio
import contextlib
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import build_data  # noqa: E402
import matchday  # noqa: E402
import rhl_media  # noqa: E402
import rhl_site  # noqa: E402
import tg_channels as tg  # noqa: E402

FIX = Path(__file__).parent / "fixtures"
TZ = ZoneInfo("Europe/Moscow")
SITE = "https://rhl.fhr.ru"


def page(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def msk(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=TZ)


def store() -> dict:
    s = {"games": {}}
    rhl_site.merge(s, rhl_site.parse_calendar(page("rhl_calendar_2026_10_03.html")))
    for gid, name in ((905113, "rhl_match_905113_final.html"), (905111, "rhl_match_905111_final.html")):
        rhl_site.apply_page(s["games"][str(gid)], rhl_site.parse_match(page(name)))
    return s


class Video(unittest.TestCase):
    def test_vk_link_from_video_tab(self):
        # плеер vk.ru и vkvideo.ru с хешем встраивания — одна и та же страница ролика на vk.com
        self.assertEqual(rhl_media.parse_video(page("rhl_video_905111.html")),
                         {"kind": "VK Видео", "url": "https://vk.com/video-187307324_456239889"})
        self.assertEqual(rhl_media.parse_video(page("rhl_video_905113.html")),
                         {"kind": "VK Видео", "url": "https://vk.com/video-60074608_456240743"})
        # кнопка узнаётся как видео, и это тот же ролик, что клубы дают в постах
        url = rhl_media.parse_video(page("rhl_video_905111.html"))["url"]
        self.assertEqual(matchday.video(url), ("VK Видео", False))
        self.assertEqual(matchday.same_video(url), matchday.same_video("https://vkvideo.ru/video-187307324_456239889?list=7"))
        self.assertEqual(matchday.same_video(url), matchday.same_video("https://vkvideo.ru/live-187307324_456239889"))

    def test_online_frame_is_not_video(self):
        # вкладка «Трансляция» — фрейм онлайна КХЛ, это текстовая трансляция, а не «Смотреть»
        self.assertIsNone(rhl_media.parse_video(page("rhl_live_tab_905111.html")))
        self.assertIsNone(rhl_media.parse_video(page("rhl_match_905111_live.html")))

    def test_embed_links(self):
        cases = {
            "https://vk.com/video_ext.php?oid=123&amp;id=45": ("VK Видео", "https://vk.com/video123_45"),
            "//vk.ru/video_ext.php?oid=-1&id=2&hd=2": ("VK Видео", "https://vk.com/video-1_2"),
            "https://www.youtube.com/embed/abcDEF12345": ("YouTube", "https://youtu.be/abcDEF12345"),
            "https://rutube.ru/play/embed/0123456789abcdef0123456789abcdef/": (
                "Rutube", "https://rutube.ru/video/0123456789abcdef0123456789abcdef/"),
        }
        for src, want in cases.items():
            self.assertEqual(rhl_media.embed_link(src), want, src)
        for src in ("https://vk.com/video_ext.php?oid=-1&id=x", "https://vk.com/video_ext.php?oid=a&id=2",
                    "https://online.khl.ru/online/905111.html", "javascript:alert(1)", "https://evil.ru/video_ext.php?oid=1&id=2"):
            self.assertIsNone(rhl_media.embed_link(src), src)


class Translations(unittest.TestCase):
    def test_cards(self):
        cards = {c["id"]: c for c in rhl_media.parse_translations(page("rhl_translations.html"))}
        self.assertEqual(sorted(cards), [905111, 905112, 905113, 905114, 905116, 905117, 905118, 905119])
        self.assertEqual(cards[905116], {"t": 1432, "id": 905116, "n": 6, "home": "МХК Рязань-ВДВ", "away": "МХК Белгород",
                                         "when": "04 окт | 17:00",
                                         "title": "«МХК Рязань-ВДВ» - «МХК Белгород». Прямая трансляция"})

    def test_cards_bind_to_calendar_by_id(self):
        s = store()
        for c in rhl_media.parse_translations(page("rhl_translations.html")):
            g = s["games"].get(str(c["id"]))
            if g:
                self.assertEqual((g["home"], g["away"]), (c["home"], c["away"]))   # тот же матч
                self.assertEqual(g.get("n", c["n"]), c["n"])


class Wanted(unittest.TestCase):
    def test_today_tomorrow_and_just_played(self):
        s = store()
        now = msk("2026-10-03T21:00:00")
        want = {int(k) for k, g in s["games"].items() if rhl_media.need_video(g, now)}
        self.assertLessEqual({905111, 905113, 905116, 905119}, want)       # сыграны сегодня, завтрашние
        self.assertFalse(any(datetime.fromisoformat(s["games"][str(i)]["start"]).date() > now.date().replace(day=4)
                             for i in want))                                  # дальше завтра — нет
        g = s["games"]["905111"]
        g["video"] = "https://vk.com/video-187307324_456239889"
        self.assertFalse(rhl_media.need_video(g, now))                      # нашлась — больше не спрашиваем
        g = s["games"]["905113"]
        g["video_asked"] = (now - timedelta(minutes=30)).isoformat()
        self.assertFalse(rhl_media.need_video(g, now))                      # сыгран, спрашивали полчаса назад
        g["video_asked"] = (now - rhl_media.VIDEO_EVERY).isoformat()
        self.assertTrue(rhl_media.need_video(g, now))                       # запись выкладывают и через часы
        g.pop("video_asked")
        self.assertTrue(rhl_media.need_video(g, msk("2026-10-06T20:00:00")))   # и на третий день
        self.assertFalse(rhl_media.need_video(s["games"]["905114"], msk("2026-10-07T12:00:00")))   # давно сыгран


class FakeResponse:
    def __init__(self, url: str, pages: dict[str, str]):
        self.url, self.pages = url, pages

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def raise_for_status(self):
        if self.url not in self.pages:
            raise rhl_site.aiohttp.ClientError(f"404, нет страницы {self.url}")

    async def text(self):
        return self.pages[self.url]


class FakeSession:
    def __init__(self, pages: dict[str, str]):
        self.pages, self.asked = pages, []

    def get(self, url: str):
        self.asked.append(url)
        return FakeResponse(url, self.pages)


class Loading(unittest.TestCase):
    def run_media(self, s: dict, session: FakeSession, now: datetime) -> None:
        with mock.patch.object(rhl_site, "PAUSE", 0):
            asyncio.run(rhl_site.update_media(session, s, SITE, now))

    def test_translations_once_and_video_until_found(self):
        s = store()
        session = FakeSession({f"{SITE}/translations/": page("rhl_translations.html"),
                               f"{SITE}/matchcenter/1432/905111/video/": page("rhl_video_905111.html"),
                               f"{SITE}/matchcenter/1432/905113/video/": page("rhl_video_905113.html")})
        now = msk("2026-10-03T21:00:00")
        with self.assertLogs(level="WARNING"):     # у остальных вкладки нет: 404 — в журнал, не падаем
            self.run_media(s, session, now)
        self.assertEqual(session.asked.count(f"{SITE}/translations/"), 1)
        self.assertEqual(len(session.asked), len(set(session.asked)))      # каждая страница — один раз
        self.assertLessEqual(len(session.asked), 1 + rhl_site.MAX_VIDEO)
        g = s["games"]["905111"]
        self.assertEqual((g["video"], g["video_kind"], g["translation"]),
                         ("https://vk.com/video-187307324_456239889", "VK Видео", True))
        self.assertTrue(s["games"]["905116"]["translation"])               # завтрашний: объявлен, плеера ещё нет
        self.assertNotIn("video", s["games"]["905116"])
        session.asked.clear()
        with self.assertLogs(level="WARNING"):
            self.run_media(s, session, now)
        self.assertNotIn(f"{SITE}/matchcenter/1432/905111/video/", session.asked)   # нашлась — не спрашиваем

    def test_played_asked_again_later_oldest_first(self):
        """Запись лига выкладывает и через часы: сыгранный матч спрашиваем снова, кого дольше не спрашивали — первым."""
        now = msk("2026-10-05T12:00:00")
        games = {str(i): {"id": i, "t": 1432, "start": "2026-10-04T17:00:00+03:00", "status": "final",
                          "video_asked": f"2026-10-05T0{i}:00:00+03:00"} for i in (5, 7, 9)}
        session = FakeSession({})
        with mock.patch.object(rhl_site, "MAX_VIDEO", 2), self.assertLogs(level="WARNING"):
            self.run_media({"games": games}, session, now)
        self.assertEqual(session.asked[1:], [f"{SITE}/matchcenter/1432/5/video/", f"{SITE}/matchcenter/1432/7/video/"])
        self.assertEqual(games["5"]["video_asked"], now.isoformat(timespec="seconds"))
        self.assertTrue(rhl_media.need_video(games["9"], now))
        self.assertFalse(rhl_media.need_video(games["5"], now + timedelta(hours=1)))

    def test_deleted_recording_asked_again_and_dropped(self):
        """Запись удалили из VK (служба clips): вкладку «Видео» спрашиваем заново и дольше (этап 0.3 плана)."""
        g = {"id": 5, "t": 1432, "start": "2026-10-04T17:00:00+03:00", "status": "final",
             "video": "https://vk.com/video-100_200", "video_kind": "VK Видео"}
        now = msk("2026-10-06T12:00:00")
        gone = {"https://vkvideo.ru/video-100_200"}                 # тот же ролик, другой адрес
        self.assertFalse(rhl_media.need_video(g, now))
        self.assertTrue(rhl_media.need_video(g, now, gone))
        self.assertTrue(rhl_media.need_video(g, msk("2026-10-14T12:00:00"), gone))   # дольше VIDEO_DAYS
        self.assertFalse(rhl_media.need_video(g, msk("2026-10-25T12:00:00"), gone))  # но не бесконечно
        self.assertFalse(rhl_media.need_video(g, now, {"https://vk.com/video-9_9"}))
        games = {"5": dict(g)}
        session = FakeSession({f"{SITE}/translations/": "<html></html>",
                               f"{SITE}/matchcenter/1432/5/video/": "<html>плеера нет</html>"})
        with mock.patch.object(rhl_site, "PAUSE", 0):
            asyncio.run(rhl_site.update_media(session, {"games": games}, SITE, now, gone))
        self.assertIn(f"{SITE}/matchcenter/1432/5/video/", session.asked)
        self.assertNotIn("video", games["5"])                       # мёртвую ссылку не держим
        self.assertNotIn("video_kind", games["5"])
        # но номер ролика помним: иначе срок схлопнулся бы до VIDEO_DAYS и новую запись мы бы не заметили
        self.assertEqual(games["5"]["video_gone"], "https://vk.com/video-100_200")
        self.assertTrue(rhl_media.need_video(games["5"], msk("2026-10-14T12:00:00"), gone))
        found = FakeSession({f"{SITE}/translations/": "<html></html>",
                             f"{SITE}/matchcenter/1432/5/video/": page("rhl_video_905111.html")})
        with mock.patch.object(rhl_site, "PAUSE", 0):
            asyncio.run(rhl_site.update_media(found, {"games": games}, SITE, msk("2026-10-14T12:00:00"), gone))
        self.assertEqual(games["5"]["video"], "https://vk.com/video-187307324_456239889")   # лига выложила заново
        self.assertNotIn("video_gone", games["5"])

    def test_gone_videos_from_clips_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "clips.json"
            self.assertEqual(rhl_site.gone_videos(path), set())
            path.write_text(json.dumps({"games": {
                "2026-10-05|kaluga|dinamo-576": {"video": "https://vk.com/video-1_2", "status": "gone"},
                "2026-10-04|proton|kristall": {"video": "https://vk.com/video-3_4", "status": "ok"},
                "2026-10-03|a|b": {"status": "gone"}}}), encoding="utf-8")
            self.assertEqual(rhl_site.gone_videos(path), {"https://vk.com/video-1_2"})

    def test_translations_page_down(self):
        s = store()
        session = FakeSession({f"{SITE}/matchcenter/1432/905111/video/": page("rhl_video_905111.html")})
        with self.assertLogs(level="WARNING"):
            self.run_media(s, session, msk("2026-10-03T21:00:00"))
        self.assertEqual(s["games"]["905111"]["video"], "https://vk.com/video-187307324_456239889")


class Watch(unittest.TestCase):
    def setUp(self):
        self.teams = build_data.load_teams()
        self.store = store()
        g = self.store["games"]
        g["905111"].update(video="https://vk.com/video-187307324_456239889", video_kind="VK Видео", translation=True)
        g["905116"]["translation"] = True

    def games(self) -> list[dict]:
        games = [{"id": "n1", "n": 1, "date": "2026-10-03", "home": "ryazan-vdv", "away": "belgorod", "official": True},
                 {"id": "n2", "n": 6, "date": "2026-10-04", "home": "ryazan-vdv", "away": "belgorod", "official": True}]
        with contextlib.redirect_stdout(io.StringIO()):   # «не узнал матч» без даты — записки сборки
            build_data.apply_site(games, self.store, self.teams)
        return games

    def test_league_video_first_then_clubs_without_repeats(self):
        channels = [c for c in tg.load_channels(ROOT / "channels.json")
                    if c["handle"] in ("mhkbelgorod31", "hcryazan_official", "nmhlpervenstvo")]
        pages = {c["handle"]: page(f"tg_{c['handle']}_2026_10_03.html") for c in channels}
        posts = tg.build(channels, pages)["channels"]
        games = self.games()
        build_data.apply_matchday(games, self.teams, channels, posts)
        g = {x["id"]: x for x in games}
        # пост «Белгорода» дал ролик лиги со своей ссылкой, пост «Рязани» — эфир того же ролика
        self.assertEqual(matchday.same_video(g["n1"]["watch"][0]["url"]), "vk-187307324_456239889")
        g["n1"]["watch"].append({"title": "YouTube · канал «РХЛ»", "url": "https://youtu.be/abc", "src": "t.me/nmhlpervenstvo"})
        self.assertEqual(build_data.apply_media(games, self.store), 2)
        self.assertEqual(g["n1"]["watch"], [
            {"title": "Трансляция лиги · VK Видео", "url": "https://vk.com/video-187307324_456239889", "src": "rhl.fhr.ru"},
            {"title": "YouTube · канал «РХЛ»", "url": "https://youtu.be/abc", "src": "t.me/nmhlpervenstvo"}])
        # завтра: плеера ещё нет, трансляция объявлена — вкладка «Видео» на сайте лиги
        self.assertEqual(g["n2"]["watch"], [{"title": "Трансляция лиги · сайт РХЛ",
                                             "url": "https://rhl.fhr.ru/matchcenter/1432/905116/video/",
                                             "src": "rhl.fhr.ru"}])

    def test_at_most_three(self):
        games = self.games()
        games[0]["watch"] = [{"title": f"VK Видео · {i}", "url": f"https://vk.com/video-1_{i}", "src": "t.me/x"}
                             for i in range(3)]
        build_data.apply_media(games, self.store)
        self.assertEqual([w["url"] for w in games[0]["watch"]],
                         ["https://vk.com/video-187307324_456239889", "https://vk.com/video-1_0", "https://vk.com/video-1_1"])

    def test_nothing_from_league(self):
        games = self.games()
        self.store["games"]["905111"].pop("video")
        self.store["games"]["905111"].pop("translation")
        self.assertEqual(build_data.apply_media(games, {"games": {}}), 0)
        self.assertEqual(build_data.apply_media(games, self.store), 1)     # только завтрашний
        self.assertNotIn("watch", games[0])

    def test_build_puts_league_video_into_league_json(self):
        with contextlib.redirect_stdout(io.StringIO()):
            data, _, _ = build_data.build(self.teams, [], {}, site=self.store)
        g = next(x for x in data["games"] if x["id"] == "n1")
        self.assertEqual(g["watch"][0]["title"], "Трансляция лиги · VK Видео")
        json.dumps(data, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
