"""Сообщения «Звена» через Bot API тем же токеном (ADR-014, раздел 10; контракт, раздел 5).

- воскресенье 18:00 по поясу любимого клуба (поля `tz` в teams.json нет — МСК) — только если есть что решить;
- вторник 18:00 — история прошедшего тура от талисмана и повышение в ступенях (о понижении не пишем);
- не больше двух в неделю; три тура не открывал «Звено» — не чаще раза в две недели;
- о стоимости — никогда; об игроке — только владельцу его наклейки: пишем только о своём составе.
"""
import asyncio
import html
import json
import logging
from datetime import datetime, time, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiohttp

from zveno import manager, names, rules
from zveno.rules import TZ

from . import db as dbm
from .game import MONTHS_IN, Game, iso, parse_iso, surname, when

log = logging.getLogger("zveno_api.messages")

WEEK_MAX = 2
SEND_AT = time(18, 0)
SEND_UNTIL = time(22, 0)          # опоздали (сервер лежал) — в этот вечер уже не пишем
SUNDAY, TUESDAY = 6, 1
QUIET_AFTER = timedelta(days=21)  # три тура без открытия «Звена»
QUIET_EVERY = timedelta(days=14)  # тогда — раз в две недели
B_OPEN = "Открыть «Звено»"


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def week_key(now: datetime) -> str:
    y, w, _ = now.astimezone(TZ).isocalendar()
    return f"{y}-W{w:02d}"


def names_list(xs: list[str], limit: int = 3) -> str:
    if len(xs) <= limit:
        return ", ".join(xs)
    return ", ".join(xs[:limit]) + f" и ещё {len(xs) - limit}"


