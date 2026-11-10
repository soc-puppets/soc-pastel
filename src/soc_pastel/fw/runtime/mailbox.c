// SPDX-License-Identifier: MIT
#include "pastel/mailbox.h"

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

void pastel_checkpoint(uint32_t id, int wait) {
  uint8_t b[4];
  put_u32(b, id);
  pastel_mbox_send(PASTEL_MBOX_CHECKPOINT, b, 4);
  while (wait) {
    pastel_mbox_id_t got;
    uint8_t p[4];
    if (pastel_mbox_recv(&got, p, 4) == 4 && got == PASTEL_MBOX_HOST_ACK &&
        (uint32_t)(p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24) == id) {
      wait = 0;
    }
  }
}

int pastel_waves(int enable) {
  uint8_t b = enable ? 1 : 0;
  pastel_mbox_send(PASTEL_MBOX_WAVES, &b, 1);
  for (;;) {
    pastel_mbox_id_t got;
    if (pastel_mbox_recv(&got, &b, 1) == 1 && got == PASTEL_MBOX_HOST_WAVES) return b;
  }
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
  uint8_t b[12];
  put_u32(b, cause);
  put_u32(b + 4, pc);
  put_u32(b + 8, tval);
  pastel_mbox_send(PASTEL_MBOX_FAULT, b, 12);
  for (;;) {
  }
}
