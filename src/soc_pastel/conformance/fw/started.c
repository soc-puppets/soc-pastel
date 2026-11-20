// For firmware.start(): takes a value at a waiting checkpoint, reports progress
// with markers, waits for more data, and returns the product.
#include "pastel/pastel.h"

int test_main(void) {
  pastel_checkpoint(1, 1);  // the test sends a value while we're held here
  uint32_t a = pastel_receive_u32();
  pastel_checkpoint(3, 0);  // markers: a repeated id is waited for again
  pastel_checkpoint(3, 0);
  pastel_checkpoint(2, 0);  // the test sends the second value after seeing this
  uint32_t b = pastel_receive_u32();
  uint32_t product = a * b;
  PASTEL_INFO("%lu x %lu = %lu", (unsigned long)a, (unsigned long)b, (unsigned long)product);
  pastel_send(&product, 4);
  return 0;
}
