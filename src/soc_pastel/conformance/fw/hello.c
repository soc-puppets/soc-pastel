// Prints over RTT, reports data and a checkpoint, and passes.
#include "pastel/pastel.h"

int test_main(void) {
  pastel_printf("Hello from %s (%s)\n", PASTEL_DEVICE, PASTEL_VARIANT);
  pastel_printf("signed %d, unsigned %u, hex 0x%08x\n", -42, 42u, 0xC0FFEEu);
  pastel_checkpoint(1, 1);
  static const uint8_t data[] = {0xde, 0xad, 0xbe, 0xef};
  pastel_send(data, sizeof data);
  return 0;
}
