"""Поле для прототипа «Карты»: гексы по силуэту России, в каждом городе клуба — база."""
import json, math

# Границы населённой России, упрощённо: (долгота, широта), по часовой с северо-запада
BORDER = [(28,70),(33,70.2),(41,68.6),(50,69.2),(60,70),(68,70.2),(76,69),(86,66),(100,62),(106,57),
          (107,54),(106,51.5),(100,50),(92,50),(85,49.5),(78,51),(70,51.5),(62,51.5),
          (56,50),(51,51),(48,49),(46,48),(47.5,44.5),(45.5,42),(41,43),(38.5,44.5),(37,46.5),
          (38.5,50),(35,51.3),(31.5,52.8),(30.5,56),(28,59),(28.5,64)]

CITY = {  # клуб → (долгота, широта)
 "arktika": (33.09, 68.97), "akhmat-granit": (45.69, 43.31), "vityaz-podolsk": (37.55, 55.43),
 "dinamo-576": (30.31, 59.94), "kaluga": (36.26, 54.51), "krasnaya-mashina": (37.62, 55.75),
 "leningradets": (30.95, 59.55), "metallurg": (37.91, 59.13), "dinamo-kareliya": (34.26, 62.21),
 "polet": (38.84, 58.05), "tverichi": (35.91, 56.86), "fakel-yamal": (66.60, 66.53),
 "bryansk": (34.36, 53.24), "dizelist": (45.00, 53.20), "ermak": (103.89, 52.54),
 "belgorod": (36.59, 50.60), "kristall": (46.03, 51.53), "ryazan-vdv": (39.74, 54.62),
 "tambov": (40.49, 52.89), "progress": (52.66, 58.14), "proton": (39.21, 51.31),
 "rostov": (39.72, 47.23), "sokol": (47.48, 56.11), "krasnodar": (38.98, 45.04),
 "samara": (50.15, 53.20), "ekoniva-bobrov": (40.03, 51.10),
}

def px(lon):
    """Запад растянут, Сибирь сжата: 25 клубов живут западнее 70°, «Ермак» — на 104°."""
    return (lon - 28) / 42 * 1000 if lon <= 70 else 1000 + (lon - 70) / 36 * 330

def py(lat):
    return (71.5 - lat) * 22


def inside(x, y, poly):
    n, ins = len(poly), False
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) / (y2 - y1) * (x2 - x1):
            ins = not ins
    return ins


def build(s=24.0):
    poly = [(px(a), py(b)) for a, b in BORDER]
    xs = [p[0] for p in poly]; ys = [p[1] for p in poly]
    cells, index = [], {}
    w, h = math.sqrt(3) * s, 1.5 * s
    rmax = int((max(ys) - min(ys)) / h) + 2
    for r in range(-1, rmax + 1):
        y = min(ys) + r * h
        q0 = int((min(xs) - w * r / 2) / w) - 1
        for q in range(q0, q0 + int((max(xs) - min(xs)) / w) + 4):
            x = w * (q + r / 2) + min(xs)
            if inside(x, y, poly):
                index[(q, r)] = len(cells)
                cells.append({"q": q, "r": r, "x": round(x, 1), "y": round(y, 1)})
    DIRS = [(1, 0), (1, -1), (0, -1), (-1, 0), (-1, 1), (0, 1)]
    adj = [[index[(c["q"] + dq, c["r"] + dr)] for dq, dr in DIRS if (c["q"] + dq, c["r"] + dr) in index] for c in cells]
    # «Ермак» должен быть связан с остальными: узкий коридор по Транссибу
    bases = {}
    for club, (lon, lat) in CITY.items():
        X, Y = px(lon), py(lat)
        i = min(range(len(cells)), key=lambda k: (cells[k]["x"] - X) ** 2 + (cells[k]["y"] - Y) ** 2)
        while i in bases.values():   # два клуба в одном гексе — берём соседний свободный
            i = next(j for j in adj[i] if j not in bases.values())
        bases[club] = i
    return {"s": s, "w": round(max(xs) - min(xs) + 2 * s, 1), "h": round(max(ys) - min(ys) + 2 * s, 1),
            "cells": cells, "adj": adj, "bases": bases}


if __name__ == "__main__":
    b = build()
    # связность всего поля
    seen, stack = {0}, [0]
    while stack:
        for j in b["adj"][stack.pop()]:
            if j not in seen:
                seen.add(j); stack.append(j)
    print("клеток:", len(b["cells"]), "| в одном куске:", len(seen), "| баз:", len(b["bases"]))
    far = [c for c in b["cells"] if c["x"] > 1000]
    print("клеток восточнее 70°:", len(far))
    json.dump(b, open("board.json", "w"), ensure_ascii=False)
