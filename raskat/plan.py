"""План дня «Раската»: поле, номера, сложность и норма времени по дню недели (разделы 1, 2).

Номер раската и список дней сезона тоже здесь: и сборке, и серверу нужно одинаково отвечать на
вопрос «какой сегодня день и открыт ли он».
"""
from dataclasses import dataclass
from datetime import date, datetime

from . import rules
from .rules import TZ


def as_date(d: date | str) -> date:
    return d if isinstance(d, date) else date.fromisoformat(d)


def today(now: datetime | None = None) -> date:
    """Сегодня по Москве. Наивный datetime не принимаем (правило 3 CLAUDE.md)."""
    if now is None:
        return datetime.now(TZ).date()
    if now.tzinfo is None:
        raise ValueError("нужно время с поясом")
    return now.astimezone(TZ).date()


@dataclass(frozen=True)
class Plan:
    """Что за раскат в этот день. k — сколько номеров ставит план; сам расклад может добавить
    номер, если бортов для единственности решения не хватило, поэтому в опубликованный файл идёт
    длина `dots`, а не это число."""
    date: str
    n: int          # номер раската с начала сезона, с единицы
    w: int
    h: int
    k: int
    hard: int
    par: int

    @property
    def cells(self) -> int:
        return self.w * self.h


def day_number(d: date | str) -> int:
    """Номер раската с начала сезона, с единицы. До сезона — ноль: раската ещё нет."""
    d = as_date(d)
    return (d - rules.SEASON_FROM).days + 1 if d >= rules.SEASON_FROM else 0


def day_plan(d: date | str) -> Plan:
    """План дня по дню недели (таблица WEEK в rules.py)."""
    d = as_date(d)
    w, h, k, hard, par = rules.WEEK[d.weekday()]
    return Plan(d.isoformat(), day_number(d), w, h, k, hard, par)


def season_days(upto: date | str) -> list[date]:
    """Дни от начала сезона до upto включительно, но не дальше последнего дня сезона.

    Прошлые дни мини-апп открывает как тренировку, поэтому список и растёт от первого дня, а не
    едет окном: собранный когда-то файл дня больше никогда не меняется."""
    last = min(as_date(upto), rules.SEASON_TO)
    out, d = [], rules.SEASON_FROM
    while d <= last:
        out.append(d)
        d = date.fromordinal(d.toordinal() + 1)
    return out


# ---------- подпись дня ----------

_NUMERALS = {4: "Четыре", 5: "Пять", 6: "Шесть", 7: "Семь", 8: "Восемь", 9: "Девять"}


def lede(k: int, walls: int = 0) -> str:
    """Строка `lede` файла дня: что за раскат, одной фразой для болельщика, а не для отладки.

    Про борта говорим только когда их нет совсем: открытое поле играется иначе, и это стоит сказать
    заранее. Сколько бортов — видно на поле, цифра в подписи ничего не добавит."""
    head = f"{_NUMERALS.get(k, k)} номеров" if k != 1 else "Один номер"
    if walls == 0:
        return f"{head}, открытый лёд и весь путь за один раскат."
    return f"{head}, весь лёд за один раскат."
