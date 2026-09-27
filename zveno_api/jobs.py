"""Фоновые задачи сервера «Звена».

- раз в 10 минут — tours.json, pool.json, matches.json: все три или ничего, при ошибке остаются прежние;
  потом скрытые игроки, дедлайны и закрытия туров;
- раз в минуту — пульс (для «сервер лежал перед дедлайном») и дедлайны: состав замораживается вовремя,
  даже если никто не заходит; запрос к API тоже сначала догоняет дедлайн;
- раз в 10 минут — сообщения по расписанию (воскресенье и вторник, 18:00 по поясу клуба).
"""
import asyncio
import logging

import aiohttp

from . import data as datam
from .game import Game
from .messages import Messenger

log = logging.getLogger("zveno_api.jobs")


class Jobs:
    def __init__(self, cfg, game: Game, messenger: Messenger | None = None):
        self.cfg = cfg
        self.game = game
        self.messenger = messenger or Messenger(game, cfg)
        self.tasks: list[asyncio.Task] = []
        self.session: aiohttp.ClientSession | None = None

    def load_cache(self) -> bool:
        files = datam.load_cache(self.cfg.cache())
        if not files:
            return False
        self.game.data.set(files["tours"], files["pool"], files["matches"])
        log.info("data from cache: %s", files["tours"].get("updated"))
        return True

    async def refresh_once(self) -> bool:
        """Подтянуть данные. True — обновились; при ошибке прежние остаются как были."""
        try:
            files = await datam.fetch(self.cfg.data_url, self.session)
            self.game.data.set(files["tours"], files["pool"], files["matches"])
        except datam.DataError as e:
            log.warning("data not refreshed: %s", e)
            return False
        try:
            datam.save_cache(self.cfg.cache(), files)
        except OSError:
            log.exception("cache not saved")
        self.game.on_data()
        return True

    def tick_once(self) -> None:
        self.game.beat()
        self.game.catch_up()

    async def messages_once(self) -> int:
        if not self.cfg.send_messages:
            return 0
        return await self.messenger.run(self.session)

    async def _every(self, seconds: int, fn, name: str) -> None:
        while True:
            try:
                res = fn()
                if asyncio.iscoroutine(res):
                    await res
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("job %s failed", name)
            await asyncio.sleep(seconds)

    async def start(self, app=None) -> None:
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60), trust_env=True)
        self.game.note_start()
        self.load_cache()
        self.tasks = [
            asyncio.create_task(self._every(self.cfg.refresh_every, self.refresh_once, "refresh")),
            asyncio.create_task(self._every(self.cfg.tick_every, self.tick_once, "tick")),
            asyncio.create_task(self._every(self.cfg.messages_every, self.messages_once, "messages")),
        ]

    async def stop(self, app=None) -> None:
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks = []
        if self.session:
            await self.session.close()
            self.session = None
