#!/usr/bin/env bash
# Выложить свежий main на сервер и перезапустить бота. Ставится как /usr/local/sbin/rhl-update
# (deploy/setup.sh) и вызывается по ключу GitHub Actions (.github/workflows/deploy.yml) или руками.
# Файлы состояния (subscribers.json, announced.json) не в git — git их не трогает.
set -euo pipefail
APP=/opt/rhl
cd "$APP"

before=$(sudo -u rhl git rev-parse HEAD)
sudo -u rhl git fetch -q origin main
sudo -u rhl git reset -q --hard origin/main
after=$(sudo -u rhl git rev-parse HEAD)
sudo -u rhl venv/bin/pip install -q --disable-pip-version-check -r requirements.txt

# Служба поменялась в git — переставить
if ! cmp -s deploy/bot.service /etc/systemd/system/bot.service; then
  install -m 644 deploy/bot.service /etc/systemd/system/bot.service
  systemctl daemon-reload
fi

systemctl restart bot
sleep 5
if ! systemctl is-active -q bot; then
  journalctl -u bot -n 30 --no-pager
  echo "Бот не поднялся после ${after::8}"
  exit 1
fi
echo "Бот работает: ${before::8} → ${after::8}"
