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


class Feed(unittest.TestCase):
    """Лента матча из постов каналов (ADR-019, раздел 5) на настоящих постах 03.10.2026: Ростов — Краснодар
    в 13:00 (начали в 14:07), Тверичи — Металлург в 15:00, Рязань-ВДВ — Белгород в 17:00."""

    HANDLES = ("HCGvardiaKrd", "rostovhc", "nmhlpervenstvo", "mhkbelgorod31", "hcryazan_official")
    GAMES = [{"id": "a", "date": "2026-10-03", "home": "rostov", "away": "krasnodar", "start": "2026-10-03T13:00:00+03:00"},
             {"id": "b", "date": "2026-10-03", "home": "tverichi", "away": "metallurg", "start": "2026-10-03T15:00:00+03:00"},
             {"id": "c", "date": "2026-10-03", "home": "ryazan-vdv", "away": "belgorod", "start": "2026-10-03T17:00:00+03:00"},
             {"id": "d", "date": "2026-10-04", "home": "rostov", "away": "krasnodar"}]

    @classmethod
    def setUpClass(cls):
        cls.channels = [c for c in tg.load_channels(ROOT / "channels.json") if c["handle"] in cls.HANDLES]
        pages = {c["handle"]: (FIX / f"tg_{c['handle'].lower()}_2026_10_03.html").read_text(encoding="utf-8")
                 for c in cls.channels}
        cls.posts = tg.build(cls.channels, pages)["channels"]
        cls.feed = md.match_events(cls.GAMES, TEAMS, cls.channels, cls.posts)

    def texts(self, gid: str) -> list[str]:
        return [e["text"] for e in self.feed.get(gid, [])]

    def test_club_posts_during_match(self):
        texts = self.texts("a")
        for want in ("0:1 — Счёт открывают гости",                        # цифры-эмодзи «0️⃣*️⃣1️⃣» — счётом
                     "🥳Шайбу забросил Вячеслав Фурлетов — 🦅 0:3 🏝",
                     "🥳ГООООЛ!", "Игра 4 на 4",                            # короткие посты — тоже в ленте
                     "😢За подножку малым штрафом наказан Кузнецов Артём — 🦅 0:4 🏝",
                     "Перерыв после 40 минут игры, верим в команду и ждём третий период!",
                     "⭐️ТРЕТИЙ ПЕРИОД ОКОНЧЕН! 🦅 0:6 🏝"):
            self.assertIn(want, texts)
        self.assertEqual(texts.index("0:1 — Счёт открывают гости") < texts.index("🥳ГООООЛ!"), True)   # по времени
        self.assertFalse(any("Первые кадры" in t or "Рябицев «Вырвали" in t for t in texts))   # после окна матча
        self.assertNotIn("d", self.feed)                                  # завтрашний: без начала ленты нет

    def test_event_fields(self):
        e = next(x for x in self.feed["a"] if x["text"].startswith("0:1"))
        self.assertEqual(e, {"kind": "text", "text": "0:1 — Счёт открывают гости", "at": "2026-10-03T14:07+03:00",
                             "src": "t.me/rostovhc", "from": "канал ХК «Ростов»", "url": "https://t.me/rostovhc/7475"})
        self.assertNotIn("team", e)   # канал «Ростова» пишет и о голах гостей — сторону не ставим

    def test_league_posts_bound_by_teams(self):
        self.assertIn("Поехали! 🏒 Стартовое вбрасывание в Ростове ✔️ Первый матч сезона начался — следите за игрой.",
                      self.texts("a"))                                    # канал лиги: одна команда идущего матча
        self.assertTrue(any(t.startswith("Первый гол сезона") for t in self.texts("a")))
        self.assertTrue(any("Тверичи-СШОР" in t for t in self.texts("b")))
        ryazan = self.feed["c"]
        self.assertEqual([e["src"] for e in ryazan], ["t.me/hcryazan_official", "t.me/nmhlpervenstvo"])
        self.assertEqual(ryazan[0]["text"], "Первый период за нами🔥")     # «#РХЛ» в начале срезан
        # «#ВХЛ Состав на матч» — взрослая команда, не о молодёжке; ссылка на эфир за час до игры — до окна
        self.assertFalse(any("Состав" in t or "Не попали" in t for t in self.texts("c")))

    def test_rules(self):
        ch = [{"handle": "rostovhc", "club": "rostov", "kind": "club", "short": "ХК «Ростов»"},
              {"handle": "nmhlpervenstvo", "club": None, "kind": "league", "title": "РХЛ"}]

        def feed(handle, *texts, at="2026-10-03T15:00+03:00"):
            data = {"rostovhc": {"ok": True, "posts": []}, "nmhlpervenstvo": {"ok": True, "posts": []}}
            data[handle]["posts"] = [{**post(t, at=at, links=(), pid=i), "url": f"https://t.me/{handle}/{i}"}
                                     for i, t in enumerate(texts, 1)]
            return [e["text"] for e in md.match_events(self.GAMES[:3], TEAMS, ch, data).get("a", [])]
        self.assertEqual(feed("rostovhc", "Гол! Счёт открыт", "Гол! Счёт открыт"), ["Гол! Счёт открыт"])   # повтор — раз
        self.assertEqual(feed("rostovhc", "Завтра едем к «Тверичам»"), [])                 # третья команда — не о матче
        self.assertEqual(feed("rostovhc", "Ставки на матч в Фонбет"), [])                  # букмекер
        self.assertEqual(feed("nmhlpervenstvo", "Ростов, Тверичи, Рязань — все матчи дня"), [])   # обо всём дне
        self.assertEqual(feed("rostovhc", "Гол!", at="2026-10-03T12:30+03:00"), [])        # до окна матча
        self.assertEqual(feed("rostovhc", "💥💥💥"), [])                                     # без слов

    def test_cache_merge(self):
        ch = [{"handle": "rostovhc", "club": "rostov", "kind": "club"}]
        old = [{"kind": "text", "text": "Старый гол", "at": "2026-10-03T14:00+03:00", "src": "t.me/rostovhc",
                "url": "https://t.me/rostovhc/1"},
               {"kind": "text", "text": "Удалённый пост", "at": "2026-10-03T15:10+03:00", "src": "t.me/rostovhc",
                "url": "https://t.me/rostovhc/2"}]
        page = {"rostovhc": {"ok": True, "posts": [{"id": 3, "url": "https://t.me/rostovhc/3", "at": "2026-10-03T15:00+03:00"}]}}
        new = [{"kind": "text", "text": "Новый гол", "at": "2026-10-03T15:00+03:00", "src": "t.me/rostovhc",
                "url": "https://t.me/rostovhc/3"}]
        # пост старше страницы канала остаётся из кэша, пост, который должен быть на странице, но пропал, — нет
        self.assertEqual([e["text"] for e in md.merge_feed(old, new, ch, page)], ["Старый гол", "Новый гол"])
        self.assertEqual([e["text"] for e in md.merge_feed(old, [], ch, {"rostovhc": {"ok": False}})],
                         ["Старый гол", "Удалённый пост"])               # канал не открылся — кэш как есть
        self.assertEqual(md.merge_feed(old, [], [{**ch[0], "optout": {"level": "all"}}], page), [])   # отказался

    def test_league_json_events_accumulate(self):
        import build_data
        teams = build_data.load_teams()
        now = datetime(2026, 10, 3, 21, 0, tzinfo=TZ)
        cache = {"games": {"2026-09-29|rostov|krasnodar": [{"kind": "text", "text": "Давний матч", "at": "x"}]}}
        games = [dict(g) for g in self.GAMES]
        self.assertEqual(build_data.apply_channel_events(games, teams, self.channels, self.posts, cache, now), 3)
        self.assertEqual(games[0]["events"], self.feed["a"])
        self.assertEqual(cache["games"]["2026-10-03|rostov|krasnodar"], self.feed["a"])
        self.assertNotIn("2026-09-29|rostov|krasnodar", cache["games"])   # старше трёх дней — из кэша вон
        self.assertNotIn("events", games[3])
        # следующий запуск: на странице канала «Краснодара» уже другие посты — лента матча не теряется
        later = {h: ({**v, "posts": [], "extra": []} if h == "HCGvardiaKrd" else v) for h, v in self.posts.items()}
        again = [dict(g) for g in self.GAMES]
        build_data.apply_channel_events(again, teams, self.channels, later, cache, now)
        self.assertEqual(again[0]["events"], games[0]["events"])

    def test_short_posts_kept_aside(self):
        rostov = self.posts["rostovhc"]
        self.assertEqual([p["id"] for p in rostov["extra"]], [7475, 7479])   # «коротко» для ленты, но для матча — да
        self.assertNotIn(7475, [p["id"] for p in rostov["posts"]])


