// Пульт админа (ADR-021): здоровье системы, аудитория, рассылки и игры одним экраном.
// Данные — GET ${LIVE_API}/admin/status с подписью Telegram, пускает только ADMIN_IDS на сервере.
// Для разработки: ?admin_mock=1 — выдуманный ответ data/admin/mock/status.json, =bad — с проблемами.
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
  // красным — только если живое не идёт ни из одного источника (как problems в admin.py)
  const working = (live.sources || []).some((x) => !x.errors && x.ok);
  for (const src of live.sources || []) {
    const level = src.errors >= 3 && !working ? "bad" : src.errors ? "warn" : src.ok ? "ok" : "";
    const sub = [src.errors ? `ошибок подряд: ${src.errors}` : `матчей: ${src.games || 0}`, src.note || ""].filter(Boolean).map(esc).join(" · ");
    html += row(level, src.name, sub, esc(ago(src.ok, now)));
  }
  const r = s.raskat || {};
  html += row(r.on === false ? "bad" : "ok", "Зачёт «Раската»", esc(r.note || ""), r.on === false ? "выключен" : "включён");
  const d = s.disk;
  if (d) html += row(d.free < 1 << 30 ? "bad" : d.free < 3 * (1 << 30) ? "warn" : "ok", "Диск", esc(`свободно ${bytes(d.free)} из ${bytes(d.total)} · state.db ${bytes(d.db)}`), "");
  return html + `</section>`;
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
  </div>`;
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

// ---------- загрузка ----------

const state = { data: null, gotAt: 0, error: null, gate: null, loading: false, open: {} };

function gate(title, text) {
  return `<div class="gate"><b>${esc(title)}</b>${esc(text)}</div>`;
}

function render() {
  const main = $("#main");
  // «N мин назад» — от времени сервера: часы телефона могут врать, а мок застыл на 03.10 в 20:56.
  // Между обновлениями добавляем, сколько прошло на телефоне
  const at0 = state.data && toDate(state.data.now);
  const now = at0 ? at0.getTime() + (Date.now() - state.gotAt) : Date.now();
  if (state.gate) {
    main.innerHTML = state.gate;
    $("#when").textContent = "";
    return;
  }
  const st = state.data;
  if (!st) {
    main.innerHTML = state.error ? gate("Не загрузилось", state.error) : `<div class="empty">Загружаю…</div>`;
    return;
  }
  const at = toDate(st.now);
  const who = inTelegram && tg.initDataUnsafe && tg.initDataUnsafe.user ? tg.initDataUnsafe.user.first_name : "";
  $("#when").textContent = `${who ? `${who} · ` : ""}данные на ${at ? hm(at) : "—"} МСК${state.error ? ` · ${state.error}` : ""}${mock ? " · мок" : ""}`;
  main.innerHTML = summary(st) + system(st, now) + audience(st) + sends(st, now) + games(st) +
    `<div class="foot">Пульт только показывает. Обновляется сам раз в минуту, пока открыт. Людей по именам и id здесь нет — только числа.</div>`;
}

async function fetchStatus() {
  if (mock) {
    const r = await fetch(`data/admin/mock/status${mock === "bad" ? "-bad" : ""}.json`, { cache: "no-store" });
    if (!r.ok) throw new Error("мок не нашёлся");
    return r.json();
  }
  const base = apiBase();
  if (!base) throw Object.assign(new Error("Сервер API не подключён: у задания Pages пуст LIVE_API."), { gate: "Нет сервера" });
  if (!inTelegram) throw Object.assign(new Error("Пульт открывается из бота: напиши ему /admin и нажми «Открыть пульт»."), { gate: "Открой из Telegram" });
  let r;
  try {
    r = await fetch(`${base}/admin/status`, { headers: { Authorization: `tma ${tg.initData}` }, cache: "no-store" });
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

async function load() {
  if (state.loading) return;
  state.loading = true;
  $("#refresh").classList.add("spin");
  try {
    state.data = await fetchStatus();
    state.gotAt = Date.now();
    state.error = null;
    state.gate = null;
  } catch (e) {
    if (e.gate) state.gate = gate(e.gate, e.message);
    else state.error = e.message;   // прежние данные остаются на экране, ошибка — в шапке
  } finally {
    state.loading = false;
    $("#refresh").classList.remove("spin");
    render();
  }
}

document.addEventListener("click", (e) => {
  const more = e.target.closest("[data-more]");
  if (more) {
    state.open[more.dataset.more] = !state.open[more.dataset.more];
    render();
    return;
  }
  if (e.target.closest("#refresh")) load();
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
