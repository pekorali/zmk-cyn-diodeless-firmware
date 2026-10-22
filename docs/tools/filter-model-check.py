#!/usr/bin/env python3
"""Проверка C-реализации фильтра призраков и защиты от залипания.

Файл — построчная транслитерация zephyr/src/kscan_gpio_matrix_hiz.c
(функции hiz_matrix_closure, hiz_ghost_explainable, hiz_filter_ghosts
и отчётного цикла со stuck-key) в Python. Любое расхождение C-кода с
моделью = ошибка.

Запуск: python docs/tools/filter-model-check.py
"""

import itertools
import sys

ROWS, COLS = 5, 6
CELLS = ROWS * COLS

# Текущая раскладка левой половины (matrix 5x6): имя -> (row, col)
KEYS = {
    "`": (0, 0), "1": (0, 1), "2": (0, 2), "3": (0, 3), "4": (0, 4), "5": (0, 5),
    "ESC": (1, 0), "Q": (1, 1), "W": (1, 2), "E": (1, 3), "R": (1, 4), "T": (1, 5),
    "TAB": (2, 0), "A": (2, 1), "S": (2, 2), "D": (2, 3), "F": (2, 4), "G": (2, 5),
    "SFT": (3, 0), "Z": (3, 1), "X": (3, 2), "C": (3, 3), "V": (3, 4), "B": (3, 5),
    "ALT": (4, 1), "CTRL": (4, 2),
}
NAME = {v: k for k, v in KEYS.items()}


def cell(row, col):
    return col * ROWS + row


