// Turns waveform dumping off and on again around a marker the host can find.
#include "pastel/pastel.h"

int test_main(void) {
  int off = pastel_waves(0);
  pastel_checkpoint(1, 1);  // host notes the time: nothing is dumped here
  int on = pastel_waves(1);
  pastel_checkpoint(2, 1);
  pastel_printf("waves off=%d on=%d\n", off, on);
  return 0;
}
