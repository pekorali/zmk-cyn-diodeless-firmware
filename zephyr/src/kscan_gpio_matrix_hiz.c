/*
 * Copyright (c) 2026, sofle_diodless contributors
 * SPDX-License-Identifier: MIT
 */

/*
 * kscan-gpio-matrix-hiz — GPIO matrix scanner for DIODELESS matrices.
 *
 * ─────────────────────────────────────────────────────────────────────────
 *  ЗАЧЕМ ЭТОТ ДРАЙВЕР
 * ─────────────────────────────────────────────────────────────────────────
 *  В матрице без диодов две клавиши, делящие общий узел (строку или
 *  столбец), соединяют два выхода микроконтроллера друг на друга.
 *
 *  Штатный драйвер ZMK (kscan_gpio_matrix.c) в нерабочем состоянии
 *  переводит неактивные выходы не в high-Z, а в активный по напряжению
 *  уровень:
 *
 *      gpio_pin_set_dt(out, 1);   // сканируем:  выход -> LOW
 *      ... читаем входы ...
 *      gpio_pin_set_dt(out, 0);   // не сканируем: выход -> HIGH
 *
 *  => при нажатии двух клавиш одного ряда (или столбца) два выхода МК
 *     оказываются замкнуты друг на друга. Пина общего узла уезжает в
 *     ~Vcc/2, оба ключа читаются как отпущенные.
 *
 *  Этот драйвер вместо HIGH переводит неактивные выходы в high-Z
 *  (плавающий вход без подтяжки):
 *
 *      gpio_pin_configure(port, pin, GPIO_INPUT);   // high-Z
 *
 *  Тогда неактивный выход ничего не замыкает, и матрица без диодов
 *  работает как честная NKRO-матрица: нажимать можно ЛЮБУЮ комбинацию
 *  клавиш, в том числе несколько клавиш одного ряда или столбца.
 *
 * ─────────────────────────────────────────────────────────────────────────
 *  ЭЛЕКТРИКА
 * ─────────────────────────────────────────────────────────────────────────
 *  Во время сканирования драйвер держит ОДИН выход активным (LOW), а
 *  остальные — в high-Z:
 *
 *      row0 = LOW, row1..row4 = high-Z   (сканируем строку 0)
 *
 *  • клавиша в сканируемой строке -> её столбец читается LOW  (нажата)
 *  • клавиша в другой строке       -> её столбец читается HIGH (не нажата,
 *                                     т.к. та строка ничего не тянет)
 *
 *  В состоянии покоя (для пробуждения по прерыванию) все выходы
 *  переводятся в ОДИН уровень (LOW). Тогда:
 *  • КЗ между выходами невозможно (все на одном уровне);
 *  • нажатая клавиша тянет свой вход в LOW -> срабатывает прерывание.
 *
 * ─────────────────────────────────────────────────────────────────────────
 *  НАПРАВЛЕНИЕ СКАНИРОВАНИЯ
 * ─────────────────────────────────────────────────────────────────────────
 *  row-gpios = ВЫХОДЫ (стробы), col-gpios = ВХОДЫ (с PULL_UP).
 *  Нумерация (row, col), которую видит matrix-transform, совпадает с
 *  нумерацией штатного драйвера: (индекс в row-gpios, индекс в col-gpios).
 */

#define DT_DRV_COMPAT zmk_kscan_gpio_matrix_hiz

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include <zephyr/device.h>
#include <zephyr/devicetree.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/kscan.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/pm/device.h>
#include <zephyr/pm/device_runtime.h>
#include <zephyr/sys/__assert.h>
#include <zephyr/sys/util.h>

#include <zmk/debounce.h>

/* CONFIG_ZMK_LOG_LEVEL always exists (ZMK's own drivers use it too). */
LOG_MODULE_REGISTER(kscan_gpio_matrix_hiz, CONFIG_ZMK_LOG_LEVEL);

/* -------------------------------------------------------------------------
 * GPIO helpers
 * ------------------------------------------------------------------------- */

struct hiz_gpio {
    struct gpio_dt_spec spec;
    size_t index;
};

struct hiz_gpio_list {
    struct hiz_gpio *gpios;
    size_t len;
};

struct hiz_port_state {
    const struct device *port;
    gpio_port_value_t value;
};

#define HIZ_GPIO_GET_BY_IDX(node_id, prop, idx)                                                     \
    ((struct hiz_gpio){.spec = GPIO_DT_SPEC_GET_BY_IDX(node_id, prop, idx), .index = idx})

