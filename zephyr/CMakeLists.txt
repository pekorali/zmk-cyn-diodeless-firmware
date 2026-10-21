# Copyright (c) 2026, sofle_diodless contributors
# SPDX-License-Identifier: MIT

# The high-Z matrix scanner for diodeless matrices.
# See src/kscan_gpio_matrix_hiz.c for what it does and why.
zephyr_library_named(kscan_matrix_hiz)

zephyr_library_sources_ifdef(CONFIG_ZMK_KSCAN_GPIO_MATRIX_HIZ src/kscan_gpio_matrix_hiz.c)