class Messenger:
    def __init__(self, game: Game, cfg):
        self.g = game
        self.cfg = cfg

    # ---------- кому и когда ----------

    def tz(self, club: str):
        name = (self.g.data.team.get(club) or {}).get("tz")
        if name:
            try:
                return ZoneInfo(name)
            except (ZoneInfoNotFoundError, ValueError):
                pass
        return TZ

    def allowed(self, m, now: datetime) -> bool:
        """Можно ли писать сейчас: галочка, бот не заблокирован, не больше 2 в неделю, редко — молчуну."""
        if not m["messages"] or not m["can_write"]:
            return False
        count = m["msg_count"] if m["msg_week"] == week_key(now) else 0
        if count >= WEEK_MAX:
            return False
        if parse_iso(m["last_seen"]) < now - QUIET_AFTER:
            return m["last_msg_at"] is None or parse_iso(m["last_msg_at"]) <= now - QUIET_EVERY
        return True

    def record(self, uid: int, now: datetime) -> None:
        wk = week_key(now)
        with dbm.tx(self.g.db):
            self.g.db.execute("""UPDATE managers SET msg_count = CASE WHEN msg_week = ? THEN msg_count + 1 ELSE 1 END,
                                 msg_week = ?, last_msg_at = ? WHERE id = ?""", (wk, wk, iso(now), uid))

    # ---------- тексты ----------

    def sunday_text(self, m, now: datetime) -> str | None:
        """«Есть что решить» перед дедлайном tour_next. None — решать нечего, молчим."""
        s = self.g.season(now)
        if s is None or s.tour_next is None or not (now < s.deadline <= now + timedelta(days=7)):
            return None
        t = s.tour_next
        row = self.g._tour_row(t) or {}
        games = row.get("games") or {}
        sq = json.loads(m["squad"])
        idx = self.g.data.idx
        items = []
        idle = []
        for slot, _, x in manager.positions(sq["lineup"]):
            p = idx.get(x) if x else None
            club = manager.club_of(x, idx) if x else None
            if p and club and games.get(club, 0) == 0 and p["status"] != "rest":
                idle.append(html.escape(surname(p)))
        if idle:
            items.append(f"{names_list(idle)} — в туре без матчей")
        cap = idx.get(sq.get("captain") or "")
        if cap and games.get(manager.club_of(cap["id"], idx), 0) == 0:
            items.append(f"У капитана {html.escape(surname(cap))} в туре нет матчей — «К» можно отдать другому")
        for w in self.g.warnings(m, sq, self.g.holdings(m["id"]), s):
            text = html.escape(w["text"].rstrip("."))
            if w.get("in"):
                text += ". Не хочешь — «Оставить» в «Звене»"
            items.append(text)
        if not items:
            return None
        who = html.escape(self.g.mascot(m["fav_club"]))
        dl = s.deadline.astimezone(TZ)
        head = f"<b>{who}</b>: до дедлайна тура {t} — {when(dl)} МСК. Есть что решить:"
        return head + "\n" + "\n".join(f"• {x}" for x in items)

    def story_tour(self, m, now: datetime) -> int | None:
        """Тур для вторничной истории: последний прошедший тур с составом, о котором ещё не писали."""
        today = now.astimezone(self.tz(m["fav_club"])).date().isoformat()
        best = None
        for r in self.g.db.execute("SELECT tour FROM lineups WHERE manager_id = ? AND tour > ? ORDER BY tour",
                                   (m["id"], m["last_story_tour"])):
            row = self.g._tour_row(r["tour"])
            if row and row["to"] < today:
                best = r["tour"]
        return best

    def story_text(self, m, t: int) -> str | None:
        idx = self.g.data.idx
        snap = self.g.db.execute("SELECT detail FROM scores WHERE manager_id = ? AND tour = ?", (m["id"], t)).fetchone()
        if snap:
            d = json.loads(snap["detail"])
            total, by_id, captain, final = d["total"], d["by_id"], d["captain"], True
        else:
            r = self.g.db.execute("SELECT * FROM lineups WHERE manager_id = ? AND tour = ?", (m["id"], t)).fetchone()
            sq = json.loads(r["squad"])
            sb = manager.tour_score(sq["lineup"], sq["captain"], sq["assistant"], t, idx, self.g.data.match_list,
                                    bench=sq["bench"], penalty=r["penalty"])
            total, by_id, captain, final = sb.total, sb.by_id, sq["captain"], False
        if not any(v["matches"] for v in by_id.values()):
            return None
        who = html.escape(self.g.mascot(m["fav_club"]))
        title = html.escape(names.title(json.loads(m["name"])))
        lines = [f"<b>{who}</b>: тур {t} позади. «{title}» — {total} {plural(total, 'очко', 'очка', 'очков')}"
                 + ("." if final else ", итог — после сверки протоколов в четверг.")]
        top = max(by_id.items(), key=lambda kv: (kv[1]["points"], kv[0]))
        if top[1]["points"] > 0:
            lines.append(f"Больше всех принёс {html.escape(surname(idx.get(top[0])))} — {top[1]['points']}.")
        cv = by_id.get(captain)
        if cv and captain != top[0] and cv["mult"] > 1:
            lines.append(f"Капитан {html.escape(surname(idx.get(captain)))} — {cv['points']}.")
        syn = sum(v["synergy"] * v["mult"] for v in by_id.values())
        if syn > 0:
            lines.append(f"Сыгранность в звеньях: +{syn}.")
        promo = self.promotion(m)
        if promo:
            lines.append(promo[1])
        return "\n".join(lines)

    def promotion(self, m) -> tuple[str, str] | None:
        """Поднялся на ступень в этом месяце и ещё не слышал об этом. О понижении не пишем."""
        month = self.g.step_month()
        if not month:
            return None
        r = self.g.db.execute("SELECT * FROM steps WHERE month = ? AND manager_id = ?", (month, m["id"])).fetchone()
        if not r or r["told"] or r["prev_step"] is None or r["step"] >= r["prev_step"]:
            return None
        return month, f"В {MONTHS_IN[int(month[5:])]} ты поднялся: {rules.STEP_NAMES[r['step']]}!"

    # ---------- отправка ----------

    def app_url(self) -> str:
        u = urlsplit(self.cfg.webapp_url)
        return urlunsplit(u._replace(query=urlencode(parse_qsl(u.query) + [("startapp", "zveno")])))

    async def send(self, session: aiohttp.ClientSession, chat_id: int, text: str) -> str:
        """'ok', 'blocked' (бот заблокирован или чата нет) или 'error'. Токен в лог не пишем."""
        url = f"{self.cfg.telegram_api}/bot{self.cfg.bot_token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True,
                   "reply_markup": {"inline_keyboard": [[{"text": B_OPEN, "web_app": {"url": self.app_url()}}]]}}
        for _ in range(3):
            try:
                async with session.post(url, json=payload) as r:
                    d = await r.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
                log.warning("send to %s failed: %s", chat_id, type(e).__name__)
                return "error"
            if d.get("ok"):
                return "ok"
            code = d.get("error_code")
            if code == 429:
                await asyncio.sleep(min(30, (d.get("parameters") or {}).get("retry_after", 1)))
                continue
            desc = str(d.get("description", ""))
            if code == 403 or (code == 400 and "chat not found" in desc):
                return "blocked"
            log.warning("send to %s: %s %s", chat_id, code, desc[:200])
            return "error"
        return "error"

    async def run(self, session: aiohttp.ClientSession | None = None, now: datetime | None = None) -> int:
        """Один проход: кому пора — воскресное или вторничное. Возвращает число отправленных."""
        if not self.g.data.loaded:
            return 0
        now = now or self.g.now()
        own = session is None
        if own:
            session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30), trust_env=True)
        sent = 0
        try:
            for uid in [r["id"] for r in self.g.db.execute("SELECT id FROM managers WHERE messages = 1 AND can_write = 1")]:
                m = self.g.manager(uid)   # свежая строка: пока шли отправки, человек мог выключить сообщения или удалиться
                if m is None or not m["messages"] or not m["can_write"]:
                    continue
                local = now.astimezone(self.tz(m["fav_club"]))
                if not (SEND_AT <= local.time() < SEND_UNTIL) or local.weekday() not in (SUNDAY, TUESDAY):
                    continue
                text, mark = None, None
                if local.weekday() == SUNDAY:
                    wk = week_key(now)
                    if m["last_sunday"] == wk:
                        continue
                    mark = ("UPDATE managers SET last_sunday = ? WHERE id = ?", (wk, m["id"]))
                    text = self.sunday_text(m, now)
                else:
                    t = self.story_tour(m, now)
                    if t is None:
                        continue
                    mark = ("UPDATE managers SET last_story_tour = ? WHERE id = ?", (t, m["id"]))
                    text = self.story_text(m, t)
                promo = self.promotion(m) if local.weekday() == TUESDAY else None
                with dbm.tx(self.g.db):
                    self.g.db.execute(*mark)
                if not text or not self.allowed(m, now):
                    continue
                res = await self.send(session, m["id"], text)
                if res == "ok":
                    self.record(m["id"], now)
                    if promo:
                        with dbm.tx(self.g.db):
                            self.g.db.execute("UPDATE steps SET told = 1 WHERE month = ? AND manager_id = ?", (promo[0], m["id"]))
                    sent += 1
                elif res == "blocked":
                    with dbm.tx(self.g.db):
                        self.g.db.execute("UPDATE managers SET can_write = 0 WHERE id = ?", (m["id"],))
                await asyncio.sleep(0.05)
        finally:
            if own:
                await session.close()
        return sent
