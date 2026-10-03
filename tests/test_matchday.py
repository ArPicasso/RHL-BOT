"""Матч-центр из постов каналов (ADR-019, разделы 2 и 7): время начала, «Смотреть», привязка к матчу."""
import json
import sys
import unittest
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import matchday as md  # noqa: E402
import tg_channels as tg  # noqa: E402

FIX = Path(__file__).parent / "fixtures"
TZ = ZoneInfo("Europe/Moscow")
TEAMS = json.loads((ROOT / "teams.json").read_text(encoding="utf-8"))
DAY = date(2026, 10, 3)
MSK, IRK, SAM = "Europe/Moscow", "Asia/Irkutsk", "Europe/Samara"

# серия: «Рязань-ВДВ» — «Белгород» два дня подряд, в тот же день «Ермак» — «Самара»
GAMES = [{"id": "n1", "date": "2026-10-03", "home": "ryazan-vdv", "away": "belgorod"},
         {"id": "n2", "date": "2026-10-04", "home": "ryazan-vdv", "away": "belgorod"},
         {"id": "rh1", "date": "2026-10-03", "home": "ermak", "away": "samara"}]
CHANNELS = [
    {"handle": "nmhlpervenstvo", "club": None, "kind": "league", "title": "РХЛ"},
    {"handle": "hcryazan_official", "club": "ryazan-vdv", "kind": "system", "title": "ХК «Рязань-ВДВ»"},
    {"handle": "mhkbelgorod31", "club": "belgorod", "kind": "club", "short": "МХК «Белгород»"},
    {"handle": "ermak_angarsk_hc", "club": "ermak", "kind": "club", "short": "ХК «Ермак»"},
    {"handle": "fans", "club": "ryazan-vdv", "kind": "fan", "title": "Болельщики"},
    {"handle": "gone", "club": "ryazan-vdv", "kind": "club", "title": "Ушли", "optout": {"level": "all"}},
]
VK = "https://vk.com/video-123_456"


def post(text, at="2026-10-03T12:00+03:00", links=(VK,), pid=1, **kw):
    """Пост в channel_posts.json, как его пишет tg_channels.entry: превью, ссылки, строки со временем."""
    title, body = tg.preview(text)
    out = {"id": pid, "url": f"https://t.me/x/{pid}", "at": at, "title": title, "text": body, "media": 1,
           "video": False, **kw}
    if links:
        out["links"] = list(links)
    times = tg.time_lines(text)
    if times:
        out["times"] = times
    return out


def found(handle, *posts, games=GAMES):
    data = {c["handle"]: {"ok": True, "posts": []} for c in CHANNELS}
    data[handle]["posts"] = list(posts)
    return md.attach(games, TEAMS, CHANNELS, data)


