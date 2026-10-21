#!/usr/bin/env python3
"""
matrix-ghost-check.py - анализ ложных нажатий в матрице без диодов.

Модель: нажатая клавиша = провод между строкой и столбцом. Пока сканируется
строка r, столбец c читается LOW, если от R_r до C_c есть путь из нажатых
клавиш. Отсюда: три нажатых угла ЛЮБОГО прямоугольника (2 строки x 2
столбца) дают ложное нажатие четвёртого.

Запуск:
    python matrix-ghost-check.py            # текущая раскладка: полный отчёт
    python matrix-ghost-check.py --scan W S D   # пошаговый разбор скана
    python matrix-ghost-check.py --optimise    # перебор вариантов row4
"""
import itertools
import sys

ROWS, COLS = 5, 6

# ─── раскладки ──────────────────────────────────────────────────────────────

CURRENT = {          # платы, которые сейчас на руках (S и F в row2)
    (0, 0): "`", (0, 1): "1", (0, 2): "2", (0, 3): "3", (0, 4): "4", (0, 5): "5",
    (1, 0): "ESC", (1, 1): "Q", (1, 2): "W", (1, 3): "E", (1, 4): "R", (1, 5): "T",
    (2, 0): "TAB", (2, 1): "A", (2, 2): "S", (2, 3): "D", (2, 4): "F", (2, 5): "G",
    (3, 0): "SHIFT", (3, 1): "Z", (3, 2): "X", (3, 3): "C", (3, 4): "V", (3, 5): "B",
    (4, 1): "ALT", (4, 2): "CTRL",
}
DIRECT = ["GUI", "LOWER", "ENTER", "ENC_BTN"]   # direct-пины: изолированы

NEXT = {             # следующая ревизия платы (S и F в row4)
    (0, 0): "`", (0, 1): "1", (0, 2): "2", (0, 3): "3", (0, 4): "4", (0, 5): "5",
    (1, 0): "ESC", (1, 1): "Q", (1, 2): "W", (1, 3): "E", (1, 4): "R", (1, 5): "T",
    (2, 0): "TAB", (2, 1): "A", (2, 3): "D", (2, 5): "G",
    (3, 0): "SHIFT", (3, 1): "Z", (3, 2): "X", (3, 3): "C", (3, 4): "V", (3, 5): "B",
    (4, 0): "GUI", (4, 2): "S", (4, 4): "F", (4, 5): "ALT",
}

# насколько неприятно ложное нажатие (5 = хуже всего)
HARM = {
    "GUI": 5, "WIN": 5,       # Start menu, горячие клавиши
    "CTRL": 5,                # залипающий модификатор
    "ALT": 4,                 # меню / модификатор ходьбы
    "SHIFT": 4,               # shift-залипание, капс
    "S": 4,                   # остановка движения в игре
    "F": 3,                   # "использовать"
    "ENTER": 3, "ENC_BTN": 2,
}
GAMING = [
    ("C S D (назад)", ["C", "S", "D"]), ("W A S D (движение)", ["W", "A", "S", "D"]),
    ("W S D", ["W", "S", "D"]), ("W E S D", ["W", "E", "S", "D"]), ("A S D", ["A", "S", "D"]),
    ("W E D", ["W", "E", "D"]), ("Q W A", ["Q", "W", "A"]), ("X D C", ["X", "D", "C"]),
    ("D F R", ["D", "F", "R"]), ("Z X C", ["Z", "X", "C"]), ("X C V", ["X", "C", "V"]),
    ("C V F", ["C", "V", "F"]), ("S F", ["S", "F"]), ("S D F", ["S", "D", "F"]),
    ("W A D", ["W", "A", "D"]), ("Q W A S", ["Q", "W", "A", "S"]),
]

# ─── электрическая модель ──────────────────────────────────────────────────


def closure(keys):
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for (r, c) in keys:
        union(("R", r), ("C", c))

    out = set()
    for r in range(ROWS):
        for c in range(COLS):
            if find(("R", r)) == find(("C", c)):
                out.add((r, c))
    return out


def idx(r, c):
    return c * ROWS + r