class GoalAuthors(unittest.TestCase):
    """Авторы голов по ходу матча из постов клубов (ADR-026) на настоящих постах 04.10.2026: Ростов — Краснодар
    в 13:00, к 14:13 0:3. Сайт лиги по ходу давал только счёт, канал «Краснодара» — кто забил."""

    def test_parse(self):
        cases = {
            "🥳Шайбу забросил Даниил Нуреев — 🦅 0:2 🏝": {"name": "Даниил Нуреев", "score": "0:2"},
            "💥Счёт в южном дерби открывает Григорий Сеснев! 🦅 0:1 🏝": {"name": "Григорий Сеснев", "score": "0:1"},
            "Кирилл Абашкин забрасывает! 2:1": {"name": "Кирилл Абашкин", "score": "2:1"},
            "0:1 — Открывает счёт в матче «Краснодар»": None,                       # без имени
            "😢За подножку малым штрафом наказан Кузнецов Артём — 🦅 0:4 🏝": None,  # удаление, не гол
            "✨У соперников удаление, а мы играем в полном составе и забрасываем шайбу 🦅 0:2 🏝": None,
            "Капитан «Краснодара» Дмитрий Рябицев открывает счёт в матче в Ростове 🏝": None,   # без счёта
            "Начало в 17:00, Иван Петров забил 1:0": None,                         # два «счёта» — не гадаем
            "⭐️ТРЕТИЙ ПЕРИОД ОКОНЧЕН! 🦅 0:6 🏝": None,
        }
        for text, want in cases.items():
            self.assertEqual(md.goal_author(text), want, text)

    def test_league_json(self):
        import build_data
        import rhl_protocol
        teams = build_data.load_teams()
        ch = [c for c in tg.load_channels(ROOT / "channels.json") if c["handle"] in ("HCGvardiaKrd", "rostovhc")]
        pages = {c["handle"]: (FIX / f"tg_{c['handle'].lower()}_2026_10_04.html").read_text(encoding="utf-8")
                 for c in ch}
        posts = tg.build(ch, pages)["channels"]
        games = [{"id": "a", "date": "2026-10-03", "home": "rostov", "away": "krasnodar", "start": "2026-10-03T13:00:00+03:00"},
                 {"id": "d", "date": "2026-10-04", "home": "rostov", "away": "krasnodar", "start": "2026-10-04T13:00:00+03:00"},
                 {"id": "r", "date": "2026-10-03", "home": "ryazan-vdv", "away": "belgorod"}]

        def proto_of(gid):
            return rhl_protocol.parse_protocol((FIX / f"rhl_protocol_{gid}.html").read_text(encoding="utf-8"), gid).to_json()
        # состав «Краснодара» — по протоколу вчерашнего матча (в нём только вратари, остальные — из голов и удалений);
        # «Рязань-ВДВ» — Белгород: имена сезона, по ним «Ратмир Тиняев» становится «Тиняев Ратмир»
        proto = {"a": proto_of(905113), "r": proto_of(905111)}
        build_data.apply_channel_events(games, teams, ch, posts, {}, datetime(2026, 10, 4, 14, 20, tzinfo=TZ))
        self.assertEqual(build_data.apply_goal_authors(games, proto, ch), 3)
        got = [e["goal"] for e in games[1]["events"] if "goal" in e]
        self.assertEqual(got, [{"team": "away", "score": "0:1", "name": "Сеснев Григорий"},   # из состава
                               {"team": "away", "score": "0:2", "name": "Нуреев Даниил"},
                               {"team": "away", "score": "0:3", "name": "Тиняев Ратмир"}])   # нет в составе — по каналу
        # скрытый по просьбе (ADR-007) — без имени; повторная сборка не копит старое
        people = [pl for x in proto["a"]["goals"] for pl in (x["author"], *x["assists"])]
        people += [x["player"] for x in proto["a"]["penalties"] if x["player"]]
        hid = next(pl["id"] for pl in people if pl["name"].startswith("Нуреев"))
        build_data.apply_goal_authors(games, proto, ch, {hid})
        self.assertEqual([e["goal"]["name"] for e in games[1]["events"] if "goal" in e][1], build_data.HIDDEN_NAME)

    def test_side_rules(self):
        import build_data
        ch = [{"handle": "rostovhc", "club": "rostov"}, {"handle": "nmhlpervenstvo", "club": None}]
        proto = {"a": {"lineups": [], "penalties": [],
                       "goals": [{"team": "away", "author": {"name": "Фурлетов Вячеслав", "id": 1}, "assists": []}]}}

        def run(src, text):
            games = [{"id": "a", "home": "rostov", "away": "krasnodar"},
                     {"id": "b", "home": "rostov", "away": "krasnodar",
                      "events": [{"kind": "text", "text": text, "src": src}]}]
            build_data.apply_goal_authors(games, proto, ch)
            return games[1]["events"][0].get("goal")
        # игрок «Краснодара» по составу — гол гостей, даже если пишет канал «Ростова» или лиги
        self.assertEqual(run("t.me/nmhlpervenstvo", "Шайбу забросил Вячеслав Фурлетов 0:1"),
                         {"team": "away", "score": "0:1", "name": "Фурлетов Вячеслав"})
        self.assertIsNone(run("t.me/nmhlpervenstvo", "Шайбу забросил Иван Петров 0:1"))   # лига, нет в составах
        self.assertEqual(run("t.me/rostovhc", "Шайбу забросил Иван Петров 1:0")["team"], "home")
        self.assertIsNone(run("t.me/rostovhc", "Шайбу забросил Иван Петров 0:1"))        # «0:1» хозяевам не гол


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
