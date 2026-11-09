// SPDX-License-Identifier: MIT
//
// pastel_server: connects the simulation to the PASTEL host and services the
// endpoint pump. Instantiate exactly once in the testbench.
//
// Plusargs:
//   +pastel_host=<ip>:<port>:<token>  set by the PASTEL platform; without it
//                                     the simulation runs standalone.
//
// It connects on the first clock edge, after every VIP has opened its
// endpoint at time 0, so HELLO lists them all. It then polls every
// POLL_CYCLES clocks and ends the simulation when the host asks.

module pastel_server #(
  parameter int POLL_CYCLES = 8
) (
  input logic clk
);
  import "DPI-C" function int  pastel_server_connect(input string spec);
  import "DPI-C" function int  pastel_server_poll(input longint time_ns);
  import "DPI-C" function void pastel_server_close();

  string spec = "";
  bit connected = 1'b0;
  int count = 0;
  int r;

  initial void'($value$plusargs("pastel_host=%s", spec));

  always @(posedge clk) begin
    if (!connected) begin
      connected = 1'b1;
      if (pastel_server_connect(spec) != 0) $fatal(1, "pastel_server: cannot connect to host");
    end
    if (++count >= POLL_CYCLES) begin
      count = 0;
      r = pastel_server_poll(longint'($time));
      if (r == 1) begin
        pastel_server_close();
        $finish;
      end else if (r == 2) begin
        $fatal(1, "pastel_server: lost connection to host");
      end
    end
  end

  final pastel_server_close();
endmodule
