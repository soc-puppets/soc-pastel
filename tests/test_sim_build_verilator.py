"""Real Verilator builds: errors in the build log fail it, warnings are kept.

Builds tiny testbenches (with PASTEL's own HDL and C++, as every model has),
so it needs Verilator but no device pack. Skipped without Verilator.
"""

import os
import shutil

import pytest

from soc_pastel.core import ConfigurationError
from soc_pastel.sim.sources import SimSources
from soc_pastel.sim.verilator import VerilatorImage

pytestmark = pytest.mark.skipif(shutil.which("verilator") is None, reason="needs Verilator")


def image(tmp_path, name: str, text: str) -> VerilatorImage:
    src = tmp_path / f"{name}.sv"
    src.write_text(text)
    sources = SimSources(top=name, args=(str(src),))
    return VerilatorImage(sources, tmp_path / "work", "verilator", os.cpu_count() or 2)


def test_a_syntax_error_fails_the_build_and_leaves_no_model(tmp_path):
    img = image(
        tmp_path, "broken_tb", 'module broken_tb;\n  initial begin $display("x") end\nendmodule\n'
    )
    with pytest.raises(ConfigurationError) as e:
        img.build()
    message = str(e.value)
    assert "%Error: " in message and "syntax error" in message  # Verilator's own message
    assert str(img.build_log) in message
    assert not img.is_built()


def test_build_warnings_are_kept_with_the_model(tmp_path):
    img = image(
        tmp_path,
        "warn_tb",
        'module warn_tb;\n  if (1) begin : g\n    $warning("a build-time warning");\n  end\n'
        "  initial $finish;\nendmodule\n",
    )
    img.build()
    assert img.is_built()
    assert any("%Warning-USERWARN" in w and "a build-time warning" in w for w in img.warnings)
    log = img.build_log.read_text()
    assert log.startswith("$ ")  # the command, echoed on one line
