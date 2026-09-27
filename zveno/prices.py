"""Стоимость наклейки и статусы (ADR-014, разделы 5, 7 и 14).

Цена — обещание очков за матч:

    ОзМ* = (0,5·Ипр·(ОзМпр + Рост) + Отек + 10·База) / (0,5·Ипр + Итек + 10)
    Стоимость = Мин + Шаг · max(0, ОзМ* − Порог), шаг 100, коридор Мин … 15 000

С затуханием: перед каждым новым сыгранным матчем все веса (прошлый сезон, база, прежние матчи)
умножаются на 0,5^(1/20) — матч двадцать игр назад весит вдвое меньше свежего.

Предел: за окно между дедлайнами стоимость уходит не дальше ±500 от стоимости на дедлайн (в
Прологе и турах 1–4 — ±800). Стоимость меняется только в окне, где игрок сыграл: кто не играл, тот
не дешевеет. Так же считает модель сезона tools/fantasy_model/season.py — цифры совпадают.
"""
from dataclasses import dataclass, field

from . import rules


def fprice(slot: str, q: float) -> int:
    """Стоимость по ОзМ*: шаг 100, коридор от минимума слота до 15 000."""
    c = rules.PRICE[slot]
    p = c["min"] + c["step"] * max(0.0, q - c["threshold"])
    p = min(max(p, c["min"]), rules.PRICE_MAX)
    return int(round(p / rules.PRICE_ROUND)) * rules.PRICE_ROUND


def promise(slot: str, price: int) -> float:
    """Обещание очков за матч по стоимости. На минимуме — чуть ниже порога: там формула плоская."""
    c = rules.PRICE[slot]
    if price <= c["min"]:
        return c["threshold"] - rules.PROMISE_BELOW_MIN
    return c["threshold"] + (price - c["min"]) / c["step"]


def newbie_price(slot: str) -> int:
    """База новичков: 5 400 нападающий, 4 800 защитник, 6 500 ворота."""
    return fprice(slot, rules.PRICE[slot]["base"])


def cap_for(window: int) -> int:
    """Предел за окно: Пролог (окно 0) и туры 1–4 — ±800, дальше ±500."""
    return rules.PRICE_CAP_EARLY if max(window, 1) <= rules.PRICE_CAP_EARLY_TOURS else rules.PRICE_CAP


@dataclass
class PricePath:
    price: int                  # стоимость сейчас
    price_monday: int           # на последний дедлайн: от неё предел за тур
    q: float                    # ОзМ* сейчас
    target: int                 # стоимость по формуле без предела
    games: int                  # сыграно в этом сезоне
    mondays: dict[int, int] = field(default_factory=dict)   # окно → стоимость на его начало


def price_path(slot: str, prior: tuple[float, float] | None, played: list[tuple[int, float]], window_now: int,
               decay_half: float | None = rules.DECAY_HALF) -> PricePath:
    """Стоимость по сыгранным матчам сезона.

    prior — (матчей, очков за матч) прошлого сезона или None для новичка.
    played — (окно, очки) каждого сыгранного матча по порядку; окно — номер последнего дедлайна
    перед матчем, 0 — Пролог (zveno.tours.price_window). window_now — окно, которое идёт сейчас."""
    c = rules.PRICE[slot]
    lam = 0.5 ** (1 / decay_half) if decay_half else 1.0
    wpr = rules.PRIOR_WEIGHT * prior[0] if prior else 0.0
    qpr = (prior[1] + c["growth"]) if prior else 0.0
    wb, sp, n = float(rules.BASE_WEIGHT), 0.0, 0.0
    q = (wpr * qpr + wb * c["base"]) / (wpr + wb)
    p = fprice(slot, q)
    by_window: dict[int, list[float]] = {}
    for w, pts in played:
        by_window.setdefault(w, []).append(pts)
    last = max([window_now, *by_window])
    mondays = {}
    for w in range(0, last + 1):
        mondays[w] = p
        pts = by_window.get(w)
        if not pts:
            continue
        for x in pts:
            if lam < 1:
                sp *= lam
                n *= lam
                wpr *= lam
                wb *= lam
            sp += x
            n += 1
        q = (wpr * qpr + sp + wb * c["base"]) / (wpr + n + wb)
        cap = cap_for(w)
        p = min(max(fprice(slot, q), mondays[w] - cap), mondays[w] + cap)
    return PricePath(price=p, price_monday=mondays[last], q=q, target=fprice(slot, q), games=len(played),
                     mondays=mondays)


def rest_status(club_matches: list[str], played: set[str]) -> str:
    """«rest» — пропустил 4 последних матча своей команды подряд. Туры без матчей клуба не считаются."""
    last = club_matches[-rules.REST_MISSED:]
    if len(last) == rules.REST_MISSED and not played.intersection(last):
        return "rest"
    return "ok"


def in_form(slot: str, price: int, match_points: list[float]) -> bool:
    """«В форме»: последние 4 сыгранных матча в среднем на очко выше обещания стоимости.
    Метка только хорошая: «не в форме» не бывает."""
    if len(match_points) < rules.FORM_MIN_GAMES:
        return False
    recent = match_points[-rules.FORM_LAST:]
    return sum(recent) / len(recent) >= promise(slot, price) + rules.FORM_MARGIN
