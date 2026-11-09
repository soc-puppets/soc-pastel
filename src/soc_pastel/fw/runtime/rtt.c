// SPDX-License-Identifier: MIT
#include "rtt.h"

#define UP0_SIZE 1024u
#define UP1_SIZE 512u
#define DOWN0_SIZE 16u
#define DOWN1_SIZE 256u

// The control block and buffers live in their own section, which the linker
// script places in the code location's RTT region: memory the host can read
// while the CPU runs. pastel_rtt_init() initialises every field it uses.
#define RTT_SECTION __attribute__((section(".rtt")))

static char up0[UP0_SIZE] RTT_SECTION;
static char up1[UP1_SIZE] RTT_SECTION;
static char down0[DOWN0_SIZE] RTT_SECTION;
static char down1[DOWN1_SIZE] RTT_SECTION;

pastel_rtt_cb_t _SEGGER_RTT __attribute__((used, aligned(4))) RTT_SECTION;

void pastel_rtt_init(void) {
  static const char id[] = "SEGGER RTT";
  pastel_rtt_cb_t *cb = &_SEGGER_RTT;
  for (int i = 0; i < 16; i++) ((volatile char *)cb->id)[i] = 0;  // invalid until set up
  cb->max_up = 2;
  cb->max_down = 2;
  cb->up[0] = (pastel_rtt_buffer_t){"Terminal", up0, UP0_SIZE, 0, 0, 2};
  cb->up[1] = (pastel_rtt_buffer_t){"Mailbox", up1, UP1_SIZE, 0, 0, 2};
  cb->down[0] = (pastel_rtt_buffer_t){"Terminal", down0, DOWN0_SIZE, 0, 0, 2};
  cb->down[1] = (pastel_rtt_buffer_t){"Mailbox", down1, DOWN1_SIZE, 0, 0, 2};
  // Write the id last, back to front, so a host never sees a valid id on a
  // half-initialised block.
  for (int i = 15; i >= 0; i--) {
    ((volatile char *)cb->id)[i] = i < (int)sizeof id ? id[i] : 0;
  }
}

void pastel_rtt_write(int channel, const void *data, uint32_t length) {
  pastel_rtt_buffer_t *b = &_SEGGER_RTT.up[channel];
  const char *p = (const char *)data;
  while (length) {
    uint32_t wr = b->wr, rd = b->rd;
    uint32_t space = rd > wr ? rd - wr - 1 : b->size - wr + rd - 1;
    if (space == 0) continue;  // wait for the host
    uint32_t n = length < space ? length : space;
    for (uint32_t i = 0; i < n; i++) {
      b->buf[wr] = p[i];
      wr = wr + 1 == b->size ? 0 : wr + 1;
    }
    b->wr = wr;  // publish after the data
    p += n;
    length -= n;
  }
}

uint32_t pastel_rtt_read(int channel, void *data, uint32_t capacity) {
  pastel_rtt_buffer_t *b = &_SEGGER_RTT.down[channel];
  char *p = (char *)data;
  uint32_t rd = b->rd, wr = b->wr, n = 0;
  while (rd != wr && n < capacity) {
    p[n++] = b->buf[rd];
    rd = rd + 1 == b->size ? 0 : rd + 1;
  }
  b->rd = rd;
  return n;
}
