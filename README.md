# sofle_diodless — ZMK-конфиг (гибридная матрица 5×6 + 4 прямых GPIO)

Готовый конфиг Zephyr/ZMK для split-клавиатуры на **nice!nano v2 (nRF52840)**.

Схема сканирования — **гибридная**:

```
kscan_matrix  5 ROW × 6 COL (11 GPIO, без диодов)  ──┐
                                                     ├──► zmk,kscan-composite ──► matrix-transform ──► keymap
kscan_direct  4 GPIO → 4 клавиши напрямую на GND     ──┘
```

Из-за этого `GUI`, `LOWER`, `ENTER`, `ENC_BTN` физически изолированы от
матрицы — ghosting для них невозможен в принципе.

---

## 0. Про NKRO и матрицу без диодов — важно прочитать

В матрице **без диодов** две клавиши, делящие общий узел (строку или столбец),
замыкают два выхода микроконтроллера друг на друга. Драйвер ZMK держит
неактивные выходы не в `high-Z`, а принудительно в HIGH:

```c
gpio_pin_set_dt(out, 1);   // сканируем:  выход -> LOW
...читаем входы...
gpio_pin_set_dt(out, 0);   // не сканируем: выход -> HIGH
```

Отсюда жёсткое ограничение: **одновременно можно нажать только те клавиши,
которые не делят ни строку, ни столбец** (как ладьи на шахматной доске).

### Почему выбрано `diode-direction = "row2col"`

| | `col2row` (выходы = COL) | `row2col` (выходы = ROW) ✅ |
|---|---|---|
| КЗ возникает между | двумя **колонками** | двумя **рядами** |
| Ломаются пары | одного **ряда** | одного **столбца** |

Для этой раскладки `col2row` был бы катастрофой:

* **home-row mods** (A+S, S+D, D+F) — все в ряду 2;
* **Shift+Z/X/C/V/B** — SHIFT физически стоит в ряду 3, рядом с Z X C V B;
* ESC+Q, ESC+W, … — любые пары в ряду 1.

После перехода на `row2col` home-row mods и Shift+буква работают.
Остаются конфликты (две клавиши одного столбца):

| Конфликт | Примеры |
|---|---|
| Ctrl (ряд 4, столбец 2) | Ctrl+W, Ctrl+S, Ctrl+X |
| Alt (ряд 4, столбец 1) | Alt+1, Alt+Q, Alt+A, Alt+Z |
| Вертикальные «роллы» | Q+A, W+S, E+D, R+F, T+G |
| ESC (ряд 1, столбец 0) + SHIFT (ряд 3, столбец 0) | ESC+SHIFT |

Прямые клавиши (`GUI`, `LOWER`, `ENTER`, `ENC_BTN`) конфликтуют с чем угодно
никогда — они вообще не в матрице.

### Как получить честный NKRO (любые комбинации)

Три пути, по возрастанию трудозатрат:

1. **Диоды на PCB (ревизия B).** 30 диодов SOT-23/SOD-323 — штатное решение,
   после этого работает стоковый драйвер ZMK без изменений.
2. **Свой kscan-драйвер.** Ключевая идея: переводить неактивные выходы в
   `high-Z` (`gpio_pin_configure_dt(out, GPIO_INPUT)`) вместо HIGH. Тогда
   неактивная колонка ничего не «замыкает», и матрица без диодов даёт полный
   NKRO. Драйвер кладётся в этот же Zephyr-модуль (`zephyr/`), ~150 строк
   поверх `app/module/drivers/kscan/kscan_gpio_matrix.c` из ZMK.
3. **1 GPIO на клавишу.** 26 + 4 = 30 выводов + энкодер + OLED — на nRF52840
   не помещается.

> Смена `diode-direction` **не меняет нумерацию** `(row, col)`: в драйвере
> `state_index_io()` для обоих направлений даёт `(индекс в row-gpios,
> индекс в col-gpios)`. Поэтому `matrix-transform` и `keymap` остаются
> без изменений при переключении ориентации.

---

## 1. Структура репозитория

