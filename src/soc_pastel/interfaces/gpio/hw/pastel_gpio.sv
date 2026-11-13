// SPDX-License-Identifier: MIT
//
// pastel_gpio: the far end of the chip's GPIO pins, for the gpio interface
// (soc_pastel.interfaces.gpio.sim). The host samples the pins, drives any of
// them to 0 or 1, and releases them again.
//
// The testbench does the tristating, so no inout port is needed:
//
//   pastel_gpio #(.NAME("gpio"), .WIDTH(16)) u_gpio (
//     .clk(clk), .pins(gpio), .drive(gpio_drive), .drive_en(gpio_drive_en));
//   for (genvar i = 0; i < 16; i++)
//     assign gpio[i] = gpio_drive_en[i] ? gpio_drive[i] : 1'bz;
//
// NAME is the interface's instance name in the device descriptor. WIDTH is
// 1 to 64; the host learns it from the agent. The agent drives nothing from
// time 0, and its outputs are never X.
//
// A request is applied at a rising edge of clk, and answered at the next
// falling edge with the pins as they then are, so when the host's call
// returns, the chip can see the change.
//
// Protocol (DATA payloads, little-endian), endpoint type "gpio":
//   host -> sim  SAMPLE : u8 1, u32 seq
//                DRIVE  : u8 2, u32 seq, u64 value, u64 mask
//                RELEASE: u8 3, u32 seq, u64 mask
//   sim -> host  REPLY  : u8 op|0x80, u32 seq, u8 width, u64 value, u64 unknown
// `value` reads X and Z pins as 0; `unknown` has a 1 for each such pin. A
// 2-state simulator (Verilator) never reports unknown pins.

module pastel_gpio #(
  parameter string NAME = "gpio",
  parameter int WIDTH = 32
) (
  input  logic             clk,
  input  logic [WIDTH-1:0] pins,      // the pins as seen at the chip boundary
  output logic [WIDTH-1:0] drive,     // value to drive, where drive_en is 1
  output logic [WIDTH-1:0] drive_en   // 1: the agent drives this pin
);
  import pastel_ep_pkg::*;

  localparam byte SAMPLE = 8'd1, DRIVE = 8'd2, RELEASE = 8'd3;

  logic [WIDTH-1:0] drive_r = '0;
  logic [WIDTH-1:0] en_r = '0;
  assign drive = drive_r;
  assign drive_en = en_r;

  int h;
  int n;
  int i;
  bit reply_due = 1'b0;
  byte op;
  int seq;
  logic [63:0] value, mask, known, unknown;

  initial begin
    if (WIDTH < 1 || WIDTH > 64) $fatal(1, "pastel_gpio: WIDTH must be 1 to 64, not %0d", WIDTH);
    h = pastel_ep_open($sformatf("%m"), "gpio", NAME);
  end

  function automatic logic [31:0] rx_u32(int at);
    return {pastel_ep_rx_byte(h, at + 3), pastel_ep_rx_byte(h, at + 2),
            pastel_ep_rx_byte(h, at + 1), pastel_ep_rx_byte(h, at)};
  endfunction

  function automatic logic [63:0] rx_u64(int at);
    return {rx_u32(at + 4), rx_u32(at)};
  endfunction

  function automatic void tx_u64(logic [63:0] v);
    for (int b = 0; b < 8; b++) pastel_ep_tx_byte(h, byte'(v >> (8 * b)));
  endfunction

  // One request per rising edge; the host waits for each reply anyway.
  always @(posedge clk) begin
    n = pastel_ep_rx_len(h);
    if (!reply_due && n >= 5) begin
      op = pastel_ep_rx_byte(h, 0);
      seq = int'(rx_u32(1));
      if (op == DRIVE && n >= 21) begin
        value = rx_u64(5);
        mask = rx_u64(13);
        en_r = en_r | mask[WIDTH-1:0];
        drive_r = (drive_r & ~mask[WIDTH-1:0]) | (value[WIDTH-1:0] & mask[WIDTH-1:0]);
      end else if (op == RELEASE && n >= 13) begin
        mask = rx_u64(5);
        en_r = en_r & ~mask[WIDTH-1:0];
      end
      pastel_ep_rx_pop(h);
      reply_due = 1'b1;
    end
  end

  // Answer half a cycle later, once the change has reached the pins.
  always @(negedge clk) begin
    if (reply_due) begin
      known = '0;
      unknown = '0;
      for (i = 0; i < WIDTH; i++) begin
        if ($isunknown(pins[i])) unknown[i] = 1'b1;
        else known[i] = pins[i];
      end
      pastel_ep_tx_begin(h);
      pastel_ep_tx_byte(h, op | 8'h80);
      for (i = 0; i < 4; i++) pastel_ep_tx_byte(h, byte'(seq >> (8 * i)));
      pastel_ep_tx_byte(h, byte'(WIDTH));
      tx_u64(known);
      tx_u64(unknown);
      pastel_ep_tx_end(h);
      reply_due = 1'b0;
    end
  end
endmodule