def rc(i):
    return (i % ROWS, i // ROWS)


def mask_of(cells):
    m = 0
    for (r, c) in cells:
        m |= 1 << idx(r, c)
    return m


def show(mask, names):
    return "{" + ",".join(sorted(names.get(rc(i), str(rc(i))) for i in range(ROWS * COLS)
                                  if mask & (1 << i))) + "}"


def nm(keys, names):
    return "+".join(sorted(names.get(k, f"{k}") for k in keys))


# ─── фильтр призраков (трансляция C-драйвера) ──────────────────────────────

BIT = lambda n: 1 << n


def cell_index(row, col, rows):
    return col * rows + row


def uf_find(parent, node):
    while parent[node] != node:
        parent[node] = parent[parent[node]]
        node = parent[node]
    return node


def uf_union(parent, a, b):
    a, b = uf_find(parent, a), uf_find(parent, b)
    if a != b:
        parent[a] = b


def hiz_matrix_closure(mask, rows, cols):
    parent = list(range(32))
    out = 0
    for r in range(rows):
        for c in range(cols):
            if (mask & BIT(cell_index(r, c, rows))) == 0:
                continue
            uf_union(parent, r, rows + c)
    for r in range(rows):
        for c in range(cols):
            if uf_find(parent, r) == uf_find(parent, rows + c):
                out |= BIT(cell_index(r, c, rows))
    return out


def hiz_ghost_explainable(cell, real_mask, confirmed, press_seq, fresh_mask, rows, cols):
    r, c = cell % rows, cell // rows
    for c1 in range(cols):
        if c1 == c:
            continue
        k1 = cell_index(r, c1, rows)
        if (real_mask & BIT(k1)) == 0:
            continue
        for r2 in range(rows):
            if r2 == r:
                continue
            k2 = cell_index(r2, c1, rows)
            k3 = cell_index(r2, c, rows)
            if (real_mask & BIT(k2)) == 0 or (real_mask & BIT(k3)) == 0:
                continue
            chain = BIT(k1) | BIT(k2) | BIT(k3)
            if (chain & fresh_mask) == 0:
                continue
            have, last, ordered = False, -1, True
            for k in (k1, k2, k3):
                if (confirmed & BIT(k)) == 0:
                    continue
                have = True
                if press_seq[k] < last:
                    ordered = False
                    break
                last = press_seq[k]
            if have and ordered:
                return True
    return False


def hiz_filter_ghosts(st, raw, rows, cols):
    cells = rows * cols
    st["confirmed"] &= raw
    st["suppressed"] &= raw
    fresh = raw & ~st["observed"]
    real = raw & ~st["suppressed"]
    for i in range(cells):
        bit = BIT(i)
        if (real & bit) == 0 or (st["confirmed"] & bit) != 0:
            continue
        if (fresh & bit) == 0:
            continue
        if hiz_matrix_closure(real & ~bit, rows, cols) != raw:
            continue
        if not hiz_ghost_explainable(i, real & ~bit, st["confirmed"], st["press_seq"], fresh, rows, cols):
            continue
        real &= ~bit
        st["suppressed"] |= bit
    added = real & ~st["confirmed"]
    for i in range(cells):
        if added & BIT(i):
            st["press_seq"][i] = st["counter"]
            st["counter"] += 1
    st["confirmed"] = real
    st["observed"] = raw
    return real


def filter_result(presses, names, rows=ROWS, cols=COLS):
    """возвращает (наблюдение, отфильтрованное) для последовательности нажатий"""
    st = {"confirmed": 0, "suppressed": 0, "observed": 0, "press_seq": [0] * 32, "counter": 0}
    raw = set()
    out = 0
    for cells in presses:
        raw = closure(cells)
        out = hiz_filter_ghosts(st, mask_of(cells), rows, cols)
    out_set = {p for p in names if out & (1 << idx(*p))}
    return raw, out_set


# ─── анализ ────────────────────────────────────────────────────────────────


def recipes(layout, ghost):
    """3-клавишные рецепты ложного нажатия для `ghost`"""
    r, c = ghost
    out = {}
    for r2 in range(ROWS):
        if r2 == r:
            continue
        for c1 in range(COLS):
            if c1 == c:
                continue
            corners = [(r, c), (r, c1), (r2, c), (r2, c1)]
            if not all(p in layout for p in corners):
                continue
            combo = [p for p in corners if p != ghost]
            if ghost in closure(combo):
                k = tuple(sorted(combo))
                adj = abs(r - r2) == 1 and abs(c - c1) == 1
                if k not in out or (adj and not out[k][1]):
                    out[k] = (combo, adj)
    return out


def full_rects(layout):
    n = 0
    for r1 in range(ROWS):
        for r2 in range(r1 + 1, ROWS):
            for c1 in range(COLS):
                for c2 in range(c1 + 1, COLS):
                    if all(p in layout for p in ((r1, c1), (r1, c2), (r2, c1), (r2, c2))):
                        n += 1
    return n


def tier(combo, layout):
    rows_ = {p[0] for p in combo}
    cols_ = {p[1] for p in combo}
    span = max(rows_) - min(rows_) + 1
    cspan = max(cols_) - min(cols_) + 1
    if span <= 2 and cspan <= 2:
        return "соседние"
    if span <= 2 or cspan <= 2:
        return "близко"
    return "далеко"


def filter_coverage(combo, layout, names):
    """из 6 порядков нажатия сколько закрывает фильтр призраков"""
    caught = 0
    for perm in itertools.permutations(combo):
        presses = []
        for i in range(len(perm)):
            presses.append([perm[j] for j in range(i + 1)])
        raw, out = filter_result(presses, names)
        ghost_cells = [p for p in raw if p not in combo]
        if ghost_cells and not (set(ghost_cells) & out):
            caught += 1
    return caught


def report(layout, names, title):
    print(f"{'='*78}\n{title}\n{'='*78}")
    print(f"  клавиш в матрице: {len(layout)}   полных прямоугольников: {full_rects(layout)}")
    print()

    print("  ЛОЖНЫЕ НАЖАТИЯ (3-клавишные рецепты), по убыванию опасности:")
    rows_out = []
    tiers = {}
    for ghost in layout:
        victim = names.get(ghost)
        harm = HARM.get(victim, 1)
        for combo, adj in recipes(layout, ghost).values():
            t = tier(combo, layout)
            likelihood = {"соседние": 3, "близко": 2, "далеко": 1}[t]
            cov = filter_coverage(combo, layout, names)
            tiers.setdefault(t, [0, 0])
            tiers[t][0] += 1
            if cov == 6:
                tiers[t][1] += 1
            rows_out.append((harm * likelihood, victim, nm(combo, names), t, cov))
    rows_out.sort(key=lambda x: (-x[0], x[2]))
    for t, (total, caught) in tiers.items():
        print(f"    [{t:<8}] рецептов {total:3d}, из них фильтр закрывает {caught:3d}")
    print()
    shown = 0
    for score, victim, combo, t, cov in rows_out:
        if score < 6:
            break
        shown += 1
        print(f"    {victim:<6} <- {combo:<22} [{t:<8} вред {score:2d}] фильтр ловит {cov}/6")
    print(f"    ... всего рецептов: {len(rows_out)}, показаны с вредом >= 6: {shown}")
    print()

    print("  ИГРОВЫЕ КОМБИНАЦИИ:")
    for label, keys in GAMING:
        pressed = []
        for name in keys:
            pos = next((p for p, n in names.items() if n == name), None)
            if pos in layout:
                pressed.append(pos)
        raw, out = filter_result([[p for p in pressed[:i + 1]] for i in range(len(pressed))], names) \
            if pressed else (set(), set())
        ghosts = sorted(names.get(k, str(k)) for k in raw if k not in pressed and k in layout)
        print(f"    {label:<28} нажато: {nm(pressed, names):<22} ложные: {ghosts if ghosts else '-'}")
    print()


def scan_demo(layout, names, keynames):
    print(f"{'='*78}\nПОШАГОВЫЙ РАЗБОР СКАНА: зажато {', '.join(keynames)}\n{'='*78}")
    pressed = []
    for name in keynames:
        pos = next((p for p, n in names.items() if n == name), None)
        if pos is not None:
            pressed.append(pos)
    for row in range(ROWS):
        parent = {}

        def find(x):
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for (r, c) in pressed:
            union(("R", r), ("C", c))
        cells = []
        for c in range(COLS):
            if find(("R", row)) == find(("C", c)):
                key = names.get((row, c))
                if key is None:
                    cells.append(f"({row},{c}) пусто")
                elif (row, c) in pressed:
                    cells.append(key)
                else:
                    cells.append(f"{key}  <-- ЛОЖНОЕ")
        print(f"    строка {row}: " + (", ".join(cells) if cells else "ничего"))


def optimise():
    print(f"{'='*78}\nОПТИМИЗАЦИЯ row4\n{'='*78}")
    base = {k: v for k, v in NEXT.items() if k[0] != 4}
    for s_col in range(6):
        for f_col in range(6):
            if s_col == f_col:
                continue
            layout = set(base) | {(4, s_col), (4, f_col)}
            names = {k: v for k, v in base.items()}
            names[(4, s_col)] = "S"
            names[(4, f_col)] = "F"
            n_adj = sum(len(v) for v in
                        [{g: [c for c, _ in recipes(layout, g).values() if _] for g in layout}].__iter__().__next__().values()) \
                if False else sum(1 for g in layout for c, a in recipes(layout, g).values() if a)
            print(f"    S=(4,{s_col}) F=(4,{f_col}): прямоугольников {full_rects(layout):3d}  "
                  f"соседних рецептов {n_adj:3d}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--scan" in args:
        i = args.index("--scan")
        scan_demo(CURRENT, CURRENT, args[i + 1:])
    elif "--optimise" in args:
        optimise()
    elif "--next" in args:
        report(NEXT, NEXT, "СЛЕДУЮЩАЯ РЕВИЗИЯ ПЛАТЫ")
    else:
        report(CURRENT, CURRENT, "ТЕКУЩАЯ ПЛАТА (S и F в row2 homerow)")
