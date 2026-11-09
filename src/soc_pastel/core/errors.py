"""Exception hierarchy.

Every exception PASTEL raises derives from :class:`PastelError`, so test code
and plugins can tell framework failures apart from failures of the device
under test.
"""


class PastelError(Exception):
    """Base class for all PASTEL errors."""


class ConfigurationError(PastelError):
    """A descriptor, option or installed pack is inconsistent or missing."""


class CapabilityError(PastelError):
    """A test or library asked for a capability the current harness lacks.

    Tests should declare what they need with ``@pytest.mark.requires(...)`` so
    that they are skipped instead; this error means something asked at run time
    without declaring it.
    """


class TargetError(PastelError):
    """The target behaved unexpectedly (failed to halt, bad memory access, fault)."""


class ProtocolError(PastelError):
    """A host/target or host/simulator protocol was violated (bad frame, version)."""


class PastelTimeout(PastelError):
    """A PASTEL timeout expired.

    Named to avoid shadowing the built-in :class:`TimeoutError`.
    """