class StartTime(unittest.TestCase):
    def t(self, text, arena=MSK, poster=MSK):
        got = md.post_time(tg.time_lines(text) or [text], DAY, arena, poster)
        return got and got.strftime("%H:%M")

    def test_cues(self):
        for text in ("Начало в 17:00", "Стартуем в 17:00!", "Начало матча — 17.00", "Сегодня в 17:00 принимаем «Белгород»",
                     "⏰ 17:00", "Стартовое вбрасывание в 17:00", "17:00 МСК", "Игра начнётся в 17:00 по Москве"):
            self.assertEqual(self.t(text), "17:00", text)

    def test_local_time_to_moscow(self):
        self.assertEqual(self.t("Начало в 19:30 по местному", arena=IRK, poster=IRK), "14:30")
        self.assertEqual(self.t("Матч в 12:00 по местному времени", arena=IRK, poster=MSK), "07:00")
        self.assertEqual(self.t("Стартуем в 18:00", arena=SAM, poster=SAM), "17:00")       # без пояса — пояс арены
        self.assertEqual(self.t("Начало в 12:00 (МСК+5)", arena=IRK, poster=MSK), "07:00")
        self.assertEqual(self.t("Начало в 22:00 по иркутскому", arena=MSK, poster=IRK), "17:00")
        self.assertEqual(self.t("⏰ 12:00 по местному (07:00 мск)", arena=IRK, poster=IRK), "07:00")   # оба сходятся

    def test_zone_unknown(self):
        # «Ермак» пишет о выезде в Рязань без пояса: его 22:00 или рязанское — не понять
        self.assertIsNone(self.t("Начало в 22:00", arena=MSK, poster=IRK))
        self.assertIsNone(self.t("Начало в 12:00 (МСК+12)"))

    def test_not_a_start_time(self):
        self.assertIsNone(self.t("3 октября — 17:00\n4 октября — 17:00"))                  # расписание без привязки
        self.assertIsNone(self.t("Сегодня в 17:00, а завтра в 16:00"))                     # два разных — не берём
        self.assertIsNone(self.t("Трансляция начнётся в 16:55"))
        self.assertIsNone(self.t("Первый период: 1:0, 2:1"))                               # счёт — не время
        self.assertIsNone(self.t("Сезон 03.10 открыт"))                                    # дата — не время
        self.assertEqual(self.t("Начало в 17:00, открытие ворот в 16:00"), "17:00")
        self.assertEqual(self.t("Первый матч — 03.10.2026 в 17:00"), "17:00")


class Links(unittest.TestCase):
    def test_video_hosts(self):
        cases = {
            "https://vk.com/video-123_456": ("VK Видео", False),
            "https://vk.ru/video-123_456": ("VK Видео", False),
            "https://vk.com/wall-1_2?z=video-123_456%2Fabc": ("VK Видео", False),
            "https://vkvideo.ru/video-123_456": ("VK Видео", False),
            "https://live.vkvideo.ru/hcryazan": ("VK Видео", True),
            "https://www.youtube.com/watch?v=abc123": ("YouTube", False),
            "https://youtu.be/abc123": ("YouTube", False),
            "https://youtube.com/live/abc123": ("YouTube", True),
            "https://rutube.ru/video/0123abc/": ("Rutube", False),
            "https://rutube.ru/live/video/0123abc/": ("Rutube", True),
            "https://matchpremier.ru/hockey": ("«Матч Премьер»", False),
            "https://smotrim.ru/live/1": ("«Смотрим»", True),
            "https://t.me/hcryazan_official?livestream": ("Эфир в Telegram", True),
        }
        for url, want in cases.items():
            self.assertEqual(md.video(url), want, url)

    def test_not_video(self):
        for url in ("https://vk.com/club123", "https://vk.com/clip-1_2", "https://rzn.kassy.ru/venue/1",
                    "https://youtube.com/@club", "https://t.me/hcryazan_official/22385", "https://max.ru/id1",
                    "https://livetv.sx/ru/eventinfo/1", "ftp://vk.com/video-1_2"):
            self.assertIsNone(md.video(url), url)

    def test_canonical_and_repeats(self):
        self.assertEqual(md.canonical("http://www.youtube.com/watch?v=abc&utm_source=tg#t"),
                         "https://youtube.com/watch?v=abc")
        self.assertEqual(md.canonical("https://t.me/club?livestream"), "https://t.me/club?livestream")
        self.assertEqual(md.same_video("https://vk.com/video-1_2"), md.same_video("https://vkvideo.ru/video-1_2"))
        self.assertEqual(md.same_video("https://youtu.be/abc"), md.same_video("https://youtube.com/watch?v=abc"))

    def test_bad_hosts(self):
        for url in ("https://fon.bet/live", "https://winline.ru/x", "https://www.betboom.ru", "https://ligastavok.ru",
                    "https://pari.ru/a", "https://vk.com/away.php?to=https%3A%2F%2F1xbet.com", "https://legalbet.ru/match"):
            self.assertEqual(tg.link_reason(url), "букмекер", url)
        for url in ("https://livetv.sx/x", "https://crackstreams.biz/nhl", "https://sportsurge.net/a"):
            self.assertEqual(tg.link_reason(url), "пиратская трансляция", url)
        for url in (VK, "https://vystavka.ru/a", "https://rzn.kassy.ru/venue/1"):
            self.assertIsNone(tg.link_reason(url), url)


