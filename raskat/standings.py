"""Очки за раскат, зачёт дня и кубок клубов (раздел 4 контракта).

Счёт считают одинаково движок и мини-апп, поэтому формула живёт здесь одним выражением: правка —
только вместе с `webapp/raskat.js` и таблицей примеров в `tests/test_raskat_points.py`.

Чисел два, и путать их нельзя:

- **очки дня** (`day_points`) — база, время и подсказка. По ним идёт зачёт дня и кубок клубов:
  «сегодня, одинаково для всех», серия на место в дне не влияет;
- **итог дня** (`points`) — очки дня плюс серия. Личное число болельщика, его видно в своей
  карточке и в зачёте серий.
"""
from dataclasses import dataclass

from . import rules


def day_points(par: int, seconds: float, hint: bool = False) -> int:
    """Очки дня: база, время, подсказка. По ним ранжируется зачёт дня — серии здесь нет.

    `seconds` — от первого касания поля до собранного раската, без пауз. Быстрее `par / 2` очков
    больше не даёт: на вершине таблицы не должно быть смысла гнать до предела. Хвост тянется до
    `3 * par`, иначе все, кто собирал дольше двух норм, получают ровно базу и кубок клубов
    превращается в ничью."""
    if par <= 0:
        raise ValueError("норма времени должна быть больше нуля")
    share = (rules.TIME_ZERO * par - seconds) / (rules.TIME_SPAN * par)
    share = min(1.0, max(0.0, share))
    total = rules.PTS_BASE + rules.PTS_TIME * share + (rules.PTS_HINT if hint else 0)
    return max(0, round(total))


def streak_points(streak: int) -> int:
    """Очки за серию: 2 за каждый день до сегодняшнего, не больше 10."""
    return min(rules.PTS_STREAK_DAY * max(0, streak), rules.PTS_STREAK_MAX)


def points(par: int, seconds: float, streak: int = 0, hint: bool = False) -> int:
    """Итог дня: очки дня плюс серия. `streak` — дни серии **до** сегодняшнего."""
    return max(0, day_points(par, seconds, hint) + streak_points(streak))


# ---------- зачёты ----------

@dataclass(frozen=True)
class Row:
    """Строка зачёта дня. Имя и «это я» подставляет сервер: движок про болельщика знает только id."""
    place: int
    fan: str
    club: str | None
    points: int
    ms: int


@dataclass(frozen=True)
class ClubRow:
    """Строка кубка клубов: среднее очков дня болельщиков клуба (за день) или среднее дневных
    средних (за сезон). `days` — по скольким дням считалось среднее."""
    place: int
    club: str
    avg: float
    fans: int
    days: int = 1


@dataclass(frozen=True)
class Standings:
    days: dict[str, list[Row]]          # дата → зачёт дня
    clubs: dict[str, list[ClubRow]]     # дата → кубок клубов этого дня
    season: list[ClubRow]               # кубок клубов за сезон
    short: dict[str, list[str]]         # дата → клубы, которым до порога не хватило болельщиков


def _fan(r: dict) -> str:
    return str(r["fan"])


def standings(results) -> Standings:
    """Зачёты по результатам. Результат — `{"date", "fan", "club", "points", "ms"}`, где `points` —
    очки дня (без серии): по ним считаются и зачёт дня, и кубок клубов.

    Повторный результат одного болельщика за один день отбрасывается: день принимается один раз
    (раздел 5), и зачёт не должен зависеть от того, сколько раз пришёл один и тот же ответ."""
    by_day: dict[str, dict[str, dict]] = {}
    for r in results:
        by_day.setdefault(str(r["date"]), {}).setdefault(_fan(r), r)

    days: dict[str, list[Row]] = {}
    clubs: dict[str, list[ClubRow]] = {}
    short: dict[str, list[str]] = {}
    # клуб → [дневные средние, болельщики за сезон, сыгранных дней]
    season: dict[str, list] = {}
    for date in sorted(by_day):
        rows = sorted(by_day[date].values(),
                      key=lambda r: (-int(r["points"]), int(r["ms"]), _fan(r)))
        days[date] = [Row(i + 1, _fan(r), r.get("club"), int(r["points"]), int(r["ms"]))
                      for i, r in enumerate(rows)]

        pack: dict[str, list[Row]] = {}
        for row in days[date]:
            if row.club:
                pack.setdefault(row.club, []).append(row)
        day_rows, thin = [], []
        for club, fans in pack.items():
            tally = season.setdefault(club, [[], set(), 0])
            tally[1].update(r.fan for r in fans)
            tally[2] += 1
            if len(fans) < rules.CLUB_MIN_FANS:
                # Клуб из приложения не исчезает: болельщик видит «собрали 3, кубок считается от 5»,
                # а его собственные очки в зачёт дня идут полностью
                thin.append(club)
                continue
            avg = round(sum(r.points for r in fans) / len(fans), rules.CLUB_ROUND)
            day_rows.append((avg, len(fans), club))
            tally[0].append(avg)
        day_rows.sort(key=lambda t: (-t[0], -t[1], t[2]))
        clubs[date] = [ClubRow(i + 1, club, avg, fans) for i, (avg, fans, club) in enumerate(day_rows)]
        short[date] = sorted(thin)

    # Сезон — среднее дневных средних по дням, где клуб прошёл порог. Складывать нельзя: клуб, не
    # набравший пятерых в октябре, не догнал бы никогда, как бы хорошо ни играл дальше. В список
    # берём тех, кто прошёл порог хотя бы в половине своих сыгранных дней: иначе сезонный кубок
    # выигрывает один удачный день
    order = []
    for club, (avgs, fans, played) in season.items():
        if not avgs or len(avgs) < played * rules.CLUB_SEASON_SHARE:
            continue
        order.append((round(sum(avgs) / len(avgs), rules.CLUB_ROUND), len(fans), club, len(avgs)))
    order.sort(key=lambda t: (-t[0], -t[1], t[2]))
    return Standings(days, clubs,
                     [ClubRow(i + 1, club, avg, fans, played)
                      for i, (avg, fans, club, played) in enumerate(order)],
                     short)
