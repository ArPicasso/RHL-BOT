"""Лист «Главной» (ADR-015): разбор t.me/s, фильтры постов и правила сборки листа."""
import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import feed  # noqa: E402
import tg_channels as tg  # noqa: E402

FIX = Path(__file__).parent / "fixtures"
TZ = ZoneInfo("Europe/Moscow")


def post(text="Тренировка перед выездом", **kw):
    return {"id": 1, "url": "https://t.me/x/1", "at": "2026-10-01T12:00+03:00", "text": text, "image": None,
            "media": 0, "video": False, "forwarded": False, "poll": False, **kw}


CLUB = {"handle": "x", "club": "a", "kind": "club", "scope": "all", "markers": []}


class Page(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.samara = tg.parse_page((FIX / "tg_hcsamara.html").read_text(encoding="utf-8"), "hcsamara")
        cls.ryazan = tg.parse_page((FIX / "tg_hcryazan_official.html").read_text(encoding="utf-8"), "hcryazan_official")

    def by_id(self, page, pid):
        return next(p for p in page["posts"] if p["id"] == pid)

    def test_title_posts_and_time(self):
        self.assertEqual(self.samara["title"], "ХК «САМАРА»")
        self.assertEqual([p["id"] for p in self.samara["posts"]], [3281, 3286, 3297, 3301])
        p = self.by_id(self.samara, 3281)
        self.assertEqual(p["url"], "https://t.me/hcsamara/3281")
        self.assertEqual(p["at"], "2026-09-26T14:00+03:00")   # в превью UTC, у нас Москва

    def test_image_only_from_telegram_cdn(self):
        p = self.by_id(self.samara, 3286)
        self.assertRegex(p["image"], tg.IMG_RE)
        self.assertEqual(p["media"], 1)
        bad = tg.POST_SPLIT + ('<div data-post="x/5"><a class="tgme_widget_message_photo_wrap" '
                               "style=\"background-image:url('https://evil.example/a.jpg')\"></a>"
                               '<a class="tgme_widget_message_date" href="#"><time datetime="2026-10-01T09:00:00+00:00">')
        self.assertIsNone(tg.parse_page(bad, "x")["posts"][0]["image"])

    def test_live_play_by_play(self):
        self.assertTrue(tg.is_live(self.by_id(self.samara, 3281)["text"]))
        self.assertFalse(tg.is_live(self.by_id(self.samara, 3286)["text"]))   # итог «завершаем со счётом»
        for text in ("27 секунд осталось до конца основного времени", "Будем ещё 52 секунды играть вчетвером",
                     "Текстовая трансляция", "5 минут до старта встречи. Ссылка на трансляцию"):
            self.assertTrue(tg.is_live(text), text)
        self.assertFalse(tg.is_live("Календарь сезона: 48 матчей, начинаем 6 октября дома. Трансляции всех "
                                    "домашних игр — в нашей группе, билеты уже в продаже на сайте клуба."))

    def test_repost_skipped(self):
        self.assertEqual(tg.skip_reason(self.by_id(self.samara, 3301), CLUB), "репост")

    def test_tail_of_channel_cut(self):
        title, text = tg.preview(self.by_id(self.samara, 3297)["text"])
        self.assertNotIn("МАКС", title + text)
        self.assertEqual(title, "Сайклы сами себя не покрутят 🚲")

    def test_adult_channel_only_youth_posts(self):
        system = {"handle": "hcryazan_official", "club": "ryazan-vdv", "kind": "system", "scope": "u21_only",
                  "markers": []}
        reasons = {p["id"]: tg.skip_reason(p, system) for p in self.ryazan["posts"]}
        self.assertEqual(reasons, {22372: "не о молодёжке", 22377: "день рождения", 22385: None})

    def test_collect_counts_dropped(self):
        keep, dropped = tg.collect(self.samara, CLUB)
        self.assertEqual([p["id"] for p in keep], [3281, 3286, 3297])
        self.assertEqual(dropped, {"репост": 1})
        self.assertTrue(keep[0]["live"])
        self.assertNotIn("live", keep[1])


class Filters(unittest.TestCase):
    def reason(self, text, channel=CLUB, names=()):
        # с картинкой: правило «коротко» проверяется отдельно, здесь — остальные фильтры
        return tg.skip_reason(post(text, media=1), channel, names)

    def test_ads_and_bookmakers(self):
        self.assertEqual(self.reason("Реклама. ООО «Ромашка», erid: 2Vtzq"), "реклама")
        self.assertEqual(self.reason("Промокод на мерч — HOCKEY"), "реклама")
        self.assertEqual(self.reason("Прогноз от Фонбета на матч"), "букмекер")
        self.assertEqual(self.reason("Ставки на победу «Рязани»"), "букмекер")
        self.assertIsNone(self.reason("Выставка к юбилейному сезону в фойе арены"))
        self.assertIsNone(self.reason("Розыгрыш абонементов на домашние матчи"))

    def test_birthdays_and_age(self):
        for text in ("С днём рождения, Иван!", "Тимофею Барсукову — 24!", "Нападающему исполняется 19 лет",
                     "Свой юбилей отмечает капитан"):
            self.assertEqual(self.reason(text), "день рождения", text)
        self.assertIsNone(self.reason("Стартует 15-й юбилейный сезон"))

    def test_age_of_players(self):
        self.assertEqual(self.reason("Знакомьтесь: нападающий Юрий Маркеев, 25.02.2006"), "возраст")
        self.assertEqual(self.reason("Команда 2009 г.р. выиграла турнир"), "возраст")
        self.assertIsNone(self.reason("Первый матч — 03.10.2026 в 17:00"))

    def test_rude_and_hidden_players(self):
        self.assertEqual(self.reason("Судья, ну бля"), "грубость")
        self.assertEqual(self.reason("Гол Петрова на 12-й минуте", names=["Петров"]), "скрытый игрок")
        self.assertIsNone(self.reason("Гол Иванова на 12-й минуте", names=["Петров"]))

    def test_markers_by_scope(self):
        general = {"handle": "fed", "club": None, "kind": "league", "scope": "u21_only",
                   "markers": ["РХЛ", "Российской хоккейной лиги"]}
        self.assertEqual(self.reason("Сборная U18 выиграла турнир", general), "не о молодёжке")
        self.assertIsNone(self.reason("Утверждён регламент РХЛ на сезон 2026/27", general))
        league = {"handle": "nmhlpervenstvo", "club": None, "kind": "league", "scope": "all", "markers": []}
        self.assertIsNone(self.reason("Итоги игрового дня", league))

    def test_own_markers_replace_general(self):
        khl = {"handle": "hcseverstal", "club": "metallurg", "kind": "system", "scope": "u21_only",
               "markers": ["МХК «Металлург»", "РХЛ"]}
        self.assertEqual(self.reason("МХК «Алмаз» обыграл «Локо»", khl), "не о молодёжке")
        self.assertIsNone(self.reason("МХК «Металлург» открыл сезон РХЛ", khl))
        vhl = {"handle": "hc_tambov", "club": "tambov", "kind": "system", "scope": "u21_only", "markers": []}
        self.assertIsNone(self.reason("Молодёжка готовится к старту", vhl))

    def test_media_only_empty_short_and_service(self):
        self.assertEqual(tg.skip_reason(post(""), CLUB), "пусто")
        self.assertEqual(tg.skip_reason(post("", media=1), CLUB), "без текста")
        self.assertEqual(tg.skip_reason(post("🤩 С победой!"), CLUB), "коротко")
        self.assertIsNone(tg.skip_reason(post("🤩 С победой!", media=1), CLUB))   # с фото — понятно
        self.assertEqual(tg.skip_reason(post("ХК «Факел» pinned a photo", service=True), CLUB), "служебное")

    def test_preview_cut_by_word(self):
        long = "Молодёжка " + "провела открытую тренировку на арене " * 10
        title, text = tg.preview(long)
        self.assertEqual(title, "")
        self.assertLessEqual(len(text), tg.PREVIEW + 1)
        self.assertTrue(text.endswith("…"))
        self.assertFalse(text[:-1].endswith(" "))

    def test_preview_title_and_links(self):
        title, text = tg.preview("Состав на матч\nСмотрим трансляцию: https://vk.com/video1 и болеем!\n\n@club")
        self.assertEqual(title, "Состав на матч")
        self.assertNotIn("http", text)
        self.assertNotIn("@club", text)

    def test_optout_images_keeps_text(self):
        p = post("Тренировка", image="https://cdn4.telesco.pe/file/a.jpg", media=1)
        self.assertIn("image", tg.entry(p, CLUB))
        self.assertNotIn("image", tg.entry(p, {**CLUB, "optout": {"level": "images", "date": "2026-10-05"}}))

    def test_wanted_channels(self):
        chans = [{**CLUB, "handle": "a"}, {**CLUB, "handle": "b", "kind": "fan"},
                 {**CLUB, "handle": "c", "optout": {"level": "all", "date": "2026-10-05"}}]
        self.assertEqual([c["handle"] for c in tg.wanted(chans)], ["a"])


# ---------- лист ----------

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=TZ)
CLUBS = ["a", "b", "c", "d", "e", "f"]


