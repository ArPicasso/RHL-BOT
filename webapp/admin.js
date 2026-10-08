// Пульт админа (ADR-021): здоровье системы, аудитория, рассылки и игры одним экраном — вкладка «Состояние».
// Данные — GET ${LIVE_API}/admin/status с подписью Telegram, пускает только ADMIN_IDS на сервере.
// Вкладка «Голы» (ADR-036, раздел 4): где каждый гол на пути к клипу — GET ${LIVE_API}/admin/goals, пускает ADMIN_IDS
// и помощников PREVIEW_IDS; помощник видит только её. ?tab=goals — открыть сразу на ней (так её открывает бот).
// Гол открывает лист с видео и отметками — строки журнала marks.py через ${LIVE_API}/admin/goal/mark (ADR-036, раздел 5).
// Для разработки: ?admin_mock=1 — выдуманный ответ data/admin/mock/status.json и goals.json, =bad — с проблемами,
// =helper — как у помощника.
// Всё, что пришло с сервера, — только через esc(): там названия источников, ошибки и имена команд.
"use strict";

const TZ = "Europe/Moscow";
const REFRESH_MS = 60e3;
const tg = (window.Telegram && window.Telegram.WebApp) || null;
const inTelegram = !!(tg && tg.initData);
const $ = (s) => document.querySelector(s);
const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const mock = (() => { try { return new URLSearchParams(location.search).get("admin_mock") || ""; } catch (e) { return ""; } })();

