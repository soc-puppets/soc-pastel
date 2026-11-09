// SPDX-License-Identifier: MIT
//
// Minimal RTT implementation. The control block layout matches SEGGER RTT, so
// standard RTT tools can read it; the code is PASTEL's own.
//   up 0 / down 0: text (printf)     up 1 / down 1: mailbox

#ifndef PASTEL_RTT_H
#define PASTEL_RTT_H

#include <stdint.h>

typedef struct {
  const char *name;
  char *buf;
  uint32_t size;
  volatile uint32_t wr;
  volatile uint32_t rd;
  uint32_t flags;
} pastel_rtt_buffer_t;

typedef struct {
  char id[16];
  int32_t max_up;
  int32_t max_down;
  pastel_rtt_buffer_t up[2];
  pastel_rtt_buffer_t down[2];
} pastel_rtt_cb_t;

extern pastel_rtt_cb_t _SEGGER_RTT;

void pastel_rtt_init(void);
// Write all of data to an up channel, waiting for the host to make room.
void pastel_rtt_write(int channel, const void *data, uint32_t length);
// Read up to capacity bytes from a down channel; returns the number read.
uint32_t pastel_rtt_read(int channel, void *data, uint32_t capacity);

#endif  // PASTEL_RTT_H
