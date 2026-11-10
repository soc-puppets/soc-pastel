// SPDX-License-Identifier: MIT
//
// PASTEL firmware runtime: the API test firmware uses.
//
// A firmware test defines `int test_main(void)`. Returning 0 passes the test;
// any other value fails it. Output from pastel_printf() appears in the host's
// test log. Unexpected exceptions are reported to the host as faults.

#ifndef PASTEL_H
#define PASTEL_H

#include <stdint.h>

#include "pastel/mailbox.h"

// printf subset: %d %i %u %x %X %p %s %c %%, with optional '0' flag and width.
void pastel_printf(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
void pastel_puts(const char *s);

// Send opaque data to the host test (PASTEL_MBOX_DATA).
void pastel_send(const void *data, uint8_t length);

// Turn waveform dumping on (nonzero) or off (0), and wait until the platform
// has done so. Returns 1 if waves are now being dumped; always 0 on a platform
// that cannot dump waves or was not asked to (pytest --waves).
int pastel_waves(int enable);

// Memory-mapped register helpers.
static inline void pastel_write32(uintptr_t addr, uint32_t v) { *(volatile uint32_t *)addr = v; }
static inline uint32_t pastel_read32(uintptr_t addr) { return *(volatile uint32_t *)addr; }

#endif  // PASTEL_H
