"""The ``pastel`` command-line tool."""

from __future__ import annotations

import argparse
import os
import sys

from . import FW_ROOT
from .core import registry
from .core.device import Access
from .core.errors import ConfigurationError


def _cores_path(_: argparse.Namespace) -> None:
    print(os.pathsep.join(str(p) for p in registry.hdl_roots().values()))


def _fw_path(_: argparse.Namespace) -> None:
    print(FW_ROOT)


def _list(_: argparse.Namespace) -> None:
    print("devices:  ", ", ".join(registry.device_names()) or "(none)")
    print("platforms:", ", ".join(registry.platform_names()) or "(none)")


def _describe(args: argparse.Namespace) -> None:
    dev = registry.device(args.device)
    print(
        f"{dev.name}: {dev.cpu.family} ({dev.cpu.isa}), reset vector {dev.cpu.reset_vector:#010x}"
    )
    for v in dev.variants.values():
        if args.variant and v.name != args.variant:
            continue
        default = " (default)" if v.name == dev.default_variant else ""
        print(f"\nvariant {v.name}{default}  platforms: {', '.join(sorted(v.platforms)) or '-'}")
        for r in sorted(v.regions, key=lambda r: r.base):
            flags = "".join(c if Access[c] in r.access else "-" for c in "RWX")
            extra = " side-effects" if r.side_effects else ""
            bd = f" backdoor={r.backdoor}" if r.backdoor else ""
            print(
                f"  {r.base:#010x}-{r.end - 1:#010x} {flags} {r.kind.value:4} {r.name}{extra}{bd}"
            )
        for c in v.code_locations:
            print(f"  code location {c.name}: text={c.text} data={c.data} rtt={c.rtt_region}")
    print("\ninterfaces:")
    for name, b in dev.interfaces.items():
        sel = ", ".join(f"{k}->{cfg['impl']}" for k, cfg in b.impls.items())
        print(f"  {name} ({b.type}): {sel}")


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``pastel`` command."""
    parser = argparse.ArgumentParser(prog="pastel", description=__doc__)
    sub = parser.add_subparsers(required=True)
    sub.add_parser("cores-path", help="print FuseSoC core roots of installed packs").set_defaults(
        func=_cores_path
    )
    sub.add_parser("fw-path", help="print the firmware runtime directory").set_defaults(
        func=_fw_path
    )
    sub.add_parser("list", help="list installed devices and platforms").set_defaults(func=_list)
    d = sub.add_parser("describe", help="show a device descriptor")
    d.add_argument("device")
    d.add_argument("--variant")
    d.set_defaults(func=_describe)
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except ConfigurationError as e:
        print(f"pastel: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