function apiBase() {
  const a = window.LIVE_API;
  if (typeof a !== "string") return "";
  return /^(https:\/\/[^\s"'<>]+|http:\/\/(localhost|127\.0\.0\.1)(:\d+)?(\/[^\s"'<>]*)?)$/.test(a) ? a.replace(/\/+$/, "") : "";
}

// ---------- время и числа ----------

const toDate = (v) => { const d = v ? new Date(v) : null; return d && !isNaN(d) ? d : null; };
const hm = (d) => d.toLocaleTimeString("ru-RU", { timeZone: TZ, hour: "2-digit", minute: "2-digit" });
function ago(v, now) {
  const d = toDate(v);
  if (!d) return "нет данных";
  const m = Math.max(0, Math.round((now - d) / 60e3));
  if (m < 1) return "только что";
  if (m < 60) return `${m} мин назад`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} ч ${m % 60 ? `${m % 60} мин ` : ""}назад`;
  return `${Math.floor(h / 24)} дн назад`;
}
const minsAgo = (v, now) => { const d = toDate(v); return d ? (now - d) / 60e3 : Infinity; };
const times = (n) => (n % 10 >= 2 && n % 10 <= 4 && !(n % 100 >= 12 && n % 100 <= 14) ? "раза" : "раз");
const num = (n) => (typeof n === "number" ? n.toLocaleString("ru-RU") : "—");
function bytes(n) {
  if (typeof n !== "number") return "—";
  if (n >= 1 << 30) return `${(n / (1 << 30)).toFixed(1).replace(".", ",")} ГБ`;
  if (n >= 1 << 20) return `${Math.round(n / (1 << 20))} МБ`;
  return `${Math.max(1, Math.round(n / 1024))} КБ`;
}
const DOW = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];
function dayLabel(iso, i) {
  if (i === 0) return "сегодня";
  if (i === 1) return "вчера";
  const [y, m, d] = iso.split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d));
  return `${DOW[dt.getUTCDay()]} ${String(d).padStart(2, "0")}.${String(m).padStart(2, "0")}`;
}
const v = (row, key) => (row && typeof row[key] === "number" ? row[key] : 0);

// ---------- части экрана ----------

const row = (level, title, sub, aside) => `<div class="row"><span class="dot ${level}"></span>
  <span><b>${esc(title)}</b>${sub ? `<small>${sub}</small>` : ""}</span><span class="aside">${aside || ""}</span></div>`;

function tile(n, cap, diff) {
  const d = typeof diff === "number" && diff !== 0 ? `<span class="diff">${diff > 0 ? "+" : "−"}${num(Math.abs(diff))}</span>` : "";
  return `<div class="tile"><div class="num">${num(n)}${d}</div><div class="cap">${esc(cap)}</div></div>`;
}

function bars(rows, limit, id) {
  if (!rows || !rows.length) return `<div class="card-title">Пока пусто</div>`;
  const all = state.open[id];
  const shown = all ? rows : rows.slice(0, limit);
  let html = `<div class="bars">${shown.map((r) => `<div class="bar-row"><span class="name">${esc(r.name)}</span><span class="n">${num(r.n)}</span></div>`).join("")}</div>`;
  if (rows.length > limit) html += `<button type="button" class="more" data-more="${esc(id)}">${all ? "Свернуть" : `Все ${rows.length}`}</button>`;
  return html;
}

function table(days, cols) {
  const head = `<tr><th>День</th>${cols.map((c) => `<th>${esc(c[1])}</th>`).join("")}</tr>`;
  const body = days.map((d, i) => `<tr class="${i === 0 ? "today" : ""}"><td>${esc(dayLabel(d.date, i))}</td>${cols.map((c) => `<td>${num(typeof c[0] === "function" ? c[0](d) : v(d, c[0]))}</td>`).join("")}</tr>`).join("");
  return `<div class="table-wrap"><table>${head}${body}</table></div>`;
}

const STATE_WORDS = { active: "работает", activating: "запускается", deactivating: "останавливается", inactive: "остановлена", failed: "упала", reloading: "перечитывает настройки", "not-found": "не установлена", unknown: "нет данных" };
const RUN_WORDS = { success: "успешно", failure: "упало", cancelled: "отменено", skipped: "пропущено", timed_out: "по таймауту", startup_failure: "не запустилось", in_progress: "идёт", queued: "в очереди" };
const KIND_WORDS = { remind_today: "Напоминание утром", remind_tomorrow: "Напоминание накануне", final: "Финал", raskat: "Зачёт «Раската» открыт" };
const LINK_WORDS = { plain: "Просто «Старт»", team: "Ссылка с командой", remind: "«Напомнить» из мини-аппа", today: "«Матчи сегодня»", leaders: "Лидеры", raskat: "«Раскат»", other: "Другие" };
const PLATFORM_WORDS = { ios: "iPhone", android: "Android", android_x: "Android (Telegram X)", tdesktop: "Telegram Desktop", macos: "Telegram для Mac", weba: "Веб (A)", webk: "Веб (K)", web: "Веб", unknown: "Неизвестно" };

function summary(st) {
  const p = st.problems || [];
  if (!p.length) return `<section class="card summary"><span class="tag ok">Всё работает</span></section>`;
  const bad = p.filter((x) => x.level === "bad").length;
  const head = bad ? `<span class="tag bad">Проблем: ${bad}</span>` : `<span class="tag ok">Работает</span>`;
  const warns = p.length - bad;
  return `<section class="card summary">${head}${warns ? ` <span class="card-title">и ${warns} на посмотреть</span>` : ""}
    <ul>${p.map((x) => `<li><span class="dot ${x.level === "bad" ? "bad" : "warn"}"></span><span>${esc(x.text)}</span></li>`).join("")}</ul></section>`;
}

function system(st, now) {
  const s = st.system || {};
  let html = `<div class="label">Система</div><section class="card">`;
  if (s.services) {
    for (const x of s.services) {
      const level = x.state === "active" ? (x.restarts ? "warn" : "ok") : x.state === "not-found" || x.state === "unknown" ? "" : "bad";
      const sub = [STATE_WORDS[x.state] || x.state, x.since && x.state === "active" ? `запущена ${ago(x.since, now)}` : "",
        x.restarts ? `падала ${x.restarts} ${times(x.restarts)}` : ""].filter(Boolean).map(esc).join(" · ");
      html += row(level, x.title, sub, "");
    }
  } else {
    html += row("", "Службы", esc(s.services_note || "systemctl не ответил"), "");
  }
  const b = s.bot;
  if (b) {
    const tgBad = toDate(b.tg_fail) && (!toDate(b.tg_ok) || toDate(b.tg_fail) > toDate(b.tg_ok));
    html += row(tgBad ? "bad" : b.tg_ok ? "ok" : "", "Telegram через туннель", tgBad ? esc(b.tg_error || "ошибка") : "бот достучался до Bot API", esc(ago(tgBad ? b.tg_fail : b.tg_ok, now)));
    html += row(minsAgo(b.beat, now) > 3 ? "bad" : "ok", "Пульс бота", b.started ? esc(`запущен ${ago(b.started, now)}`) : "", esc(ago(b.beat, now)));
  } else {
    html += row("bad", "Пульс бота", "status/bot.json нет: бот не запущен или старая версия", "");
  }
  html += `</section><section class="card"><div class="card-title">Сборки GitHub${s.builds_at ? ` · проверено ${esc(ago(s.builds_at, now))}` : ""}</div>`;
  if (s.builds && s.builds.length) {
    for (const r of s.builds) {
      const failed = ["failure", "timed_out", "startup_failure"].includes(r.conclusion);
      const word = RUN_WORDS[r.conclusion] || RUN_WORDS[r.status] || r.conclusion || r.status || "нет запусков";
      const sub = `${esc(word)}${r.last_ok ? ` · удачно ${esc(ago(r.last_ok, now))}` : ""} · за сутки ${num(r.runs_24h)}${r.fails_24h ? `, красных ${num(r.fails_24h)}` : ""}`;
      const title = r.url && /^https:\/\/github\.com\//.test(r.url) ? `<a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.title)}</a>` : esc(r.title);
      html += `<div class="row"><span class="dot ${failed ? "bad" : r.conclusion === "success" ? "ok" : ""}"></span><span><b>${title}</b><small>${sub}</small></span><span class="aside">${esc(ago(r.at, now))}</span></div>`;
    }
  } else {
    html += row("", "Нет данных", s.kick && !s.kick.token ? "у службы pages нет PAGES_TOKEN" : "служба pages ещё не прочитала Actions", "");
  }
  if (s.kick && s.kick.token) html += row(minsAgo(s.kick.ok, now) > 30 ? "warn" : "ok", "Пинок сборки с сервера", s.kick.fail && (!s.kick.ok || toDate(s.kick.fail) > toDate(s.kick.ok)) ? "последний пинок не прошёл" : "раз в 15 минут, ночью спит", esc(ago(s.kick.ok, now)));
  html += `</section><section class="card"><div class="card-title">Данные</div>`;
  html += row(minsAgo(s.league_updated, now) > 120 ? "bad" : "ok", "Мини-апп (league.json)", "календарь, результаты, таблица", esc(ago(s.league_updated, now)));
  const live = s.live || {};
  html += row(live.updated ? (minsAgo(live.updated, now) > 20 ? "warn" : "ok") : "", "Живое (live/today.json)", "статусы и счёт по ходу", esc(ago(live.updated, now)));
  for (const src of live.sources || []) {
    const level = src.errors >= 3 ? "bad" : src.errors ? "warn" : src.ok ? "ok" : "";
    const sub = [src.errors ? `ошибок подряд: ${src.errors}` : `матчей: ${src.games || 0}`, src.note || ""].filter(Boolean).map(esc).join(" · ");
    html += row(level, src.name, sub, esc(ago(src.ok, now)));
  }
  const r = s.raskat || {};
  html += row(r.on === false ? "bad" : "ok", "Зачёт «Раската»", esc(r.note || ""), r.on === false ? "выключен" : "включён");
  const c = s.clips;
  if (c) {
    const vkBad = v(c, "vk_fail") >= 3 && !v(c, "vk_ok");
    html += row(minsAgo(c.beat, now) > 60 ? "bad" : "ok", "Пульс службы клипов", c.started ? esc(`запущена ${ago(c.started, now)}`) : "", esc(ago(c.beat, now)));
    html += row(vkBad ? "bad" : v(c, "vk_fail") ? "warn" : v(c, "vk_ok") ? "ok" : "", "Записи трансляций из VK",
      esc([`сегодня отдал ${v(c, "vk_ok")}, отказал ${v(c, "vk_fail")}`, v(c, "vk_fail") && c.vk_error ? c.vk_error : ""].filter(Boolean).join(" · ")),
      c.vk_last_ok ? esc(ago(c.vk_last_ok, now)) : "");
  }
  const k = s.cuts;
  if (k) {
    const q = k.queue || {};
    const failing = v(k, "fail") >= 3 && k.fail_at && (!k.ok_at || toDate(k.fail_at) > toDate(k.ok_at));
    html += row(minsAgo(k.beat, now) > 20 ? "bad" : "ok", "Пульс службы нарезки", k.started ? esc(`запущена ${ago(k.started, now)}`) : "", esc(ago(k.beat, now)));
    html += row(failing ? "bad" : v(k, "fail") ? "warn" : v(k, "done") ? "ok" : "", "Видео для админов",
      esc([`сегодня вырезано ${v(k, "done")}, не вышло ${v(k, "fail")}`, `в очереди ${v(q, "queued")}`, v(k, "fail") && k.error ? k.error : ""].filter(Boolean).join(" · ")),
      k.ok_at ? esc(ago(k.ok_at, now)) : "");
  }
  const d = s.disk;
  if (d) html += row(d.free < 1 << 30 ? "bad" : d.free < 3 * (1 << 30) ? "warn" : "ok", "Диск", esc(`свободно ${bytes(d.free)} из ${bytes(d.total)} · state.db ${bytes(d.db)}`), "");
  return html + `</section>`;
}

function share(part, whole) {
  if (!whole) return "—";
  return `${num(part)} из ${num(whole)} · ${Math.round((100 * part) / whole)}%`;
}

function retention(r) {
  if (!r) return "";
  const d1 = r.d1 || {}, week = r.week || {};
  return `<section class="card"><div class="card-title">Возвращаются ли</div>
    ${row("ok", "Сегодня", "впервые и те, кто уже был", `${num(r.new)} новых · ${num(r.back)} вернулись`)}
    ${row(d1.of && !d1.back ? "warn" : "ok", "На следующий день",
          "из пришедших вчера впервые", share(v(d1, "back"), v(d1, "of")))}
    ${row(week.of && !week.back ? "warn" : "ok", `Через ${num(week.days || 7)} дней`,
          "из пришедших неделю назад впервые заходили ещё хоть раз", share(v(week, "back"), v(week, "of")))}
    ${row(r.sleeping ? "warn" : "ok", "Уснули", "были от трёх дней до пяти недель назад", num(r.sleeping))}
    ${row("ok", "Знаем всего", "людей, открывавших мини-апп", num(r.known))}</section>`;
}

function audience(st) {
  const a = st.audience || {};
  const days = st.days || [];
  const [t, y] = [days[0] || {}, days[1] || {}];
  let html = `<div class="label">Аудитория</div><div class="tiles">
    ${tile(v(t, "app_users"), "открыли мини-апп сегодня", v(t, "app_users") - v(y, "app_users"))}
    ${tile(a.subscribers, "подписаны на напоминания", typeof t.subs === "number" && typeof y.subs === "number" ? t.subs - y.subs : null)}
    ${tile(v(t, "starts"), "нажали «Старт» в боте", v(t, "starts") - v(y, "starts"))}
    ${tile(v(t, "sub_new") - v(t, "sub_off"), `подписок за день: +${v(t, "sub_new")} / −${v(t, "sub_off")}${v(t, "blocked") ? `, заблокировали ${v(t, "blocked")}` : ""}`)}
  </div>`;
  html += retention(a.retention);
  html += `<section class="card" style="margin-top:12px">${table(days, [["app_users", "Открыли"], ["starts", "Старт"], ["sub_new", "+подп"], ["sub_off", "−подп"], ["blocked", "Блок"]])}</section>`;
  html += `<section class="card"><div class="card-title">Подписчики по командам</div>${bars(a.by_team, 6, "teams")}</section>`;
  html += `<section class="card"><div class="card-title">За кого болеют те, кто открыл сегодня</div>${bars(a.fans, 6, "fans")}</section>`;
  html += `<section class="card"><div class="card-title">Платформы сегодня</div>${bars((a.platforms || []).map((x) => ({ ...x, name: PLATFORM_WORDS[x.id] || x.id })), 6, "platforms")}</section>`;
  html += `<section class="card"><div class="card-title">Откуда «Старт» за неделю</div>${bars((a.links_week || []).map((x) => ({ ...x, name: LINK_WORDS[x.id] || x.id })), 7, "links")}</section>`;
  return html;
}

function sends(st, now) {
  const s = st.sends || {};
  const days = st.days || [];
  const t = days[0] || {};
  let html = `<div class="label">Рассылки</div><div class="tiles">
    ${tile(v(t, "remind_sent"), `напоминаний ушло сегодня${v(t, "remind_fail") ? `, не ушло ${v(t, "remind_fail")}` : ""}`)}
    ${tile(v(t, "final_sent"), `финалов ушло сегодня${v(t, "final_fail") ? `, не ушло ${v(t, "final_fail")}` : ""}`)}
    ${tile(v(t, "replays_todo"), "матчей без превью и секунд (/replay)")}
    ${tile(v(t, "replay_nag"), "напоминаний о превью админам сегодня")}
  </div>`;
  html += clipTiles(s.clips, s.my_players, t);
  html += `<section class="card" style="margin-top:12px">${table(days, [["remind_sent", "Напом."], ["final_sent", "Финалы"], [(d) => v(d, "remind_fail") + v(d, "final_fail"), "Не ушло"], ["errors", "Ошибки"]])}</section>`;
  html += `<section class="card"><div class="card-title">Последние рассылки</div>`;
  if (s.log && s.log.length) {
    for (const x of s.log) {
      const title = KIND_WORDS[x.kind] || x.kind || "Рассылка";
      const sub = [x.match, x.day ? `матчи ${x.day.split("-").reverse().slice(0, 2).join(".")}` : "", `ушло ${num(x.sent)}`, x.failed ? `не ушло ${num(x.failed)}` : "", typeof x.seconds === "number" ? `за ${x.seconds} с` : ""].filter(Boolean).map(esc).join(" · ");
      html += row(x.failed ? "warn" : "ok", title, sub, esc(ago(x.at, now)));
    }
  } else {
    html += row("", "Пока не было", "с запуска бота", "");
  }
  if (s.last_error) html += row("warn", "Последняя ошибка бота", esc([s.last_error.what, s.last_error.exc].filter(Boolean).join(" · ")), esc(ago(s.last_error.at, now)));
  return html + `</section>`;
}

// Каталог голов сезона (ADR-030, раздел 7): снимок службы clips на её последний проход
function clipTiles(c, myPlayers, t) {
  if (!c && typeof myPlayers !== "number") return "";
  const timed = c ? `с точной секундой: сами ${num(v(c, "timed_auto"))}, админ ${num(v(c, "timed_admin"))}` : "";
  let html = `<div class="card-title" style="margin:14px 2px 8px">Голы и клипы сезона</div><div class="tiles">`;
  if (c) {
    // покрытие повторами (ADR-031): матчи, где повтор есть у каждого гола, и почему не у всех — в вечернем напоминании
    if (v(c, "m_total")) html += tile(v(c, "m_full"), `матчей с повтором у всех голов из ${num(v(c, "m_total"))}, без единого — ${num(v(c, "m_none"))}`)
      + tile(v(c, "g_replay"), `голов с повтором${v(c, "run") ? `, по ходу часов — ${num(v(c, "run"))}` : ""}`);
    html += tile(v(c, "goals"), "голов в матчах с записью")
      + tile(v(c, "timed"), timed)
      + tile(v(c, "clips"), `клипов в хранилище, к клипу готовы ${num(v(c, "two"))} — два свидетеля`)
      + tile(v(c, "ask"), "голов ждут ответа на превью")
      + tile(v(c, "no_video"), "матчей без записи лиги и клуба")
      // табло клуба-хозяина не размечено: голов этих матчей служба не видит, кадры для разметки — у админов в боте
      + (v(c, "no_board") ? tile(v(c, "no_board"), `матчей ждут разметки табло, клубов — ${num(v(c, "boards"))}`) : "")
      + (v(c, "mismatch") ? tile(v(c, "mismatch"), "голов табло нет в протоколе") : "");
  }
  if (typeof myPlayers === "number") html += tile(myPlayers, `отметили «Моего игрока»${v(t, "my_goal_sent") ? `, голов ушло сегодня ${v(t, "my_goal_sent")}` : ""}`);
  return html + `</div>`;
}

function games(st) {
  const g = st.games || {};
  const days = st.days || [];
  const t = days[0] || {};
  const y = days[1] || {};
  let html = `<div class="label">Игры</div><div class="tiles">
    ${tile(v(t, "raskat"), "раскатов собрали в зачёт сегодня", v(t, "raskat") - v(y, "raskat"))}
    ${tile(g.raskat_players, "человек играли в зачёт за сезон")}
    ${tile(v(t, "votes"), "голосов «Кто победит?» сегодня", v(t, "votes") - v(y, "votes"))}
    ${tile(v(t, "voters"), "человек голосовали сегодня")}
  </div>`;
  html += `<section class="card" style="margin-top:12px">${table(days, [["raskat", "Раскаты"], ["votes", "Голоса"], ["voters", "Голосовали"]])}</section>`;
  const top = g.predict_top || [];
  html += `<section class="card"><div class="card-title">Матчи дня по голосам</div>${top.length ? `<div class="bars">${top.map((m) => `<div class="bar-row"><span class="name">${esc(m.title)} <span class="card-title">${num(m.home)} : ${num(m.away)}</span></span><span class="n">${num(m.votes)}</span></div>`).join("")}</div>` : `<div class="card-title">Сегодня ещё не голосовали</div>`}</section>`;
  return html;
}

// ---------- вкладка «Голы» (ADR-036, раздел 4) ----------
// Слова и цвета статусов — DESIGN.md → «Пульт → вкладки». Статус гола считает служба clips, здесь только показ.

const GOAL_WORDS = { clip: "клип", ready: "к клипу", done: "точно", confirm: "подтвердить", dispute: "спор", approx: "примерно", search: "поиск", absent: "нет в записи", stuck: "стоит" };
const GOAL_HINTS = {
  clip: "клип у болельщиков",
  ready: "два свидетеля — клип будет",
  done: "секунда точная, клипа нет: запись клуба, игрок скрыт или протокола ещё нет",
  confirm: "точная секунда одного свидетеля — нужно «✅ Гол виден»",
  dispute: "отметка человека и табло не сошлись",
  approx: "известно окно в пару минут — найти секунду",
  search: "где гол, не знает никто — искать в записи",
  absent: "человек сказал: в записи гола нет",
  stuck: "матч застрял раньше голов",
};
// почему матч не дошёл до повтора у каждого гола — как в /replay и вечерней сводке бота (COVER_WHY).
// Что сделать — только админу: /replay помощнику бот не открывает
const WHY_WORDS = {
  ok: "повтор у каждого гола",
  no_video: "нет записи ни лиги, ни клуба",
  gone: "запись удалили из VK",
  pending: "ждёт разбора службой",
  error: "VK не отдал запись",
  no_board: "табло клуба не размечено — служба голы не ищет",
  not_found: "табло нашло не все голы",
};
const WHY_TODO = { no_video: "пришли ссылку в /replay", gone: "нужна новая ссылка в /replay" };
const WHY_LEVEL = { ok: "ok", pending: "", no_video: "bad", gone: "bad", error: "bad", no_board: "warn", not_found: "warn" };
const CUT_KINDS = { preview: "превью", review: "30 с гола", search: "окно поиска", clip: "клип" };
const PIPE_STATES = ["clip", "ready", "done", "confirm", "dispute", "approx", "search", "absent", "stuck"];

const sticker = (st) => `<span class="st st-${esc(st)}">${esc(GOAL_WORDS[st] || st)}</span>`;
function dm(iso) {
  const [, m, d] = String(iso || "").split("-");
  return d && m ? `${d}.${m}` : "";
}
function goalLine(x) {
  const per = x.period ? (/^\d+$/.test(String(x.period)) ? `${x.period}-й` : String(x.period)) : "";
  const who = x.author ? `${x.author}${x.assists && x.assists.length ? ` (${x.assists.join(", ")})` : ""}` : "";
  return [`гол ${x.score}`, [per, x.time].filter(Boolean).join(", "), who].filter(Boolean).map(esc).join(" · ");
}

function pipeline(gd) {
  const p = gd.pipeline || {};
  const s = p.states || {};
  let html = `<div class="label">Конвейер сезона</div><div class="tiles">
    ${tile(p.played, "матчей сыграно")}
    ${tile(p.video, "из них с записью")}
    ${tile(p.parsed, "разобраны службой")}
    ${tile(p.full, "с повтором у каждого гола")}
    ${tile(p.goals, "голов в этих матчах")}
    ${tile(p.replays, "голов с повтором")}
  </div><section class="card" style="margin-top:12px"><div class="card-title">Голы по статусам</div>`;
  for (const st of PIPE_STATES) {
    if (!v(s, st) && !["clip", "ready", "confirm"].includes(st)) continue;
    html += `<div class="row st-row">${sticker(st)}<span><small>${esc(GOAL_HINTS[st])}</small></span><span class="aside"><b>${num(v(s, st))}</b></span></div>`;
  }
  for (const b of p.boards || []) html += row("warn", `Табло не размечено: ${b.name}`, esc(`домашних матчей без секунд и клипов: ${b.matches}`), "");
  return html + `</section>`;
}

function work(gd, now) {
  const w = gd.work || {};
  const c = w.clips, k = w.cuts;
  let html = `<div class="label">Сейчас</div><section class="card">`;
  if (c) {
    const sub = c.scan ? `разбирает запись: ${c.scan.title} ${dm(String(c.scan.key || "").slice(0, 10))}${c.scan.at ? ` · с ${hm(toDate(c.scan.at))}` : ""}` : "новых записей не разбирает";
    const wait = typeof c.waiting === "number" ? ` · ждут разбора ${num(c.waiting)}` : "";
    html += row(minsAgo(c.beat, now) > 60 ? "bad" : "ok", "Служба клипов", esc(sub + wait), esc(ago(c.beat, now)));
    const vkBad = toDate(c.vk_fail) && (!toDate(c.vk_ok) || toDate(c.vk_fail) > toDate(c.vk_ok));
    const vkSub = vkBad ? c.vk_error || "последний раз отказал" : c.vk_ok ? "последняя запись скачалась" : "с запуска службы записей не качали";
    html += row(vkBad ? "warn" : c.vk_ok ? "ok" : "", "VK отдаёт записи", esc(vkSub), c.vk_ok || c.vk_fail ? esc(ago(vkBad ? c.vk_fail : c.vk_ok, now)) : "");
    const cutOff = c.cut === "off" || c.bucket === false;
    html += row(cutOff ? "warn" : "ok", "Клипы болельщикам", esc(c.cut === "off" ? "нарезка на паузе: CLIPS_CUT=off в bot.env"
      : c.bucket === false ? "не режутся: нет ключей хранилища CLIPS_S3_* в bot.env" : "режутся у голов с двумя свидетелями"), "");
  } else {
    html += row("bad", "Служба клипов", "status/clips.json нет: служба не запущена", "");
  }
  if (k) {
    const q = k.queue || {};
    const job = k.job ? `режет ${CUT_KINDS[k.job.kind] || k.job.kind}${k.job.len ? `, ${k.job.len} с` : ""}` : "ничего не режет";
    const queue = `в очереди ${num(v(q, "queued"))}${v(q, "urgent") ? `, ждут люди — ${num(v(q, "urgent"))}` : ""}`;
    html += row(minsAgo(k.beat, now) > 20 ? "bad" : "ok", "Видео для людей", esc(`${job} · ${queue}`), esc(ago(k.beat, now)));
  }
  if (gd.updated) html += row("", "Статусы голов", "служба пересчитывает их каждым проходом, раз в 10 минут", esc(ago(gd.updated, now)));
  return html + `</section>`;
}

function waiting(gd) {
  const w = gd.wait || {};
  const items = w.items || [];
  let html = `<div class="label">Ждут вас · ${num(w.total || 0)}</div><section class="card">`;
  if (!items.length) return html + row("ok", "Никто не ждёт", "у каждого гола либо секунда, либо матч стоит до голов — смотри «Матчи»", "") + `</section>`;
  const all = state.open.wait;
  for (const x of all ? items : items.slice(0, 8)) {
    html += `<div class="row st-row goal-open" role="button" tabindex="0" data-key="${esc(x.key)}" data-score="${esc(x.score)}">${sticker(x.state)}<span><b>${esc(x.title)}, ${esc(dm(x.date))}</b><small>${goalLine(x)}</small></span><span class="aside">▶</span></div>`;
  }
  if (items.length > 8) html += `<button type="button" class="more" data-more="wait">${all ? "Свернуть" : `Все ${items.length}`}</button>`;
  if (w.total > items.length) html += `<div class="card-title">Показаны первые ${num(items.length)} из ${num(w.total)}</div>`;
  html += `<div class="card-title">Нажми гол — видео этого места записи.</div>`;
  return html + `</section>`;
}

function matchCard(m) {
  const id = `m:${m.key}`;
  const open = !!state.open[id];
  const counts = PIPE_STATES.filter((st) => m.states && m.states[st]).map((st) => `${GOAL_WORDS[st]} ${m.states[st]}`).join(" · ");
  const todo = state.role === "admin" && WHY_TODO[m.why] ? ` — ${WHY_TODO[m.why]}` : "";
  const why = (WHY_WORDS[m.why] || m.why || "") + todo;
  const sub = [why + (m.why === "error" && m.error ? `: ${m.error}` : ""), counts].filter(Boolean).map(esc).join("<br>");
  const title = m.score ? `${dm(m.date)} ${m.title.replace(" — ", ` ${m.score} `)}` : `${dm(m.date)} ${m.title}`;
  let html = `<div class="row match" role="button" tabindex="0" data-open="${esc(id)}" aria-expanded="${open}">
    <span class="dot ${WHY_LEVEL[m.why] || ""}"></span><span><b>${esc(title)}</b><small>${sub}</small></span>
    <span class="aside">${num(m.replays)} / ${num(m.goals)}<span class="chev">${open ? "▴" : "▾"}</span></span></div>`;
  if (open) {
    html += `<div class="goals">${(m.list || []).length ? m.list.map((x) => `<div class="goal${x.state === "stuck" || x.state === "absent" ? "" : " goal-open"}"${x.state === "stuck" || x.state === "absent" ? "" : ` role="button" tabindex="0" data-key="${esc(m.key)}" data-score="${esc(x.score)}"`}>${sticker(x.state)}<span>${goalLine(x)}${x.why ? `<small>табло: ${esc(x.why)}</small>` : ""}</span></div>`).join("") : `<div class="card-title">Голов протокола ещё нет</div>`}</div>`;
  }
  return html;
}

function matchList(gd) {
  const ms = gd.matches || [];
  let html = `<div class="label">Матчи</div><section class="card">`;
  if (!ms.length) return html + row("", "Пока пусто", "служба clips ещё не писала разбор покрытия", "") + `</section>`;
  html += `<div class="card-title">Справа — голов с повтором из всех. Нажми матч — его голы</div>`;
  return html + ms.map(matchCard).join("") + `</section>`;
}

function goalsTab(gd, now) {
  return pipeline(gd) + work(gd, now) + waiting(gd) + matchList(gd) +
    `<div class="foot">«Голы» показывают и видео, а отметить гол — в боте, ${state.role === "helper" ? "кнопкой под превью" : "/replay"}. Обновляется раз в минуту, пока открыто.</div>`;
}

// ---------- карточка гола: видео (ADR-036, раздел 4) ----------
// Окна записи считает сервер (goalplan.py — те же, что у бота), режет служба cuts. Видео — fetch с подписью Telegram →
// blob → <video>: у файлов нет публичных адресов. Время в видео переводим во время записи: начало окна + плеер.

const CUT_POLL_MS = 2000;
const CUT_WAIT_MS = 4 * 60e3;
const SRC_WORDS = { clock: "⏱ встали часы", board: "📺 задержка табло клуба", run: "🕐 ход часов от соседнего гола", admin: "✅ отметка человека" };
const card = { key: "", score: "", shift: 0, plan: null, error: "", note: "", done: "", busy: false, history: [], vids: {}, token: 0, back: null };
// история отметок гола — те же слова, что в /replay (bot.py: ROLE_WORD, VIA_WORD, KIND_WORD)
const ROLE_WORDS = { admin: "админ", helper: "помощник", import: "до журнала" };
const VIA_WORDS = { replay: "/replay", preview: "превью", video: "видео", confirm: "клип", import: "перенесено, не проверено", panel: "пульт" };
const MARK_WORDS = { absent: "🚫 нет в записи", wrong: "⚠️ табло сбилось", confirm: "✅ гол виден", reject: "❌ гола тут нет" };

function clock(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}

function goalOf(key, score) {
  const gd = state.goals.data || {};
  const m = (gd.matches || []).find((x) => x.key === key);
  const x = (m && (m.list || []).find((g) => g.score === score)) || ((gd.wait || {}).items || []).find((g) => g.key === key && g.score === score);
  return { title: (m && m.title) || (x && x.title) || "", date: key.slice(0, 10), goal: x || { score, state: "" } };
}

function planText(p) {
  if (!p) return "";
  const w = p.windows || [];
  if (p.shift) return `Запись ${w[0] ? w[0].what : ""}. Увидишь гол — поставь видео на паузу и нажми «Гол здесь».`;
  if (p.kind === "exact") return `${SRC_WORDS[p.src] || "✅"} — гол на ${clock(p.t - w[0].start)} этого видео: так его увидят болельщики.`;
  if (p.kind === "dispute") return `⚠️ Спор: отметка ${clock(p.t)} записи не сходится с табло${p.why ? ` (${p.why})` : ""}. Первое видео — по отметке, второе — ${w[1] ? w[1].what : "другой версии нет"}.`;
  if (p.kind === "approx") return `Примерное место — ${w[0] ? w[0].what : ""}.`;
  return `Где гол, служба не знает: 3 минуты записи ${w[0] ? w[0].what : ""}.`;
}

function vidBlock(w, k) {
  const v = card.vids[w.job] || {};
  const head = (card.plan.windows || []).length > 1 ? `<div class="card-title">${k + 1}. ${esc(w.what)}</div>` : "";
  let body;
  if (v.url) {
    body = `<video controls playsinline preload="metadata" src="${esc(v.url)}" data-start="${esc(w.start)}"></video>
      <div class="pos" data-pos="${esc(w.job)}">0:00 видео = ${esc(clock(w.start))} записи</div>`;
  } else if (v.error) {
    body = `<div class="vid-wait">Видео не вырезалось: ${esc(v.error)}</div>`;
  } else {
    body = `<div class="vid-wait">⏳ Режу видео… обычно 10–40 секунд</div>`;
  }
  const here = v.url && card.plan.kind !== "exact"
    ? `<button type="button" class="pill act" data-here="${esc(w.job)}" data-start="${esc(w.start)}"${card.busy ? " disabled" : ""}>Гол здесь — 0:00 видео</button>` : "";
  const cand = k === 0 && v.url && (card.plan.cand || []).length
    ? `<div class="cand"><span class="card-title">Часы вставали:</span>${card.plan.cand.map((t) => `<button type="button" class="pill" data-seek="${esc(t - w.start)}" data-job="${esc(w.job)}">${esc(clock(t - w.start))}</button>`).join("")}</div>` : "";
  return `<div class="vid" data-vid="${esc(w.job)}">${head}${body}${cand}${here ? `<div class="nav">${here}</div>` : ""}</div>`;
}

// одно видео готово: меняем только его блок — второе видео спора, если уже играет, не сбрасывается
function renderVid(job) {
  const el = document.querySelector(`#card [data-vid="${CSS.escape(String(job))}"]`);
  const k = ((card.plan && card.plan.windows) || []).findIndex((w) => w.job === job);
  if (el && k >= 0) el.outerHTML = vidBlock(card.plan.windows[k], k);
}

