"""«Раскат» — движок головоломки дня (ADR-018, docs/raskat/contract.md).

Только стандартная библиотека, без сети: пакет импортируют сборка `build_raskat.py` и сервер
зачётов (контракт, раздел 3).

    from raskat import day_plan, generate, solve, check, points, standings
"""
from .plan import Plan, day_number, day_plan, lede, season_days, today
from .puzzle import OK, Puzzle, Verdict, as_json, check, generate, index_row, solve
from .standings import ClubRow, Row, Standings, day_points, points, standings, streak_points

__all__ = ["Plan", "Puzzle", "Verdict", "OK", "Row", "ClubRow", "Standings",
           "day_plan", "day_number", "season_days", "today", "lede",
           "generate", "solve", "check", "as_json", "index_row",
           "points", "day_points", "streak_points", "standings"]
