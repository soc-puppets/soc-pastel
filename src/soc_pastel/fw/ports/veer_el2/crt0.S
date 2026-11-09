// SPDX-License-Identifier: MIT
//
// Start-up code for VeeR EL2. PASTEL loads code and sets the PC directly,
// bypassing any boot ROM, so memory attributes (MRAC) are programmed here.
// PASTEL_VEER_MRAC comes from the generated memory_map.h.

#define CSR_MRAC 0x7c0

  .section .text.init, "ax"
  .globl _start
_start:
  li    t0, PASTEL_VEER_MRAC
  csrw  CSR_MRAC, t0
  fence.i

  la    sp, __stack_top
  .option push
  .option norelax
  la    gp, __global_pointer$
  .option pop

  la    t0, pastel_trap_entry
  csrw  mtvec, t0

  call  pastel_port_early_init

  la    a0, __bss_start
  la    a1, __bss_end
1:
  bgeu  a0, a1, 2f
  sw    zero, 0(a0)
  addi  a0, a0, 4
  j     1b
2:
  call  pastel_main
3:
  j     3b

  .align 4
pastel_trap_entry:
  csrr  a0, mcause
  csrr  a1, mepc
  csrr  a2, mtval
  j     pastel_fault