function renderCard() {
  const el = $("#card");
  if (!card.key) {
    el.hidden = true;
    $("#card-back").hidden = true;
    return;
  }
  const info = goalOf(card.key, card.score);
  const p = card.plan;
  let html = `<div class="handle" aria-hidden="true"></div><div class="sheet-head"><div><h2 id="card-title">${esc(info.title)}, ${esc(dm(info.date))}</h2>
    <div class="sheet-sub">${info.goal.state ? sticker(info.goal.state) : ""} ${goalLine(info.goal)}</div></div>
    <button type="button" class="btn-round" data-close aria-label="Закрыть">✕</button></div>`;
  if (card.error) html += `<div class="vid-wait">${esc(card.error)}</div>`;
  else if (!p) html += `<div class="vid-wait">⏳ Смотрю, что известно о голе…</div>`;
  else {
    html += `<p class="plan">${esc(planText(p))}</p>` + (p.windows || []).map(vidBlock).join("");
    html += markButtons(p);
    if (p.kind === "approx" || p.kind === "search") {
      if (card.note) html += `<div class="card-title">${esc(card.note)}</div>`;
      html += `<div class="nav"><button type="button" class="pill" data-shift="-1">⏪ 3 мин раньше</button><button type="button" class="pill" data-shift="1">⏩ 3 мин позже</button></div>`;
      if (card.shift) html += `<button type="button" class="more" data-shift="0">Вернуться к ${p.base === "approx" ? "примерному месту" : p.base === "exact" ? "30 с гола" : "оценке"}</button>`;
    }
  }
  html += `<div class="marks">${marksBlock()}</div>`;
  // фокус с клавиатуры переживает перерисовку: та же кнопка или ✕
  const f = document.activeElement && el.contains(document.activeElement) ? document.activeElement : null;
  // та же кнопка — по её data-атрибутам; кнопки, которой после перерисовки нет, — фокус остаётся у листа
  const again = !f ? "" : f.dataset.shift !== undefined ? `[data-shift="${f.dataset.shift}"]`
    : f.dataset.mark !== undefined ? `[data-mark="${f.dataset.mark}"]${f.dataset.step ? `[data-step="${f.dataset.step}"]` : ""}`
    : f.dataset.close !== undefined ? "[data-close]" : "";
  el.innerHTML = html;
  el.hidden = false;
  $("#card-back").hidden = false;
  if (again) { const x = el.querySelector(again); if (x) x.focus(); }
}

