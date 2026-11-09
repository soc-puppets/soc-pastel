"""On-chip debug implementations.

The contract is :class:`soc_pastel.core.OnChipDebug`; it lives in the core
because the framework itself depends on it.
"""

from soc_pastel.core.on_chip_debug import OnChipDebug, TargetState

__all__ = ["OnChipDebug", "TargetState"]
