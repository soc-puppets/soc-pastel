// SPDX-License-Identifier: MIT
//
// PASTEL mailbox protocol, version 1.
//
// The mailbox runs over SEGGER RTT: up-channel 0 carries printf output, and
// the mailbox uses up/down channel 1. Each message is
//
//     u8  id        pastel_mbox_id_t
//     u8  length    payload length in bytes (0..255)
//     u8  payload[length]
//
// Multi-byte payload fields are little-endian. The host checks
// PASTEL_MBOX_VERSION in the HELLO message before anything else.

#ifndef PASTEL_MAILBOX_H
#define PASTEL_MAILBOX_H

#include <stdint.h>

#define PASTEL_MBOX_VERSION 1u
#define PASTEL_MBOX_RTT_CHANNEL 1u

typedef enum {
  // Target -> host
  PASTEL_MBOX_HELLO      = 0x01,  // u8 version; firmware is up
  PASTEL_MBOX_CHECKPOINT = 0x02,  // u32 checkpoint id
  PASTEL_MBOX_DATA       = 0x03,  // opaque test data
  PASTEL_MBOX_FINISH     = 0x04,  // i32 result: 0 = pass, anything else = fail
  PASTEL_MBOX_FAULT      = 0x05,  // u32 cause, u32 pc, u32 tval
  // Host -> target
  PASTEL_MBOX_HOST_DATA  = 0x81,  // opaque test data
  PASTEL_MBOX_HOST_ACK   = 0x82,  // u32 checkpoint id being released
} pastel_mbox_id_t;

// Send a message. Blocks until it fits in the RTT buffer.
void pastel_mbox_send(pastel_mbox_id_t id, const void *payload, uint8_t length);

// Receive a message. Returns the payload length, or -1 if none is pending.
int pastel_mbox_recv(pastel_mbox_id_t *id, void *payload, uint8_t capacity);

// Report a checkpoint; if wait is nonzero, block until the host acknowledges it.
void pastel_checkpoint(uint32_t id, int wait);

// Report the test result. Does not return.
__attribute__((noreturn)) void pastel_finish(int32_t result);

#endif  // PASTEL_MAILBOX_H
