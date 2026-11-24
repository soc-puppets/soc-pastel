// SPDX-License-Identifier: MIT
#include "pastel/mailbox.h"

#include <stdarg.h>

#include "pastel/pastel.h"
#include "pastel/port.h"
#include "rtt.h"

void pastel_mbox_send(pastel_mbox_id_t id, const void *payload, uint8_t length) {
  uint8_t hdr[2] = {(uint8_t)id, length};
  pastel_rtt_write(PASTEL_MBOX_RTT_CHANNEL, hdr, 2);
  if (length) pastel_rtt_write(PASTEL_MBOX_RTT_CHANNEL, payload, length);
}

// Messages from the host arrive as a byte stream; reassemble them here.
static uint8_t rx[2 + 255];
static uint32_t rx_len;

int pastel_mbox_recv(pastel_mbox_id_t *id, void *payload, uint8_t capacity) {
  rx_len += pastel_rtt_read(PASTEL_MBOX_RTT_CHANNEL, rx + rx_len, sizeof rx - rx_len);
  if (rx_len < 2 || rx_len < 2u + rx[1]) return -1;
  uint8_t len = rx[1];
  *id = (pastel_mbox_id_t)rx[0];
  uint8_t *out = (uint8_t *)payload;
  for (uint8_t i = 0; i < len && i < capacity; i++) out[i] = rx[2 + i];
  for (uint32_t i = 2u + len; i < rx_len; i++) rx[i - 2u - len] = rx[i];
  rx_len -= 2u + len;
  return len;
}

static void put_u32(uint8_t *p, uint32_t v) {
  for (int i = 0; i < 4; i++) p[i] = (uint8_t)(v >> (8 * i));
}

static uint32_t get_u32(const uint8_t *p) {
  return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

// Data the host sent (HOST_DATA) while the firmware was waiting for something
// else, kept for pastel_receive(): each message as a length byte, then its bytes.
static uint8_t pending[PASTEL_HOST_DATA_QUEUE];
static uint32_t pending_len;

static void keep_host_data(const uint8_t *data, uint8_t length) {
  if (pending_len + 1u + length > sizeof pending) {
    pastel_error("host data dropped: more than %u bytes waiting (PASTEL_HOST_DATA_QUEUE)",
                 (unsigned)sizeof pending);
    return;
  }
  pending[pending_len++] = length;
  for (uint8_t i = 0; i < length; i++) pending[pending_len++] = data[i];
}

// Wait for the next host message of type `want`, keeping any host data that
// arrives first. Returns its length; the payload (up to 255 bytes) is in `payload`.
static int wait_for(pastel_mbox_id_t want, uint8_t payload[255]) {
  for (;;) {
    pastel_mbox_id_t got;
    int n = pastel_mbox_recv(&got, payload, 255);
    if (n < 0) continue;
    if (got == want) return n;
    if (got == PASTEL_MBOX_HOST_DATA) keep_host_data(payload, (uint8_t)n);
  }
}

void pastel_checkpoint(uint32_t id, int wait) {
  uint8_t b[255];
  put_u32(b, id);
  b[4] = wait ? 1 : 0;  // the host only acknowledges a checkpoint that waits
  pastel_mbox_send(PASTEL_MBOX_CHECKPOINT, b, 5);
  while (wait) {
    if (wait_for(PASTEL_MBOX_HOST_ACK, b) == 4 && get_u32(b) == id) wait = 0;
  }
}

int pastel_waves(int enable) {
  uint8_t b[255];
  b[0] = enable ? 1 : 0;
  pastel_mbox_send(PASTEL_MBOX_WAVES, b, 1);
  for (;;) {
    if (wait_for(PASTEL_MBOX_HOST_WAVES, b) == 1) return b[0];
  }
}

int pastel_receive(void *buf, uint8_t capacity) {
  uint8_t *out = (uint8_t *)buf;
  uint8_t msg[255];
  int n;
  if (pending_len) {  // the oldest message the host sent while we were busy
    n = pending[0];
    for (int i = 0; i < n; i++) msg[i] = pending[1 + i];
    for (uint32_t i = 1u + (uint32_t)n; i < pending_len; i++) pending[i - 1u - (uint32_t)n] = pending[i];
    pending_len -= 1u + (uint32_t)n;
  } else {
    n = wait_for(PASTEL_MBOX_HOST_DATA, msg);
  }
  for (int i = 0; i < n && i < capacity; i++) out[i] = msg[i];
  return n;
}

uint32_t pastel_receive_u32(void) {
  uint8_t b[4];
  int n = pastel_receive(b, 4);
  if (n != 4) pastel_error("pastel_receive_u32: the host sent %d bytes, not 4", n);
  return get_u32(b);
}

void pastel_send(const void *data, uint8_t length) {
  pastel_mbox_send(PASTEL_MBOX_DATA, data, length);
}

void pastel_finish(int32_t result) {
  uint8_t b[4];
  put_u32(b, (uint32_t)result);
  pastel_mbox_send(PASTEL_MBOX_FINISH, b, 4);
  for (;;) {
  }
}

void pastel_fault(uint32_t cause, uint32_t pc, uint32_t tval) {
  // Static, not on the stack: the fault may be the stack's own memory (see
  // the port's trap entry, which calls this at most twice).
  static uint8_t b[12];
  put_u32(b, cause);
  put_u32(b + 4, pc);
  put_u32(b + 8, tval);
  pastel_mbox_send(PASTEL_MBOX_FAULT, b, 12);
  for (;;) {
  }
}

// --- Errors and checks ----------------------------------------------------------

static void send_error(const char *text) {
  uint32_t n = 0;
  while (text[n] && n < 255) n++;
  pastel_mbox_send(PASTEL_MBOX_ERROR, text, (uint8_t)n);
}

void pastel_error(const char *fmt, ...) {
  char text[256];
  va_list ap;
  va_start(ap, fmt);
  pastel_vsnprintf(text, sizeof text, fmt, ap);
  va_end(ap);
  send_error(text);
}

void pastel_check_failed(int fatal, const char *file, int line, const char *expr,
                         const char *fmt, ...) {
  const char *base = file;
  for (const char *p = file; *p; p++)
    if (*p == '/' || *p == '\\') base = p + 1;
  char text[256];
  int n = pastel_snprintf(text, sizeof text, "%s:%d: %s failed: %s", base, line,
                          fatal ? "ASSERT" : "CHECK", expr);
  if (*fmt && n + 2 < (int)sizeof text) {
    text[n] = ':';
    text[n + 1] = ' ';
    va_list ap;
    va_start(ap, fmt);
    pastel_vsnprintf(text + n + 2, sizeof text - (uint32_t)(n + 2), fmt, ap);
    va_end(ap);
  }
  send_error(text);
  if (fatal) pastel_finish(1);
}
