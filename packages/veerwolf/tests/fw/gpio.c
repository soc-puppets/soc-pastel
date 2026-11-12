// GPIO through the chip, coordinated with the host at checkpoints.
//
// VeeRwolf's testbench: pins 15:0 are chip outputs, pins 31:16 chip inputs
// driven by the pastel_gpio agent. The syscon GPIO register at 0x80001010
// sets the outputs when written and returns all 32 pins when read.
#include "pastel/pastel.h"

#define GPIO 0x80001010u

int test_main(void) {
  pastel_write32(GPIO, 0xa5c3u);  // outputs on pins 15:0
  pastel_checkpoint(1, 1);        // host checks them, and drives pins 31:16
  uint32_t pins = pastel_read32(GPIO);
  pastel_send(&pins, sizeof pins);
  pastel_checkpoint(2, 1);        // host releases pins 31:16
  pins = pastel_read32(GPIO);
  pastel_send(&pins, sizeof pins);
  return 0;
}
