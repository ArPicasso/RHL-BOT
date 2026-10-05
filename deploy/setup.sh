#!/usr/bin/env bash
# Первая настройка VPS под бота РХЛ U21. Запускать от root на самом сервере:
#
#   curl -fsSL https://raw.githubusercontent.com/ArPicasso/RHL-BOT/main/deploy/setup.sh -o setup.sh
#   bash setup.sh
#
# Повторный запуск безопасен: что уже сделано, пропускается. Токен бота скрипт спросит
# сам и положит только в /etc/rhl/bot.env (права 640) — в git, чат и журналы он не попадает.
# Ставит службы bot, live и api; HTTPS для API — отдельно, deploy/https.sh.
# Порядок и пояснения — BOT_README.md.
set -euo pipefail

REPO=https://github.com/ArPicasso/RHL-BOT.git
APP=/opt/rhl
ENV_DIR=/etc/rhl
DEPLOY_KEY=/root/.ssh/rhl_actions

[ "$(id -u)" = 0 ] || { echo "Запусти от root"; exit 1; }
step() { printf '\n== %s\n' "$*"; }

step "Пакеты и время"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q git python3-venv python3-pip ufw curl ffmpeg
timedatectl set-timezone Europe/Moscow

step "Файрвол: наружу открыт только SSH"
ufw allow OpenSSH >/dev/null
ufw --force enable >/dev/null
ufw status | head -5

step "Пользователь rhl и код в $APP"
id rhl >/dev/null 2>&1 || adduser --system --group --home "$APP" --shell /usr/sbin/nologin rhl
if [ ! -d "$APP/.git" ]; then
  tmp=$(mktemp -d)
  git clone -q "$REPO" "$tmp/rhl"
  cp -a "$tmp/rhl/." "$APP/"
  rm -rf "$tmp"
fi
chown -R rhl:rhl "$APP"
sudo -u rhl git -C "$APP" config pull.ff only
[ -x "$APP/venv/bin/python" ] || sudo -u rhl python3 -m venv "$APP/venv"
sudo -u rhl "$APP/venv/bin/pip" install -q --disable-pip-version-check -r "$APP/requirements.txt"

step "Тесты"
if ! (cd "$APP" && sudo -u rhl venv/bin/python -m unittest discover -s tests >/tmp/rhl-tests.log 2>&1); then
  echo "ВНИМАНИЕ: тесты упали, журнал — /tmp/rhl-tests.log. Продолжаю."
fi
tail -1 /tmp/rhl-tests.log

step "Токен бота"
install -d -m 750 -o root -g rhl "$ENV_DIR"
if [ ! -f "$ENV_DIR/bot.env" ]; then
  install -m 640 -o root -g rhl "$APP/deploy/bot.env.example" "$ENV_DIR/bot.env"
fi
if ! grep -q '^BOT_TOKEN=.\+' "$ENV_DIR/bot.env"; then
  read -rsp "Вставь токен бота от @BotFather (символы не видны): " token; echo
  [[ "$token" =~ ^[0-9]+:[A-Za-z0-9_-]+$ ]] || { echo "Это не похоже на токен"; exit 1; }
  sed -i "s#^BOT_TOKEN=.*#BOT_TOKEN=$token#" "$ENV_DIR/bot.env"
  unset token
fi
echo "Токен на месте: $ENV_DIR/bot.env"

step "Старый ryazan-bot, если был"
if [ -f /etc/systemd/system/ryazan-bot.service ]; then
  systemctl disable --now ryazan-bot 2>/dev/null || true
  for f in subscribers.json announced.json zveno_waitlist.json; do
    if [ -f "/opt/ryazan_bot/$f" ] && [ ! -f "$APP/$f" ]; then
      install -m 640 -o rhl -g rhl "/opt/ryazan_bot/$f" "$APP/$f" && echo "перенесён $f"
    fi
  done
fi

step "Службы bot, live и api"
# Ставит и запускает их выкладка rhl-update — та же, что потом зовёт GitHub Actions.
# Службу, чьего кода ещё нет в main (live.py), она пропускает
install -m 755 "$APP/deploy/update.sh" /usr/local/sbin/rhl-update
/usr/local/sbin/rhl-update || echo "ВНИМАНИЕ: не все службы поднялись — журнал выше. Продолжаю."
systemctl --no-pager --lines=5 status bot || true

step "Ключ для выкладки из GitHub Actions"
# Ключ умеет ровно одно — запустить rhl-update. Ни шелла, ни проброса портов.
install -d -m 700 /root/.ssh
[ -f "$DEPLOY_KEY" ] || ssh-keygen -q -t ed25519 -N '' -C rhl-actions -f "$DEPLOY_KEY"
touch /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys
if ! grep -q 'rhl-actions$' /root/.ssh/authorized_keys; then
  echo "command=\"/usr/local/sbin/rhl-update\",restrict $(cat "$DEPLOY_KEY.pub")" >> /root/.ssh/authorized_keys
fi
ip=$(curl -fsS -4 --max-time 5 https://api.ipify.org || hostname -I | awk '{print $1}')

cat <<EOF

Готово. Бот работает как служба: journalctl -u bot -f
API зачёта и прогнозов слушает только 127.0.0.1:8080: journalctl -u api -f

Чтобы GitHub сам выкладывал код после слияния в main, заведи в репозитории
Settings → Secrets and variables → Actions → New repository secret три секрета:

DEPLOY_HOST
$ip

DEPLOY_KNOWN_HOSTS
$(for k in /etc/ssh/ssh_host_*_key.pub; do echo "$ip $(cut -d' ' -f1,2 "$k")"; done)

DEPLOY_SSH_KEY  (весь блок, вместе со строками BEGIN и END)
$(cat "$DEPLOY_KEY")

После этого приватный ключ на сервере больше не нужен: rm $DEPLOY_KEY

Сервер в России — сначала туннель до Telegram: bash $APP/deploy/tunnel.sh user@ЗАРУБЕЖНЫЙ-IP
Потом HTTPS для зачёта «Раската» и прогнозов, один раз: bash $APP/deploy/https.sh
EOF