```
.
├── .github/workflows/build.yml     # GitHub Actions: сборка обеих половин
├── build.yaml                      # матрица сборки (для zmk CLI / общего workflow ZMK)
├── config/
│   ├── west.yml                    # west-манифест (пин на ZMK v0.3)
│   └── sofle_diodless.conf         # пользовательские настройки (NKRO и т.п.)
├── zephyr/
│   └── module.yml                  # описание Zephyr-модуля (board_root)
└── boards/shields/sofle_diodless/
    ├── Kconfig.shield
    ├── Kconfig.defconfig
    ├── sofle_diodless.conf         # предложения по опциям (закомментированы)
    ├── sofle_diodless.dtsi         # общий devicetree: transform, kscan, энкодеры, OLED
    ├── sofle_diodless.keymap       # 4 слоя по 72 биндинга
    ├── sofle_diodless_left.overlay
    ├── sofle_diodless_right.overlay
    └── sofle_diodless.zmk.yml      # метаданные оборудования
```

---

## 2. Распиновка

### Левая половина (15 GPIO)

| Пин | Роль | Функция |
|-----|------|---------|
| `P0.20` | ROW0 | `` `  1  2  3  4  5 |
| `P0.22` | ROW1 | `ESC Q  W  E  R  T` |
| `P0.24` | ROW2 ★ | `TAB A  S  D  F  G` (home row) |
| `P1.00` | ROW3 | `SHIFT Z  X  C  V  B` |
| `P0.11` | ROW4 ★★ | `— ALT CTRL — — —` |
| `P1.04` | COL0 | столбец 0 (внешний край) |
| `P1.06` | COL1 | столбец 1 |
| `P0.31` | COL2 | столбец 2 |
| `P0.29` | COL3 | столбец 3 |
| `P0.02` | COL4 | столбец 4 |
| `P1.15` | COL5 | столбец 5 (край у центра) |
| **`P1.13`** | **DIRECT 🔴** | **GUI** (кнопка на GND) |
| **`P1.11`** | **DIRECT 🔴** | **LOWER** |
| **`P0.10`** | **DIRECT 🔴** | **ENTER** |
| **`P0.09`** | **DIRECT 🔴** | **ENC_BTN** |
| `P1.08` | энкодер A | EC11 (опционально) |
| `P1.09` | энкодер B | EC11 (опционально) |

### Правая половина

Зеркало левой: те же 15 GPIO, но порядок COL **развёрнут**
(`P1.15, P0.02, P0.29, P0.31, P1.06, P1.04`), ROW4 используется на
позициях col3/col4 (`CTRL`, `ALT`), а блок прямых клавиш сдвинут
на `col-offset = 2` → `(5,2)…(5,5)` = `SPACE, RAISE, RGUI, ENC_BTN`.

> ⚠️ Распиновка правой половины выведена из зеркала левой (в исходном ТЗ
> она не была задана). Если на вашей правой PCB выводы разведены иначе —
> правьте `col-gpios` в `sofle_diodless_right.overlay`, `input-gpios` и
> `sofle_diodless_direct_child { col-offset }` соответственно.

### Конфликты nice!nano, отключённые в dtsi

| Вывод | Что мешает | Что сделано |
|-------|-----------|-------------|
| `P0.20` | I2C0 (SCL) | `&pro_micro_i2c { status = "disabled"; }` |
| `P0.17` | I2C0 (SDA) | OLED переведён на **I2C1** |
| `P1.13`, `P0.10`, `P1.11` | SPI1 (SCK/MOSI/MISO) | `&pro_micro_spi { status = "disabled"; }` |

Из-за этого **RGB-подсветка на WS2812 невозможна без правки dtsi**.

---

## 3. Раскладка keymap (4 слоя × 72 позиции)

```
┌──────────────────────────────────────┬──────────────────────────────────────┐
│  `   1   2   3   4   5              │  6   7   8   9   0   —            │
│ ESC  Q   W   E   R   T              │  Y   U   I   O   P   BACKSPACE    │
│ TAB  A   S   D   F   G   ★ home     │  H   J   K   L   ;   '     ★ home │
│ SFT  Z   X   C   V   B              │  N   M   ,   .   /   SHIFT       │
│ —   ALT CTRL  —   —   —             │  —   —   —  CTRL ALT  —           │
│ GUI LOW ENT ENC — —    🔴 direct     │  —   —  SPC RAISE GUI ENC 🔴 direct │
└──────────────────────────────────────┴──────────────────────────────────────┘
```

| Слой | Имя | Как включить |
|------|-----|--------------|
| 0 | `QWERTY` | — |
| 1 | `Lower` | удерживать `LOWER` (левый thumb) |
| 2 | `Raise` | удерживать `RAISE` (правый thumb) |
| 3 | `Adjust` | удерживать `LOWER` + `RAISE` |

Энкодеры (если включены):

