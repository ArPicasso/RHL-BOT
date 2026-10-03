#!/usr/bin/env bash
# Пустить бота в Telegram через зарубежный сервер, когда с VPS в России api.telegram.org закрыт.
# Запускать от root на российском сервере, после setup.sh:
#
#   bash /opt/rhl/deploy/tunnel.sh user@ЗАРУБЕЖНЫЙ-IP [порт-ssh]
#
# На зарубежном сервере ничего ставить не надо, нужен только вход по SSH. Пароль спросят один раз:
# скрипт положит туда ключ, которому разрешён только проброс портов — ни шелла, ни команд.
set -euo pipefail

[ "$(id -u)" = 0 ] || { echo "Запусти от root"; exit 1; }
TARGET=${1:?"Укажи зарубежный сервер: bash tunnel.sh user@IP [порт]"}
PORT=${2:-22}
KEY=/root/.ssh/rhl_tunnel
APP=/opt/rhl
step() { printf '\n== %s\n' "$*"; }

step "Ключ туннеля"
install -d -m 700 /root/.ssh
[ -f "$KEY" ] || ssh-keygen -q -t ed25519 -N '' -C rhl-tunnel -f "$KEY"
ssh-keyscan -p "$PORT" -H "${TARGET#*@}" >> /root/.ssh/known_hosts 2>/dev/null
sort -u -o /root/.ssh/known_hosts /root/.ssh/known_hosts

step "Ключ на зарубежный сервер (спросит пароль от $TARGET)"
# Ключ уже стоит — ssh войдёт и получит /bin/false (код 1); не стоит — код 255
code=0; ssh -i "$KEY" -p "$PORT" -o BatchMode=yes -o ConnectTimeout=10 "$TARGET" 2>/dev/null || code=$?
if [ "$code" = 255 ]; then
  pub=$(cat "$KEY.pub")
  ssh -p "$PORT" "$TARGET" "mkdir -p ~/.ssh && chmod 700 ~/.ssh && \
    echo 'restrict,port-forwarding,command=\"/bin/false\" $pub' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
fi

step "Служба tg-tunnel"
install -d -m 750 -o root -g rhl /etc/rhl
printf 'TUNNEL_HOST=%s\nTUNNEL_PORT=%s\n' "$TARGET" "$PORT" > /etc/rhl/tunnel.env
install -m 644 "$APP/deploy/tg-tunnel.service" /etc/systemd/system/tg-tunnel.service
systemctl daemon-reload
systemctl enable tg-tunnel >/dev/null
systemctl restart tg-tunnel
sleep 4

step "Проверка: Telegram через туннель"
if ! curl -sS -m 15 -o /dev/null -w "api.telegram.org: %{http_code}\n" \
     --socks5-hostname 127.0.0.1:1080 https://api.telegram.org; then
  journalctl -u tg-tunnel -n 20 --no-pager
  echo "Туннель не работает — смотри журнал выше"
  exit 1
fi

step "Бот через туннель"
if grep -q '^TELEGRAM_PROXY=' /etc/rhl/bot.env; then
  sed -i 's#^TELEGRAM_PROXY=.*#TELEGRAM_PROXY=socks5://127.0.0.1:1080#' /etc/rhl/bot.env
else
  echo 'TELEGRAM_PROXY=socks5://127.0.0.1:1080' >> /etc/rhl/bot.env
fi
sudo -u rhl "$APP/venv/bin/pip" install -q --disable-pip-version-check -r "$APP/requirements.txt"
systemctl restart bot
sleep 8
journalctl -u bot -n 15 --no-pager
systemctl is-active -q bot && echo "Бот работает через туннель. Пиши ему /start."