#define HIZ_GPIO_LIST(gpio_array)                                                                 \
    ((struct hiz_gpio_list){.gpios = gpio_array, .len = ARRAY_SIZE(gpio_array)})

/**
 * Read one input pin. The whole port is read at once, which is possible
 * because gpio_port_get() applies the per-pin inversion mask, so the bits
 * we get back are already logical values.
 */
static int hiz_gpio_pin_get(const struct hiz_gpio *gpio, struct hiz_port_state *state) {
    if (state->port != gpio->spec.port) {
        gpio_port_value_t value = 0;
        int err = gpio_port_get(gpio->spec.port, &value);
        if (err) {
            return err;
        }
        state->port = gpio->spec.port;
        state->value = value;
    }

    return (state->value & (gpio_port_pins_t)BIT(gpio->spec.pin)) ? 1 : 0;
}

/**
 * The physical level which means "active" for this pin.
 * GPIO_ACTIVE_LOW is (1 << 0) in the devicetree flags, GPIO_ACTIVE_HIGH is 0.
 */
static int hiz_active_level(const struct hiz_gpio *gpio) {
    return (gpio->spec.dt_flags & GPIO_ACTIVE_LOW) ? 0 : 1;
}

/** Drive an output to its active level. */
static int hiz_output_activate(const struct hiz_gpio *gpio) {
    /* Set the output latch first, so the pin starts driving the right
     * level the instant it becomes an output (no glitch). */
    int err = gpio_pin_set_raw(gpio->spec.port, gpio->spec.pin, hiz_active_level(gpio));
    if (err) {
        return err;
    }

    return gpio_pin_configure(gpio->spec.port, gpio->spec.pin, GPIO_OUTPUT);
}

/** Put an output in high-Z: a floating input, without any pull. */
static int hiz_output_release(const struct hiz_gpio *gpio) {
    return gpio_pin_configure(gpio->spec.port, gpio->spec.pin, GPIO_INPUT);
}

/* -------------------------------------------------------------------------
 * Driver structures
 * ------------------------------------------------------------------------- */

struct kscan_hiz_config {
    struct hiz_gpio_list inputs;
    struct hiz_gpio_list outputs;
    struct zmk_debounce_config debounce_config;
    size_t rows;
    size_t cols;
    int32_t debounce_scan_period_ms;
    int32_t poll_period_ms;
};

struct kscan_hiz_irq_callback {
    const struct device *dev;
    struct gpio_callback callback;
};

struct kscan_hiz_data {
    const struct device *dev;
    struct kscan_hiz_irq_callback *irqs;
    struct zmk_debounce_state *matrix_state;
    kscan_callback_t callback;
    struct k_work_delayable work;
    int64_t scan_time;
};

/* -------------------------------------------------------------------------
 * Forward declarations
 * ------------------------------------------------------------------------- */

static int kscan_hiz_interrupt_enable(const struct device *dev);
static int kscan_hiz_interrupt_disable(const struct device *dev);
static void kscan_hiz_irq_callback_handler(const struct device *port, struct gpio_callback *cb,
                                           gpio_port_pins_t pin);

/* -------------------------------------------------------------------------
 * Pin setup
 * ------------------------------------------------------------------------- */

static int hiz_setup_inputs(const struct device *dev) {
    const struct kscan_hiz_config *cfg = dev->config;

    for (size_t i = 0; i < cfg->inputs.len; i++) {
        const struct hiz_gpio *gpio = &cfg->inputs.gpios[i];

        if (!device_is_ready(gpio->spec.port)) {
            LOG_ERR("GPIO is not ready: %s", gpio->spec.port->name);
            return -ENODEV;
        }

        int err = gpio_pin_configure_dt(&gpio->spec, GPIO_INPUT);
        if (err) {
            LOG_ERR("Unable to configure pin %u on %s for input", gpio->spec.pin,
                    gpio->spec.port->name);
            return err;
        }
    }

    return 0;
}

static int hiz_setup_outputs(const struct device *dev) {
    const struct kscan_hiz_config *cfg = dev->config;

    for (size_t i = 0; i < cfg->outputs.len; i++) {
        const struct hiz_gpio *gpio = &cfg->outputs.gpios[i];

        if (!device_is_ready(gpio->spec.port)) {
            LOG_ERR("GPIO is not ready: %s", gpio->spec.port->name);
            return -ENODEV;
        }

        /* Start out in high-Z. */
        int err = hiz_output_release(gpio);
        if (err) {
            LOG_ERR("Unable to release pin %u on %s", gpio->spec.pin, gpio->spec.port->name);
            return err;
        }
    }

    return 0;
}