def game(gid, day, home, away, score=None):
    g = {"id": gid, "n": 1, "date": day, "home": home, "away": away, "official": False}
    if score:
        g["score"] = {"home": score[0], "away": score[1], "decision": ""}
    return g


def channel(handle, club, kind="club", notified="2026-09-01", **kw):
    return {"handle": handle, "club": club, "kind": kind, "scope": "all", "markers": [], "notified": notified,
            "optout": None, **kw}


def posts_of(handle, *ages_h, live=False, title="Канал"):
    """Посты канала возрастом ages_h часов от NOW."""
    out = []
    for i, h in enumerate(ages_h):
        p = {"id": 100 + i, "url": f"https://t.me/{handle}/{100 + i}", "title": "", "text": f"Пост {i}", "media": 0,
             "video": False, "at": feed.iso(NOW - timedelta(hours=h))}
        if live:
            p["live"] = True
        out.append(p)
    return {"ok": True, "title": title, "posts": out}


class World:
    """Клуб a: вчера сыграл с b, через 2 дня — с c. У всех клубов каналы с постами."""

    def __init__(self):
        self.games = [game("g1", "2026-10-09", "a", "b", (3, 2)), game("g2", "2026-10-12", "c", "a"),
                      game("g3", "2026-10-10", "d", "e"), game("g4", "2026-10-10", "f", "b")]
        self.h2h = {"a|c": {"games": 4, "wins": {"a": 3, "c": 1}, "goals": {"a": 12, "c": 7}, "since": "2021",
                            "last": [{"date": "2026-02-01", "home": "c", "away": "a", "score": [2, 3],
                                      "decision": "ОТ", "id": "h7"}]}}
        self.history = [{"date": "2024-10-10", "season": "24/25", "home": "a", "away": "d", "score": [5, 4],
                         "decision": "Б", "game_id": 9},
                        {"date": "2022-10-10", "season": "22/23", "home": "a", "away": "e", "score": [1, 2],
                         "decision": "", "game_id": 8}]
        self.recaps = {"h7": {"story": "Камбэк «a»."}, "h9": {"story": "Всё решили буллиты."},
                       "g1": {"story": "Три шайбы подряд у «a»."}}
        self.standings = {"east": [{"team": "a", "gp": 1, "pts": 2}, {"team": "b", "gp": 1, "pts": 0}]}
        self.channels = [channel(f"ch_{c}", c) for c in CLUBS] + [channel("fhr", None, "league", markers=["РХЛ"])]
        self.posts = {f"ch_{c}": posts_of(f"ch_{c}", 1, 5, 9) for c in CLUBS}
        self.posts["fhr"] = posts_of("fhr", 3)

    def build(self, club="a", now=NOW, **kw):
        args = dict(clubs=CLUBS, games=self.games, standings=self.standings, h2h=self.h2h, history=self.history,
                    recaps=self.recaps, channels=self.channels, posts=self.posts)
        args.update(kw)
        return feed.build(club, now, **args)


