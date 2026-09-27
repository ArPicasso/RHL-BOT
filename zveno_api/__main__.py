"""Запуск сервера «Звена»: `python -m zveno_api`.

Переменные окружения — zveno_api/config.py и deploy/README.md. Для ручной проверки на сервере:
- `python -m zveno_api --once` — подтянуть данные, догнать дедлайны, напечатать сезон и выйти;
- `python -m zveno_api --init-data 12345` — подписанный initData для curl (токен не покидает сервер).
"""
import argparse
import asyncio
import json
import logging
import time

from aiohttp import web

from . import auth, config, db
from .data import Data, load_teams
from .game import Game
from .jobs import Jobs
from .routes import make_app


def build(cfg: config.Config) -> tuple[Game, Jobs]:
    conn = db.connect(cfg.db)
    data = Data()
    data.set_teams(load_teams(cfg.teams_file))
    game = Game(conn, data)
    return game, Jobs(cfg, game)


async def once(cfg: config.Config) -> int:
    game, jobs = build(cfg)
    jobs.load_cache()
    ok = await jobs.refresh_once()
    s = game.season()
    print(json.dumps({"refreshed": ok, "season": s.as_json() if s else None,
                      "managers": game.db.execute("SELECT COUNT(*) FROM managers").fetchone()[0]},
                     ensure_ascii=False, indent=1))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(prog="python -m zveno_api", description="Сервер «Звена»")
    ap.add_argument("--once", action="store_true", help="подтянуть данные, напечатать сезон и выйти")
    ap.add_argument("--init-data", type=int, metavar="TG_ID", help="напечатать подписанный initData для curl")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = config.from_env()
    if args.init_data:
        print(auth.make_init_data({"id": args.init_data, "first_name": "Проверка"}, cfg.bot_token, int(time.time())))
        return 0
    if args.once:
        return asyncio.run(once(cfg))
    game, jobs = build(cfg)
    app = make_app(cfg, game, jobs)
    web.run_app(app, host=cfg.host, port=cfg.port, access_log=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