def cell_name(idx):
    return NAME.get((idx % ROWS, idx // ROWS), f"?({idx % ROWS},{idx // ROWS})")


def bit(i):
    return 1 << i


def closure(mask):
    """hiz_matrix_closure: ячейка читается нажатой, если R_r и C_c в одной
    компоненте графа (нажатые клавиши = рёбра R-C)."""
    parent = list(range(ROWS + COLS))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        a, b = find(a), find(b)
        if a != b:
            parent[a] = b

    for r in range(ROWS):
        for c in range(COLS):
            if mask & bit(cell(r, c)):
                union(r, ROWS + c)

    out = 0
    for r in range(ROWS):
        for c in range(COLS):
            if find(r) == find(ROWS + c):
                out |= bit(cell(r, c))
    return out


def explainable(cell_idx, real_mask, confirmed, press_seq, fresh_mask,
                require_fresh, require_order):
    """hiz_ghost_explainable: цепочка K1=(r,c1) - K2=(r2,c1) - K3=(r2,c)."""
    r = cell_idx % ROWS
    c = cell_idx // ROWS

    for c1 in range(COLS):
        if c1 == c:
            continue
        k1 = cell(r, c1)
        if not (real_mask & bit(k1)):
            continue
        for r2 in range(ROWS):
            if r2 == r:
                continue
            k2 = cell(r2, c1)
            k3 = cell(r2, c)
            if not (real_mask & bit(k2)) or not (real_mask & bit(k3)):
                continue
            chain = bit(k1) | bit(k2) | bit(k3)
            if require_fresh and not (chain & fresh_mask):
                continue
            keys = [k1, k2, k3]
            have_confirmed = False
            last = -1
            ordered = True
            for k in keys:
                if not (confirmed & bit(k)):
                    continue
                have_confirmed = True
                if press_seq[k] < last:
                    ordered = False
                    break
                last = press_seq[k]
            if have_confirmed and (ordered or not require_order):
                return True
    return False


class Filter:
    """Транслитерация hiz_filter_ghosts + логика stuck-key отчётного цикла.

    Дебаунс моделируется идеальным: changed = сменилось ли состояние
    между сканами (для stuck-логики этого достаточно).
    """

    def __init__(self, stuck_timeout_ms=None):
        # --- фильтр (поля kscan_hiz_data) ---
        self.confirmed = 0
        self.suppressed = 0
        self.observed = 0
        self.press_seq = [0] * CELLS
        self.press_counter = 0
        # --- stuck-key ---
        self.stuck_timeout = stuck_timeout_ms
        self.held_since = [0] * CELLS
        self.stuck = 0
        # --- модель отчётного цикла ---
        self.reported_state = 0  # что уже отослано в ZMK
        self.now = 0
        self.events = []  # события последнего скана: [(idx, pressed)]
        self.continue_scan = False

    def filter_ghosts(self, raw):
        self.confirmed &= raw
        self.suppressed &= raw

        # ВОСКРЕШЕНИЕ: подавленная ячейка всё ещё зажата, но цепочки из
        # трёх реальных клавиш, объясняющей её как призрак, больше нет —
        # значит, это настоящее нажатие.
        for i in range(CELLS):
            b = bit(i)
            if not (self.suppressed & b):
                continue
            if explainable(i, self.confirmed, self.confirmed, self.press_seq,
                           0, require_fresh=False, require_order=False):
                continue
            self.suppressed &= ~b

        fresh = raw & ~self.observed
        real = raw & ~self.suppressed

        for i in range(CELLS):
            b = bit(i)
            if not (real & b) or (self.confirmed & b):
                continue
            if not (fresh & b):
                continue
            # ячейка нужна, чтобы объяснить наблюдение -> настоящая
            if closure(real & ~b) != raw:
                continue
            if not explainable(i, real & ~b, self.confirmed, self.press_seq,
                               fresh, require_fresh=True, require_order=True):
                continue
            real &= ~b
            self.suppressed |= b

        added = real & ~self.confirmed
        for i in range(CELLS):
            if added & bit(i):
                self.press_seq[i] = self.press_counter
                self.press_counter += 1

        self.confirmed = real
        self.observed = raw
        return real

    def scan(self, pressed_mask, dt_ms=1):
        """Один цикл kscan_hiz_read: фильтр + отчёт. Возвращает clean-маску."""
        self.now += dt_ms
        clean = self.filter_ghosts(pressed_mask)
        self.events = []
        continue_scan = False

        for i in range(CELLS):
            b = bit(i)
            active = bool(clean & b)
            changed = active != bool(self.reported_state & b)
            report = changed
            pressed = active if changed else bool(self.reported_state & b)
            stuck = False

            if self.stuck_timeout is not None:
                if changed and pressed:
                    self.held_since[i] = self.now

                if self.stuck & b:
                    if active:
                        # Карантин: клавиша физически зажата, события глушим.
                        stuck = True
                        report = False
                    else:
                        # Физическое отпускание: карантин снят, событие
                        # отпускания подавляем (оно уже было отправлено).
                        self.stuck &= ~b
                        report = False
                elif bool(self.reported_state & b) and \
                        self.now - self.held_since[i] >= self.stuck_timeout:
                    # Принудительное отпускание + карантин (проверяется на
                    # каждом скане, как в C: is_pressed, не только changed).
                    report = True
                    pressed = False
                    self.stuck |= b
                    stuck = True

            if report:
                self.events.append((i, pressed))
                if pressed:
                    self.reported_state |= b
                else:
                    self.reported_state &= ~b

            # вклад в continue_scan: активная клавиша, не находящаяся в карантине
            if not stuck and (active or bool(self.reported_state & b)):
                continue_scan = True

        self.continue_scan = continue_scan
        return clean

    def force_idle(self):
        """Скан «всё отпущено» (полезно после сценария с карантином)."""
        return self.scan(closure(0))


def keys_mask(names):
    m = 0
    for n in names:
        r, c = KEYS[n]
        m |= bit(cell(r, c))
    return m


def mask_names(m):
    return sorted(cell_name(i) for i in range(CELLS) if m & bit(i))


FAILS = []


def check(name, cond, detail=""):
    status = "OK " if cond else "FAIL"
    line = f"  [{status}] {name}"
    if not cond and detail:
        line += f"  ({detail})"
    print(line)
    if not cond:
        FAILS.append(name)


def press(f, names, dt=5):
    """Последовательное нажатие: по скану на каждую клавишу."""
    held = 0
    for n in names:
        held |= keys_mask([n])
        f.scan(closure(held), dt_ms=dt)
    return f.scan(closure(held), dt_ms=dt)


def release(f, names, keep=(), dt=5):
    """Отпускание keys из names, оставляя keep."""
    held = keys_mask(keep)
    for n in names:
        if n in keep:
            continue
        f.scan(closure(held), dt_ms=dt)
    return f.scan(closure(held), dt_ms=dt)


# ── Тест 1: 376 рецептов x 6 порядков — выход всегда валидная трактовка ────
#
# Физика: {три угла} и {четыре угла} электрически неразличимы, поэтому для
# КАЖДОГО событие-уголка фильтр обязан угадывать. Честный критерий:
#   * reported ⊆ raw и closure(reported) == raw (выход согласован с электрикой);
#   * из raw убрана максимум одна ячейка (угадавшая трактовка);
#   * после отпускания всего состояние чисто.
#
# Дополнительно считаем статистику: в скольких случаях угадано правильно
# (призрак подавлен), в скольких — «зеркально» (реальная клавиша подавлена,
# призрак прошёл). Это СОБСТВЕННОСТЬ ЭВРИСТИКИ, а не бага: у зеркальных
# рецептов порядок нажатия физически не оставляет сигнала.
print("Тест 1: 376 рецептов фантомов, все порядки нажатия/отпускания")

rects = []
occupied = set(KEYS.values())
for r1, r2 in itertools.combinations(range(ROWS), 2):
    for c1, c2 in itertools.combinations(range(COLS), 2):
        corners = [cell(r1, c1), cell(r1, c2), cell(r2, c1), cell(r2, c2)]
        # пустые ячейки матрицы не нажимаются — прямоугольники только
        # из занятых клавишами углов (94 в GHOST-FILTER.md)
        if all((i % ROWS, i // ROWS) in occupied for i in corners):
            rects.append(corners)

recipes = []
for corners in rects:
    for trio in itertools.combinations(corners, 3):
        ghost = [x for x in corners if x not in trio][0]
        recipes.append((trio, ghost))
assert len(recipes) == 376, len(recipes)

bad = 0
clean = swap = 0
for trio, ghost in recipes:
    for order in itertools.permutations(trio):
        f = Filter()
        held = 0
        for k in order:
            held |= bit(k)
            f.scan(closure(held))
        rep = f.scan(closure(held))  # установившийся скан
        raw = closure(held)
        dropped = raw & ~rep
        # валидность: убрано не больше одной ячейки, выход согласован
        valid = (bin(dropped).count("1") <= 1) and closure(rep) == raw and (rep & ~raw) == 0
        if not valid:
            bad += 1
        elif dropped & bit(ghost):
            clean += 1
        else:
            swap += 1
        # полное отпускание: состояние обязано очиститься
        f.scan(closure(0))
        f.scan(closure(0))
        if f.reported_state or f.confirmed or f.suppressed:
            bad += 1
check("376 x 6: выход всегда валидная трактовка, состояние чистое",
      bad == 0, f"провалов: {bad}")
print(f"       статистика угадывания: чисто {clean}, зеркально {swap} "
      f"({100 * clean / (clean + swap):.0f}% правильных)")

# ── Тест 2: воскрешение при разрыве цепочки ────────────────────────────────
print("Тест 2: воскрешение подавленной клавиши при разрыве цепочки")
f = Filter()
press(f, ["W", "S", "D"])
e = bit(cell(*KEYS["E"]))
rep = f.scan(closure(keys_mask(["W", "S", "D"])))
check("W+S+D: E подавлен", not (rep & e) and bool(f.suppressed & e))

f.scan(closure(keys_mask(["W", "S", "D", "E"])))  # E зажата, остаётся призраком
check("E зажата: цепочка самоподдерживается (D читается через E), E подавлен",
      bool(f.suppressed & e))
# отпускание W: D всё ещё читается через E (квадрат самоподдерживается)
f.scan(closure(keys_mask(["S", "D", "E"])))
check("отпущен W: E всё ещё подавлен (D читается)", bool(f.suppressed & e))
# отпускание S: квадрат разрушился, E физически зажата -> воскрешение
rep = f.scan(closure(keys_mask(["D", "E"])))
check("разрыв цепочки: E воскрешена (confirmed)",
      bool(f.confirmed & e), mask_names(f.confirmed))
check("разрыв цепочки: E не в suppressed", not (f.suppressed & e))
check("разрыв цепочки: E в отчёте как нажатая", bool(rep & e))
check("разрыв цепочки: E в reported_state (ZMK знает)",
      bool(f.reported_state & e))

# ── Тест 3: E не воскресает, пока цепочка жива ─────────────────────────────
print("Тест 3: подавление держится, пока цепочка жива")
f = Filter()
press(f, ["W", "S", "D"])
f.scan(closure(keys_mask(["W", "S", "D", "E"])))
f.scan(closure(keys_mask(["W", "S", "D"])))  # E отпущена
rep = f.scan(closure(keys_mask(["W", "S", "D", "E"])))  # E снова зажата
check("повторное нажатие E при живой цепочке подавлено", not (rep & e))

# ── Тест 4: одновременный 3-ключевой уголок за один скан ───────────────────
print("Тест 4: одновременный уголок W+S+D за один скан")
f = Filter()
rep = f.scan(closure(keys_mask(["W", "S", "D"])))
# C-поведение: при первом скане нет confirmed клавиш -> объяснение
# невозможно -> все 4 ячейки реальные (трактовка "нажали всё")
check("поведение идентично C (все реальные)", rep == closure(keys_mask(["W", "S", "D"])))

# ── Тест 5: защита от залипания ────────────────────────────────────────────
print("Тест 5: stuck-key")
TIMEOUT = 100
f = Filter(stuck_timeout_ms=TIMEOUT)
w = bit(cell(*KEYS["W"]))

f.scan(closure(keys_mask(["W"])), dt_ms=10)  # нажатие отрепорчено
check("нажатие W отрепорчено", bool(f.reported_state & w))

forced = 0
for _ in range(40):  # 40 * 10 = 400 мс > TIMEOUT
    f.scan(closure(keys_mask(["W"])), dt_ms=10)
    if any(i == cell(*KEYS["W"]) and not p for (i, p) in f.events):
        forced += 1
check("принудительное отпускание отправлено", forced >= 1)
check("клавиша в карантине", bool(f.stuck & w))
check("ZMK считает W отпущенной", not (f.reported_state & w))

f.scan(closure(keys_mask(["W"])), dt_ms=10)
presses = [1 for (i, p) in f.events if i == cell(*KEYS["W"]) and p]
check("в карантине повторных нажатий нет", len(presses) == 0)

f.scan(closure(0), dt_ms=10)  # физическое отпускание
check("после отпускания карантин снят", not (f.stuck & w))
dups = [1 for (i, p) in f.events if i == cell(*KEYS["W"]) and not p]
check("дублирующего отпускания нет", len(dups) == 0)

f.scan(closure(keys_mask(["W"])), dt_ms=10)
presses = [1 for (i, p) in f.events if i == cell(*KEYS["W"]) and p]
check("новое нажатие после карантина репортится", len(presses) == 1)

# залипание не мешает фильтру и воскрешению
print("Тест 5b: stuck-key и фильтр призраков независимо")
f = Filter(stuck_timeout_ms=TIMEOUT)
press(f, ["W", "S", "D"])
for _ in range(40):
    f.scan(closure(keys_mask(["W", "S", "D"])), dt_ms=10)
check("W, S, D в карантине (все 3 держались дольше лимита)",
      f.stuck == keys_mask(["W", "S", "D"]))
f.scan(closure(keys_mask(["W", "S", "D"])), dt_ms=10)
rep = f.scan(closure(keys_mask(["D", "E"])))  # W и S отпущены, E зажата
check("после разрыва цепочки E восстанавливается несмотря на карантин W,S",
      bool(f.confirmed & e))

# ── Тест 6: обычная печать — санити ────────────────────────────────────────
print("Тест 6: обычная печать, состояния чистые после каждого слова")
f = Filter(stuck_timeout_ms=None)
ok = True
for word in ["WATER", "GRAVES", "DECAF", "BASES", "TRADES"]:
    prev = set()
    for ch in word:
        r, c = KEYS[ch]
        prev.add((r, c))
        f.scan(closure(sum(bit(cell(rr, cc)) for rr, cc in prev)), dt_ms=5)
        f.scan(closure(sum(bit(cell(rr, cc)) for rr, cc in prev)), dt_ms=5)
    f.scan(closure(0), dt_ms=5)
    f.scan(closure(0), dt_ms=5)
    if f.reported_state or f.confirmed or f.suppressed or f.stuck:
        ok = False
check("после каждого слова confirmed/suppressed/stuck чисты", ok)

print()
if FAILS:
    print(f"ПРОВАЛЕНО ({len(FAILS)}): {FAILS}")
    sys.exit(1)
print("ВСЕ ТЕСТЫ ПРОЙДЕНЫ")
