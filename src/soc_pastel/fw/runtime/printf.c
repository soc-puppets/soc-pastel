// SPDX-License-Identifier: MIT
//
// printf, and formatting into buffers, for test firmware. Supports
// %d %i %u %x %X %p %s %c %% with an optional '0' flag and width.
#include <stdarg.h>
#include <stdint.h>

#include "pastel/pastel.h"
#include "rtt.h"

#define TEXT_CHANNEL 0

static char line[128];
static uint32_t used;

static void flush(void) {
  if (used) pastel_rtt_write(TEXT_CHANNEL, line, used);
  used = 0;
}

static void out(char c) {
  line[used++] = c;
  if (c == '\n' || used == sizeof line) flush();
}

// A destination for formatted characters: the RTT text channel, or a buffer.
typedef struct {
  void (*put)(void *ctx, char c);
  void *ctx;
} sink_t;

static void put_line(void *ctx, char c) {
  (void)ctx;
  out(c);
}

static void out_num(const sink_t *k, uint32_t v, unsigned base, int upper, int neg, int width,
                    char pad) {
  char tmp[11];
  int n = 0;
  const char *digits = upper ? "0123456789ABCDEF" : "0123456789abcdef";
  do {
    tmp[n++] = digits[v % base];
    v /= base;
  } while (v);
  int len = n + neg;
  if (neg && pad == '0') k->put(k->ctx, '-');
  for (; len < width; len++) k->put(k->ctx, pad);
  if (neg && pad != '0') k->put(k->ctx, '-');
  while (n) k->put(k->ctx, tmp[--n]);
}

static void format(const sink_t *k, const char *fmt, va_list ap) {
  for (; *fmt; fmt++) {
    if (*fmt != '%') {
      k->put(k->ctx, *fmt);
      continue;
    }
    char pad = ' ';
    int width = 0;
    if (*++fmt == '0') {
      pad = '0';
      fmt++;
    }
    while (*fmt >= '0' && *fmt <= '9') width = width * 10 + (*fmt++ - '0');
    switch (*fmt) {
      case 'd':
      case 'i': {
        int32_t v = va_arg(ap, int32_t);
        out_num(k, v < 0 ? (uint32_t)-v : (uint32_t)v, 10, 0, v < 0, width, pad);
        break;
      }
      case 'u': out_num(k, va_arg(ap, uint32_t), 10, 0, 0, width, pad); break;
      case 'x': out_num(k, va_arg(ap, uint32_t), 16, 0, 0, width, pad); break;
      case 'X': out_num(k, va_arg(ap, uint32_t), 16, 1, 0, width, pad); break;
      case 'p':
        k->put(k->ctx, '0');
        k->put(k->ctx, 'x');
        out_num(k, (uint32_t)va_arg(ap, void *), 16, 0, 0, 8, '0');
        break;
      case 'c': k->put(k->ctx, (char)va_arg(ap, int)); break;
      case 's': {
        const char *s = va_arg(ap, const char *);
        while (*s) k->put(k->ctx, *s++);
        break;
      }
      case '%': k->put(k->ctx, '%'); break;
      case '\0': fmt--; break;
      default: k->put(k->ctx, '%'); k->put(k->ctx, *fmt);
    }
  }
}

void pastel_printf(const char *fmt, ...) {
  sink_t k = {put_line, 0};
  va_list ap;
  va_start(ap, fmt);
  format(&k, fmt, ap);
  va_end(ap);
  flush();
}

void pastel_info(const char *fmt, ...) {
  sink_t k = {put_line, 0};
  va_list ap;
  va_start(ap, fmt);
  format(&k, fmt, ap);
  va_end(ap);
  out('\n');
  flush();
}

typedef struct {
  char *buf;
  uint32_t size, used;
} buffer_t;

static void put_buffer(void *ctx, char c) {
  buffer_t *b = (buffer_t *)ctx;
  if (b->used + 1 < b->size) b->buf[b->used] = c;
  b->used++;
}

int pastel_vsnprintf(char *buf, uint32_t size, const char *fmt, va_list ap) {
  buffer_t b = {buf, size, 0};
  sink_t k = {put_buffer, &b};
  format(&k, fmt, ap);
  if (size) buf[b.used < size ? b.used : size - 1] = '\0';
  return (int)b.used;
}

int pastel_snprintf(char *buf, uint32_t size, const char *fmt, ...) {
  va_list ap;
  va_start(ap, fmt);
  int n = pastel_vsnprintf(buf, size, fmt, ap);
  va_end(ap);
  return n;
}

void pastel_puts(const char *s) {
  while (*s) out(*s++);
  out('\n');
  flush();
}
