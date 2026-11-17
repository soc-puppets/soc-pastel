// Errors and checks: PASTEL_CHECK carries on, PASTEL_ERROR carries on,
// PASTEL_ASSERT (with PASTEL_ASSERT_TEST defined) ends the test at once.
#include "pastel/pastel.h"

int test_main(void) {
  int three = 3;
  PASTEL_INFO("checking %d things", three);
  PASTEL_CHECK(three == 3);             // passes: nothing reported
  PASTEL_CHECK(three == 4);             // fails, no message
  PASTEL_CHECK(three < 0, "three is %d", three);
  PASTEL_ERROR("custom error %x", 0xbeefu);
#ifdef PASTEL_ASSERT_TEST
  PASTEL_ASSERT(three == 5, "stop here");
  PASTEL_INFO("not reached");
#endif
  PASTEL_INFO("still running");
  return 0;
}
