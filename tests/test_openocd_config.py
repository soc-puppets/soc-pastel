from soc_pastel.interfaces.on_chip_debug.openocd import OpenOcdConfig, fragment_path


def test_render_order():
    cfg = OpenOcdConfig(
        transport=fragment_path("transport", "remote_bitbang").read_text(),
        chain="set _TARGETNAME riscv.cpu",
        cpu=fragment_path("cpu", "veer").read_text(),
    ).render()
    assert cfg.index("remote_bitbang") < cfg.index("_TARGETNAME riscv.cpu") < cfg.index("1988")
