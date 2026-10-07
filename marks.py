"""Журнал отметок голов (ADR-033): отметки людей — показания, а не истина.

Каждая отметка админа или помощника — время гола в записи (`time`), «гола нет в записи» (`absent`), «табло сбилось»
(`wrong`), позже — ответ на готовый клип (`confirm`, `reject`) — строка таблицы `goal_marks` в state.db. Строки только
добавляются: изменить или удалить их запрещают триггеры. Отмена — строка `revoke` со ссылкой на отменяемую; отменить
отмену — значит вернуть отметку. Единственное разрешённое изменение — стереть Telegram id отметившего
(`/marks_forget`, персональные данные): кто ушёл из ADMIN_IDS и PREVIEW_IDS, остаётся в журнале только ролью.

Отметка без гола и без секунды (пустой `score`, `kind: time`, есть `video`) говорит только одно: вот запись этого
матча. Её админ ставит в `/replay`, присылая ссылку без времён (этап 0.4 плана): голы в записи служба clips найдёт
по табло сама, а где не сможет — пришлёт превью.

Что сейчас действует у матча (`resolve`): ролик — тот, к которому относится последняя отметка; у гола — последняя
неотменённая отметка этого ролика: время или «нет в записи»; «табло сбилось» — пометки этого ролика. Из этого бот
собирает live/replays.json в прежнем формате (ADR-027): сборка, API и служба clips читают его, как раньше. Пишет журнал
только бот; отметки из replays.json, сделанные до журнала, переносятся в него один раз (`import_replays`, кто — неизвестен).

Только stdlib, без сети.
"""
import json
import sqlite3
from datetime import datetime

import replay

