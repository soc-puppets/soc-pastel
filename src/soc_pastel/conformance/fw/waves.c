// Asks for waveform dumping to be paused and resumed, and reports the answers.
#include "pastel/pastel.h"

int test_main(void) {
  int off = pastel_waves(0);
  int on = pastel_waves(1);
  pastel_printf("waves off=%d on=%d\n", off, on);
  return 0;
}
