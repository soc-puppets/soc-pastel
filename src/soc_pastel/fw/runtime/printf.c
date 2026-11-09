// SPDX-License-Identifier: MIT
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

static void out_num(uint32_t v, unsigned base, int upper, int neg, int width, char pad) {
  char tmp[11];
  int n = 0;
  const char *digits = upper ? "0123456789ABCDEF" : "0123456789abcdef";
  do {
    tmp[n++] = digits[v % base];
    v /= base;
  } while (v);
  int len = n + neg;
  if (neg && pad == '0') out('-');
  for (; len < width; len++) out(pad);
  if (neg && pad != '0') out('-');
  while (n) out(tmp[--n]);
}

void pastel_printf(const char *fmt, ...) {
  va_list ap;
  va_start(ap, fmt);
  for (; *fmt; fmt++) {
    if (*fmt != '%') {
      out(*fmt);
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
        out_num(v < 0 ? (uint32_t)-v : (uint32_t)v, 10, 0, v < 0, width, pad);
        break;
      }
      case 'u': out_num(va_arg(ap, uint32_t), 10, 0, 0, width, pad); break;
      case 'x': out_num(va_arg(ap, uint32_t), 16, 0, 0, width, pad); break;
      case 'X': out_num(va_arg(ap, uint32_t), 16, 1, 0, width, pad); break;
      case 'p': out('0'); out('x'); out_num((uint32_t)va_arg(ap, void *), 16, 0, 0, 8, '0'); break;
      case 'c': out((char)va_arg(ap, int)); break;
      case 's': {
        const char *s = va_arg(ap, const char *);
        while (*s) out(*s++);
        break;
      }
      case '%': out('%'); break;
      case '\0': fmt--; break;
      default: out('%'); out(*fmt);
    }
  }
  va_end(ap);
  flush();
}

void pastel_puts(const char *s) {
  while (*s) out(*s++);
  out('\n');
  flush();
}