static int hiz_setup_interrupts(const struct device *dev) {
    const struct kscan_hiz_config *cfg = dev->config;
    struct kscan_hiz_data *data = dev->data;

    for (size_t i = 0; i < cfg->inputs.len; i++) {
        const struct hiz_gpio *gpio = &cfg->inputs.gpios[i];
        struct kscan_hiz_irq_callback *irq = &data->irqs[i];

        irq->dev = dev;
        gpio_init_callback(&irq->callback, kscan_hiz_irq_callback_handler, BIT(gpio->spec.pin));
        int err = gpio_add_callback(gpio->spec.port, &irq->callback);
        if (err) {
            LOG_ERR("Error adding the callback to the input device: %i", err);
            return err;
        }
    }

    return 0;
}

/* -------------------------------------------------------------------------
 * Scanning
 * ------------------------------------------------------------------------- */

static void kscan_hiz_read_continue(const struct device *dev) {
    const struct kscan_hiz_config *cfg = dev->config;
    struct kscan_hiz_data *data = dev->data;

    data->scan_time += cfg->debounce_scan_period_ms;

    k_work_reschedule(&data->work, K_TIMEOUT_ABS_MS(data->scan_time));
}

static int kscan_hiz_read_end(const struct device *dev) {
    const struct kscan_hiz_config *cfg = dev->config;
    struct kscan_hiz_data *data = dev->data;

    data->scan_time += cfg->poll_period_ms;

    k_work_reschedule(&data->work, K_TIMEOUT_ABS_MS(data->scan_time));

    return kscan_hiz_interrupt_enable(dev);
}

/**
 * Put all outputs into the idle state: all of them at the same (active)
 * level. This is safe (no output can short another one) and a pressed key
 * pulls its input to the active level, which raises the interrupt.
 */
static int kscan_hiz_set_all_outputs_active(const struct device *dev) {
    const struct kscan_hiz_config *cfg = dev->config;

    for (size_t i = 0; i < cfg->outputs.len; i++) {
        int err = hiz_output_activate(&cfg->outputs.gpios[i]);
        if (err) {
            return err;
        }
    }

    return 0;
}

static int kscan_hiz_interrupt_enable(const struct device *dev) {
    const struct kscan_hiz_config *cfg = dev->config;

    int err = kscan_hiz_set_all_outputs_active(dev);
    if (err) {
        return err;
    }

    for (size_t i = 0; i < cfg->inputs.len; i++) {
        err = gpio_pin_interrupt_configure_dt(&cfg->inputs.gpios[i].spec, GPIO_INT_LEVEL_ACTIVE);
        if (err) {
            LOG_ERR("Error configuring the interrupt to %u on %s: %i", cfg->inputs.gpios[i].spec.pin,
                    cfg->inputs.gpios[i].spec.port->name, err);
            return err;
        }
    }

    return 0;
}

static int kscan_hiz_interrupt_disable(const struct device *dev) {
    const struct kscan_hiz_config *cfg = dev->config;

    for (size_t i = 0; i < cfg->inputs.len; i++) {
        int err = gpio_pin_interrupt_configure_dt(&cfg->inputs.gpios[i].spec, GPIO_INT_DISABLE);
        if (err) {
            return err;
        }
    }

    return 0;
}

static int kscan_hiz_read(const struct device *dev) {
    const struct kscan_hiz_config *cfg = dev->config;
    struct kscan_hiz_data *data = dev->data;

    /* Everything in high-Z to begin with. */
    for (size_t i = 0; i < cfg->outputs.len; i++) {
        int err = hiz_output_release(&cfg->outputs.gpios[i]);
        if (err) {
            LOG_ERR("Failed to release output %zu: %d", i, err);
            return err;
        }
    }

    for (size_t i = 0; i < cfg->outputs.len; i++) {
        /* Only this output drives the matrix, the rest are floating. */
        int err = hiz_output_activate(&cfg->outputs.gpios[i]);
        if (err) {
            LOG_ERR("Failed to activate output %zu: %d", i, err);
            return err;
        }

        struct hiz_port_state state = {0};

        for (size_t j = 0; j < cfg->inputs.len; j++) {
            const struct hiz_gpio *in = &cfg->inputs.gpios[j];

            int active = hiz_gpio_pin_get(in, &state);
            if (active < 0) {
                LOG_ERR("Failed to read port %s: %i", in->spec.port->name, active);
                return active;
            }

            /* row-gpios are the outputs, col-gpios are the inputs. */
            const size_t r = i;
            const size_t c = j;
            const size_t index = (c * cfg->rows) + r;

            zmk_debounce_update(&data->matrix_state[index], active, cfg->debounce_scan_period_ms,
                                &cfg->debounce_config);
        }

        /* Release it again before strobing the next one. */
        err = hiz_output_release(&cfg->outputs.gpios[i]);
        if (err) {
            LOG_ERR("Failed to release output %zu: %d", i, err);
            return err;
        }
    }

    /* Report the changes. */
    bool continue_scan = false;

    for (size_t r = 0; r < cfg->rows; r++) {
        for (size_t c = 0; c < cfg->cols; c++) {
            const size_t index = (c * cfg->rows) + r;
            struct zmk_debounce_state *state = &data->matrix_state[index];

            if (zmk_debounce_get_changed(state)) {
                const bool pressed = zmk_debounce_is_pressed(state);

                LOG_DBG("Sending event at %zu,%zu state %s", r, c, pressed ? "on" : "off");
                if (data->callback) {
                    data->callback(dev, r, c, pressed);
                }
            }

            continue_scan = continue_scan || zmk_debounce_is_active(state);
        }
    }

    if (continue_scan) {
        /* At least one key is pressed (or the debouncer has not decided yet).
         * Poll quickly until everything is released. */
        kscan_hiz_read_continue(dev);
    } else {
        /* All keys are released. Return to the idle state. */
        kscan_hiz_read_end(dev);
    }

    return 0;
}

