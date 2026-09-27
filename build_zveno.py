"""Собирает данные «Звена» для мини-аппа и сервера: webapp/data/zveno/ (ADR-014, docs/zveno/contract.md).

    venv/bin/python build_zveno.py                    # после build_data.py: results.json + league.json
    venv/bin/python build_zveno.py --league tools/fantasy_model/data/calendar_2627.json   # без сети

Пишет tours.json, pool.json, matches.json и names.json. Строки на ручную проверку связки с прошлым
сезоном — в лог (stdout).
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import build_data
import league
from zveno import build, names, prior
from zveno.rules import TZ

BASE = Path(__file__).parent
OUT = BASE / "webapp" / "data" / "zveno"


def write(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Собрать webapp/data/zveno/ — туры, пул наклеек, матчи")
    ap.add_argument("--results", type=Path, default=league.RESULTS_FILE)
    ap.add_argument("--league", type=Path, default=build_data.OUT, help="календарь: webapp/data/league.json")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--now", help="момент сборки, ISO с поясом; по умолчанию сейчас (для проверки)")
    args = ap.parse_args(argv)

    try:
        calendar = json.loads(args.league.read_text(encoding="utf-8"))["games"]
    except (FileNotFoundError, ValueError, KeyError) as e:
        print(f"Нет календаря {args.league}: {e}. Сначала build_data.py", file=sys.stderr)
        return 1
    now = datetime.fromisoformat(args.now) if args.now else datetime.now(TZ)
    if now.tzinfo is None:
        print("--now нужен с поясом, например 2026-10-12T10:00:00+03:00", file=sys.stderr)
        return 1
    now = now.astimezone(TZ)
    teams = build_data.load_teams()
    protos, unmatched = build.regular_protocols(league.load_results(args.results), teams.find_past)
    season = build.build(protos, calendar, teams.all, now, prior.load_prior(), prior.load_links(),
                         build_data.load_hidden())

    args.out.mkdir(parents=True, exist_ok=True)
    write(args.out / "tours.json", season.tours)
    write(args.out / "pool.json", season.pool)
    write(args.out / "matches.json", season.matches)
    write(args.out / "names.json", names.as_json())

    t = season.tours
    print(f"«Звено»: {t['status']}, протокол у {t['clubs_with_protocol']} клубов из {len(teams.all)}, "
          f"тур на сбор {t['tour_next']}, наклеек {len(season.pool['players'])}, матчей "
          f"{len(season.matches['matches'])} → {args.out}")
    for u in unmatched:
        print("Протокол не привязан к клубам:", u)
    for r in season.review:
        print("Проверить руками:", r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
