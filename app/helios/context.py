"""
PyHelios singleton management.

Holds the three PyHelios singletons (_context, _wpt, _plantarch) and exposes
typed accessors. All other modules import from here — never instantiate directly.
"""
# TODO: implement in Step 3
# Move PyHelios path setup and import logic from current main.py lines 44–138
# Move _context, _wpt, _plantarch globals from main.py lines 141–162

PYHELIOS_AVAILABLE: bool = False
PLANTARCH_AVAILABLE: bool = False


def get_context():
    """Return the active Context, raising 503 if PyHelios is unavailable."""
    raise NotImplementedError("Implement in Step 3")


def get_wpt():
    """Return the active WeberPennTree instance."""
    raise NotImplementedError("Implement in Step 3")


def get_plantarch():
    """Return the active PlantArchitecture instance."""
    raise NotImplementedError("Implement in Step 3")


def reset_context() -> None:
    """Destroy all singletons — called by project create/load."""
    raise NotImplementedError("Implement in Step 3")


def init_pyhelios() -> None:
    """Called at startup to validate PyHelios availability."""
    raise NotImplementedError("Implement in Step 3")
