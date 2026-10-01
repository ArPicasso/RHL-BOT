#!/usr/bin/env python3
"""Собирает «Буллит» в одну HTML-страницу: тот же движок, что в мини-аппе (ADR-017).

Нужен, чтобы игру можно было открыть с телефона до того, как собраны данные лиги и поднят
мини-апп. Движок берётся из webapp/bullit.js как есть — до черты «связь с мини-аппом»,
стикеры вратарей зашиваются в страницу data-URI, своя связка внизу хранит рекорд в localStorage.

    python3 tools/bullit_prototype.py --out bullit.html              # страница целиком
    python3 tools/bullit_prototype.py --out bullit.html --artifact    # без каркаса: для Artifact
"""
import argparse
import base64
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
WEBAPP = BASE / "webapp"
MARK = "// ---------- связь с мини-аппом ----------"

LIGHT = {"canvas": "#ffffff", "paper": "#ffffff", "ink": "#000000", "muted": "#555555", "mist": "#e9e9e9",
         "edge": "#000000", "edge-strong": "#000000", "sel": "#dceeff", "act": "#000000", "on-act": "#ffffff"}
DARK = {"canvas": "#131922", "paper": "#1a212c", "ink": "#e4e8ee", "muted": "#8e98a8", "mist": "#252e3a",
        "edge": "#2c3645", "edge-strong": "#4a5567", "sel": "#1f2c3f", "act": "#4da2ff", "on-act": "#08121f"}
STICKER = {"blue": "#4da2ff", "mint": "#55db9c", "lavender": "#e9ccff", "ember": "#fb4903",
           "sun": "#ffd731", "violet": "#5c4ade"}


def data_uri(path: Path) -> str:
    kind = "image/webp" if path.suffix == ".webp" else "image/png"
    return f"data:{kind};base64," + base64.b64encode(path.read_bytes()).decode()


def tokens(name: str, values: dict) -> str:
    rows = "".join(f"  --{k}: {v};\n" for k, v in values.items())
    return f"{name} {{\n{rows}}}\n"


def build(data_file: Path, artifact: bool = False) -> str:
    engine = (WEBAPP / "bullit.js").read_text(encoding="utf-8").split(MARK)[0]
    css = (WEBAPP / "bullit.css").read_text(encoding="utf-8")
    data = json.loads(data_file.read_text(encoding="utf-8"))
    for club in data["clubs"]:                       # стикеры внутрь страницы: она работает без сети
        club["goalie"] = data_uri(WEBAPP / club["goalie"])
    skater = data_uri(WEBAPP / "players" / "skater.webp")
    # Тема: светлая на голом :root, тёмная — и по настройке системы, и по кнопке
    root = (tokens(":root", {**LIGHT, **STICKER, "color-scheme": "light"})
            + "@media (prefers-color-scheme: dark) {\n"
            + tokens('  :root:not([data-theme="light"])', {**DARK, "color-scheme": "dark"})
            + "}\n"
            + tokens(':root[data-theme="dark"]', {**DARK, "color-scheme": "dark"}))
    page = (HEAD if not artifact else "") + BODY + (FOOT if not artifact else "")
    return page.format(root=root, css=css, engine=engine,
                       data=json.dumps(data, ensure_ascii=False), skater=json.dumps(skater))


HEAD = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
"""

FOOT = """
</body>
</html>
"""

BODY = """<title>Буллит</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Onest:wght@500;700;800&family=Unbounded:wght@800&display=swap">
<style>
{root}
:root {{
  --line: 1px solid var(--edge);
  --display: "Unbounded", "Arial Black", system-ui, sans-serif;
  --ui: "Onest", system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}}
