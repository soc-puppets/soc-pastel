// Takes an illegal-instruction exception, which must be reported as a fault.
#include "pastel/pastel.h"

int test_main(void) {
  __asm__ volatile(".word 0x00000000");  // all-zero is an illegal instruction
  return 0;
}
