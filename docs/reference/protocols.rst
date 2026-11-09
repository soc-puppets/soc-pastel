Protocols
=========

All multi-byte integers are little-endian.

Endpoint protocol (host and simulator)
--------------------------------------

The test process listens; the simulator connects to the address in its
``+pastel_host`` plusarg. Each frame is:

.. list-table::
   :header-rows: 1

   * - Field
     - Type
     - Meaning
   * - length
     - u32
     - Bytes that follow: 3 + payload length
   * - endpoint
     - u16
     - Endpoint id; ``0xFFFF`` is the control endpoint
   * - kind
     - u8
     - See below
   * - payload
     - bytes
     - 

.. list-table::
   :header-rows: 1

   * - Kind
     - Value
     - Direction
     - Payload
   * - HELLO
     - 0
     - sim → host, control
     - UTF-8 JSON, below
   * - DATA
     - 1
     - both
     - Defined by the endpoint type
   * - ERROR
     - 2
     - both, control
     - UTF-8 message
   * - BYE
     - 3
     - both, control
     - Empty; ends the session
   * - SYNC
     - 4
     - host → sim
     - u32 seq
   * - SYNC_ACK
     - 5
     - sim → host
     - u32 seq; sent once the VIP has consumed every message sent before the SYNC

The simulator's first frame is HELLO:

.. code-block:: json

   {"version": 1, "token": "...",
    "endpoints": [{"id": 0, "name": "ram", "type": "pastel.backdoor", "path": "tb.u_backdoor"}],
    "info": {"jtag_port": 41234}}

The host checks the version and token, and checks the endpoints against the
device descriptor.

``pastel.platform``
~~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1

   * - Message
     - Direction
     - Bytes
   * - FINISH
     - host → sim
     - ``01``
   * - GET_TIME
     - host → sim
     - ``02``
   * - TIME
     - sim → host
     - ``82``, u64 nanoseconds

``pastel.backdoor``
~~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1

   * - Message
     - Direction
     - Bytes
   * - READ
     - host → sim
     - ``01``, u32 seq, u32 offset, u32 length
   * - WRITE
     - host → sim
     - ``02``, u32 seq, u32 offset, u32 length, data
   * - REPLY
     - sim → host
     - op \| ``80``, u32 seq, u8 status (0 ok, 1 out of range), data for READ

Offsets are bytes from the region base.

VIP DPI functions
-----------------

Declared in ``pastel_ep_pkg.sv``. A VIP opens its endpoint at time 0 and polls
on its own clock; nothing here consumes simulated time.

.. list-table::
   :header-rows: 1

   * - Function
     - Purpose
   * - ``pastel_ep_open(path, type, name)``
     - Register an endpoint; returns a handle
   * - ``pastel_ep_rx_len(h)``
     - Length of the next inbound message, 0 if none
   * - ``pastel_ep_rx_byte(h, i)``
     - Byte ``i`` of that message
   * - ``pastel_ep_rx_pop(h)``
     - Discard it
   * - ``pastel_ep_tx_begin(h)``, ``pastel_ep_tx_byte(h, b)``, ``pastel_ep_tx_end(h)``
     - Build and send a message

Mailbox (firmware and host)
---------------------------

Carried on RTT channel 1. Each message is u8 id, u8 length, payload.

.. list-table::
   :header-rows: 1

   * - Message
     - Id
     - Direction
     - Payload
   * - HELLO
     - ``01``
     - target → host
     - u8 protocol version (1)
   * - CHECKPOINT
     - ``02``
     - target → host
     - u32 id
   * - DATA
     - ``03``
     - target → host
     - Opaque
   * - FINISH
     - ``04``
     - target → host
     - i32 result
   * - FAULT
     - ``05``
     - target → host
     - u32 mcause, u32 mepc, u32 mtval
   * - HOST_DATA
     - ``81``
     - host → target
     - Opaque
   * - HOST_ACK
     - ``82``
     - host → target
     - u32 checkpoint id

RTT control block
-----------------

SEGGER-compatible, at the symbol ``_SEGGER_RTT``, so standard RTT tools can read
it: a 16-byte id (``SEGGER RTT``), the number of up and down buffers (i32
each), then 24-byte buffer descriptors (name, buffer, size, write offset, read
offset, flags). PASTEL uses two of each: channel 0 for text, channel 1 for
the mailbox. The firmware writes the id last, so a host never sees a
half-initialised block.
