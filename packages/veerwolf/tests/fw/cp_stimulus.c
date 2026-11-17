// Checkpoint example 1: the host changes something while the firmware waits.
#include "pastel/pastel.h"

#define GPIO 0x80001010u   // reads return the pins
#define BUTTON (1u << 16)  // a chip input, driven by the testbench's GPIO agent

int test_main(void) {
  PASTEL_CHECK((pastel_read32(GPIO) & BUTTON) == 0, "button pressed too early");
  pastel_checkpoint(1, 1);  // wait: the host presses the button, then lets us go
  PASTEL_CHECK(pastel_read32(GPIO) & BUTTON, "button press not seen");
  return 0;
}
