// SPDX-License-Identifier: MIT
//
// Start-up code for VeeR EL2. PASTEL loads code and sets the PC directly,
// bypassing any boot ROM, so memory attributes (MRAC) are programmed here.
// PASTEL_VEER_MRAC comes from the generated memory_map.h.
//
// Memory with ECC (VeeR's DCCM, when configured with it) must be written a
// whole word at a time before anything stores less than a word into it: a
// byte or halfword store reads the word to merge into, and a word never
// written has no valid check bits (X in a 4-state simulator, an ECC error on
// silicon). So .bss, .stack and .rtt, the memory the runtime writes without
// the loader having written it first, are cleared with word stores here,
// before anything touches the stack.

#define CSR_MRAC 0x7c0

  .section .text.init, "ax"
  .globl _start
_start:
  li    t0, PASTEL_VEER_MRAC
  csrw  CSR_MRAC, t0
  fence.i

  // Trap early: a fault from here on is reported (see pastel_trap_entry).
  la    t0, pastel_trap_entry
  csrw  mtvec, t0

  // Clear .bss, .stack and .rtt with word stores, before using the stack.
  la    a0, __bss_start
  la    a1, __bss_end
  call  clear_words
  la    a0, __stack_bottom
  la    a1, __stack_top
  call  clear_words
  la    a0, __rtt_start
  la    a1, __rtt_end
  call  clear_words

  la    sp, __stack_top
  .option push
  .option norelax
  la    gp, __global_pointer$
  .option pop

  call  pastel_port_early_init
  call  pastel_main
1:
  j     1b

// Store zero to each word in [a0, a1). Uses no stack (a leaf, called before
// sp is set), and only a0 and a1.
clear_words:
  bgeu  a0, a1, 2f
  sw    zero, 0(a0)
  addi  a0, a0, 4
  j     clear_words
2:
  ret

// Any trap is a fault. The first one's mcause, mepc and mtval are kept in
// pastel_first_fault, and pastel_fault reports them. If reporting it faults
// in turn (the memory it uses is bad, say), the handler is entered again: it
// then reports the first fault once more, from a fresh stack, rather than
// building another report on the same stack, which would fault again one
// frame lower, and so on down the stack. A third entry gives up and waits
// for the host to time the test out.
  .align 4
pastel_trap_entry:
  la    t0, pastel_first_fault
  lw    t1, 0(t0)                  // entries so far
  addi  t2, t1, 1
  sw    t2, 0(t0)
  bnez  t1, 3f
  csrr  a0, mcause                 // the first entry: keep the fault
  csrr  a1, mepc
  csrr  a2, mtval
  sw    a0, 4(t0)
  sw    a1, 8(t0)
  sw    a2, 12(t0)
  j     pastel_fault
3:
  li    t2, 1
  bne   t1, t2, 4f                 // entered more than twice: give up
  la    sp, __stack_top            // the second: report the first fault again
  lw    a0, 4(t0)
  lw    a1, 8(t0)
  lw    a2, 12(t0)
  j     pastel_fault
4:
  j     4b

// In .data, so the loader has set it to zero before the firmware starts:
// even a fault before .bss is cleared finds it valid.
  .section .data
  .balign 4
  .globl pastel_first_fault
pastel_first_fault:
  .word 0, 0, 0, 0                 // entries, mcause, mepc, mtval
