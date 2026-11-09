// SPDX-License-Identifier: MIT
//
// What each CPU port of the PASTEL firmware runtime must provide.
//
// The runtime calls these in order: pastel_port_early_init() before .data and
// .bss are set up, pastel_port_init() after, then test_main(). Faults are
// routed to pastel_fault(), which reports PASTEL_MBOX_FAULT and finishes the
// test instead of hanging until a timeout.

#ifndef PASTEL_PORT_H
#define PASTEL_PORT_H

#include <stdint.h>

// Earliest initialisation: memory attributes and anything needed before RAM
// is touched. A VeeR port programs MRAC here from the side_effects flags of
// the descriptor's regions (via the generated memory_map.h), because PASTEL
// loads code directly and so bypasses any boot ROM that would do it.
void pastel_port_early_init(void);

// Remaining CPU set-up after C runtime initialisation: trap vector, caches.
void pastel_port_init(void);

// Called from the port's trap handler for any unexpected exception.
__attribute__((noreturn)) void pastel_fault(uint32_t cause, uint32_t pc, uint32_t tval);

// The test entry point, provided by each test. Returning 0 passes the test;
// any other value fails it.
int test_main(void);

#endif  // PASTEL_PORT_H
