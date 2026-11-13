"""PASTEL's own HDL avoids legal SystemVerilog that some simulators reject.

Covers the HDL in this repository (PASTEL itself and the packs maintained
here), so PASTEL works with whichever simulator a user picks. The list of
constructs is in CONTRIBUTING.md.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HDL = sorted(
    p
    for d in (ROOT / "src", ROOT / "packages")
    for p in d.rglob("*")
    if p.suffix in {".sv", ".svh", ".v", ".vh"}
)

_COMMENTS_AND_STRINGS = re.compile(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"', re.S)
# "(name = ...", "(name += ...", ... but not "==", and not a for-loop's initialiser.
_ASSIGN_IN_EXPR = re.compile(
    r"(?<!\bfor)\s*\(\s*\(?\s*[A-Za-z_][\w.\[\]]*\s*(?:[-+*/%&|^]|<<|>>)?=(?!=)"
)


def assignments_in_expressions(text: str) -> list[str]:
    code = _COMMENTS_AND_STRINGS.sub(lambda m: " " * len(m.group()), text)
    found = []
    for m in _ASSIGN_IN_EXPR.finditer(code):
        line_start = code.rfind("\n", 0, m.start()) + 1
        line = text[line_start : text.find("\n", m.start())].strip()
        if not re.match(r"(for|foreach)\b", code[line_start:].lstrip()):
            found.append(line)
    return found


def test_the_check_finds_what_xcelium_rejects():
    assert assignments_in_expressions("while ((pbd_n = pastel_ep_rx_len(h)) >= 13) begin")
    assert assignments_in_expressions("if ((x += 1) > 3) y = 0;")
    for ok in (
        "for (int i = 0; i < 4; i++) send(i);",
        "if (a == b) c = d;",
        "while (pbd_n >= 13) begin",
        'void\'($value$plusargs("pastel_host=%s", spec));',
        "// while ((n = f()) >= 13)",
        "pastel_server #(.POLL_CYCLES(8)) u (.clk(clk));",
    ):
        assert not assignments_in_expressions(ok), ok


@pytest.mark.parametrize("path", HDL, ids=lambda p: str(p.relative_to(ROOT)))
def test_no_assignments_inside_expressions(path):
    assert assignments_in_expressions(path.read_text()) == []
