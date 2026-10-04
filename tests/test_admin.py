"""Пульт админа (ADR-021): счётчики служб, открытия мини-аппа, systemctl, сборки GitHub и сводка проблем."""
import json
import logging
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import admin  # noqa: E402
import predict  # noqa: E402
from raskat_store import RaskatStore  # noqa: E402

TZ = ZoneInfo("Europe/Moscow")
NOW = datetime(2026, 10, 3, 20, 0, tzinfo=TZ)
TEAMS = {"ryazan-vdv": "Рязань-ВДВ", "tambov": "Тамбов", "sokol": "Сокол"}


def ago(**kw) -> str:
    return admin.iso(NOW - timedelta(**kw))


class TrackerTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.now = NOW
        self.t = admin.Tracker("bot", self.dir / "bot.json", clock=lambda: self.now)

    def test_counts_by_moscow_day_and_survives_restart(self):
        self.t.add("starts")
        self.t.add("starts", 2)
        self.now = NOW + timedelta(hours=5)   # 01:00 МСК — уже следующий день
        self.t.add("starts")
        self.assertTrue(self.t.flush())
        again = admin.Tracker("bot", self.dir / "bot.json", clock=lambda: self.now)
        self.assertEqual(again.days, {"2026-10-03": {"starts": 3}, "2026-10-04": {"starts": 1}})

    def test_old_days_dropped(self):
        self.t.days["2026-08-01"] = {"starts": 5}
        self.t.add("starts")
        self.t.flush()
        data = json.loads((self.dir / "bot.json").read_text(encoding="utf-8"))
        self.assertEqual(list(data["days"]), ["2026-10-03"])
        self.assertEqual(data["beat"], admin.iso(NOW))

    def test_log_keeps_last_ten_newest_first(self):
        for i in range(12):
            self.t.note({"kind": "final", "sent": i})
        self.assertEqual([x["sent"] for x in self.t.log_][:2], [11, 10])
        self.assertEqual(len(self.t.log_), admin.LOG_KEEP)

    def test_unwritable_dir_does_not_raise(self):
        blocker = self.dir / "file"
        blocker.write_text("x")
        t = admin.Tracker("bot", blocker / "bot.json", clock=lambda: NOW)
        with self.assertLogs("admin", level="WARNING"):
            self.assertFalse(t.flush())

    def test_error_counter_hides_chat_ids(self):
        log = logging.getLogger("test-admin-errors")
        log.propagate = False
        log.addHandler(admin.ErrorCount(self.t))
        try:
            raise ValueError("boom")
        except ValueError:
            log.exception("send to %s failed", 123456789)
        log.error("result to 987654321 failed")
        self.assertEqual(self.t.today()["errors"], 2)
        self.assertEqual(self.t.info_["last_error"]["what"], "result to … failed")
        self.assertNotIn("123456789", json.dumps(self.t.snapshot()))


class AdminStoreTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.s = admin.AdminStore(self.conn)

    def test_one_person_counted_once_a_day(self):
        d = date(2026, 10, 3)
        self.assertTrue(self.s.seen(1, d, "ios", "tambov"))
        self.assertFalse(self.s.seen(1, d, "android", "sokol"))
        self.assertTrue(self.s.seen(2, d, "android", None))
        self.assertTrue(self.s.seen(1, d + timedelta(days=1), "ios", "tambov"))
        got = self.s.counts(d)
        self.assertEqual(got["2026-10-03"], {"app_users": 2, "platform:ios": 1, "platform:android": 1, "fav:tambov": 1})
        self.assertEqual(got["2026-10-04"]["app_users"], 1)

    def test_odd_platform_not_stored(self):
        self.s.seen(1, date(2026, 10, 3), "<script>", None)
        self.assertEqual(self.s.counts(date(2026, 10, 3))["2026-10-03"], {"app_users": 1})

    def test_ids_kept_until_end_of_yesterday_and_forgettable(self):
        self.s.seen(1, date(2026, 10, 1))
        self.s.seen(2, date(2026, 10, 2))
        self.s.seen(3, date(2026, 10, 3))
        fans = sorted(f for (f,) in self.conn.execute("SELECT fan FROM admin_seen"))
        self.assertEqual(fans, [2, 3])
        self.s.forget(3)
        self.assertEqual([f for (f,) in self.conn.execute("SELECT fan FROM admin_seen")], [2])
        # счётчик дня от этого не меняется: в нём нет id
        self.assertEqual(self.s.counts(date(2026, 10, 3))["2026-10-03"]["app_users"], 1)


