#!/usr/bin/env bash
# Бэкап базы «Звена»: sqlite3 .backup — безопасно при работающем сервере (WAL), потом проверка
# целостности и gzip. Храним 14 последних копий, старые удаляем: «Удалить моё „Звено“» доходит и до
# бэкапов не позже чем через 14 дней. Запускает zveno-backup.timer раз в сутки (deploy/README.md, шаг 12).
set -euo pipefail

DB="${ZVENO_DB:-/var/lib/rhl/zveno.db}"
DIR="${BACKUP_DIR:-/var/backups/rhl}"
KEEP="${KEEP:-14}"

if [ ! -f "$DB" ]; then
  echo "Нет базы $DB — нечего сохранять" >&2
  exit 1
fi
umask 077
mkdir -p "$DIR"
stamp="$(TZ=Europe/Moscow date +%Y-%m-%d_%H%M)"
out="$DIR/zveno-$stamp.db"

sqlite3 "$DB" ".backup '$out'"
if [ "$(sqlite3 "$out" 'PRAGMA integrity_check;')" != "ok" ]; then
  echo "Копия $out не прошла проверку целостности" >&2
  rm -f "$out"
  exit 1
fi
gzip -f "$out"

# оставить KEEP свежих
ls -1t "$DIR"/zveno-*.db.gz 2>/dev/null | tail -n +"$((KEEP + 1))" | xargs -r rm -f --
echo "Бэкап готов: $out.gz"
