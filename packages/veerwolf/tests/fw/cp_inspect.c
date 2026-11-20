// Checkpoint example 2: the host looks at the chip while the firmware waits.
#include "pastel/pastel.h"

#define GPIO 0x80001010u  // writes set the output pins 15:0

volatile uint32_t total;  // the host reads this by name

int test_main(void) {
  for (uint32_t i = 1; i <= 10; i++) total += i;
  pastel_write32(GPIO, total);
  pastel_checkpoint(1, 1);  // wait while the host checks `total` and the pins
  return 0;
}
