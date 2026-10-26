"""Load the real controller with only FL's in-process API modules substituted."""
import importlib.util
from pathlib import Path
import sys
import types

import pytest


@pytest.fixture
def controller(monkeypatch):
    for name in ("channels", "device", "general", "midi", "mixer", "patterns",
                 "playlist", "plugins", "transport", "ui", "arrangement", "utils"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    path = (Path(__file__).resolve().parents[1] / "fl_controller"
            / "FLStudioMCP" / "device_FLStudioMCP.py")
    spec = importlib.util.spec_from_file_location("fl_controller_regression", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