class GameStatsTest(unittest.TestCase):
    def test_raskat_and_votes_by_day(self):
        conn = sqlite3.connect(":memory:", isolation_level=None)
        RaskatStore(conn)
        pr = predict.PredictStore(conn)
        conn.execute("INSERT INTO raskat_results VALUES (1, '2026-10-03', 'tambov', 10, 10, 1000, 0, 1, 'x')")
        conn.execute("INSERT INTO raskat_results VALUES (2, '2026-10-03', NULL, 10, 10, 1000, 0, 1, 'x')")
        conn.execute("INSERT INTO raskat_results VALUES (1, '2026-10-02', 'tambov', 10, 10, 1000, 0, 1, 'x')")
        key = "2026-10-03|ryazan-vdv|tambov"
        pr.vote(1, key, "home", NOW)
        pr.vote(2, key, "away", NOW)
        pr.vote(2, "2026-10-03|sokol|tambov", "home", NOW)
        got = admin.game_stats(conn, date(2026, 9, 27), date(2026, 10, 3))
        self.assertEqual(got["days"]["2026-10-03"], {"raskat": 2, "votes": 3, "voters": 2})
        self.assertEqual(got["days"]["2026-10-02"], {"raskat": 1})
        self.assertEqual(got["raskat_players"], 2)
        self.assertEqual(got["predict_top"][0], {"key": key, "home": 1, "away": 1, "votes": 2})

    def test_no_tables_yet(self):
        got = admin.game_stats(sqlite3.connect(":memory:"), date(2026, 9, 27), date(2026, 10, 3))
        self.assertEqual(got, {"days": {}, "raskat_players": None, "predict_top": []})


class SystemctlTest(unittest.TestCase):
    OUT = ("Id=bot.service\nLoadState=loaded\nActiveState=active\nSubState=running\nNRestarts=2\n"
           "ActiveEnterTimestamp=@1791043200\n\n"
           "Id=caddy.service\nLoadState=not-found\nActiveState=inactive\nSubState=dead\nNRestarts=0\n"
           "ActiveEnterTimestamp=\n\n"
           "Id=live.service\nLoadState=loaded\nActiveState=failed\nSubState=failed\nNRestarts=0\n"
           "ActiveEnterTimestamp=@0\n")

    def test_parse(self):
        got = admin.parse_systemctl(self.OUT)
        self.assertEqual(got["bot"]["state"], "active")
        self.assertEqual(got["bot"]["restarts"], 2)
        self.assertTrue(got["bot"]["since"].endswith("+03:00"))
        self.assertEqual(got["caddy"]["state"], "not-found")
        self.assertEqual(got["live"], {"state": "failed", "sub": "failed", "since": None, "restarts": 0})

    def test_args_list_all_units(self):
        args = admin.systemctl_args()
        self.assertEqual(args[:2], ["systemctl", "show"])
        self.assertIn("tg-tunnel.service", args)


class RunsTest(unittest.TestCase):
    def run_(self, wf, conclusion, minutes, status="completed"):
        at = (NOW - timedelta(minutes=minutes)).astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {"path": f".github/workflows/{wf}", "status": status, "conclusion": conclusion,
                "created_at": at, "updated_at": at, "html_url": "https://github.com/x/y/actions/runs/1"}

    def test_summary(self):
        runs = [self.run_("pages.yml", None, 2, "in_progress"), self.run_("pages.yml", "failure", 20),
                self.run_("pages.yml", "success", 40), self.run_("pages.yml", "failure", 2000),
                self.run_("deploy.yml", "success", 300)]
        got = {r["id"]: r for r in admin.summarize_runs(runs, NOW)}
        self.assertEqual(got["pages"]["status"], "in_progress")
        self.assertEqual(got["pages"]["last_ok"], ago(minutes=40))
        self.assertEqual((got["pages"]["runs_24h"], got["pages"]["fails_24h"]), (3, 1))
        self.assertEqual(got["deploy"]["conclusion"], "success")
        self.assertIsNone(got["tests"]["at"])


