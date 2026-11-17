// Checkpoint example 3: markers that record how far the firmware got.
#include "pastel/pastel.h"

int test_main(void) {
  pastel_checkpoint(10, 0);  // don't wait: just a marker
  for (volatile int i = 0; i < 100; i++) {
  }
  pastel_checkpoint(20, 0);
  pastel_checkpoint(30, 0);
  return 0;
}
