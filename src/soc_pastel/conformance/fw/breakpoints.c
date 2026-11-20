// Calls two functions alternately for ever, counting the calls, for breakpoints to stop in.
#include "pastel/pastel.h"

volatile uint32_t first_count, second_count;

__attribute__((noinline)) void first_target(void) { first_count++; }
__attribute__((noinline)) void second_target(void) { second_count++; }

int test_main(void) {
  pastel_checkpoint(1, 1);  // the test sets its breakpoints here
  PASTEL_INFO("looping");
  for (;;) {
    first_target();
    second_target();
  }
}