// ответ человека — строка журнала отметок (ADR-036, раздел 5): кнопки как под видео в боте (ADR-036, раздел 1)
function markButtons(p) {
  const w = p.windows || [];
  const b = (label, attrs) => `<button type="button" class="pill"${attrs}${card.busy ? " disabled" : ""}>${esc(label)}</button>`;
  let out = "";
  if (p.kind === "exact" && w[0]) {
    if (!p.own) out += b("✅ Гол виден", ` data-mark="confirm" data-job="${esc(w[0].job)}" data-sec="${esc(p.t)}"`);
    out += b("⏪ Гол раньше", ` data-mark="reject" data-step="-1" data-job="${esc(w[0].job)}" data-sec="${esc(p.t)}"`)
      + b("⏩ Гол позже", ` data-mark="reject" data-step="1" data-job="${esc(w[0].job)}" data-sec="${esc(p.t)}"`)
      + b("🚫 Гола нет в записи", ` data-mark="absent" data-job="${esc(w[0].job)}"`);
  } else if (p.kind === "dispute" && w[0]) {
    if (!p.own) out += b(`✅ Верно по отметке ${clock(p.t)}`, ` data-mark="confirm" data-job="${esc(w[0].job)}" data-sec="${esc(p.t)}"`);
    if (p.tb != null && w[1]) out += b(`✅ Верно по табло ${clock(p.tb)}`, ` data-mark="time" data-job="${esc(w[1].job)}" data-sec="${esc(p.tb)}"`);
  } else if (w[0]) {
    out += p.kind === "approx" && !p.shift ? b("⚠️ Табло сбилось", ` data-mark="wrong" data-job="${esc(w[0].job)}"`)
      : b("🚫 Гола нет в записи", ` data-mark="absent" data-job="${esc(w[0].job)}"`);
  }
  const own = p.own ? `<div class="card-title">Эту секунду отметил ты (или неизвестно кто — до журнала): «✅» на ней ставит кто-то другой.</div>` : "";
  return out ? `${own}<div class="nav">${out}</div>` : "";
}

