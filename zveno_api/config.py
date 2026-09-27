"""Настройки сервера «Звена» — только из переменных окружения. Токен в git не попадает (правило 2)."""
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEBAPP_URL = "https://arpicasso.github.io/bogdanov/"
PREFIX = "/api/zveno"   # база API: https://<домен>/api/zveno (контракт, раздел 4)


def _data_url(webapp: str) -> str:
    return webapp.rstrip("/") + "/data/zveno/"


def _origin(webapp: str) -> str:
    """https://arpicasso.github.io/bogdanov/ → https://arpicasso.github.io"""
    parts = webapp.split("/")
    return "/".join(parts[:3]) if len(parts) >= 3 else webapp


@dataclass
class Config:
    bot_token: str
    db: str = "zveno.db"
    data_url: str = _data_url(WEBAPP_URL)            # опубликованные data/zveno/: URL или путь к каталогу
    origin: str = _origin(WEBAPP_URL)                # CORS — только адрес Pages
    port: int = 8090
    host: str = "127.0.0.1"                          # наружу — только через nginx
    webapp_url: str = WEBAPP_URL                     # кнопка «Открыть «Звено»» в сообщениях
    telegram_api: str = "https://api.telegram.org"
    teams_file: Path = field(default_factory=lambda: ROOT / "teams.json")
    cache_dir: Path | None = None                    # последние удачные данные; по умолчанию — рядом с базой
    refresh_every: int = 600                         # раз в 10 минут подтягиваем данные
    tick_every: int = 60                             # раз в минуту — дедлайны и пульс
    messages_every: int = 600                        # раз в 10 минут — сообщения по расписанию
    send_messages: bool = True

    def cache(self) -> Path:
        if self.cache_dir:
            return Path(self.cache_dir)
        return Path(self.db).resolve().parent / "zveno_cache"


def from_env(env=None) -> Config:
    env = os.environ if env is None else env
    token = env.get("BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("Нет переменной BOT_TOKEN: без токена бота не проверить initData. См. deploy/README.md")
    webapp = env.get("WEBAPP_URL") or WEBAPP_URL
    return Config(
        bot_token=token,
        db=env.get("ZVENO_DB") or "zveno.db",
        data_url=env.get("ZVENO_DATA_URL") or _data_url(webapp),
        origin=(env.get("ZVENO_ORIGIN") or _origin(webapp)).rstrip("/"),
        port=int(env.get("ZVENO_PORT") or 8090),
        host=env.get("ZVENO_HOST") or "127.0.0.1",
        webapp_url=webapp,
        telegram_api=(env.get("TELEGRAM_API") or "https://api.telegram.org").rstrip("/"),
        cache_dir=Path(env["ZVENO_CACHE"]) if env.get("ZVENO_CACHE") else None,
        send_messages=(env.get("ZVENO_MESSAGES") or "1") != "0",
    )
