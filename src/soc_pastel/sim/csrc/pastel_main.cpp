// SPDX-License-Identifier: MIT
//
// Verilator main for PASTEL testbenches. The testbench top must have a single
// input `clk`; this drives it until $finish, or until SIGINT/SIGTERM.
//
// Clock period: +pastel_clk_period_ps=<n> (default 20000, i.e. 50 MHz). Time
// advances in the model's time precision, so the period is correct whatever
// timescale the design uses (the precision must be 1 ps or finer for periods
// that are not a whole number of ns).

#include <cmath>
#include <csignal>
#include <cstdio>
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

  long long period_ps = 20000;
  const char* arg = ctx->commandArgsPlusMatch("pastel_clk_period_ps=");
  if (arg && *arg) std::sscanf(arg, "+pastel_clk_period_ps=%lld", &period_ps);
  // Half period in units of the time precision (10^timeprecision seconds).
  double half_s = period_ps * 1e-12 / 2.0;
  uint64_t half = uint64_t(std::llround(half_s / std::pow(10.0, ctx->timeprecision())));
  if (half == 0) half = 1;

  top->clk = 0;
  while (!ctx->gotFinish() && !stop_requested) {
    top->eval();
    ctx->timeInc(half);
    top->clk = !top->clk;
  }
  top->final();
  pastel_server_close();
  return stop_requested ? 1 : 0;
}
