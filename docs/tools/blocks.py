"""
Разбивка ложных нажатий по прямоугольникам.

376 рецептов = 94 прямоугольника x 4 (любые 3 угла -> ложно срабатывает 4-й).
Прямоугольники делятся на:
  * СОСЕДНИЕ  - 2x2 из четырёх соседних клеток: человек нажимает их одной рукой,
                это самые реальные случаи;
  * РАСТЯНУТЫЕ - углы разнесены дальше (через строку/столбец): маловероятно.
"""
import itertools

ROWS, COLS = 5, 6
CURRENT = {
    (0, 0): "`", (0, 1): "1", (0, 2): "2", (0, 3): "3", (0, 4): "4", (0, 5): "5",
    (1, 0): "ESC", (1, 1): "Q", (1, 2): "W", (1, 3): "E", (1, 4): "R", (1, 5): "T",
    (2, 0): "TAB", (2, 1): "A", (2, 2): "S", (2, 3): "D", (2, 4): "F", (2, 5): "G",
    (3, 0): "SHIFT", (3, 1): "Z", (3, 2): "X", (3, 3): "C", (3, 4): "V", (3, 5): "B",
    (4, 1): "ALT", (4, 2): "CTRL",
}
HARM = {"CTRL": 5, "ALT": 4, "SHIFT": 4, "S": 4, "F": 3, "GUI": 5, "ENTER": 3}
HARM_DEFAULT = 1   # буква


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


def nm(keys):
    return "+".join(sorted(CURRENT.get(k, str(k)) for k in keys))


rects = []
for r1 in range(ROWS):
    for r2 in range(r1 + 1, ROWS):
        for c1 in range(COLS):
            for c2 in range(c1 + 1, COLS):
                corners = [(r1, c1), (r1, c2), (r2, c1), (r2, c2)]
                if not all(p in CURRENT for p in corners):
                    continue
                if not all(CURRENT[p] is not None for p in corners):
                    continue
                # проверяем, что прямоугольник реально даёт ложное нажатие
                ok = False
                for miss in corners:
                    combo = [p for p in corners if p != miss]
                    if miss in closure(combo):
                        ok = True
                if not ok:
                    continue
                adj = (r2 - r1 == 1) and (c2 - c1 == 1)
                harm = max(HARM.get(CURRENT[p], HARM_DEFAULT) for p in corners)
                rects.append((corners, adj, harm))

adj_rects = [r for r in rects if r[1]]
far_rects = [r for r in rects if not r[1]]

print("=" * 78)
print(f"СОСЕДНИЕ блоки 2x2 (реальные): {len(adj_rects)}   "
      f"РАСТЯНУТЫЕ: {len(far_rects)}   всего {len(rects)} блоков -> {len(rects)*4} рецептов")
print("=" * 78)
print()
print("  Правило: нажали любые 3 из 4 -> четвёртый сработает ложно.")
print("  Урон = что будет нажато лишним.")
print()

for title, group in (("СОСЕДНИЕ блоки (2x2 из соседних клеток)", adj_rects),
                     ("РАСТЯНУТЫЕ блоки (углы разнесены)", far_rects)):
    print(f"--- {title} ---")
    for corners, _, harm in sorted(group, key=lambda x: (-x[2], x[0])):
        keys = [CURRENT[p] for p in corners]
        # что сработает ложно при нажатии каждой тройки
        outs = []
        for miss in corners:
            combo = [p for p in corners if p != miss]
            if miss in closure(combo):
                outs.append(f"без {CURRENT[miss]} -> {CURRENT[miss]}")
        mark = "!!" if harm >= 4 else ("!" if harm >= 3 else " ")
        print(f"  {mark} {{{nm(corners)}}}  урон {harm}: " + "; ".join(outs))
    print()