class PostLinks(unittest.TestCase):
    """tg_channels.py сохраняет ссылки поста (`links`) и строки со временем (`times`) добавочными полями."""

    def page(self, body, extra=""):
        return tg.POST_SPLIT + (
            '<div data-post="hcryazan_official/7"><div class="tgme_widget_message_text js-message_text" dir="auto">'
            f"{body}</div>{extra}"
            '<a class="tgme_widget_message_date" href="https://t.me/hcryazan_official/7">'
            '<time datetime="2026-10-03T09:00:00+00:00"></time></a></div>')

    def test_links_from_text_preview_and_buttons(self):
        body = ('Трансляция: <a href="https://vk.com/away.php?to=https%3A%2F%2Fvkvideo.ru%2Fvideo-1_2&amp;utf=1">ссылка</a>'
                '<br/>Ещё youtu.be/abc123. <a href="?q=%23рхл">#рхл</a> <a href="https://t.me/hcryazan_official/5">пост</a>')
        extra = ('<a class="tgme_widget_message_link_preview" href="https://rutube.ru/video/abc/"></a>'
                 '<a class="tgme_widget_message_inline_button url_button" href="https://t.me/hcryazan_official?livestream">Эфир</a>')
        p = tg.parse_page(self.page(body, extra), "hcryazan_official")["posts"][0]
        self.assertEqual(p["links"], ["https://vkvideo.ru/video-1_2", "https://rutube.ru/video/abc/",
                                      "https://t.me/hcryazan_official?livestream", "https://youtu.be/abc123"])

    def test_fixture_posts(self):
        ryazan = tg.parse_page((FIX / "tg_hcryazan_official.html").read_text(encoding="utf-8"), "hcryazan_official")
        p = next(x for x in ryazan["posts"] if x["id"] == 22385)
        self.assertEqual(p["links"], ["https://rzn.kassy.ru/venue/ds-olimpijjskijj-8/"])   # переход ВК раскрыт
        e = tg.entry(p, {"handle": "hcryazan_official"})
        self.assertEqual(e["times"][0], "3 октября — 17:00")
        self.assertLessEqual(len(e["times"]), tg.TIMES_MAX)
        samara = tg.parse_page((FIX / "tg_hcsamara.html").read_text(encoding="utf-8"), "hcsamara")
        self.assertTrue(all(not x["links"] or "t.me" not in x["links"][0] for x in samara["posts"]))

    def test_bookmaker_link_drops_post(self):
        p = {"id": 1, "url": "u", "at": "2026-10-03T12:00+03:00", "text": "Смотрим матч вместе!", "image": None,
             "media": 1, "video": False, "forwarded": False, "poll": False, "links": ["https://fon.bet/promo"]}
        self.assertEqual(tg.skip_reason(p, {"kind": "club", "scope": "all"}), "букмекер")
        self.assertIsNone(tg.skip_reason({**p, "links": [VK]}, {"kind": "club", "scope": "all"}))

    def test_short_post_with_link_kept_aside(self):
        """«Трансляция 👇» и ссылка — лента такой пост не берёт, а «Смотреть» — да."""
        body = 'Трансляция 👇<br/><a href="https://vk.com/video-1_2">смотреть</a>'
        channel = {"handle": "hcryazan_official", "club": "ryazan-vdv", "kind": "club", "scope": "all", "markers": []}
        parsed = tg.parse_page(self.page(body), "hcryazan_official")
        out = tg.build([channel], {"hcryazan_official": self.page(body)})["channels"]["hcryazan_official"]
        self.assertEqual(out["posts"], [])                         # лента — как раньше
        self.assertEqual(out["dropped"], {"коротко": 1})
        self.assertEqual(out["extra"][0]["links"], ["https://vk.com/video-1_2"])
        self.assertEqual(tg.extras(parsed, {**channel, "scope": "u21_only"}), [])   # не о молодёжке — нет и тут


