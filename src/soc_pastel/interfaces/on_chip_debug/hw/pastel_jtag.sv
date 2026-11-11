// SPDX-License-Identifier: MIT
//
// pastel_jtag: JTAG driven by OpenOCD over remote_bitbang (pastel_jtag.cpp).
// Instantiate once in the testbench and connect to the chip's JTAG pins.
// Serves JTAG on a Unix socket for OpenOCD running beside the simulator.
// Optional plusarg: +pastel_jtag_port=<n> to serve it on TCP port n on
// loopback instead (0: any free port), for use with your own OpenOCD.

module pastel_jtag (
  input  logic clk,
  output logic tck,
  output logic tms,
  output logic tdi,
  output logic trst_n,
  input  logic tdo
);
  import "DPI-C" function int pastel_jtag_open(input int port);
  import "DPI-C" function void pastel_jtag_tick(input bit tdo, output bit tck, output bit tms,
                                                output bit tdi, output bit trst_n);
  bit tck_b, tms_b, tdi_b, trst_n_b = 1'b1;
  int port = -1;  // Unix socket

  initial begin
    void'($value$plusargs("pastel_jtag_port=%d", port));
    if (pastel_jtag_open(port) < 0) $fatal(1, "pastel_jtag: could not open JTAG server");
  end

  always @(posedge clk) pastel_jtag_tick(tdo, tck_b, tms_b, tdi_b, trst_n_b);

  assign tck = tck_b;
  assign tms = tms_b;
  assign tdi = tdi_b;
  assign trst_n = trst_n_b;
endmodule
