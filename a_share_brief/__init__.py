"""Compatibility imports for the renamed :mod:`ashare` package."""

from importlib import import_module
import sys

from ashare import __version__

for _name in ("client", "compose", "fetch", "format", "models", "narrative", "parse", "rank", "render", "social"):
    _module = import_module(f"ashare.{_name}")
    sys.modules[f"{__name__}.{_name}"] = _module
    globals()[_name] = _module
