#!/usr/bin/env bash
# HTTPS для API (ADR-019, раздел 3): Caddy с бесплатным сертификатом перед server.py.
# Запускать от root на сервере один раз, после setup.sh (и tunnel.sh, если сервер в России):
#
#   bash /opt/rhl/deploy/https.sh                  # адрес <ip-через-дефисы>.sslip.io
#   bash /opt/rhl/deploy/https.sh api.example.ru   # свой домен: A-запись уже указывает на сервер
#
# Наружу смотрит только Caddy: /api/* уходит в 127.0.0.1:8080, всё остальное — 404. Повторный
# запуск безопасен: Caddyfile пишется заново, правила ufw и строки в bot.env не дублируются.
# Порядок и пояснения — BOT_README.md.
set -euo pipefail

APP=/opt/rhl
ENV_FILE=/etc/rhl/bot.env
CADDYFILE=/etc/caddy/Caddyfile

[ "$(id -u)" = 0 ] || { echo "Запусти от root"; exit 1; }
[ -f "$ENV_FILE" ] || { echo "Нет $ENV_FILE — сначала deploy/setup.sh"; exit 1; }
step() { printf '\n== %s\n' "$*"; }

ip=$(curl -fsS -4 --max-time 5 https://api.ipify.org || hostname -I | awk '{print $1}')
HOST=${1:-${ip//./-}.sslip.io}
HOST=${HOST,,}
[[ "$HOST" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ && "$HOST" == *.* ]] || { echo "Это не похоже на адрес: $HOST"; exit 1; }
port=$(sed -n 's/^API_PORT=//p' "$ENV_FILE" | tail -1)
port=${port:-8080}
echo "Адрес API: https://$HOST/api (сервер $ip)"

step "Адрес смотрит на этот сервер"
seen=$(getent ahostsv4 "$HOST" | awk 'NR == 1 {print $1}' || true)
if [ "$seen" != "$ip" ]; then
  echo "ВНИМАНИЕ: $HOST указывает на «${seen:-никуда}», а сервер — $ip."
  echo "Сертификат не выпустится, пока A-запись домена не укажет на $ip. Продолжаю."
else
  echo "$HOST → $ip"
fi

step "Службы bot, live и api"
# Свежая выкладка ставит и новые службы; старая rhl-update о них не знала
install -m 755 "$APP/deploy/update.sh" /usr/local/sbin/rhl-update.new
mv -f /usr/local/sbin/rhl-update.new /usr/local/sbin/rhl-update
/usr/local/sbin/rhl-update || echo "ВНИМАНИЕ: не все службы поднялись — журнал выше. Продолжаю с API."
ok=0
for _ in $(seq 1 30); do
  if curl -fsS -m 3 -o /dev/null "http://127.0.0.1:$port/api/health"; then ok=1; break; fi
  sleep 1
done
if [ "$ok" != 1 ]; then
  journalctl -u api -n 30 --no-pager
  echo "API не отвечает на 127.0.0.1:$port — смотри журнал выше"
  exit 1
fi
echo "API отвечает на 127.0.0.1:$port"

step "Файрвол: 80 и 443 для Caddy"
# 80 нужен для выпуска сертификата и перехода на https, 443 — сам API. Открываем до Caddy:
# он просит сертификат сразу при старте
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw status | head -8

step "Caddy"
if ! command -v caddy >/dev/null; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -q
  apt-get install -y -q caddy
fi
caddy version
tmp=$(mktemp)
cat > "$tmp" <<EOF
# Пишет deploy/https.sh: правки руками перезапишет следующий запуск.
# Наружу — только API: /api/* уходит в server.py на 127.0.0.1:$port, всё остальное — 404.
$HOST {
	encode gzip
	handle /api/* {
		reverse_proxy 127.0.0.1:$port
	}
	handle {
		respond 404
	}
}
EOF
caddy validate --adapter caddyfile --config "$tmp" >/dev/null
if ! cmp -s "$tmp" "$CADDYFILE"; then
  install -m 644 "$tmp" "$CADDYFILE"
  echo "Caddyfile записан: $CADDYFILE"
fi
rm -f "$tmp"
systemctl enable -q caddy
systemctl reload-or-restart caddy

step "Сертификат и https://$HOST/api/health"
# Проверяем изнутри, но с настоящим сертификатом: --resolve ведёт на этот же сервер без DNS
ok=0
for i in $(seq 1 36); do
  if health=$(curl -fsS -m 10 --resolve "$HOST:443:127.0.0.1" "https://$HOST/api/health" 2>/dev/null); then
    ok=1
    break
  fi
  [ "$i" = 1 ] && echo "Жду сертификат, до трёх минут…"
  sleep 5
done
if [ "$ok" != 1 ]; then
  journalctl -u caddy -n 40 --no-pager
  echo "HTTPS не заработал. Чаще всего — закрыт 80/443 у хостинга или домен смотрит не сюда."
  exit 1
fi
echo "$health"
salt=$(printf '%s' "$health" | python3 -c 'import json, sys; r = json.load(sys.stdin)["raskat"]; print(("" if r["on"] else "ЗАЧЁТ ВЫКЛЮЧЕН: ") + r["note"])' || true)
echo "Сверка соли: $salt"

step "Бот узнаёт адрес зачёта"
api="https://$HOST/api/raskat"
if grep -qx "RASKAT_API=$api" "$ENV_FILE"; then
  echo "Уже стоит: RASKAT_API=$api"
else
  if grep -q '^RASKAT_API=' "$ENV_FILE"; then
    sed -i "s#^RASKAT_API=.*#RASKAT_API=$api#" "$ENV_FILE"
  else
    printf '\n# Зачёт «Раската»: дописал deploy/https.sh\nRASKAT_API=%s\n' "$api" >> "$ENV_FILE"
  fi
  echo "Записано в $ENV_FILE: RASKAT_API=$api"
  systemctl restart bot
  sleep 3
  systemctl is-active -q bot && echo "Бот перезапущен"
fi

cat <<EOF

Готово. Проверь с телефона: https://$HOST/api/health

Заведи в репозитории Settings → Secrets and variables → Actions → Variables → New repository
variable две переменные:

LIVE_API
https://$HOST/api

RASKAT_API
https://$HOST/api/raskat

Потом Actions → «Мини-апп» → Run workflow: мини-апп подхватит адреса, и зачёт откроется.
Бот позовёт лист ожидания зачёта в ближайшее время напоминаний (10:00 или 19:00 МСК) — заведи
переменные до него.

Соль раскладов: секрет RASKAT_SALT задания Pages и RASKAT_SALT в $ENV_FILE должны совпадать.
Не совпали — зачёт отвечает «временно выключен», причина — в https://$HOST/api/health.
EOF