| Слой | Поворот → | Поворот ← |
|------|-----------|-----------|
| Default | Vol Up / Page Up | Vol Dn / Page Dn |
| Lower | Brightness Up / Next | Brightness Dn / Prev |
| Raise | Right / Down | Left / Up |

### Про SPACE на левой половине

В заданной схеме все 4 прямых GPIO заняты (`GUI`, `LOWER`, `ENTER`, `ENC_BTN`),
поэтому отдельного `SPACE` слева нет — `SPACE` есть только справа.
**Способы добавить:**

1. Распаять ещё один выключатель на свободной позиции матрицы
   `ROW4 × COL5` (`P0.11` × `P1.15`) — это keymap-позиция **53**.
   Затем в `sofle_diodless.keymap` замените в слое 0 строку `pos 48`:
   ```
   /* pos 48 */ &none &kp LALT &kp LCTRL &none &none &kp SPACE ...
   ```
2. Либо временно использовать комбинацию: в ZMK доступны
   `&kp LGUI(SPACE)`, `&kp LALT(SPACE)` и т.п.

---

## 4. Сборка

### Вариант A — GitHub Actions (рекомендуется)

1. Создайте пустой репозиторий на GitHub и залейте в него эти файлы.
2. Откройте вкладку **Actions** → дождитесь сборки.
3. Скачайте артефакт `firmware`:
   * `sofle-diodless-left.uf2`
   * `sofle-diodless-right.uf2`
4. Прошейте: двойной RESET → USB-накопитель → скопируйте `.uf2`.

Workflow сам определяет корректное имя платы (`nice_nano//zmk` /
`nice_nano_v2` / `nice_nano/nrf52840/zmk`), так что переименование
платы в ZMK его не сломает.

### Вариант B — официальный workflow ZMK

`.github/workflows/build.yml` можно заменить на 4 строки из шаблона ZMK:

```yaml
name: Build ZMK firmware
on: [push, pull_request, workflow_dispatch]
jobs:
  build:
    uses: zmkfirmware/zmk/.github/workflows/build-user-config.yml@v0.3
```

Он прочитает `build.yaml` из корня репозитория.

### Вариант C — локально

```bash
west init -l ./config
west update
west zephyr-export

west build -s zmk/app -d build-left -- \
    -DSHIELD=sofle_diodless_left \
    -DZMK_CONFIG="$PWD/config" \
    -DZMK_EXTRA_MODULES="$PWD"

west build -s zmk/app -d build-right -- \
    -DSHIELD=sofle_diodless_right \
    -DZMK_CONFIG="$PWD/config" \
    -DZMK_EXTRA_MODULES="$PWD"
```

Артефакты: `build-*/zephyr/zmk.uf2`.

---

## 5. Опции

Всё выключено по умолчанию, кроме NKRO. Раскомментируйте нужное:

| Что | Где | Строки |
|-----|-----|--------|
| NKRO (уже включён) | `config/sofle_diodless.conf` | `CONFIG_ZMK_HID_REPORT_TYPE_NKRO=y` |
| OLED SSD1306 (I2C1, `P0.16`/`P0.17`) | `config/sofle_diodless.conf` | `CONFIG_ZMK_DISPLAY=y` |
| Энкодеры EC11 (`P1.08`/`P1.09`) | `config/sofle_diodless.conf` | `CONFIG_EC11=y`, `CONFIG_EC11_TRIGGER_GLOBAL_THREAD=y` |
| Глубокий сон | `config/sofle_diodless.conf` | `CONFIG_ZMK_SLEEP=y` |

---

## 6. Как это работает (для тех, кто будет править)

Позиции композитного kscan:

```
row 0..4  <- kscan_matrix   (0..4, 0..5)
row 5     <- kscan_direct   (5, 0..3)   [левая]
                       (5, 2..5)   [правая, col-offset = 2]
```

Далее `matrix-transform` переводит `(row, col)` в индекс keymap:

* левая половина — без смещения, колонки 0..5;
* правая половина — `col-offset = <6>`, колонки 0..5 → 6..11.

`map` содержит ровно 72 записи и **порядок должен совпадать с порядком
`bindings`** в каждом слое keymap. Если добавляете клавишу — правьте
оба места одновременно.

Позиции матрицы `ROW4 × COL0/COL3/COL4/COL5` (keymap 48, 51, 52, 53)
существуют в transform, но физически не распаяны — они помечены `&none`
и готовы к использованию без изменения прошивки.

---

## 7. Лицензия

MIT (см. заголовки файлов).