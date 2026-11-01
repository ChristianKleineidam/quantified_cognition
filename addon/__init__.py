"""Anki loads this module when the add-on is installed."""

try:
    from aqt import mw
except ImportError:
    mw = None

if mw is not None:
    from .ui import register

    register(__name__)
