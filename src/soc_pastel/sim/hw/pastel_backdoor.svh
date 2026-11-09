// SPDX-License-Identifier: MIT
//
// PASTEL_BACKDOOR_MEM: expose a memory array to the host as a backdoor
// endpoint of type "pastel.backdoor".
//
//   module my_backdoor (input logic clk);
//     import pastel_ep_pkg::*;
//     `PASTEL_BACKDOOR_MEM(clk, "ram", tb.u_ram.mem, 8)
//   endmodule
//
// NAME must match the region's `backdoor` name in the device descriptor.
// MEM is a hierarchical reference to an unpacked array of WORD_BYTES-byte
// little-endian words; offsets are bytes from the region base.
//
// Protocol (DATA payloads, little-endian):
//   host -> sim  READ : u8 1, u32 seq, u32 offset, u32 length
//                WRITE: u8 2, u32 seq, u32 offset, u32 length, u8 data[length]
//   sim -> host  REPLY: u8 op|0x80, u32 seq, u8 status (0 ok, 1 out of range),
//                       u8 data[length] (READ only, when status is 0)
// Accesses complete within one clock edge, so they are atomic with respect
// to the design.

`ifndef PASTEL_BACKDOOR_SVH
`define PASTEL_BACKDOOR_SVH

`define PASTEL_BACKDOOR_MEM(CLK, NAME, MEM, WORD_BYTES) \
  int pbd_h, pbd_n; \
  initial pbd_h = pastel_ep_open($sformatf("%m"), "pastel.backdoor", NAME); \
  function automatic int pbd_u32(int at); \
    return {pastel_ep_rx_byte(pbd_h, at + 3), pastel_ep_rx_byte(pbd_h, at + 2), \
            pastel_ep_rx_byte(pbd_h, at + 1), pastel_ep_rx_byte(pbd_h, at)}; \
  endfunction \
  always @(posedge CLK) begin \
    while ((pbd_n = pastel_ep_rx_len(pbd_h)) >= 13) begin \
      automatic byte op = pastel_ep_rx_byte(pbd_h, 0); \
      automatic int seq = pbd_u32(1); \
      automatic longint unsigned off = longint'(unsigned'(pbd_u32(5))); \
      automatic longint unsigned len = longint'(unsigned'(pbd_u32(9))); \
      automatic bit ok = off + len <= longint'($size(MEM)) * (WORD_BYTES); \
      pastel_ep_tx_begin(pbd_h); \
      pastel_ep_tx_byte(pbd_h, op | 8'h80); \
      for (int i = 0; i < 4; i++) pastel_ep_tx_byte(pbd_h, byte'(seq >> (8 * i))); \
      pastel_ep_tx_byte(pbd_h, ok ? 8'd0 : 8'd1); \
      if (ok) begin \
        for (longint unsigned i = 0; i < len; i++) begin \
          automatic longint unsigned a = off + i; \
          if (op == 8'd1) \
            pastel_ep_tx_byte(pbd_h, byte'(MEM[a / (WORD_BYTES)] >> (8 * (a % (WORD_BYTES))))); \
          else if (op == 8'd2 && 13 + i < longint'(pbd_n)) \
            MEM[a / (WORD_BYTES)][8 * (a % (WORD_BYTES)) +: 8] = pastel_ep_rx_byte(pbd_h, 13 + int'(i)); \
        end \
      end \
      pastel_ep_tx_end(pbd_h); \
      pastel_ep_rx_pop(pbd_h); \
    end \
  end

`endif