function historyLine(r) {
  const when = toDate(r.at);
  const what = r.kind === "time" && typeof r.sec === "number" ? `гол на ${clock(r.sec)} записи`
    : r.kind === "confirm" || r.kind === "reject" ? `${MARK_WORDS[r.kind]} на ${clock(r.sec)}` : MARK_WORDS[r.kind] || r.kind;
  return [when ? when.toLocaleString("ru-RU", { timeZone: TZ, day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }).replace(",", "") : "",
    r.mine ? "ты" : ROLE_WORDS[r.role] || r.role, what, VIA_WORDS[r.via] || r.via].filter(Boolean).join(" · ");
}

function marksBlock() {
  let html = card.done ? `<div class="done" role="status">${esc(card.done)}</div>` : "";
  const h = card.history || [];
  if (h.length) {
    html += `<div class="card-title">Отметки этого гола</div><div class="hist">${h.map((r) => `<div class="hist-row${r.revoked ? " off" : ""}"><span>${esc(historyLine(r))}${r.revoked ? " — отозвано" : ""}</span>${r.can_revoke ? `<button type="button" class="more" data-revoke="${esc(r.id)}"${card.busy ? " disabled" : ""}>Отозвать</button>` : ""}</div>`).join("")}</div>`;
  }
  return html + `<div class="card-title sheet-foot">Отметка — та же, что в боте: служба сверит её с табло, а повтор и статус гола обновятся в течение минуты.</div>`;
}

