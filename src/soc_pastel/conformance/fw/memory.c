// Reads a pattern the host wrote, writes its complement back.
#include "pastel/pastel.h"

#define WORDS 16
volatile uint32_t pastel_shared[WORDS];

int test_main(void) {
  pastel_checkpoint(1, 1);  // host writes the pattern while we wait
  for (int i = 0; i < WORDS; i++) {
    if (pastel_shared[i] != 0x01010101u * (uint32_t)i) return 100 + i;
    pastel_shared[i] = ~pastel_shared[i];
  }
  pastel_checkpoint(2, 1);  // host checks the complement
  return 0;
}
