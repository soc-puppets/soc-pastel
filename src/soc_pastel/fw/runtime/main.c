// SPDX-License-Identifier: MIT
#include "pastel/mailbox.h"
#include "pastel/port.h"
#include "rtt.h"

// Called by the port's start-up code once the stack and .bss are ready.
void pastel_main(void) {
  pastel_rtt_init();
  pastel_port_init();
  uint8_t version = PASTEL_MBOX_VERSION;
  pastel_mbox_send(PASTEL_MBOX_HELLO, &version, 1);
  pastel_finish(test_main());  // the return value is the result
}