function renderMarks() {
  const el = $("#card .marks");
  if (el) el.innerHTML = marksBlock();
  for (const x of document.querySelectorAll("#card [data-mark], #card [data-here]")) x.disabled = card.busy;
}

function mockMark(body) {   // ?admin_mock: отметка не уходит на сервер, только в историю на экране
  if (body.id) return { history: card.history.map((r) => (r.id === body.id ? { ...r, revoked: true, can_revoke: false } : r)), note: "Отозвано (мок)." };
  const row = { id: Date.now(), at: new Date().toISOString(), kind: body.kind, sec: body.sec, role: "admin", via: "panel", mine: true, revoked: false, can_revoke: true };
  return { history: [...card.history, row].slice(-8), note: "Записано (мок): повтор и статус гола обновятся в течение минуты." };
}

async function sendMark(body, after) {
  if (card.busy) return;
  const was = { key: card.key, score: card.score };
  card.busy = true;
  card.done = "";
  renderMarks();
  try {
    const d = mock ? mockMark(body) : await api(body.id ? "goal/revoke" : "goal/mark", { body: { ...was, ...body } });
    if (card.key !== was.key || card.score !== was.score) return;   // лист закрыли или открыли другой гол — ответ не его
    card.history = d.history || card.history;
    card.done = d.note || "Записано.";
  } catch (e) {
    if (card.key === was.key && card.score === was.score) card.done = `Не записалось: ${e.message}`;
    after = null;
  } finally {
    card.busy = false;
    renderMarks();
  }
  if (after) after();
}

