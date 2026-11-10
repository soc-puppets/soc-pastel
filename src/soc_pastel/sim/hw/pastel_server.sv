// SPDX-License-Identifier: MIT
//
// pastel_server: connects the simulation to the PASTEL host and services the
// endpoint pump. Instantiate exactly once in the testbench.
//
// Plusargs:
//   +pastel_host=<ip>:<port>:<token>  set by the PASTEL platform; without it
//                                     the simulation runs standalone.
//   +pastel_waves=<file>              dump a waveform of the whole testbench
//                                     to <file> from time 0. Needs a model
//                                     compiled with +define+PASTEL_WAVES and
//                                     the simulator's own tracing option.
//
// It connects on the first clock edge, after every VIP has opened its
// endpoint at time 0, so HELLO lists them all. It then polls every
// POLL_CYCLES clocks and ends the simulation when the host asks. The host can
// also pause and resume waveform dumping, on simulators that honour $dumpoff
// and $dumpon; that takes effect at a poll. Verilator ignores both, so there
// a waveform always covers the whole run and the host is told so.

module pastel_server #(
  parameter int POLL_CYCLES = 8
) (
  input logic clk
);
  timeunit 1ns;
  timeprecision 1ps;  // $time below is therefore in ns, whatever the design uses

  import "DPI-C" function int  pastel_server_connect(input string spec);
  import "DPI-C" function int  pastel_server_poll(input longint time_ns);
  import "DPI-C" function void pastel_server_close();
  import "DPI-C" function void pastel_server_waves_init(input int can_pause);
  import "DPI-C" function int  pastel_server_waves_on();

  string spec = "";
  bit connected = 1'b0;
  int count = 0;
  int r;

  initial void'($value$plusargs("pastel_host=%s", spec));

  // --- Waves ---------------------------------------------------------------
  string waves_file = "";
`ifdef PASTEL_WAVES
`ifdef VERILATOR
  localparam int CAN_PAUSE = 0;  // $dumpoff and $dumpon compile to nothing
`else
  localparam int CAN_PAUSE = 1;
`endif
  bit waves = 1'b0;    // a waveform file is open
  bit dumping = 1'b0;  // ... and is being written to

  initial begin
    if ($value$plusargs("pastel_waves=%s", waves_file)) begin
      $dumpfile(waves_file);
      $dumpvars;
      waves = 1'b1;
      dumping = 1'b1;
      pastel_server_waves_init(CAN_PAUSE);  // tell the host waves exist, and are on
    end
  end

  function automatic void update_waves();
    if (waves && (pastel_server_waves_on() != 0) != dumping) begin
      dumping = !dumping;
      if (dumping) $dumpon;
      else $dumpoff;
    end
  endfunction
`else
  initial begin
    if ($value$plusargs("pastel_waves=%s", waves_file))
      $display("pastel_server: +pastel_waves ignored: model built without PASTEL_WAVES");
  end
`endif

  always @(posedge clk) begin
    if (!connected) begin
      connected = 1'b1;
      if (pastel_server_connect(spec) != 0) $fatal(1, "pastel_server: cannot connect to host");
    end
    if (++count >= POLL_CYCLES) begin
      count = 0;
      r = pastel_server_poll(longint'($time));
`ifdef PASTEL_WAVES
      update_waves();
`endif
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
