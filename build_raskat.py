"""Собирает расклады «Раската» для мини-аппа и бота: webapp/data/raskat/ (ADR-018, docs/raskat/contract.md).

    venv/bin/python build_raskat.py                      # все дни сезона до сегодняшнего
    venv/bin/python build_raskat.py --date 2026-11-20    # один день, в stdout и в файл
    venv/bin/python build_raskat.py --check              # проверить уже собранное

Пишет index.json и по файлу на день. Расклад выводится из даты и соли сезона, поэтому собранный
файл дня больше никогда не меняется, а задание Pages может запускаться хоть каждый час.
Решения в файлах нет: его всегда можно получить движком (контракт, раздел 2).
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import raskat
from raskat import puzzle as pz
from raskat import rules

BASE = Path(__file__).parent
OUT = BASE / "webapp" / "data" / "raskat"


def write(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def build(out: Path, now: datetime) -> dict:
    """Расклады всех дней сезона до сегодняшнего включительно и index.json к ним."""
    today = raskat.today(now)
    out.mkdir(parents=True, exist_ok=True)
    days = []
    for day in raskat.season_days(today):
        p = raskat.generate(day)
        write(out / f"{p.date}.json", raskat.as_json(p))
        days.append(raskat.index_row(p))
    index = {"built": now.astimezone(rules.TZ).isoformat(timespec="seconds"),
             "season": rules.SEASON, "today": today.isoformat(), "days": days}
    write(out / "index.json", index)
    return index


def check(out: Path) -> list[str]:
    """Самопроверка собранного: формат по контракту, расклад сходится с движком, решение одно.

    Нужна не мини-аппу, а нам: расклад считается на раннере, и молча разъехавшийся с движком файл
    дня заметить больше негде."""
    bad = []
    try:
        index = json.loads((out / "index.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError) as e:
        return [f"index.json не читается: {e}"]
    if set(index) != {"built", "season", "today", "days"} or index["season"] != rules.SEASON:
        bad.append("index.json: не те поля или не тот сезон")
    dates = [d["date"] for d in index["days"]]
    if dates != sorted(dates) or [d["n"] for d in index["days"]] != list(range(1, len(dates) + 1)):
        bad.append("index.json: дни не по порядку или номера с пропусками")
    for row in index["days"]:
        name = f"{row['date']}.json"
        try:
            got = json.loads((out / name).read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError) as e:
            bad.append(f"{name}: {e}")
            continue
        want = raskat.as_json(raskat.generate(row["date"]))
        if got != want:
            bad.append(f"{name}: расклад не сходится с движком")
            continue
        if set(got) & {"path", "solution", "solve"}:
            bad.append(f"{name}: в файле лежит решение")
        p = raskat.generate(row["date"])
        if {k: row[k] for k in ("w", "h", "k", "hard", "par")} != \
                {"w": p.w, "h": p.h, "k": p.k, "hard": p.hard, "par": p.par}:
            bad.append(f"{name}: строка index.json не сходится с файлом дня")
        if len(raskat.solve(p, 2)) != 1:
            bad.append(f"{name}: решений не одно")
    return bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Собрать webapp/data/raskat/ — расклады «Раската»")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--now", help="момент сборки, ISO с поясом; по умолчанию сейчас")
    ap.add_argument("--date", help="собрать только этот день, YYYY-MM-DD")
    ap.add_argument("--check", action="store_true", help="проверить уже собранное и ничего не писать")
    args = ap.parse_args(argv)

    if args.check:
        bad = check(args.out)
        for line in bad:
            print(line, file=sys.stderr)
        print(f"«Раскат»: проверено {args.out}, замечаний {len(bad)}")
        return 1 if bad else 0

    try:
        now = datetime.fromisoformat(args.now) if args.now else datetime.now(rules.TZ)
    except ValueError:
        print("--now нужен в виде 2026-10-03T09:17:00+03:00", file=sys.stderr)
        return 1
    if now.tzinfo is None:
        print("--now нужен с поясом, например 2026-10-03T09:17:00+03:00", file=sys.stderr)
        return 1

    if args.date:
        try:
            p = raskat.generate(args.date)
        except (ValueError, KeyError):
            print(f"Не дата: {args.date}", file=sys.stderr)
            return 1
        if p.n < 1:
            print(f"{args.date} — до начала сезона, раската в этот день нет", file=sys.stderr)
            return 1
        args.out.mkdir(parents=True, exist_ok=True)
        write(args.out / f"{p.date}.json", raskat.as_json(p))
        print(json.dumps(raskat.as_json(p), ensure_ascii=False))
        print(f"«Раскат» № {p.n} на {p.date}: поле {p.w}×{p.h}, номеров {p.k}, бортов {len(p.walls)}, "
              f"сложность {p.hard}, норма {p.par} с → {args.out}")
        return 0

    index = build(args.out, now)
    if not index["days"]:
        print(f"«Раскат»: сезон {rules.SEASON} начнётся {rules.SEASON_FROM.isoformat()}, "
              f"раскладов пока нет → {args.out}")
        return 0
    last = index["days"][-1]
    print(f"«Раскат»: раскладов {len(index['days'])}, последний № {last['n']} на {last['date']} — "
          f"поле {last['w']}×{last['h']}, номеров {last['k']}, норма {last['par']} с → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
