#!/usr/bin/env bash
# Выложить свежий main на сервер и перезапустить службы bot, live, api и pages. Ставится как
# /usr/local/sbin/rhl-update (deploy/setup.sh, deploy/https.sh) и вызывается по ключу GitHub Actions
# (.github/workflows/deploy.yml) или руками. Файлы состояния (subscribers.json, announced.json,
# state.db, live/, status/) не в git — git их не трогает.
set -euo pipefail
APP=/opt/rhl
SELF=/usr/local/sbin/rhl-update
# служба:файл кода. Файла ещё нет в main — служба пропускается, выкладка не падает
SERVICES="bot:bot.py live:live.py api:server.py pages:pages_kick.py"
cd "$APP"

if [ -z "${RHL_BEFORE:-}" ]; then
  before=$(sudo -u rhl git rev-parse HEAD)
  sudo -u rhl git fetch -q origin main
  sudo -u rhl git reset -q --hard origin/main
  # Выкладка сама поменялась в git — ставим новую и продолжаем уже ею
  if ! cmp -s deploy/update.sh "$SELF"; then
    install -m 755 deploy/update.sh "$SELF.new"
    mv -f "$SELF.new" "$SELF"
    if [ "$(readlink -f "$0")" = "$SELF" ]; then
      RHL_BEFORE=$before exec "$SELF"
    fi
  fi
else
  before=$RHL_BEFORE
fi
after=$(sudo -u rhl git rev-parse HEAD)
sudo -u rhl venv/bin/pip install -q --disable-pip-version-check -r requirements.txt

# Правки только в webapp/, docs/, art/, stickers/ и *.md службам не нужны — как paths-ignore в
# deploy.yml: такие коммиты служб не перезапускают. Запуск руками без новых коммитов — перезапускает
code=1
if [ "$before" != "$after" ]; then
  changed=$(sudo -u rhl git diff --name-only "$before" "$after" 2>/dev/null || echo "?")
  if [ -n "$changed" ] && ! grep -qvE '^(webapp|docs|art|stickers)/|^[^/]+\.md$' <<<"$changed"; then
    code=0
  fi
fi

# Счётчики и пульс служб для пульта админа (ADR-021): бот и pages пишут сюда, api читает
install -d -m 750 -o rhl -g rhl "$APP/status"

# Бэкап состояния раз в сутки (deploy/backup.sh): каталог заводим мы — у rhl нет прав на /var/backups
if [ -f deploy/backup.timer ]; then
  install -d -m 750 -o rhl -g rhl /var/backups/rhl
  reload=0
  for unit in backup.service backup.timer; do
    if ! cmp -s "deploy/$unit" "/etc/systemd/system/$unit"; then
      install -m 644 "deploy/$unit" "/etc/systemd/system/$unit"
      reload=1
    fi
  done
  [ "$reload" = 0 ] || systemctl daemon-reload
  systemctl enable -q --now backup.timer
  echo "Бэкап состояния: $(systemctl show -p NextElapseUSecRealtime --value backup.timer 2>/dev/null || true)"
fi

restarted=""
for pair in $SERVICES; do
  s=${pair%%:*}
  file=${pair#*:}
  unit=/etc/systemd/system/$s.service
  if [ ! -f "$APP/$file" ]; then
    echo "Служба $s пропущена: $file ещё нет в main"
    if [ -f "$unit" ] && systemctl is-enabled -q "$s" 2>/dev/null; then
      systemctl disable --now -q "$s" || true
    fi
    continue
  fi
  fresh=0
  if ! cmp -s "deploy/$s.service" "$unit"; then
    install -m 644 "deploy/$s.service" "$unit"
    systemctl daemon-reload
    fresh=1
  fi
  systemctl enable -q "$s"
  if [ "$code" = 1 ] || [ "$fresh" = 1 ] || ! systemctl is-active -q "$s"; then
    systemctl restart "$s"
    restarted="$restarted $s"
  else
    echo "Служба $s не перезапускалась: менялись только webapp/, docs/ и тексты"
  fi
done

[ -z "$restarted" ] || sleep 5
failed=""
for s in $restarted; do
  ok=0
  if [ "$s" = api ]; then
    # API при старте сверяет соль «Раската» с Pages — даём ему до 30 секунд
    port=$(sed -n 's/^API_PORT=//p' /etc/rhl/bot.env 2>/dev/null | tail -1 || true)
    for _ in $(seq 1 30); do
      if curl -fsS -m 3 -o /dev/null "http://127.0.0.1:${port:-8080}/api/health"; then ok=1; break; fi
      sleep 1
    done
  elif systemctl is-active -q "$s"; then
    ok=1
  fi
  if [ "$ok" = 1 ]; then
    echo "Служба $s работает"
  else
    journalctl -u "$s" -n 30 --no-pager
    failed="$failed $s"
  fi
done
if [ -n "$failed" ]; then
  echo "Не поднялись:$failed после ${after::8}"
  exit 1
fi
echo "Выложено: ${before::8} → ${after::8}"