* {{ box-sizing: border-box; }}
[hidden] {{ display: none !important; }}
html, body {{ margin: 0; background: var(--canvas); color: var(--ink); font: 500 15px/1.4 var(--ui); }}
body {{ padding-block: 16px; padding-inline: 16px; max-width: 520px; margin: 0 auto; -webkit-tap-highlight-color: transparent; }}
.top {{ display: flex; align-items: flex-start; gap: 12px; margin-bottom: 14px; }}
.top div {{ flex: 1; min-width: 0; }}
h1 {{ font: 800 28px/0.95 var(--display); margin: 2px 0 4px; text-wrap: balance; }}
.sub {{ color: var(--muted); font-size: 13px; margin: 0; }}
.btn {{
  display: block; width: 100%; max-width: 528px; margin: 0 auto; border: var(--line); border-radius: 9999px;
  padding: 15px 18px; background: var(--act); color: var(--on-act); border-color: var(--act);
  font: 700 15px/1.2 var(--ui); letter-spacing: .03em; cursor: pointer;
}}
.btn:active {{ transform: scale(.97); }}
.theme {{
  flex: none; width: 40px; height: 40px; border-radius: 50%; border: var(--line);
  background: var(--paper); color: var(--ink); font-size: 16px; cursor: pointer;
}}
.theme:focus-visible, .btn:focus-visible {{ outline: 3px solid var(--act); outline-offset: 3px; }}
.note {{ color: var(--muted); font-size: 12px; line-height: 1.5; margin-top: 18px; }}
.note b {{ color: var(--ink); font-weight: 700; }}
{css}
</style>
<div class="top">
  <div>
    <h1>Буллит</h1>
    <p class="sub">Три буллита на клуб, нужен один гол. Пройди все 26 ворот РХЛ.</p>
  </div>
  <button class="theme" type="button" id="theme" aria-label="Сменить тему">◐</button>
</div>
<div class="bl">
  <div class="bl-head"><span class="bl-club"><span class="bl-name"></span></span><span class="bl-count"></span>
    <span class="bl-dots" role="img" aria-label="Буллиты на этих воротах"></span></div>
  <div class="bl-road" role="img" aria-label="Пройденные ворота"></div>
  <div class="bl-play" tabindex="0" role="application" aria-label="Буллит: тапни, чтобы бросить">
    <canvas class="bl-ice"></canvas>
    <div class="bl-over" hidden></div>
  </div>
  <p class="bl-hint"></p>
</div>
<p class="note">Прототип для обсуждения: в мини-аппе тот же движок. <b>Ворота идут от самых пробиваемых
к самым непробиваемым</b> — по пропущенным за игру в сезоне 25/26, от «Белгорода» (5,83) до
«Рязани-ВДВ» (1,60). Вратарь реагирует на бросок с задержкой и не умеет отменять инерцию: забивают
против его движения. С шестых ворот появляется второй тап — высота.</p>
<script>
{engine}
</script>
<script>
"use strict";
const DATA = {data};
const SKATER = {skater};
const KEY = "bl_best_proto";
const best = () => {{ try {{ return parseInt(localStorage.getItem(KEY) || "0", 10) || 0; }} catch (e) {{ return 0; }} }};

function over(passed, total, stopper, rec) {{
  const won = !stopper;
  const title = won ? "Вся лига" : String(passed);
  const stop = won ? "Все 26 ворот пройдены" : stopper ? "Остановил «" + stopper.name + "»" : "";
  const line = rec > passed ? "Лучший поход — " + rec : passed && !won ? "Это твой лучший поход" : "";
  return '<div class="bl-big">' + title + '</div>'
    + '<div class="bl-of">' + (won ? "26 из 26" : "из " + total + " ворот") + '</div>'
    + '<div class="bl-stop">' + stop + '</div>'
    + (line ? '<div class="bl-rec">' + line + '</div>' : "")
    + '<button type="button" class="btn" data-again>Ещё раз</button>';
}}

Bullit.Game(document.querySelector(".bl"), {{
  clubs: DATA.clubs,
  record: best(),
  skater: SKATER,
  overHTML: over,
  onRecord: (n) => {{ try {{ localStorage.setItem(KEY, String(n)); }} catch (e) {{}} }},
}});

document.getElementById("theme").addEventListener("click", () => {{
  const root = document.documentElement;
  const dark = root.dataset.theme
    ? root.dataset.theme === "dark"
    : window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches;
  root.dataset.theme = dark ? "light" : "dark";
}});
</script>
"""


def main() -> None:
    ap = argparse.ArgumentParser(description="Собрать прототип «Буллита» одной страницей")
    ap.add_argument("--data", type=Path, default=WEBAPP / "data" / "bullit.json")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--artifact", action="store_true", help="без <html>/<head>: страницу обернёт Artifact")
    args = ap.parse_args()
    args.out.write_text(build(args.data, args.artifact), encoding="utf-8")
    print(f"{args.out} · {args.out.stat().st_size // 1024} КБ")


if __name__ == "__main__":
    main()
