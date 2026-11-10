// SPDX-License-Identifier: MIT
//
// Verilator main for PASTEL testbenches. The testbench top must have a single
// input `clk`; this drives it until $finish, or until SIGINT/SIGTERM.
//
// Clock period: +pastel_clk_period_ps=<n> (default 20000, i.e. 50 MHz). Time
// advances in the model's time precision, so the period is correct whatever
// timescale the design uses (the precision must be 1 ps or finer for periods
// that are not a whole number of ns).
//
// Waves: +pastel_waves=<file> dumps an FST waveform of the whole testbench to
// <file>, from time 0. It needs a model built with --trace-fst. The host can
// pause and resume dumping (see pastel_sim.h).

#include <cmath>
#include <csignal>
#include <cstdio>
#include <cstring>
#include <memory>

#include "verilated.h"
#if VM_TRACE_FST
#include "verilated_fst_c.h"
#endif
#include "pastel_sim.h"
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

  const char* waves = ctx->commandArgsPlusMatch("pastel_waves=");
#if VM_TRACE_FST
  std::unique_ptr<VerilatedFstC> trace;
  if (waves && *waves) {
    ctx->traceEverOn(true);
    trace = std::make_unique<VerilatedFstC>();
    top->trace(trace.get(), 99);
    trace->open(waves + std::strlen("+pastel_waves="));
    pastel_sim_waves_init(true);
  }
#else
  if (waves && *waves) fprintf(stderr, "pastel: %s ignored: model built without tracing\n", waves);
#endif

  top->clk = 0;
  while (!ctx->gotFinish() && !stop_requested) {
    top->eval();
#if VM_TRACE_FST
    if (trace && pastel_sim_waves_on()) trace->dump(ctx->time());
#endif
    ctx->timeInc(half);
    top->clk = !top->clk;
  }
  top->final();
#if VM_TRACE_FST
  if (trace) trace->close();
#endif
  pastel_server_close();
  return stop_requested ? 1 : 0;
}
