// SPDX-License-Identifier: MIT
//
// PASTEL firmware runtime: the API test firmware uses.
//
// A firmware test defines `int test_main(void)`. Returning 0 passes the test;
// any other value fails it. Output from pastel_printf() appears in the host's
// test log. Unexpected exceptions are reported to the host as faults.

#ifndef PASTEL_H
#define PASTEL_H

#include <stdarg.h>
#include <stdint.h>

#include "pastel/mailbox.h"

// printf subset: %d %i %u %x %X %p %s %c %%, with optional '0' flag, width, and
// the l, h, z, t length modifiers (so %lu and PRIu32 work); not ll (64-bit).
void pastel_printf(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
void pastel_puts(const char *s);
// Format into buf (always NUL-terminated if size > 0); return the full length.
int pastel_snprintf(char *buf, uint32_t size, const char *fmt, ...)
    __attribute__((format(printf, 3, 4)));
int pastel_vsnprintf(char *buf, uint32_t size, const char *fmt, va_list ap);

// --- Messages, errors and checks ------------------------------------------------
//
// PASTEL_INFO(fmt, ...)         print a line (pastel_printf plus a newline)
// PASTEL_ERROR(fmt, ...)        report an error and carry on; the test fails
// PASTEL_CHECK(expr[, fmt, ...]) if expr is false, report
//                               "file.c:12: CHECK failed: expr: message" and
//                               carry on; the test fails
// PASTEL_ASSERT(expr[, fmt, ...]) the same, then end the test at once
//
// Errors go to the host as they happen, so they appear in the test log at
// once and fail the test even if test_main returns 0. Messages longer than
// 255 bytes are cut short.
#define PASTEL_INFO(...) pastel_info(__VA_ARGS__)
#define PASTEL_ERROR(...) pastel_error(__VA_ARGS__)
#define PASTEL_CHECK(expr, ...)                                                    \
  do {                                                                             \
    if (!(expr)) pastel_check_failed(0, __FILE__, __LINE__, #expr, "" __VA_ARGS__); \
  } while (0)
#define PASTEL_ASSERT(expr, ...)                                                   \
  do {                                                                             \
    if (!(expr)) pastel_check_failed(1, __FILE__, __LINE__, #expr, "" __VA_ARGS__); \
  } while (0)

void pastel_info(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
void pastel_error(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
void pastel_check_failed(int fatal, const char *file, int line, const char *expr,
                         const char *fmt, ...) __attribute__((format(printf, 5, 6)));

// Send opaque data to the host test (PASTEL_MBOX_DATA).
void pastel_send(const void *data, uint8_t length);

// Wait for data the host test sends (firmware.send()), and copy up to
// `capacity` bytes of it into buf. Returns the length the host sent, which
// may be more than `capacity` (the rest is dropped). Messages arrive in the
// order sent; ones that arrive while the firmware waits at a checkpoint are
// kept (up to PASTEL_HOST_DATA_QUEUE bytes in all) until it asks for them.
int pastel_receive(void *buf, uint8_t capacity);
// Wait for a 32-bit value the host sends (firmware.send_u32()).
uint32_t pastel_receive_u32(void);

#ifndef PASTEL_HOST_DATA_QUEUE
#define PASTEL_HOST_DATA_QUEUE 512u
#endif

// Turn waveform dumping on (nonzero) or off (0), and wait until the platform
// has done so. Returns 1 if waves are now being dumped: always 0 on a platform
// that cannot dump waves or was not asked to (pytest --waves), and always 1 on
// a simulator that cannot pause dumping (Verilator).
int pastel_waves(int enable);

// Memory-mapped register helpers.
static inline void pastel_write32(uintptr_t addr, uint32_t v) { *(volatile uint32_t *)addr = v; }
static inline uint32_t pastel_read32(uintptr_t addr) { return *(volatile uint32_t *)addr; }

#endif  // PASTEL_H
