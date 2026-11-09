// SPDX-License-Identifier: MIT
//
// pastel_ep_pkg: the DPI contract between testbench VIPs and the PASTEL
// endpoint pump.
//
// Each VIP opens one endpoint and exchanges opaque byte messages with its
// host-side handler. The VIP always *pulls*: it polls for inbound messages on
// its own clock, so the pump never calls into SystemVerilog and no DPI call
// consumes simulation time. Message contents are defined by the VIP's
// interface pack; the pump and the core never interpret them.
//
// Byte-at-a-time calls avoid open-array arguments, which simulators support
// inconsistently. They are fast enough for control traffic and serial
// interfaces; high-bandwidth endpoints may need a pack-specific DPI library.

package pastel_ep_pkg;

  // Register an endpoint. Must be called at time 0, before the pump connects
  // to the host, so the endpoint appears in HELLO.
  //   path    : $sformatf("%m") of the calling VIP, for diagnostics
  //   ep_type : endpoint type, e.g. "spi"; selects the host-side handler
  //   name    : instance name, matching the device descriptor, e.g. "spi0"
  // Returns a handle for the calls below.
  import "DPI-C" function int pastel_ep_open(input string path,
                                              input string ep_type,
                                              input string name);

  // Inbound (host -> VIP). Length in bytes of the next message, 0 if none.
  import "DPI-C" function int  pastel_ep_rx_len(input int h);
  // Byte i of the current inbound message.
  import "DPI-C" function byte pastel_ep_rx_byte(input int h, input int i);
  // Discard the current inbound message.
  import "DPI-C" function void pastel_ep_rx_pop(input int h);

  // Outbound (VIP -> host). Build a message byte by byte, then send it.
  import "DPI-C" function void pastel_ep_tx_begin(input int h);
  import "DPI-C" function void pastel_ep_tx_byte(input int h, input byte b);
  import "DPI-C" function void pastel_ep_tx_end(input int h);

endpackage
