// Checkpoint example 4: a conversation, one step per checkpoint.
#include "pastel/pastel.h"

volatile uint32_t command;  // written by the host at each step
volatile uint32_t answer;   // read by the host at the next step

int test_main(void) {
  for (uint32_t step = 1; step <= 3; step++) {
    pastel_checkpoint(step, 1);  // wait for the host's command
    answer = command * command;
  }
  pastel_checkpoint(4, 1);  // let the host check the last answer
  return 0;
}
