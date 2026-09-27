"""Туры «Звена»: даты, дедлайны, закрытие, месяц и круг (ADR-014, раздел 3).

Построить таблицу туров — `tour_table()` (её пишет build_zveno.py в tours.json).
Прочитать опубликованную — `load_tours()`, дальше `deadline_of`, `close_of`, `tour_at`, `next_tour`.
"""
import json
from datetime import date, datetime, time, timedelta
from pathlib import Path

from . import rules
from .rules import TZ

TOURS_FILE = Path(__file__).resolve().parent.parent / "webapp" / "data" / "zveno" / "tours.json"


def _d(s: str | date) -> date:
    return s if isinstance(s, date) else date.fromisoformat(s)


def _msk(when: datetime | date) -> datetime:
    """Момент по Москве. Дата — её начало. Наивный datetime не принимаем (правило 3 CLAUDE.md)."""
    if isinstance(when, datetime):
        if when.tzinfo is None:
            raise ValueError("нужно время с поясом")
        return when.astimezone(TZ)
    return datetime.combine(when, time(0), TZ)


def iso(dt: datetime) -> str:
    return dt.astimezone(TZ).isoformat(timespec="seconds")

# ---------- правила по номеру тура ----------


def tour_dates(t: int) -> tuple[date, date]:
    _, a, b = rules.TOURS[t - 1]
    return date.fromisoformat(a), date.fromisoformat(b)


def tour_of_date(d: date | str) -> int | None:
    """Тур по дате матча, Пн–Вс по Москве. Пролог, пауза и после сезона — None."""
    d = _d(d)
    for t, a, b in rules.TOURS:
        if a <= d.isoformat() <= b:
            return t
    return None


def month_of(t: int) -> str:
    """Месяц тура — по его первому дню: туры 1–3 октябрьские, 4–8 ноябрьские и так далее."""
    return tour_dates(t)[0].strftime("%Y-%m")


def circle_of(t: int) -> int:
    return 1 if t < rules.SECOND_CIRCLE else 2


def deadline(t: int, match_days: list[date] | None = None) -> datetime:
    """Понедельник 09:00. После паузы — 09:00 дня первого матча тура по календарю. Тур 20 — 07.03 23:00."""
    if t in rules.DEADLINE_FIXED:
        return datetime.fromisoformat(rules.DEADLINE_FIXED[t]).replace(tzinfo=TZ)
    a, b = tour_dates(t)
    day = a
    if t in rules.AFTER_PAUSE:
        days = sorted(d for d in (match_days or []) if a <= d <= b)
        day = days[0] if days else date.fromisoformat(rules.AFTER_PAUSE[t])
    return datetime.combine(day, time(rules.DEADLINE_HOUR), TZ)


def close_planned(t: int) -> datetime:
    """Четверг 12:00 после последнего дня тура: лига правит протоколы до трёх дней."""
    last = tour_dates(t)[1]
    days = (rules.CLOSE_WEEKDAY - last.weekday()) % 7 or 7
    return datetime.combine(last + timedelta(days=days), time(rules.CLOSE_HOUR), TZ)


def is_closed(t: int, now: datetime, expected: int, present: int) -> tuple[bool, bool]:
    """(закрыт, перенесён). Нет трети протоколов к четвергу — ждём, но не дольше 14 дней."""
    close = close_planned(t)
    if now < close:
        return False, False
    missing = max(0, expected - present)
    waiting = missing > 0 and missing >= expected * rules.CLOSE_MISSING
    if not waiting or now >= close + timedelta(days=rules.CLOSE_MAX_DELAY_DAYS):
        return True, False
    return False, True


def price_window(d: date | str, deadlines: dict[int, datetime]) -> int:
    """Окно стоимости матча: последний дедлайн до него (0 — до первого дедлайна, Пролог).

    Стоимость на дедлайн — «price_monday», от неё предел ±500. Точного времени матча по Москве в
    протоколе нет (там местное), поэтому матч в день дедлайна считается после него, если дедлайн
    утренний, и до него, если вечерний (тур 20: 07.03 23:00)."""
    d = _d(d)
    w = 0
    for t in sorted(deadlines):
        dl = deadlines[t]
        if d > dl.date() or (d == dl.date() and dl.hour < 12):
            w = t
    return w

# ---------- таблица туров ----------


def tour_table(games: list[dict], clubs: list[str], now: datetime,
               present: dict[int, int] | None = None) -> list[dict]:
    """Туры для tours.json. games — матчи календаря league.json (date, home, away),
    present — сколько матчей тура уже с протоколом (для закрытия)."""
    days = sorted({_d(g["date"]) for g in games})
    out = []
    for t, a, b in rules.TOURS:
        tg = [g for g in games if a <= g["date"] <= b]
        n = {c: 0 for c in clubs}
        for g in tg:
            for side in ("home", "away"):
                if g[side] in n:
                    n[g[side]] += 1
        closed, postponed = is_closed(t, _msk(now), len(tg), (present or {}).get(t, 0))
        row = {"t": t, "from": a, "to": b, "deadline": iso(deadline(t, days)), "close": iso(close_planned(t)),
               "closed": closed, "month": month_of(t), "circle": circle_of(t), "games": n}
        if postponed:
            row["postponed"] = True
        out.append(row)
    return out

# ---------- опубликованный tours.json ----------


def load_tours(src: str | Path | dict | None = None) -> dict:
    """tours.json целиком. src — путь, уже прочитанный dict или None (файл в webapp/data/zveno/)."""
    if isinstance(src, dict):
        return src
    return json.loads(Path(src or TOURS_FILE).read_text(encoding="utf-8"))


def _row(tours: dict, t: int) -> dict:
    for r in tours["tours"]:
        if r["t"] == t:
            return r
    raise KeyError(f"нет тура {t}")


def deadline_of(tours: dict, t: int) -> datetime:
    return datetime.fromisoformat(_row(tours, t)["deadline"]).astimezone(TZ)


def close_of(tours: dict, t: int) -> datetime:
    return datetime.fromisoformat(_row(tours, t)["close"]).astimezone(TZ)


def tour_at(tours: dict, when: datetime | date) -> int | None:
    """Тур, к которому по датам относится момент (Пн–Вс по Москве, тур 12 — две недели).
    Пролог, пауза и после сезона — None."""
    d = _msk(when).date().isoformat()
    for r in tours["tours"]:
        if r["from"] <= d <= r["to"]:
            return r["t"]
    return None


def next_tour(tours: dict, when: datetime) -> int | None:
    """Тур, состав на который собирают в момент when: первый с дедлайном позже. После 21-го — None."""
    when = _msk(when)
    for r in sorted(tours["tours"], key=lambda r: r["t"]):
        if deadline_of(tours, r["t"]) > when:
            return r["t"]
    return None