class Binding(unittest.TestCase):
    def test_own_club_post_on_match_day(self):
        got = found("hcryazan_official", post("Сегодня принимаем «Белгород»! Начало в 17:00"))
        self.assertEqual(got["n1"]["watch"], [{"title": "VK Видео · канал ХК «Рязань-ВДВ»", "url": VK,
                                                "src": "t.me/hcryazan_official"}])
        self.assertEqual(got["n1"]["start"], datetime(2026, 10, 3, 17, 0, tzinfo=TZ))
        self.assertNotIn("n2", got)

    def test_opponent_channel_and_league(self):
        self.assertIn("watch", found("mhkbelgorod31", post("Играем в Рязани, смотрите прямой эфир"))["n1"])
        self.assertEqual(found("mhkbelgorod31", post("Трансляция"))["n1"]["watch"][0]["title"],
                         "VK Видео · канал МХК «Белгород»")
        league = post("МХК «Рязань-ВДВ» — МХК «Белгород»: трансляция матча")
        self.assertEqual(found("nmhlpervenstvo", league)["n1"]["watch"][0]["title"], "VK Видео · канал «РХЛ»")

    def test_post_must_be_about_this_match(self):
        # ни соперника, ни слов о трансляции — видео не о матче
        self.assertEqual(found("hcryazan_official", post("Открытая тренировка завтра, видео по ссылке")), {})
        # слова о трансляции хватает: у клуба в этот день один матч
        self.assertEqual(set(found("ermak_angarsk_hc", post("Смотрите трансляцию, болеем вместе"))), {"rh1"})
        self.assertNotIn("n1", found("ermak_angarsk_hc", post("Белгород, смотрите трансляцию")))   # чужой канал
        self.assertEqual(found("fans", post("Белгород, трансляция")), {})                       # фан-канал
        self.assertEqual(found("gone", post("Белгород, трансляция")), {})                       # отказался
        # канал лиги: обе команды и ни одной лишней
        self.assertEqual(found("nmhlpervenstvo", post("Трансляция матча «Рязань-ВДВ»")), {})
        self.assertEqual(found("nmhlpervenstvo", post("Рязань-ВДВ — Белгород, Ермак — Самара: трансляции")), {})

    def test_day_window(self):
        eve = post("Завтра снова «Белгород», трансляция тут", at="2026-10-03T20:30+03:00")
        self.assertEqual(set(found("hcryazan_official", eve)), {"n2"})          # серия: «завтра» — о втором матче
        today = post("Смотрите трансляцию матча с «Белгородом»", at="2026-10-03T19:10+03:00")
        self.assertEqual(set(found("hcryazan_official", today)), {"n1"})        # вечером в день матча — о нём
        before = post("Смотрите трансляцию матча с «Белгородом»", at="2026-10-02T19:00+03:00")
        self.assertEqual(set(found("hcryazan_official", before)), {"n1"})       # накануне после 18:00
        early = post("Смотрите трансляцию матча с «Белгородом»", at="2026-10-02T12:00+03:00")
        self.assertEqual(found("hcryazan_official", early), {})                 # накануне днём — рано
        review = post("Обзор вчерашнего матча с «Белгородом»", at="2026-10-04T10:00+03:00")
        self.assertEqual(found("hcryazan_official", review), {})                # вчерашний матч — не сегодняшний
        revenge = post("Вчера уступили, сегодня реванш с «Белгородом»! Трансляция", at="2026-10-04T10:00+03:00")
        self.assertEqual(set(found("hcryazan_official", revenge)), {"n2"})

    def test_bookmaker_and_ads_never(self):
        self.assertEqual(found("hcryazan_official", post("Трансляция матча с «Белгородом», ставки на Фонбет")), {})
        self.assertEqual(found("hcryazan_official", post("Трансляция с «Белгородом»", links=[VK, "https://winline.ru/a"])), {})
        self.assertEqual(found("hcryazan_official", post("Трансляция с «Белгородом»", links=["https://livetv.sx/a"])), {})

    def test_limits_and_repeats(self):
        links = [VK, "https://vkvideo.ru/video-123_456", "https://youtu.be/a1", "https://rutube.ru/video/b/",
                 "https://smotrim.ru/live/1"]
        got = found("hcryazan_official", post("Трансляция матча с «Белгородом»", links=links))["n1"]["watch"]
        self.assertEqual([w["url"] for w in got], [VK, "https://youtu.be/a1", "https://rutube.ru/video/b/"])
        # пост о трансляции — выше поста, где соперник просто упомянут
        weak = post("Разминка перед «Белгородом»", links=["https://youtu.be/warm"], at="2026-10-03T11:00+03:00", pid=1)
        strong = post("Прямая трансляция", links=[VK], at="2026-10-03T16:50+03:00", pid=2)
        got = found("hcryazan_official", weak, strong)["n1"]["watch"]
        self.assertEqual([w["url"] for w in got], [VK, "https://youtu.be/warm"])

    def test_time_from_latest_post_and_not_from_live(self):
        first = post("С «Белгородом» — начало в 17:00", links=(), at="2026-10-03T09:00+03:00", pid=1)
        moved = post("Внимание! Матч с «Белгородом» начнётся в 18:00", links=(), at="2026-10-03T11:00+03:00", pid=2)
        live = post("Матч с «Белгородом» — 12:34, гол!", links=(), at="2026-10-03T18:30+03:00", pid=3, live=True)
        got = found("hcryazan_official", first, moved, live)["n1"]
        self.assertEqual(got["start"].strftime("%H:%M"), "18:00")
        self.assertNotIn("watch", got)

    def test_arena_zone_from_teams(self):
        got = found("ermak_angarsk_hc", post("Сегодня «Самара»! Начало в 12:00", links=()))
        self.assertEqual(got["rh1"]["start"], datetime(2026, 10, 3, 7, 0, tzinfo=TZ))   # Ангарск — МСК+5

    def test_extra_posts_and_old_cache(self):
        data = {"hcryazan_official": {"ok": True, "posts": [], "extra": [post("Трансляция 👇")]}}
        self.assertIn("n1", md.attach(GAMES, TEAMS, CHANNELS, data))
        old = {"hcryazan_official": {"ok": True, "posts": [{"id": 1, "url": "u", "at": "2026-10-03T12:00+03:00",
                                                           "title": "", "text": "Трансляция с «Белгородом»"}]}}
        self.assertEqual(md.attach(GAMES, TEAMS, CHANNELS, old), {})            # кэш без ссылок — без «Смотреть»
        self.assertEqual(md.attach(GAMES, TEAMS, CHANNELS, {"hcryazan_official": {"ok": False}}), {})


class Names(unittest.TestCase):
    pats = md.name_patterns(TEAMS)

    def test_cases_and_spellings(self):
        cases = {"с «Белгородом»": {"belgorod"}, "в Рязани": {"ryazan-vdv"}, "ВДВ": {"ryazan-vdv"},
                 "с «Динамо-576»": {"dinamo-576"}, "против Динамо": set(), "у «Динамо-Карелии»": {"dinamo-kareliya"},
                 "с «Красной машиной»": {"krasnaya-mashina"}, "в гостях у «Ленинградца»": {"leningradets"},
                 "с «Полётом»": {"polet"}, "«Калужских ракет»": {"kaluga"}, "с «Воеводой»": {"vityaz-podolsk"},
                 "«Ермака»": {"ermak"}, "с «Самарой»": {"samara"}, "в красной форме": set()}
        for text, want in cases.items():
            self.assertEqual(md.mentioned(text, self.pats), want, text)

    def test_every_team_recognised_by_name(self):
        for t in TEAMS:
            self.assertEqual(md.mentioned(f"Матч с «{t['name']}»", self.pats), {t["id"]}, t["name"])


if __name__ == "__main__":
    unittest.main()
