"""Проверка Telegram.WebApp.initData (контракт, раздел 4).

Ключ — HMAC-SHA256 от токена бота со строкой «WebAppData» в роли ключа. Подпись — HMAC-SHA256 этим
ключом от строки проверки: все поля, кроме hash, по алфавиту, «ключ=значение» через перевод строки.
Пользователь — только из проверенного initData; свежесть auth_date — 24 часа.
"""
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl

MAX_AGE = 24 * 3600
FUTURE_SKEW = 300            # часы телефона и сервера расходятся — пять минут вперёд прощаем


class AuthError(Exception):
    """Текст — для болельщика, по-русски."""


@dataclass(frozen=True)
class TgUser:
    id: int
    first_name: str = ""
    last_name: str = ""
    username: str = ""

    @property
    def display(self) -> str:
        return " ".join(x for x in (self.first_name, self.last_name) if x).strip() or self.username


def secret_key(bot_token: str) -> bytes:
    return hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()


def check_string(pairs: dict[str, str]) -> str:
    return "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs) if k != "hash")


def sign(pairs: dict[str, str], bot_token: str) -> str:
    return hmac.new(secret_key(bot_token), check_string(pairs).encode(), hashlib.sha256).hexdigest()


def verify(init_data: str, bot_token: str, now: float | None = None, max_age: int = MAX_AGE) -> TgUser:
    """Проверенный пользователь из initData или AuthError."""
    if not init_data:
        raise AuthError("Открой «Звено» из Telegram: без него не узнать, чья это команда.")
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        raise AuthError("Не получилось проверить вход. Открой «Звено» из Telegram ещё раз.") from None
    got = pairs.get("hash", "")
    if not got or not hmac.compare_digest(sign(pairs, bot_token), got.lower()):
        raise AuthError("Не получилось проверить вход. Открой «Звено» из Telegram ещё раз.")
    try:
        auth_date = int(pairs.get("auth_date", ""))
    except ValueError:
        raise AuthError("Не получилось проверить вход. Открой «Звено» из Telegram ещё раз.") from None
    now = time.time() if now is None else now
    if now - auth_date > max_age or auth_date - now > FUTURE_SKEW:
        raise AuthError("Вход устарел. Закрой и снова открой мини-апп из Telegram.")
    try:
        u = json.loads(pairs.get("user", ""))
        uid = int(u["id"])
    except (ValueError, KeyError, TypeError):
        raise AuthError("Telegram не передал, кто ты. Открой «Звено» из чата с ботом.") from None
    if uid <= 0:
        raise AuthError("Telegram не передал, кто ты. Открой «Звено» из чата с ботом.")
    return TgUser(uid, str(u.get("first_name") or ""), str(u.get("last_name") or ""), str(u.get("username") or ""))


def from_header(value: str | None, bot_token: str, now: float | None = None) -> TgUser:
    """Заголовок `Authorization: tma <initData>`."""
    if not value or not value.startswith("tma "):
        raise AuthError("Открой «Звено» из Telegram: без него не узнать, чья это команда.")
    return verify(value[4:].strip(), bot_token, now)


def make_init_data(user: dict, bot_token: str, auth_date: int, **extra: str) -> str:
    """Подписанный initData — для тестов и ручной проверки curl'ом (deploy/README.md)."""
    from urllib.parse import urlencode
    pairs = {"auth_date": str(auth_date), "user": json.dumps(user, ensure_ascii=False, separators=(",", ":")), **extra}
    pairs["hash"] = sign(pairs, bot_token)
    return urlencode(pairs)