KINDS = ("time", "absent", "wrong", "confirm", "reject", "revoke")
ROLES = ("admin", "helper", "import")
SEEN_MAX = 500   # знаков: что было перед глазами отметившего — его сообщение или окно превью

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS goal_marks (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    at      TEXT    NOT NULL,
    match   TEXT    NOT NULL,
    score   TEXT    NOT NULL,
    period  TEXT,
    time    TEXT,
    kind    TEXT    NOT NULL CHECK (kind IN ({", ".join(f"'{k}'" for k in KINDS)})),
    video   TEXT,
    sec     INTEGER,
    target  INTEGER REFERENCES goal_marks (id),
    who     INTEGER,
    role    TEXT    NOT NULL CHECK (role IN ({", ".join(f"'{r}'" for r in ROLES)})),
    via     TEXT    NOT NULL,
    seen    TEXT
);
CREATE INDEX IF NOT EXISTS goal_marks_match ON goal_marks (match);
CREATE TABLE IF NOT EXISTS goal_marks_meta (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS goal_marks_keep BEFORE DELETE ON goal_marks
BEGIN
    SELECT RAISE(ABORT, 'журнал отметок только пополняется (ADR-033)');
END;
CREATE TRIGGER IF NOT EXISTS goal_marks_fixed BEFORE UPDATE ON goal_marks
WHEN NOT (NEW.who IS NULL AND NEW.id IS OLD.id AND NEW.at IS OLD.at AND NEW.match IS OLD.match
          AND NEW.score IS OLD.score AND NEW.period IS OLD.period AND NEW.time IS OLD.time AND NEW.kind IS OLD.kind
          AND NEW.video IS OLD.video AND NEW.sec IS OLD.sec AND NEW.target IS OLD.target AND NEW.role IS OLD.role
          AND NEW.via IS OLD.via AND NEW.seen IS OLD.seen)
BEGIN
    SELECT RAISE(ABORT, 'отметку не меняют — её отменяют новой строкой (ADR-033)');
END;
"""

COLUMNS = ("id", "at", "match", "score", "period", "time", "kind", "video", "sec", "target", "who", "role", "via",
           "seen")


def revoked(rows: list[dict]) -> set[int]:
    """Id отметок, которые сейчас отменены. Отмена отмены возвращает отметку: идём от последних строк к первым."""
    by_target: dict[int, list[int]] = {}
    for r in rows:
        if r["kind"] == "revoke" and r.get("target"):
            by_target.setdefault(r["target"], []).append(r["id"])
    out: set[int] = set()
    for r in sorted(rows, key=lambda x: x["id"], reverse=True):   # отмена всегда позже отменяемой
        if any(v not in out for v in by_target.get(r["id"], [])):
            out.add(r["id"])
    return out


def resolve(rows: list[dict]) -> dict | None:
    """Что действует у матча по его отметкам: {"video", "anchors" (счёт → секунда), "absent", "wrong", "confirm",
    "reject"}. confirm и reject — счёт → секунды, на которых человек посмотрел 30 с гола и сказал «✅ Гол виден» или
    «⏪/⏩ гол раньше/позже» (ADR-033, раздел 4): у одной секунды действует последнее из двух. Ни одной действующей
    отметки с роликом — None: повторов по разметке у матча нет."""
    off = revoked(rows)
    live = [r for r in sorted(rows, key=lambda x: x["id"]) if r["id"] not in off and r["kind"] != "revoke"]
    with_video = [r for r in live if r.get("video")]
    if not with_video:
        return None
    last = with_video[-1]["video"]
    ours = [r for r in with_video if r["video"] == last or replay.same_video(r["video"], last)]
    video = ours[0]["video"]   # первая запись того же ролика: ссылка не скачет между vk.com и vkvideo.ru
    goal: dict[str, dict] = {}
    wrong: set[str] = set()
    votes: dict[str, dict[int, str]] = {}
    for r in ours:
        if r["kind"] in ("time", "absent") and r["score"]:
            goal[r["score"]] = r
        elif r["kind"] == "wrong" and r["score"]:
            wrong.add(r["score"])
        elif r["kind"] in ("confirm", "reject") and r["score"] and isinstance(r.get("sec"), int):
            votes.setdefault(r["score"], {})[int(r["sec"])] = r["kind"]
    anchors = {s: int(r["sec"]) for s, r in goal.items() if r["kind"] == "time" and isinstance(r.get("sec"), int)}
    absent = sorted(s for s, r in goal.items() if r["kind"] == "absent")
    said = lambda kind: {s: sorted(t for t, k in v.items() if k == kind)   # noqa: E731
                         for s, v in sorted(votes.items()) if kind in v.values()}
    return {"video": video, "anchors": dict(sorted(anchors.items())), "absent": absent, "wrong": sorted(wrong),
            "confirm": said("confirm"), "reject": said("reject")}


def active(rows: list[dict], score: str | None = None) -> list[dict]:
    """Неотменённые отметки (без самих отмен), по порядку; score — только этого гола."""
    off = revoked(rows)
    return [r for r in sorted(rows, key=lambda x: x["id"])
            if r["id"] not in off and r["kind"] != "revoke" and (score is None or r["score"] == score)]


class MarksStore:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        conn.executescript(SCHEMA)

    def add(self, now: datetime, match: str, score: str, kind: str, *, role: str, via: str, who: int | None = None,
            video: str | None = None, sec: int | None = None, target: int | None = None, period=None, time=None,
            seen=None) -> int:
        """Новая отметка. seen — что было перед глазами (текст или словарь), обрезается до SEEN_MAX. Возвращает id."""
        if kind not in KINDS or role not in ROLES:
            raise ValueError(f"отметка {kind!r} от {role!r} — таких нет")
        if seen is not None and not isinstance(seen, str):
            seen = json.dumps(seen, ensure_ascii=False)
        cur = self.conn.execute(
            "INSERT INTO goal_marks (at, match, score, period, time, kind, video, sec, target, who, role, via, seen) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (now.isoformat(timespec="seconds"), match, score or "", None if period is None else str(period),
             None if time is None else str(time), kind, video, sec, target, who, role, via,
             seen[:SEEN_MAX] if seen else None))
        return cur.lastrowid

    def of(self, match: str) -> list[dict]:
        """Все строки журнала матча, по порядку."""
        cur = self.conn.execute(f"SELECT {', '.join(COLUMNS)} FROM goal_marks WHERE match = ? ORDER BY id", (match,))
        return [dict(zip(COLUMNS, row)) for row in cur]

    def get(self, mark_id: int) -> dict | None:
        row = self.conn.execute(f"SELECT {', '.join(COLUMNS)} FROM goal_marks WHERE id = ?", (mark_id,)).fetchone()
        return dict(zip(COLUMNS, row)) if row else None

    def state(self, match: str) -> dict | None:
        return resolve(self.of(match))

    def revoke(self, now: datetime, mark_id: int, *, role: str, via: str, who: int | None = None) -> int | None:
        """Отменить действующую отметку. Уже отменена или её нет — None."""
        mark = self.get(mark_id)
        if not mark or mark["kind"] == "revoke" or mark_id in revoked(self.of(mark["match"])):
            return None
        return self.add(now, mark["match"], mark["score"], "revoke", role=role, via=via, who=who, target=mark_id)

    def revoke_match(self, now: datetime, match: str, *, role: str, via: str, who: int | None = None) -> int:
        """Отменить все действующие отметки матча («Сбросить повторы матча»). Сколько отменено."""
        rows = active(self.of(match))
        for r in rows:
            self.add(now, match, r["score"], "revoke", role=role, via=via, who=who, target=r["id"])
        return len(rows)

    def forget(self, who: int) -> int:
        """Стереть Telegram id отметившего во всех строках: остаётся роль. Сколько строк."""
        return self.conn.execute("UPDATE goal_marks SET who = NULL WHERE who = ?", (who,)).rowcount

    def import_replays(self, games: dict, now: datetime) -> int:
        """Отметки из live/replays.json, сделанные до журнала, — в журнал, один раз: опоры (`time`), «нет в записи»,
        «табло сбилось». Кто отметил — неизвестно (ADR-027 не хранил), `role: import`. Сколько строк добавлено."""
        if self.conn.execute("SELECT 1 FROM goal_marks_meta WHERE key = 'import'").fetchone():
            return 0
        n = 0
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            for match, e in sorted((games or {}).items()):
                if not isinstance(e, dict) or not isinstance(e.get("video"), str):
                    continue
                at = _when(e.get("updated"), now)
                for score, sec in sorted((e.get("anchors") or {}).items()):
                    if isinstance(sec, int):
                        self.add(at, match, score, "time", role="import", via="import", video=e["video"], sec=sec)
                        n += 1
                for kind in ("absent", "wrong"):
                    for score in e.get(kind) or []:
                        self.add(at, match, score, kind, role="import", via="import", video=e["video"])
                        n += 1
            self.conn.execute("INSERT INTO goal_marks_meta (key, value) VALUES ('import', ?)",
                              (now.isoformat(timespec="seconds"),))
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        return n


def _when(value, now: datetime) -> datetime:
    try:
        got = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return now
    return got if got.tzinfo else now
