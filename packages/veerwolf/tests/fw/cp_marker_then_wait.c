// A marker, then a waiting checkpoint with the same id: the firmware must
// still wait for the host at the second (mailbox v1 acknowledged markers too,
// and the stale acknowledgement released the second checkpoint early).
#include "pastel/pastel.h"

#define GPIO 0x80001010u
#define FLAG (1u << 17)  // a chip input the host drives at the waiting checkpoint

int test_main(void) {
  pastel_checkpoint(1, 0);
  pastel_checkpoint(1, 1);
  PASTEL_CHECK(pastel_read32(GPIO) & FLAG, "carried on before the host acted");
  return 0;
}