static void kscan_hiz_work_handler(struct k_work *work) {
    struct k_work_delayable *dwork = k_work_delayable_from_work(work);
    struct kscan_hiz_data *data = CONTAINER_OF(dwork, struct kscan_hiz_data, work);

    kscan_hiz_read(data->dev);
}

/* -------------------------------------------------------------------------
 * KSCAN API
 * ------------------------------------------------------------------------- */

static int kscan_hiz_configure(const struct device *dev, kscan_callback_t callback) {
    struct kscan_hiz_data *data = dev->data;

    if (!callback) {
        return -EINVAL;
    }

    data->callback = callback;

    return 0;
}

static int kscan_hiz_enable(const struct device *dev) {
    struct kscan_hiz_data *data = dev->data;

    data->scan_time = k_uptime_get();

    return kscan_hiz_read(dev);
}

static int kscan_hiz_disable(const struct device *dev) {
    struct kscan_hiz_data *data = dev->data;

    /* No new scans from interrupts while we are disabled. */
    kscan_hiz_interrupt_disable(dev);

    k_work_cancel_delayable(&data->work);

    return 0;
}

static void kscan_hiz_irq_callback_handler(const struct device *port, struct gpio_callback *cb,
                                           gpio_port_pins_t pin) {
    struct kscan_hiz_irq_callback *irq = CONTAINER_OF(cb, struct kscan_hiz_irq_callback, callback);
    const struct device *dev = irq->dev;
    struct kscan_hiz_data *data = dev->data;

    /* Disable the interrupts to avoid re-entrancy while we scan. */
    kscan_hiz_interrupt_disable(dev);

    data->scan_time = k_uptime_get();

    k_work_reschedule(&data->work, K_NO_WAIT);
}

static const struct kscan_driver_api kscan_hiz_api = {
    .config = kscan_hiz_configure,
    .enable_callback = kscan_hiz_enable,
    .disable_callback = kscan_hiz_disable,
};

/* -------------------------------------------------------------------------
 * Power management
 * ------------------------------------------------------------------------- */

#if IS_ENABLED(CONFIG_PM_DEVICE)
static int kscan_hiz_pm_action(const struct device *dev, enum pm_device_action action) {
    switch (action) {
    case PM_DEVICE_ACTION_SUSPEND:
        return kscan_hiz_disable(dev);
    case PM_DEVICE_ACTION_RESUME:
        return kscan_hiz_enable(dev);
    default:
        return -ENOTSUP;
    }
}

#define KSCAN_HIZ_PM_ACTION(n) PM_DEVICE_DT_INST_DEFINE(n, kscan_hiz_pm_action);
#define KSCAN_HIZ_PM_GET(n) PM_DEVICE_DT_INST_GET(n)
#else
#define KSCAN_HIZ_PM_ACTION(n)
#define KSCAN_HIZ_PM_GET(n) NULL
#endif

/* -------------------------------------------------------------------------
 * Device instantiation
 * ------------------------------------------------------------------------- */

#define INST_ROWS_LEN(n) DT_INST_PROP_LEN(n, row_gpios)
#define INST_COLS_LEN(n) DT_INST_PROP_LEN(n, col_gpios)
#define INST_MATRIX_LEN(n) (INST_ROWS_LEN(n) * INST_COLS_LEN(n))
#define INST_INPUTS_LEN(n) INST_COLS_LEN(n)

