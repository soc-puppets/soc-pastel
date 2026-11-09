// SPDX-License-Identifier: MIT
//
// Verilator main for PASTEL testbenches. The testbench top must have a single
// input `clk`; this drives it with a 20-unit period (50 MHz at 1 ns units)
// until $finish, or until SIGINT/SIGTERM.

#include <csignal>
#include <memory>

#include "verilated.h"
#include "pastel_top.h"  // generated: includes V<top>.h, defines PASTEL_TOP_CLASS

namespace {
volatile std::sig_atomic_t stop_requested = 0;
void on_signal(int) { stop_requested = 1; }
}  // namespace

extern "C" void pastel_server_close(void);

int main(int argc, char** argv) {
  auto ctx = std::make_unique<VerilatedContext>();
  ctx->commandArgs(argc, argv);
  auto top = std::make_unique<PASTEL_TOP_CLASS>(ctx.get(), "TOP");
  std::signal(SIGINT, on_signal);
  std::signal(SIGTERM, on_signal);

  top->clk = 0;
  while (!ctx->gotFinish() && !stop_requested) {
    top->eval();
    ctx->timeInc(10);
    top->clk = !top->clk;
  }
  top->final();
  pastel_server_close();
  return stop_requested ? 1 : 0;
}