def kinds(sheet):
    return [c["kind"] for c in sheet["cards"]]


class State(unittest.TestCase):
    def state(self, games, today):
        nxt, last = feed.schedule("a", games, today)
        return feed.day_state(nxt, last, today)

    def test_states(self):
        played = game("p", "2026-10-01", "a", "b", (1, 0))
        g = lambda day: game("n", day, "a", "c")   # noqa: E731
        self.assertEqual(self.state([played, g("2026-10-10")], date(2026, 10, 10)), "match")
        self.assertEqual(self.state([played, g("2026-10-11")], date(2026, 10, 10)), "eve")
        self.assertEqual(self.state([played, g("2026-10-13")], date(2026, 10, 10)), "normal")
        self.assertEqual(self.state([played, g("2026-10-20")], date(2026, 10, 10)), "pause")
        self.assertEqual(self.state([g("2026-10-13")], date(2026, 10, 10)), "start")
        self.assertEqual(self.state([played], date(2026, 10, 2)), "after")
        self.assertEqual(self.state([played], date(2026, 10, 10)), "over")


class Sheet(unittest.TestCase):
    def setUp(self):
        self.w = World()

    def test_shares_and_order(self):
        sheet = self.w.build()
        cards = sheet["cards"]
        self.assertNotEqual(cards[0]["kind"], "post")
        n_posts = sum(1 for c in cards if c["kind"] == "post")
        self.assertGreater(n_posts, 0)
        # посты своего клуба в долю 40% не идут (пересмотр 01.10)
        rest = [c for c in cards if c.get("slot") != "mine"]
        self.assertLessEqual(sum(1 for c in rest if c["kind"] == "post"), 0.4 * len(rest) + 1e-9)
        for x, y in zip(cards, cards[1:]):
            self.assertFalse(x["kind"] == y["kind"] == "post", kinds(sheet))
        self.assertLessEqual(len(cards), feed.MAX_CARDS)
        self.assertEqual(len({c["id"] for c in cards}), len(cards))

    def test_own_cards_of_the_day(self):
        sheet = self.w.build()
        self.assertEqual(sheet["state"], "after")
        self.assertEqual(sheet["next"], "g2")
        by = {c["kind"]: c for c in sheet["cards"]}
        self.assertEqual(by["h2h"]["wins"], [3, 1])
        self.assertEqual(by["meeting"]["match"], "h7")
        self.assertEqual(by["day"]["season"], "24/25")        # самый свежий сезон
        self.assertEqual(by["day"]["match"], "h9")
        self.assertEqual(by["today"]["n"], 2)
        self.assertEqual(by["story"]["match"], "g1")          # других сюжетов вчера нет — свой
        self.assertEqual((by["table"]["place"], "up" in by["table"]), (1, False))

    def test_h2h_three_days_before(self):
        far = self.w.build(now=NOW - timedelta(days=2))       # 8.10: до матча 4 дня
        self.assertNotIn("h2h", kinds(far))
        self.assertIn("h2h", kinds(self.w.build(now=NOW - timedelta(days=1))))

    def test_posts_without_letter(self):
        # предварительного согласия не ждём: письма нет — посты всё равно идут (ADR-015, 30.09)
        self.w.channels = [channel(f"ch_{c}", c, notified=None) for c in CLUBS]
        self.assertIn("post", kinds(self.w.build()))

    def test_slots_mine_rival_first(self):
        posts = [c for c in self.w.build()["cards"] if c["kind"] == "post"]
        self.assertEqual(posts[0]["slot"], "mine")
        self.assertEqual(posts[1]["slot"], "opp")
        self.assertEqual(posts[1]["club"], "c")

    def test_channel_limits(self):
        many_own = [{"id": f"o{i}", "kind": "day"} for i in range(9)]
        queue = [feed.post_card(p, channel("ch_a", "a"), "A", "mine") for p in posts_of("ch_a", 1, 2, 3)["posts"]]
        queue += [feed.post_card(p, channel("ch_d", "d"), "D", "club") for p in posts_of("ch_d", 1, 2)["posts"]]
        cards = feed.assemble(many_own, queue)
        chans = [(i, c["channel"]) for i, c in enumerate(cards) if c["kind"] == "post"]
        first8 = [ch for i, ch in chans if i < feed.FIRST]
        self.assertEqual(len(first8), len(set(first8)))                       # в первых восьми — по одному
        self.assertLessEqual(sum(1 for _, ch in chans if ch == "ch_a"), feed.PER_CHANNEL)
        self.assertEqual(sum(1 for _, ch in chans if ch == "ch_d"), 1)       # чужой клуб — один пост

    def test_freshness_and_live(self):
        self.w.posts["ch_a"] = posts_of("ch_a", 2, 4, live=True)
        self.w.posts["ch_c"] = posts_of("ch_c", 1, live=True)
        cards = [c for c in self.w.build()["cards"] if c["kind"] == "post"]
        mine = [c for c in cards if c["slot"] == "mine"]
        self.assertEqual([c["id"] for c in mine], ["post-ch_a-100"])          # трансляция 4 часа назад — нет
        self.assertFalse([c for c in cards if c["slot"] == "opp"])           # трансляция соперника — нет
        self.w.posts["ch_a"] = posts_of("ch_a", 49)
        self.assertFalse([c for c in self.w.build()["cards"] if c.get("slot") == "mine"])

    def test_optout_all_and_fan(self):
        self.w.channels = [channel("ch_a", "a", optout={"level": "all", "date": "2026-10-01"}),
                           channel("ch_c", "c", kind="fan")]
        self.assertNotIn("post", kinds(self.w.build()))

    def test_own_club_posts_outside_share(self):
        # одна своя карточка («Дальше») — доля чужих постов ноль, но новость своего клуба встаёт
        own = [{"id": "next-1", "kind": "upcoming"}]
        queue = [feed.post_card(p, channel("ch_a", "a"), "A", "mine") for p in posts_of("ch_a", 1, 2)["posts"]]
        queue += [feed.post_card(p, channel("ch_d", "d"), "D", "club") for p in posts_of("ch_d", 1)["posts"]]
        self.assertEqual([c["kind"] for c in feed.assemble(own, queue)], ["upcoming", "post"])
        self.assertEqual(feed.assemble(own, queue)[1]["slot"], "mine")
        self.assertEqual(feed.assemble(own, queue[2:]), own)              # чужой пост — нет

    def test_short_channel_name(self):
        c = channel("ch_a", "a", short="ХК «Сокол»")
        posts = {"ch_a": posts_of("ch_a", 1, title="🦅 ХК «Сокол» – Новочебоксарск")}
        card = feed.club_posts("a", [c], posts, NOW, "mine", 1)[0]
        self.assertEqual((card["ctitle"], card["cfull"]), ("ХК «Сокол»", "🦅 ХК «Сокол» – Новочебоксарск"))
        plain = feed.club_posts("a", [channel("ch_a", "a")], posts, NOW, "mine", 1)[0]
        self.assertNotIn("cfull", plain)

    def test_no_posts_without_own_cards(self):
        self.assertEqual(feed.assemble([], [feed.post_card(p, channel("ch_a", "a"), "A", "mine")
                                            for p in posts_of("ch_a", 1)["posts"]]), [])

    def test_rotation_visits_every_club(self):
        seen = set()
        others = [c for c in CLUBS if c not in ("a", "c")]
        for day in range(len(others)):
            seen.add(feed.rotation("a", CLUBS, {"c"}, day)[0])
        self.assertEqual(seen, set(others))
        # в один день клубы видят разных «первых» по кругу
        firsts = [feed.rotation(c, CLUBS, set(), 5)[0] for c in CLUBS]
        self.assertGreater(len(set(firsts)), 1)

    def test_story_prefers_brightest_other_match(self):
        games = [game("x1", "2026-10-09", "d", "e", (2, 1)), game("x2", "2026-10-09", "f", "b", (4, 3)),
                 game("x3", "2026-10-09", "a", "c", (3, 2))]
        recaps = {"x1": {"story": "В овертайме победу принёс Иванов."}, "x2": {"story": "Камбэк «f»: уступали 1:3."},
                  "x3": {"story": "Камбэк «a»."}}
        self.assertEqual(feed.story_card("a", games, recaps, date(2026, 10, 10))["match"], "x2")

    def test_upcoming_is_never_new(self):
        games = [game(f"u{i}", f"2026-10-1{i}", "a", "b") for i in range(1, 6)]
        nxt, _ = feed.schedule("a", games, date(2026, 10, 10))
        card = feed.upcoming_card("a", nxt, games, date(2026, 10, 10))
        self.assertEqual(card["games"], ["u2", "u3", "u4"])
        self.assertIsNone(card["at"])

    def test_leaders_only_good_and_best_place(self):
        leaders = {"season": "2025/26", "league": "НМХЛ", "categories": {
            "pim": [{"rank": 1, "name": "Драчунов Иван", "team": "a"}],
            "pts": [{"rank": 3, "name": "Иванов Пётр", "team": "a"}, {"rank": 1, "name": "Чужой", "team": "b"}],
            "g": [{"rank": 2, "name": "Иванов Пётр", "team": "a"}, {"rank": 7, "name": "Сидоров Олег", "team": "a"}],
        }}
        card = feed.leaders_card("a", leaders, date(2026, 10, 10))
        self.assertEqual(card["rows"], [{"name": "Иванов Пётр", "rank": 2, "cat": "g"},
                                        {"name": "Сидоров Олег", "rank": 7, "cat": "g"}])
        self.assertIsNone(feed.leaders_card("c", leaders, date(2026, 10, 10)))
        # в игровой день лидеров в листе нет, в тихий — есть
        w = World()
        self.assertNotIn("leaders", kinds(w.build(leaders=leaders)))
        self.assertIn("leaders", kinds(w.build(now=NOW + timedelta(days=5), leaders=leaders, games=w.games[:1] + [
            game("g9", "2026-10-25", "a", "d")])))

    def test_table_card_is_positive(self):
        rows = [{"team": t, "gp": 3, "pts": p} for t, p in zip("bcdefghij", (9, 8, 7, 6, 5, 4, 3, 2, 1))]
        standings = {"east": rows + [{"team": "a", "gp": 3, "pts": 0}]}
        last = game("g", "2026-10-09", "a", "b", (1, 2))
        card = feed.table_card("a", standings, last, date(2026, 10, 10))
        self.assertEqual((card["place"], card["to8"]), (10, 2))
        self.assertIsNone(feed.table_card("a", standings, last, date(2026, 10, 12)))
        # первому — отрыв от второго
        top = feed.table_card("b", standings, game("g", "2026-10-09", "b", "a", (2, 1)), date(2026, 10, 10))
        self.assertEqual((top["place"], top["lead"]), (1, 1))



