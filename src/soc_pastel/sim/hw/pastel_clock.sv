// SPDX-License-Identifier: MIT
//
// pastel_clock: a free-running testbench clock, 50 MHz by default.
//
// Plusargs:
//   +pastel_clk_period_ps=<n>  clock period in picoseconds; overrides PERIOD_PS.
//
// Using this is optional: a testbench may generate its clock any way it
// likes. The period is in ps whatever timescale the design uses.

module pastel_clock #(
  parameter int PERIOD_PS = 20000
) (
  output logic clk
);
  timeunit 1ps;
  timeprecision 1ps;

  int period_ps = PERIOD_PS;

  initial begin
    void'($value$plusargs("pastel_clk_period_ps=%d", period_ps));
    if (period_ps < 2) $fatal(1, "pastel_clock: period must be at least 2 ps");
    clk = 1'b0;
    forever #(period_ps / 2) clk = ~clk;
  end
endmodule