def healthy(**over) -> dict:
    """Всё здорово — как на сервере в обычный вечер."""
    kw = dict(
        now=NOW, teams=TEAMS,
        services={u: {"state": "active", "sub": "running", "since": ago(hours=3), "restarts": 0} for u in admin.UNITS},
        bot={"started": ago(hours=3), "beat": ago(seconds=30), "info": {"tg_ok": ago(seconds=30)},
             "days": {"2026-10-03": {"starts": 10, "start:team": 4, "start:plain": 6, "sub_new": 3}},
             "log": [{"at": ago(minutes=5), "kind": "final", "sent": 3}]},
        pages={"info": {"token": True, "kick_ok": ago(minutes=5),
                        "runs": [{"id": "pages", "title": "Мини-апп", "conclusion": "success", "last_ok": ago(minutes=10)},
                                 {"id": "deploy", "title": "Выложить бота", "conclusion": "success"}]}},
        league_updated=ago(minutes=10), live_today={"updated": ago(seconds=20)},
        sources={"online.khl.ru": {"ok": ago(seconds=20), "errors": 0, "games": 4, "note": ""}},
        raskat={"on": True, "note": "соль сошлась"},
        disk={"free": 10 << 30, "total": 25 << 30, "db": 1 << 20},
        subs={"1": ["tambov", "sokol"], "2": ["tambov"], "3": ["ryazan-vdv"]},
        app_counts={"2026-10-03": {"app_users": 7, "platform:ios": 7, "fav:tambov": 5}},
        games={"days": {"2026-10-03": {"raskat": 4, "votes": 9}}, "raskat_players": 12,
               "predict_top": [{"key": "2026-10-03|ryazan-vdv|tambov", "home": 5, "away": 4, "votes": 9}]})
    kw.update(over)
    return admin.build_status(**kw)


class BuildStatusTest(unittest.TestCase):
    def test_healthy_has_no_problems(self):
        st = healthy()
        self.assertEqual(st["problems"], [])
        self.assertEqual(st["audience"]["subscribers"], 3)
        self.assertEqual(st["audience"]["by_team"][0], {"id": "tambov", "name": "Тамбов", "n": 2})
        self.assertEqual(st["audience"]["fans"], [{"id": "tambov", "name": "Тамбов", "n": 5}])
        self.assertEqual(st["audience"]["links_week"][0], {"id": "plain", "name": "plain", "n": 6})
        self.assertEqual(st["games"]["predict_top"][0]["title"], "Рязань-ВДВ — Тамбов")
        today = st["days"][0]
        self.assertEqual((today["date"], today["starts"], today["app_users"], today["raskat"]), ("2026-10-03", 10, 7, 4))
        self.assertEqual(len(st["days"]), admin.WEEK)

    def test_old_subscribers_format(self):
        self.assertEqual(healthy(subs=[1, 2])["audience"]["subscribers"], 2)

    def texts(self, **over):
        return [(p["level"], p["text"]) for p in healthy(**over)["problems"]]

    def test_service_down_and_restarts(self):
        services = {u: {"state": "active", "restarts": 0} for u in admin.UNITS}
        services["live"] = {"state": "failed", "sub": "failed"}
        services["bot"] = {"state": "active", "restarts": 3}
        services["caddy"] = {"state": "not-found"}   # https.sh ещё не ставили — не тревога
        got = self.texts(services=services)
        self.assertIn(("bad", "Живое: служба упала"), got)
        self.assertIn(("warn", "Бот: служба падала и поднималась 3 раза с последней выкладки"), got)
        self.assertEqual(len(got), 2)

    def test_bot_silent_or_no_telegram(self):
        bot = {"beat": ago(minutes=10), "info": {"tg_ok": ago(minutes=12), "tg_fail": ago(minutes=10),
                                                  "tg_error": "TelegramNetworkError: timeout"}}
        got = [t for _, t in self.texts(bot=bot)]
        self.assertTrue(any(t.startswith("Бот молчит: последний пульс 10 мин назад") for t in got), got)
        self.assertTrue(any("tg-tunnel" in t for t in got), got)
        self.assertIn("Бот не пишет пульс", self.texts(bot=None)[0][1])

    def test_pages_build_stale_only_by_day(self):
        pages = {"info": {"token": True, "runs": [{"id": "pages", "conclusion": "failure", "last_ok": ago(hours=2)}]}}
        self.assertIn(("bad", "Мини-апп не собирался 2 ч: последний запуск — упал"), self.texts(pages=pages))
        night = NOW.replace(hour=3)
        self.assertEqual(self.texts(now=night, pages=pages, league_updated=admin.iso(night - timedelta(hours=1)),
                                    bot={"beat": admin.iso(night), "info": {}}), [])

    def test_league_sources_raskat_disk_deploy(self):
        got = self.texts(league_updated=ago(hours=3),
                         sources={"online.khl.ru": {"errors": 3, "note": "HTTP 403 для chat 1234567"}},
                         raskat={"on": False, "note": "файл дня не читается"},
                         disk={"free": 300 << 20, "total": 25 << 30, "db": 0},
                         pages={"info": {"token": False, "runs": [{"id": "deploy", "conclusion": "failure"}]}})
        texts = [t for _, t in got]
        self.assertIn("Данные мини-аппа (league.json) собраны 3 ч назад", texts)
        self.assertIn("Источник online.khl.ru: ошибок подряд — 3. HTTP 403 для chat …", texts)
        self.assertIn("Зачёт «Раската» выключен: файл дня не читается", texts)
        self.assertIn("На диске меньше 1 ГБ: 300 МБ", texts)
        self.assertIn("Последняя выкладка бота на сервер красная", texts)
        self.assertIn("У службы pages нет PAGES_TOKEN: сборку не будим, за сборками не следим", texts)

    def test_no_systemctl_is_a_warning_not_a_crash(self):
        st = healthy(services=None, services_note="systemctl: FileNotFoundError")
        self.assertIsNone(st["system"]["services"])
        self.assertEqual(st["problems"], [{"level": "warn", "key": "services",
                                           "text": "Состояние служб не прочиталось: systemctl: FileNotFoundError"}])