class Stream(unittest.TestCase):
    def test_week_newest_first_without_live(self):
        chans = [channel("ch_a", "a"), channel("ch_b", "b", kind="fan")]
        posts = {"ch_a": posts_of("ch_a", 1, 30, 160, 200), "ch_b": posts_of("ch_b", 2)}
        posts["ch_a"]["posts"][0]["live"] = True
        got = feed.stream_posts(chans, posts, NOW)
        self.assertEqual([p["id"] for p in got], ["post-ch_a-101", "post-ch_a-102"])   # 200 ч — старше недели
        self.assertEqual(got[0]["slot"], "stream")

    def test_one_channel_not_three_in_a_row(self):
        chans = [channel("ch_a", "a"), channel("ch_b", "b")]
        posts = {"ch_a": posts_of("ch_a", 1, 2, 3, 4, 5), "ch_b": posts_of("ch_b", 10, 11)}
        order = [p["channel"] for p in feed.stream_posts(chans, posts, NOW)]
        self.assertEqual(order, ["ch_a", "ch_a", "ch_b", "ch_a", "ch_a", "ch_b", "ch_a"])

    def test_own_card_every_five_posts(self):
        w = World()
        w.games[0]["date"] = "2026-10-09"
        stream = feed.build_stream(NOW, games=w.games, history=w.history, recaps=w.recaps,
                                   channels=w.channels, posts=w.posts)
        kinds_ = [c["kind"] for c in stream["items"]]
        self.assertEqual(kinds_[:6], ["post"] * 5 + ["story"])
        self.assertIn("day", kinds_)
        self.assertEqual(len({c["id"] for c in stream["items"]}), len(kinds_))

    def test_story_per_played_day(self):
        games = [game("x1", "2026-10-09", "d", "e", (2, 1)), game("x2", "2026-10-07", "f", "b", (4, 3))]
        recaps = {"x1": {"story": "Всё решили буллиты."}, "x2": {"story": "Камбэк «f»."}}
        cards = feed.stream_cards(games, [], recaps, date(2026, 10, 10))
        self.assertEqual([(c["match"], c["date"]) for c in cards], [("x1", "2026-10-09"), ("x2", "2026-10-07")])

    def test_this_day_one_per_season_story_first(self):
        history = [{"date": f"{y}-10-10", "season": "", "home": "a", "away": b, "score": [1, 0], "decision": "",
                    "game_id": n} for n, (y, b) in enumerate([(2022, "b"), (2022, "c"), (2023, "d"), (2024, "e")])]
        cards = feed.stream_cards([], history, {"h2": {"story": "Камбэк."}}, date(2026, 10, 10))
        self.assertEqual([(c["date"][:4], c.get("match")) for c in cards], [("2023", "h2"), ("2024", None), ("2022", None)])


    def test_own_cards_before_first_tour(self):
        # до тура сюжетов нет: матчи ближайших дней, очные встречи серий и лидеры по клубам — по кругу
        games = [game("t1", "2026-10-11", "a", "b"), game("t2", "2026-10-12", "a", "b"),
                 game("t3", "2026-10-11", "c", "d"), game("t4", "2026-10-20", "e", "f")]
        h2h = {"a|b": {"games": 3, "wins": {"a": 2, "b": 1}, "goals": {"a": 9, "b": 5}, "since": "2022"},
               "e|f": {"games": 1, "wins": {"e": 1}, "goals": {"e": 2, "f": 1}}}
        leaders = {"season": "2025/26", "league": "НМХЛ", "categories": {
            "pts": [{"rank": 1, "name": "Иванов Пётр", "team": "c"}, {"rank": 2, "name": "Петров Иван", "team": "b"}]}}
        cards = feed.stream_cards(games, [], {}, date(2026, 10, 10), h2h=h2h, leaders=leaders, clubs=CLUBS)
        self.assertEqual([c["kind"] for c in cards], ["today", "h2h", "leaders", "today", "leaders"])
        self.assertEqual((cards[0]["date"], cards[0]["n"], cards[0]["games"]), ("2026-10-11", 2, ["t1", "t3"]))
        self.assertEqual((cards[1]["id"], cards[1]["club"], cards[1]["wins"]), ("h2h-t1", "a", [2, 1]))  # пара — одна
        self.assertEqual(cards[3]["date"], "2026-10-12")
        self.assertEqual({c["club"] for c in cards if c["kind"] == "leaders"}, {"b", "c"})
        self.assertEqual(sum(1 for c in cards if c["kind"] == "h2h"), 1)   # e—f через 10 дней — не в неделе
        self.assertEqual(len({c["id"] for c in cards}), len(cards))

    def test_stream_ids_match_sheet(self):
        # те же id, что в листе клуба: в «Все» лента не повторяет лист
        w = World()
        sheet = {c["id"] for c in w.build(now=NOW - timedelta(days=1))["cards"]}
        stream = feed.stream_cards(w.games, w.history, w.recaps, date(2026, 10, 9), h2h=w.h2h, clubs=CLUBS)
        self.assertIn("h2h-g2", sheet & {c["id"] for c in stream})


if __name__ == "__main__":
    unittest.main()