async function api(path, opts = {}) {
  const base = apiBase();
  if (!base || !inTelegram) throw new Error("Видео открывается в пульте из Telegram.");
  let r;
  try {
    r = await fetch(`${base}/admin/${path}`, {
      method: opts.body ? "POST" : "GET", cache: "no-store",
      headers: { Authorization: `tma ${tg.initData}`, ...(opts.body ? { "Content-Type": "application/json" } : {}) },
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
  } catch (e) {
    throw new Error("Нет связи с сервером");
  }
  if (r.ok && opts.blob) return r.blob();
  let d = null;
  try { d = await r.json(); } catch (e) { d = null; }
  if (!r.ok) throw new Error((d && typeof d.error === "string" && d.error) || `Сервер ответил ${r.status}`);
  return d;
}

function dropVideos() {
  for (const v of Object.values(card.vids)) if (v.url) URL.revokeObjectURL(v.url);
  card.vids = {};
}

async function waitCut(job, token) {
  const until = Date.now() + CUT_WAIT_MS;
  while (token === card.token) {
    let st;
    try {
      st = await api(`cut/${job}`);
    } catch (e) {
      card.vids[job] = { error: e.message };
      break;
    }
    if (token !== card.token) return;
    if (st.status === "done") {
      try {
        const blob = await api(`cut/${job}.mp4`, { blob: true });
        if (token !== card.token) return;
        card.vids[job] = { url: URL.createObjectURL(blob) };
      } catch (e) {
        card.vids[job] = { error: e.message };
      }
      break;
    }
    if (st.status === "error") { card.vids[job] = { error: st.error || "служба cuts не смогла" }; break; }
    if (Date.now() > until) { card.vids[job] = { error: "служба cuts не успела за 4 минуты — открой гол ещё раз позже" }; break; }
    await new Promise((res) => setTimeout(res, CUT_POLL_MS));
  }
  if (token === card.token) renderVid(job);
}

async function loadCard(prevShift) {
  const token = ++card.token;
  const prev = card.plan;
  card.note = "";
  if (prevShift === undefined) {   // открыли гол — всё заново; листаем — прежнее видео остаётся, пока не придёт новое
    dropVideos();
    card.plan = null;
    card.error = "";
    renderCard();
  }
  let plan;
  try {
    if (mock) {
      const r = await fetch("data/admin/mock/goal-video.json", { cache: "no-store" });
      plan = { ...(await r.json()), shift: card.shift };
    } else {
      plan = await api("goal/video", { body: { key: card.key, score: card.score, shift: card.shift } });
    }
  } catch (e) {
    if (token !== card.token) return;
    if (prev && prevShift !== undefined) {   // дальше записи нет — остаёмся где были, кнопки на месте
      card.shift = prevShift;
      card.note = e.message;
    } else {
      card.error = e.message;
    }
    renderCard();
    return;
  }
  if (token !== card.token) return;
  dropVideos();   // прежнее видео играло, пока ждали новый план
  card.plan = plan;
  card.history = plan.history || card.history || [];
  if (mock) for (const w of plan.windows) card.vids[w.job] = { error: "в моке видео не режется" };
  renderCard();
  if (!mock) for (const w of card.plan.windows) waitCut(w.job, token);
}

function openCard(key, score, from) {
  card.key = key;
  card.score = score;
  card.shift = 0;
  card.done = "";
  card.history = [];
  card.back = from || null;
  document.body.classList.add("locked");
  loadCard();
  setTimeout(() => { const x = $("#card [data-close]"); if (x) x.focus(); }, 0);
}

function closeCard() {
  card.token++;
  dropVideos();
  card.key = "";
  card.plan = null;
  document.body.classList.remove("locked");
  renderCard();
  if (card.back && document.contains(card.back)) card.back.focus();
}

document.addEventListener("timeupdate", (e) => {
  const v = e.target;
  if (!(v instanceof HTMLVideoElement) || !v.closest("#card")) return;
  const pos = v.nextElementSibling;
  if (pos && pos.dataset.pos) pos.textContent = `${clock(v.currentTime)} видео = ${clock(Number(v.dataset.start) + v.currentTime)} записи`;
  const here = v.closest(".vid").querySelector("[data-here]");
  if (here) here.textContent = `Гол здесь — ${clock(v.currentTime)} видео = ${clock(Number(v.dataset.start) + v.currentTime)} записи`;
}, true);

// ---------- загрузка ----------

const tabArg = (() => { try { return new URLSearchParams(location.search).get("tab") || ""; } catch (e) { return ""; } })();
// по вкладке: данные, когда пришли, ошибка в шапке, экран «нет доступа»
const state = { tab: tabArg === "goals" ? "goals" : "status", role: "", open: {}, loading: false,
  status: { data: null, gotAt: 0, error: null, gate: null }, goals: { data: null, gotAt: 0, error: null, gate: null } };

function gate(title, text) {
  return `<div class="gate"><b>${esc(title)}</b>${esc(text)}</div>`;
}

function renderTabs() {
  const tabs = $("#tabs");
  // помощнику — только «Голы» (ADR-036, раздел 4): сегментов нет совсем
  tabs.hidden = state.role === "helper" || (!state.status.data && !state.goals.data);
  for (const b of tabs.querySelectorAll("[data-tab]")) {
    const on = b.dataset.tab === state.tab;
    b.classList.toggle("on", on);
    b.setAttribute("aria-selected", on ? "true" : "false");
  }
  const w = state.goals.data && state.goals.data.wait;
  $("#goals-n").textContent = w && w.total ? ` · ${w.total}` : "";
}

function render() {
  const main = $("#main");
  const t = state[state.tab];
  renderTabs();
  // «N мин назад» — от времени сервера: часы телефона могут врать, а мок застыл на 03.10 в 20:56.
  // Между обновлениями добавляем, сколько прошло на телефоне
  const at0 = t.data && toDate(t.data.now);
  const now = at0 ? at0.getTime() + (Date.now() - t.gotAt) : Date.now();
  if (t.gate) {
    main.innerHTML = t.gate;
    $("#when").textContent = "";
    return;
  }
  const st = t.data;
  if (!st) {
    main.innerHTML = t.error ? gate("Не загрузилось", t.error) : `<div class="empty">Загружаю…</div>`;
    return;
  }
  const at = toDate(st.now);
  const who = inTelegram && tg.initDataUnsafe && tg.initDataUnsafe.user ? tg.initDataUnsafe.user.first_name : "";
  $("#when").textContent = `${who ? `${who} · ` : ""}данные на ${at ? hm(at) : "—"} МСК${t.error ? ` · ${t.error}` : ""}${mock ? " · мок" : ""}`;
  main.innerHTML = state.tab === "goals" ? goalsTab(st, now) : summary(st) + system(st, now) + audience(st) + sends(st, now) + games(st) +
    `<div class="foot">Пульт только показывает. Обновляется сам раз в минуту, пока открыт. Людей по именам и id здесь нет — только числа.</div>`;
}

async function fetchJson(path) {
  if (mock) {
    const name = path === "goals" ? "goals" : `status${mock === "bad" ? "-bad" : ""}`;
    if (mock === "helper" && path === "status") throw Object.assign(new Error("Пульт только для админов."), { gate: "Нет доступа" });
    const r = await fetch(`data/admin/mock/${name}.json`, { cache: "no-store" });
    if (!r.ok) throw new Error("мок не нашёлся");
    const d = await r.json();
    return mock === "helper" && path === "goals" ? { ...d, role: "helper" } : d;
  }
  const base = apiBase();
  if (!base) throw Object.assign(new Error("Сервер API не подключён: у задания Pages пуст LIVE_API."), { gate: "Нет сервера" });
  if (!inTelegram) throw Object.assign(new Error("Пульт открывается из бота: напиши ему /admin и нажми «Открыть пульт»."), { gate: "Открой из Telegram" });
  let r;
  try {
    r = await fetch(`${base}/admin/${path}`, { headers: { Authorization: `tma ${tg.initData}` }, cache: "no-store" });
  } catch (e) {
    throw new Error("Нет связи с сервером");
  }
  let d = null;
  try { d = await r.json(); } catch (e) { d = null; }
  const text = (d && typeof d.error === "string" && d.error) || `Сервер ответил ${r.status}`;
  if (r.status === 401 || r.status === 403) throw Object.assign(new Error(text), { gate: "Нет доступа" });
  if (!r.ok) throw new Error(text);
  return d;
}

async function loadOne(path) {
  const t = state[path];
  try {
    t.data = await fetchJson(path);
    t.gotAt = Date.now();
    t.error = null;
    t.gate = null;
    if (path === "goals" && t.data && (t.data.role === "admin" || t.data.role === "helper")) state.role = t.data.role;
  } catch (e) {
    if (e.gate) t.gate = gate(e.gate, e.message);
    else t.error = e.message;   // прежние данные остаются на экране, ошибка — в шапке
  }
}

async function load() {
  if (state.loading) return;
  state.loading = true;
  $("#refresh").classList.add("spin");
  try {
    // «Голы» — всегда: их число на вкладке. «Состояние» помощнику не положено — не спрашиваем
    await Promise.all([loadOne("goals"), state.role === "helper" ? null : loadOne("status")]);
    if (state.role === "helper") state.tab = "goals";
  } finally {
    state.loading = false;
    $("#refresh").classList.remove("spin");
    render();
  }
}

function toggle(id) {
  state.open[id] = !state.open[id];
  render();
}

document.addEventListener("click", (e) => {
  if (e.target.closest("[data-close]") || e.target.id === "card-back") return closeCard();
  const seek = e.target.closest("[data-seek]");
  if (seek) {
    const v = $("#card video");
    if (v) { v.currentTime = Number(seek.dataset.seek); v.play().catch(() => {}); }
    return;
  }
  const mark = e.target.closest("[data-mark]");
  if (mark) {
    const sec = mark.dataset.sec !== undefined ? Number(mark.dataset.sec) : undefined;
    const step = Number(mark.dataset.step || 0);
    // «⏪ / ⏩» под точной секундой: гола тут нет — и сразу запись раньше или позже, как в боте
    return sendMark({ kind: mark.dataset.mark, job: Number(mark.dataset.job), sec }, step ? () => { card.shift = step; loadCard(0); } : null);
  }
  const here = e.target.closest("[data-here]");
  if (here) {
    const v = here.closest(".vid").querySelector("video");
    if (!v) return;
    v.pause();
    return sendMark({ kind: "time", job: Number(here.dataset.here), sec: Number(here.dataset.start) + Math.floor(v.currentTime) });
  }
  const revoke = e.target.closest("[data-revoke]");
  if (revoke) return sendMark({ id: Number(revoke.dataset.revoke) });
  const shift = e.target.closest("[data-shift]");
  if (shift) {
    const was = card.shift;
    card.shift = shift.dataset.shift === "0" ? 0 : card.shift + Number(shift.dataset.shift);
    loadCard(was);
    return;
  }
  const goal = e.target.closest(".goal-open");
  if (goal) return openCard(goal.dataset.key, goal.dataset.score, goal);
  const more = e.target.closest("[data-more]");
  if (more) return toggle(more.dataset.more);
  const open = e.target.closest("[data-open]");
  if (open) return toggle(open.dataset.open);
  const tab = e.target.closest("[data-tab]");
  if (tab) {
    state.tab = tab.dataset.tab;
    render();
    window.scrollTo(0, 0);
    return;
  }
  if (e.target.closest("#refresh")) load();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && card.key) return closeCard();
  const goal = (e.key === "Enter" || e.key === " ") && e.target.closest && e.target.closest(".goal-open");
  if (goal) {
    e.preventDefault();
    return openCard(goal.dataset.key, goal.dataset.score, goal);
  }
  const open = (e.key === "Enter" || e.key === " ") && e.target.closest && e.target.closest("[data-open]");
  if (open) {
    e.preventDefault();
    toggle(open.dataset.open);
    const again = document.querySelector(`[data-open="${CSS.escape(open.dataset.open)}"]`);
    if (again) again.focus();
  }
});

if (inTelegram) {
  tg.ready();
  tg.expand();
  tg.onEvent("themeChanged", () => { document.documentElement.dataset.theme = tg.colorScheme === "dark" ? "dark" : "light"; });
  try { tg.setHeaderColor(tg.colorScheme === "dark" ? "#1c222c" : "#cccccc"); } catch (e) { /* старый клиент */ }
}
load();
setInterval(() => { if (document.visibilityState === "visible") load(); }, REFRESH_MS);
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") load(); });
