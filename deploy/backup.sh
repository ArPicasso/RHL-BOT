#!/usr/bin/env bash
# Бэкап состояния: state.db (зачёт «Раската» и прогнозы, ADR-018, ADR-020) и файлы подписок бота.
# Их нет в git, и восстановить их больше негде: сервер умрёт — подписчики нажмут «Напоминать»
# заново, а зачёт начнётся с нуля. Раз в сутки запускает таймер systemd (deploy/backup.timer),
# руками — bash /opt/rhl/deploy/backup.sh. Ставит и включает rhl-update (deploy/update.sh).
#
# Переменные (по умолчанию хватает): BACKUP_DIR — куда, BACKUP_KEEP — сколько дней держать.
set -euo pipefail

APP=${APP:-/opt/rhl}
DEST=${BACKUP_DIR:-/var/backups/rhl}
KEEP=${BACKUP_KEEP:-14}
STATE="subscribers.json announced.json raskat_waitlist.json reminded.json goals_off.json"
day=$(date +%F)

# Каталог заводит rhl-update: от пользователя rhl в /var/backups не создать
install -d -m 750 "$DEST" 2>/dev/null || [ -d "$DEST" ] || {
  echo "Нет каталога $DEST, и создать его отсюда нельзя — заведи его: rhl-update"; exit 1; }
[ -w "$DEST" ] || { echo "В $DEST не пишется: он должен принадлежать rhl"; exit 1; }

# SQLite на живой базе копируется только через .backup: простой cp посреди записи даёт битый файл
if [ -f "$APP/state.db" ]; then
  "$APP/venv/bin/python" - "$APP/state.db" "$DEST/state-$day.db" <<'PY'
import sqlite3
import sys

src, dst = sys.argv[1], sys.argv[2]
with sqlite3.connect(f"file:{src}?mode=ro", uri=True) as s, sqlite3.connect(dst) as d:
    s.backup(d)
PY
  echo "state.db → $DEST/state-$day.db"
fi

for f in $STATE; do
  if [ -f "$APP/$f" ]; then
    cp -p "$APP/$f" "$DEST/${f%.json}-$day.json"
    echo "$f → $DEST/${f%.json}-$day.json"
  fi
done

chmod 640 "$DEST"/*-"$day".* 2>/dev/null || true

# Старое убираем сами: в $DEST лежат только наши файлы
find "$DEST" -maxdepth 1 -type f -mtime +"$KEEP" -delete
echo "Бэкап за $day готов, держим $KEEP дней: $(find "$DEST" -maxdepth 1 -type f | wc -l) файлов"
