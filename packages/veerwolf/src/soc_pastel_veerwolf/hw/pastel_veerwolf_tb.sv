// SPDX-License-Identifier: MIT
//
// PASTEL testbench for VeeRwolf (veerwolf_core with VeeR EL2).
//
// The single `clk` input is driven by the PASTEL Verilator main. Reset is
// generated here. JTAG goes through pastel_jtag (OpenOCD remote_bitbang) and
// VeeR's dmi_wrapper; RAM is an AXI RAM model with a PASTEL backdoor.

`include "pastel_backdoor.svh"

`default_nettype none

module pastel_veerwolf_tb (
  input logic clk
);
  localparam int RAM_SIZE = 32'h10_0000;  // keep in step with the "ram" region

  // --- Reset: held for 16 cycles -------------------------------------------
  logic [4:0] rst_count = '0;
  logic rst;
  always_ff @(posedge clk) if (!rst_count[4]) rst_count <= rst_count + 1'b1;
  assign rst = !rst_count[4];

  // --- PASTEL infrastructure -------------------------------------------------
  pastel_server u_pastel (.clk(clk));

  logic jtag_tck, jtag_tms, jtag_tdi, jtag_trst_n, jtag_tdo;
  pastel_jtag u_jtag (
    .clk(clk), .tck(jtag_tck), .tms(jtag_tms), .tdi(jtag_tdi), .trst_n(jtag_trst_n),
    .tdo(jtag_tdo)
  );

  // --- Debug module interface ---------------------------------------------
  logic        dmi_reg_en, dmi_reg_wr_en, dmi_hard_reset;
  logic [ 6:0] dmi_reg_addr;
  logic [31:0] dmi_reg_wdata, dmi_reg_rdata;

  dmi_wrapper u_dmi (
    .trst_n(jtag_trst_n), .tck(jtag_tck), .tms(jtag_tms), .tdi(jtag_tdi), .tdo(jtag_tdo),
    .tdoEnable(),
    .core_rst_n(!rst), .core_clk(clk), .jtag_id(31'd0),
    .rd_data(dmi_reg_rdata), .reg_wr_data(dmi_reg_wdata), .reg_wr_addr(dmi_reg_addr),
    .reg_en(dmi_reg_en), .reg_wr_en(dmi_reg_wr_en), .dmi_hard_reset(dmi_hard_reset)
  );

  // --- RAM ----------------------------------------------------------------------
  logic [ 5:0] awid, arid, bid, rid;
  logic [31:0] awaddr, araddr;
  logic [ 7:0] awlen, arlen, wstrb;
  logic [ 2:0] awsize, arsize;
  logic [ 1:0] awburst, arburst, bresp, rresp;
  logic        awvalid, awready, arvalid, arready, wlast, wvalid, wready;
  logic        bvalid, bready, rlast, rvalid, rready;
  logic [63:0] wdata, rdata;

  axi_ram #(.DATA_WIDTH(64), .ADDR_WIDTH($clog2(RAM_SIZE)), .ID_WIDTH(6)) ram (
    .clk(clk), .rst(rst),
    .s_axi_awid(awid), .s_axi_awaddr(awaddr[$clog2(RAM_SIZE)-1:0]), .s_axi_awlen(awlen),
    .s_axi_awsize(awsize), .s_axi_awburst(awburst), .s_axi_awlock(1'b0), .s_axi_awcache(4'd0),
    .s_axi_awprot(3'd0), .s_axi_awvalid(awvalid), .s_axi_awready(awready),
    .s_axi_wdata(wdata), .s_axi_wstrb(wstrb), .s_axi_wlast(wlast), .s_axi_wvalid(wvalid),
    .s_axi_wready(wready),
    .s_axi_bid(bid), .s_axi_bresp(bresp), .s_axi_bvalid(bvalid), .s_axi_bready(bready),
    .s_axi_arid(arid), .s_axi_araddr(araddr[$clog2(RAM_SIZE)-1:0]), .s_axi_arlen(arlen),
    .s_axi_arsize(arsize), .s_axi_arburst(arburst), .s_axi_arlock(1'b0), .s_axi_arcache(4'd0),
    .s_axi_arprot(3'd0), .s_axi_arvalid(arvalid), .s_axi_arready(arready),
    .s_axi_rid(rid), .s_axi_rdata(rdata), .s_axi_rresp(rresp), .s_axi_rlast(rlast),
    .s_axi_rvalid(rvalid), .s_axi_rready(rready)
  );

  pastel_veerwolf_backdoor u_backdoor (.clk(clk));

  // --- SoC ----------------------------------------------------------------------
  /* verilator lint_off PINCONNECTEMPTY */
  veerwolf_core #(.clk_freq_hz(32'd50_000_000)) veerwolf (
    .clk(clk), .rstn(!rst),
    .dmi_reg_rdata(dmi_reg_rdata), .dmi_reg_wdata(dmi_reg_wdata), .dmi_reg_addr(dmi_reg_addr),
    .dmi_reg_en(dmi_reg_en), .dmi_reg_wr_en(dmi_reg_wr_en), .dmi_hard_reset(dmi_hard_reset),
    .o_flash_sclk(), .o_flash_cs_n(), .o_flash_mosi(), .i_flash_miso(1'b0),
    .i_uart_rx(1'b1), .o_uart_tx(),
    .o_ram_awid(awid), .o_ram_awaddr(awaddr), .o_ram_awlen(awlen), .o_ram_awsize(awsize),
    .o_ram_awburst(awburst), .o_ram_awlock(), .o_ram_awcache(), .o_ram_awprot(),
    .o_ram_awregion(), .o_ram_awqos(), .o_ram_awvalid(awvalid), .i_ram_awready(awready),
    .o_ram_arid(arid), .o_ram_araddr(araddr), .o_ram_arlen(arlen), .o_ram_arsize(arsize),
    .o_ram_arburst(arburst), .o_ram_arlock(), .o_ram_arcache(), .o_ram_arprot(),
    .o_ram_arregion(), .o_ram_arqos(), .o_ram_arvalid(arvalid), .i_ram_arready(arready),
    .o_ram_wdata(wdata), .o_ram_wstrb(wstrb), .o_ram_wlast(wlast), .o_ram_wvalid(wvalid),
    .i_ram_wready(wready),
    .i_ram_bid(bid), .i_ram_bresp(bresp), .i_ram_bvalid(bvalid), .o_ram_bready(bready),
    .i_ram_rid(rid), .i_ram_rdata(rdata), .i_ram_rresp(rresp), .i_ram_rlast(rlast),
    .i_ram_rvalid(rvalid), .o_ram_rready(rready),
    .i_ram_init_done(1'b1), .i_ram_init_error(1'b0),
    .i_gpio(64'd0), .o_gpio()
  );
  /* verilator lint_on PINCONNECTEMPTY */

  // Park the boot ROM in a tight loop: PASTEL halts the core at the reset
  // vector over JTAG, loads firmware and sets the PC itself.
  initial veerwolf.bootrom.ram.mem[0] = 64'h0000_0000_0000_006f;  // j .
endmodule

`default_nettype wire

module pastel_veerwolf_backdoor (input logic clk);
  import pastel_ep_pkg::*;
  `PASTEL_BACKDOOR_MEM(clk, "ram", pastel_veerwolf_tb.ram.mem, 8)
endmodule
