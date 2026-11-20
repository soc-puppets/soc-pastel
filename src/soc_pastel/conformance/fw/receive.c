// Takes data from the host: some sent while it waits at a checkpoint (kept
// until asked for), some sent while it is already waiting in pastel_receive.
#include "pastel/pastel.h"

int test_main(void) {
  pastel_checkpoint(1, 1);  // the host sends a value and a string, then lets us go
  uint32_t value = pastel_receive_u32();
  char text[16];
  int n = pastel_receive(text, sizeof text);
  PASTEL_CHECK(value == 0xC0FFEE55u, "value is 0x%08lx", (unsigned long)value);
  PASTEL_CHECK(n == 5, "string length %d", n);
  for (int i = 0; i < n && i < 5; i++) PASTEL_CHECK(text[i] == "hello"[i]);

  pastel_checkpoint(2, 0);  // don't wait: the host sends while we sit in pastel_receive
  uint8_t big[255];
  n = pastel_receive(big, sizeof big);
  PASTEL_CHECK(n == 255, "big message length %d", n);
  for (int i = 0; i < n; i++) PASTEL_CHECK(big[i] == (uint8_t)(i * 7), "byte %d", i);

  uint8_t sum = 0;
  for (int i = 0; i < n; i++) sum = (uint8_t)(sum + big[i]);
  pastel_send(&sum, 1);  // proves the host's data arrived intact
  return 0;
}