#define INST_DEBOUNCE_PRESS_MS(n) DT_INST_PROP_OR(n, debounce_press_ms, 5)
#define INST_DEBOUNCE_RELEASE_MS(n) DT_INST_PROP_OR(n, debounce_release_ms, 5)

#define HIZ_GPIO_ROW_CFG_INIT(idx, n) HIZ_GPIO_GET_BY_IDX(DT_DRV_INST(n), row_gpios, idx)
#define HIZ_GPIO_COL_CFG_INIT(idx, n) HIZ_GPIO_GET_BY_IDX(DT_DRV_INST(n), col_gpios, idx)

#define KSCAN_HIZ_INIT(n)                                                                          \
    BUILD_ASSERT(INST_ROWS_LEN(n) > 0, "row-gpios must not be empty");                             \
    BUILD_ASSERT(INST_COLS_LEN(n) > 0, "col-gpios must not be empty");                             \
    BUILD_ASSERT(INST_DEBOUNCE_PRESS_MS(n) <= DEBOUNCE_COUNTER_MAX,                                \
                 "debounce-press-ms is too large");                                               \
    BUILD_ASSERT(INST_DEBOUNCE_RELEASE_MS(n) <= DEBOUNCE_COUNTER_MAX,                              \
                 "debounce-release-ms is too large");                                             \
                                                                                                   \
    static struct hiz_gpio kscan_hiz_rows_##n[] = {                                               \
        LISTIFY(INST_ROWS_LEN(n), HIZ_GPIO_ROW_CFG_INIT, (, ), n)};                                \
    static struct hiz_gpio kscan_hiz_cols_##n[] = {                                               \
        LISTIFY(INST_COLS_LEN(n), HIZ_GPIO_COL_CFG_INIT, (, ), n)};                                \
    static struct zmk_debounce_state kscan_hiz_state_##n[INST_MATRIX_LEN(n)];                       \
    static struct kscan_hiz_irq_callback kscan_hiz_irqs_##n[INST_INPUTS_LEN(n)];                     \
                                                                                                   \
    static struct kscan_hiz_data kscan_hiz_data_##n = {                                           \
        .irqs = kscan_hiz_irqs_##n,                                                               \
        .matrix_state = kscan_hiz_state_##n,                                                      \
    };                                                                                            \
                                                                                                   \
    static const struct kscan_hiz_config kscan_hiz_config_##n = {                                 \
        .inputs = HIZ_GPIO_LIST(kscan_hiz_cols_##n),                                              \
        .outputs = HIZ_GPIO_LIST(kscan_hiz_rows_##n),                                             \
        .rows = ARRAY_SIZE(kscan_hiz_rows_##n),                                                   \
        .cols = ARRAY_SIZE(kscan_hiz_cols_##n),                                                   \
        .debounce_config =                                                                        \
            {                                                                                      \
                .debounce_press_ms = INST_DEBOUNCE_PRESS_MS(n),                                   \
                .debounce_release_ms = INST_DEBOUNCE_RELEASE_MS(n),                               \
            },                                                                                     \
        .debounce_scan_period_ms = DT_INST_PROP_OR(n, debounce_scan_period_ms, 1),                 \
        .poll_period_ms = DT_INST_PROP_OR(n, poll_period_ms, 10),                                 \
    };                                                                                            \
                                                                                                   \
    static int kscan_hiz_init_##n(const struct device *dev) {                                    \
        struct kscan_hiz_data *data = dev->data;                                                  \
        data->dev = dev;                                                                          \
        k_work_init_delayable(&data->work, kscan_hiz_work_handler);                               \
        int err = hiz_setup_inputs(dev);                                                          \
        if (err) {                                                                                \
            return err;                                                                           \
        }                                                                                         \
        err = hiz_setup_outputs(dev);                                                             \
        if (err) {                                                                                \
            return err;                                                                           \
        }                                                                                         \
        return hiz_setup_interrupts(dev);                                                         \
    }                                                                                             \
                                                                                                   \
    KSCAN_HIZ_PM_ACTION(n)                                                                        \
                                                                                                   \
    DEVICE_DT_INST_DEFINE(n, kscan_hiz_init_##n, KSCAN_HIZ_PM_GET(n), &kscan_hiz_data_##n,          \
                          &kscan_hiz_config_##n, POST_KERNEL, CONFIG_KSCAN_INIT_PRIORITY,           \
                          &kscan_hiz_api);

DT_INST_FOREACH_STATUS_OKAY(KSCAN_HIZ_INIT)
