"""Опубликованные данные «Звена»: tours.json, pool.json, matches.json (контракт, раздел 2).

Сервер сам к сайту лиги не ходит (ADR-014, раздел 13): берёт то, что движок публикует вместе с
мини-аппом. Три файла подменяются только вместе и только целыми — при любой ошибке остаются прежние.
Последние удачные копии лежат на диске: после перезапуска без сети сервер работает на них.
"""
import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import aiohttp

from zveno import manager

FILES = ("tours", "pool", "matches")
log = logging.getLogger("zveno_api.data")


class DataError(Exception):
    pass


def check(tours, pool, matches) -> None:
    """Минимальная проверка формата: без неё битый файл уронил бы расчёты."""
    if not isinstance(tours, dict) or not isinstance(tours.get("tours"), list) or not tours["tours"]:
        raise DataError("tours.json: нет списка туров")
    for r in tours["tours"]:
        if not isinstance(r, dict) or not {"t", "from", "to", "deadline", "close"} <= set(r):
            raise DataError("tours.json: тур без дат")
    if not isinstance(pool, dict) or not isinstance(pool.get("players"), list):
        raise DataError("pool.json: нет списка наклеек")
    for p in pool["players"]:
        if not isinstance(p, dict) or not {"id", "slot", "club", "price", "status"} <= set(p):
            raise DataError("pool.json: наклейка без полей")
    if not isinstance(matches, dict) or not isinstance(matches.get("matches"), list):
        raise DataError("matches.json: нет списка матчей")


@dataclass
class Data:
    tours: dict | None = None
    pool: dict | None = None
    matches: dict | None = None
    idx: dict[str, dict] = field(default_factory=dict)
    teams: list[dict] = field(default_factory=list)
    team: dict[str, dict] = field(default_factory=dict)
    loaded: bool = False

    def set(self, tours, pool, matches) -> None:
        check(tours, pool, matches)
        self.tours, self.pool, self.matches = tours, pool, matches
        self.idx = manager.index(pool)
        self.loaded = True

    def set_teams(self, teams: list[dict]) -> None:
        self.teams = teams
        self.team = {t["id"]: t for t in teams}

    @property
    def match_list(self) -> list[dict]:
        return (self.matches or {}).get("matches", [])


def load_teams(path: Path) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _is_url(src: str) -> bool:
    return src.startswith(("http://", "https://"))


async def fetch(src: str, session: aiohttp.ClientSession | None = None) -> dict[str, dict]:
    """Все три файла из src (URL каталога или путь). Любая ошибка — DataError."""
    out = {}
    if not _is_url(src):
        base = Path(src)
        for name in FILES:
            try:
                out[name] = json.loads((base / f"{name}.json").read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                raise DataError(f"{name}.json: {e}") from None
        check(out["tours"], out["pool"], out["matches"])
        return out
    base = src if src.endswith("/") else src + "/"
    own = session is None
    if own:
        session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60), trust_env=True)
    try:
        for name in FILES:
            try:
                async with session.get(f"{base}{name}.json", headers={"Cache-Control": "no-cache"}) as r:
                    if r.status != 200:
                        raise DataError(f"{name}.json: HTTP {r.status}")
                    out[name] = await r.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
                raise DataError(f"{name}.json: {e!r}") from None
    finally:
        if own:
            await session.close()
    check(out["tours"], out["pool"], out["matches"])
    return out


def save_cache(cache: Path, files: dict[str, dict]) -> None:
    """Атомарно: сначала .tmp, потом переименование."""
    cache.mkdir(parents=True, exist_ok=True)
    for name, obj in files.items():
        tmp = cache / f"{name}.json.tmp"
        tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, cache / f"{name}.json")


def load_cache(cache: Path) -> dict[str, dict] | None:
    try:
        files = {n: json.loads((cache / f"{n}.json").read_text(encoding="utf-8")) for n in FILES}
        check(files["tours"], files["pool"], files["matches"])
        return files
    except (OSError, ValueError, DataError):
        return None
