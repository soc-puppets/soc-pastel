"""A stand-in for SEGGER's J-Link Commander (JLinkExe), for testing the jlink driver.

Answers the commands the driver sends, in Commander's formats, with a fake CPU
and memory. FAKE_JLINK_VTREF sets the reported target voltage, and
FAKE_JLINK_NO_PROBE makes it start as the real one does with no J-Link attached.
"""

import os
import re
import sys

PROMPT = "J-Link>"
mem: dict[int, int] = {}  # byte address -> byte
state = {"halted": False, "pc": 0, "regs": {}, "bps": {}, "next_bp": 1}
RESET_VECTOR = 0x8000_0000


def word(addr):
    return sum(mem.get(addr + i, 0) << (8 * i) for i in range(4))


def put_word(addr, value):
    for i in range(4):
        mem[addr + i] = (value >> (8 * i)) & 0xFF


def reply(text=""):
    sys.stdout.write(text + ("\n" if text else "") + PROMPT)
    sys.stdout.flush()


def handle(line):
    parts = line.split()
    cmd, args = parts[0].lower(), parts[1:]
    num = lambda s: int(s, 16)  # noqa: E731 - Commander takes hex
    if cmd in ("device", "if", "speed", "jtagconf"):
        return reply(f"{parts[0]} set.")
    if cmd == "connect":
        return reply("Found RISC-V core.\nConnected.")
    if cmd == "showhwstatus":
        return reply(f"VTref={os.environ.get('FAKE_JLINK_VTREF', '3.300')}V\nITarget=0mA")
    if cmd == "ishalted":
        return reply("CPU is halted." if state["halted"] else "CPU is not halted.")
    if cmd == "r":
        state.update(halted=True, pc=RESET_VECTOR)
        return reply("Reset delay: 0 ms\nReset type NORMAL: Resets core & peripherals.")
    if cmd in ("clrreset", "setreset"):
        return reply("RESET pin changed.")
    if cmd == "h":
        state["halted"] = True
        return reply(f"PC = {state['pc']:08X}")
    if cmd == "g":
        state["halted"] = False
        return reply()
    if cmd == "s":
        state["pc"] += 4
        return reply(f"{state['pc']:08X}:  13 00 00 00  NOP")
    if cmd == "setpc":
        state["pc"] = num(args[0])
        return reply()
    if cmd == "rreg":
        name = args[0].upper()
        value = state["pc"] if name == "PC" else state["regs"].get(name)
        if value is None:
            return reply(f"****** Error: Register {args[0]} not found.")
        return reply(f"{name} = 0x{value:08X}")
    if cmd == "wreg":
        state["regs"][args[0].upper()] = num(args[1])
        return reply()
    if cmd == "mem32":
        addr, count = num(args[0]), num(args[1])
        if addr >= 0xF000_0000:
            return reply("Could not read memory.")
        lines = []
        for row in range(0, count, 4):
            words = " ".join(f"{word(addr + 4 * i):08X}" for i in range(row, min(row + 4, count)))
            lines.append(f"{addr + 4 * row:08X} = {words}")
        return reply("\n".join(lines))
    if cmd == "write4":
        addr = num(args[0])
        for i, value in enumerate(args[1:]):
            put_word(addr + 4 * i, num(value))
        return reply()
    if cmd in ("savebin", "loadbin"):
        path = re.search(r'"([^"]+)"', line).group(1)
        rest = line.split('"')[2].split()
        addr = num(rest[0])
        if cmd == "savebin":
            length = num(rest[1])
            with open(path, "wb") as f:
                f.write(bytes(mem.get(addr + i, 0) for i in range(length)))
            return reply(f"Reading {length} bytes from addr 0x{addr:08X} into file...O.K.")
        data = open(path, "rb").read()
        for i, b in enumerate(data):
            mem[addr + i] = b
        return reply("Downloading file... O.K.")
    if cmd == "setbp":
        handle_no = state["next_bp"]
        state["next_bp"] += 1
        state["bps"][handle_no] = num(args[0])
        return reply(f"Breakpoint set @ addr 0x{num(args[0]):08X} (handle = 0x{handle_no:08X})")
    if cmd == "clrbp":
        if num(args[0]) not in state["bps"]:
            return reply("****** Error: Breakpoint not found.")
        del state["bps"][num(args[0])]
        return reply()
    return reply(f"Syntax error: unknown command {parts[0]}")


def main():
    banner = "SEGGER J-Link Commander V8.98 (fake)\n"
    if os.environ.get("FAKE_JLINK_NO_PROBE"):  # as the real one says with nothing plugged in
        banner += "Connecting to J-Link via USB...FAILED: Cannot connect to J-Link.\n"
    sys.stdout.write(banner + PROMPT)
    sys.stdout.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            reply()
        elif line.lower() in ("exit", "q", "qc"):
            return
        else:
            handle(line)


if __name__ == "__main__":
    main()