class Alerts(unittest.TestCase):
    """Тревоги админу (ADR-022, раздел 3): новое — сразу, то же — раз в час, ушло — «починилось»."""

    tg = {"level": "bad", "key": "bot:telegram", "text": "Бот не достучался до Telegram. Проверь tg-tunnel"}
    tests_red = {"level": "warn", "key": "build:tests", "text": "Последний прогон тестов красный"}
    live = {"level": "bad", "key": "service:live", "text": "Живое: служба упала"}

    def test_new_problem_goes_out_at_once(self):
        groups, state = admin.alert_plan([self.tg, self.tests_red], {}, NOW)
        self.assertEqual(groups, {"broke": [self.tg["text"]], "watch": [self.tests_red["text"]]})
        self.assertEqual(set(state), {"bot:telegram", "build:tests"})
        self.assertEqual(state["bot:telegram"]["at"], admin.iso(NOW))

    def test_same_cause_waits_an_hour(self):
        _, state = admin.alert_plan([self.tg], {}, NOW)
        later = NOW + timedelta(minutes=59)
        groups, kept = admin.alert_plan([self.tg], state, later)
        self.assertEqual(groups, {})
        self.assertEqual(kept["bot:telegram"]["at"], admin.iso(NOW))   # час считаем от первой тревоги
        groups, after = admin.alert_plan([self.tg], state, NOW + timedelta(hours=1, seconds=1))
        self.assertEqual(groups, {"still": [self.tg["text"]]})
        self.assertEqual(after["bot:telegram"]["at"], admin.iso(NOW + timedelta(hours=1, seconds=1)))

    def test_text_changes_but_cause_is_the_same(self):
        """«молчит 7 мин» → «молчит 8 мин» — та же поломка, второй раз не пишем."""
        a = {"level": "bad", "key": "bot:beat", "text": "Бот молчит: последний пульс 7 мин назад"}
        b = {**a, "text": "Бот молчит: последний пульс 8 мин назад"}
        _, state = admin.alert_plan([a], {}, NOW)
        groups, kept = admin.alert_plan([b], state, NOW + timedelta(minutes=1))
        self.assertEqual(groups, {})
        self.assertEqual(kept["bot:beat"]["text"], b["text"])   # помним свежий текст

    def test_warning_is_said_once(self):
        _, state = admin.alert_plan([self.tests_red], {}, NOW)
        groups, _ = admin.alert_plan([self.tests_red], state, NOW + timedelta(hours=5))
        self.assertEqual(groups, {})

    def test_warning_that_became_a_breakage_goes_out(self):
        _, state = admin.alert_plan([{**self.live, "level": "warn", "text": "Живое: служба падала"}], {}, NOW)
        groups, _ = admin.alert_plan([self.live], state, NOW + timedelta(minutes=1))
        self.assertEqual(groups, {"broke": [self.live["text"]]})

    def test_gone_means_fixed_and_forgotten(self):
        _, state = admin.alert_plan([self.tg, self.live], {}, NOW)
        groups, after = admin.alert_plan([self.live], state, NOW + timedelta(minutes=5))
        self.assertEqual(groups, {"fixed": [self.tg["text"]]})
        self.assertEqual(set(after), {"service:live"})
        groups, empty = admin.alert_plan([], after, NOW + timedelta(minutes=10))
        self.assertEqual(groups, {"fixed": [self.live["text"]]})
        self.assertEqual(empty, {})

    def test_junk_and_doubles_are_skipped(self):
        groups, state = admin.alert_plan(
            [{"level": "bad", "text": "без ключа"}, {"level": "bad", "key": "disk", "text": ""},
             self.live, {**self.live, "text": "он же вторым разом"}], {}, NOW)
        self.assertEqual(groups, {"broke": [self.live["text"]]})
        self.assertEqual(list(state), ["service:live"])

    def test_payload_for_the_bot(self):
        self.assertEqual(admin.alerts_payload([self.live], NOW),
                         {"at": admin.iso(NOW), "problems": [self.live]})


if __name__ == "__main__":
    unittest.main()
