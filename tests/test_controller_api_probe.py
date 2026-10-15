"""Regression tests for api_probe -- PR #20 review round 1.

Two review cases reproduced with stub FL APIs (no FL required):

1. op=step must budget the COMPLETE serialized response, including the
   ASCII-escaped Unicode caption/hint and any exception strings (review stub
   measured 964 B against the 600 B budget: each Tamil char escapes to 6 B);
2. op=jump must restore the pattern ACTUALLY selected at entry, in a finally,
   independent of a caller-supplied `back` (review: back=9 left pattern 9
   selected when the project started on 7).

The controller imports FL modules that exist only inside FL, so it is loaded
once against stub modules (sys.modules is saved/restored around the import;
the module keeps direct references to the stubs afterwards).
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

_CONTROLLER_PATH = (Path(__file__).resolve().parents[1] / "fl_controller"
                    / "FLStudioMCP" / "device_FLStudioMCP.py")
_STUB_NAMES = ("channels", "device", "general", "midi", "mixer", "patterns",
               "playlist", "plugins", "transport", "ui", "arrangement", "utils")
_BUDGET = 600  # _LIST_BUDGET in the controller


def _load_controller():
    """Import device_FLStudioMCP against stub FL modules -> (module, stubs)."""
    saved = {n: sys.modules.get(n) for n in _STUB_NAMES}
    stubs = {}
    for name in _STUB_NAMES:
        stubs[name] = types.ModuleType(name)
        sys.modules[name] = stubs[name]
    stubs["midi"].widPianoRoll = 42
    try:
        spec = importlib.util.spec_from_file_location(
            "flstudio_mcp_controller_under_test", _CONTROLLER_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        for name, prev in saved.items():
            if prev is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = prev
    return mod, stubs


_CONTROLLER, STUBS = _load_controller()
_probe = _CONTROLLER._h_api_probe


def _bytes(env) -> int:
    """Size of the compact JSON the controller would return."""
    return len(json.dumps(env, separators=(",", ":")))


@pytest.fixture
def ui_stub():
    """Reviewer's step repro inputs: Tamil caption x40, hint x60, zeros."""
    ui = STUBS["ui"]
    ui.getStepEditMode = lambda: 0
    ui.getFocused = lambda _wid: False
    ui.getFocusedFormID = lambda: 1
    ui.getFocusedFormCaption = lambda: "த" * 40
    ui.getHintMsg = lambda: "த" * 60
    ui.getVisible = lambda _wid: False
    return ui


@pytest.fixture
def channels_zeros():
    ch = STUBS["channels"]
    ch.getGridBit = lambda _c, _s: False
    ch.isGridBitAssigned = lambda _c, _s: False
    return ch


@pytest.fixture
def patterns_stub():
    """Stateful patterns stub: current pattern + every jump call recorded."""
    pat = STUBS["patterns"]
    state = {"current": 7, "calls": [], "fail_on": None}

    def jump(idx):
        if state["fail_on"] is not None and idx == state["fail_on"]:
            raise RuntimeError(f"boom {idx}")
        state["calls"].append(idx)
        state["current"] = idx

    def number():
        return state["current"]

    pat.jumpToPattern = jump
    pat.patternNumber = number
    return state


# ---------------------------------------------------------------------------
# op=step: complete-envelope budget (review case 1)
# ---------------------------------------------------------------------------

def test_step_unicode_repro_fits_budget(ui_stub, channels_zeros):
    out = _probe({"op": "step"})
    assert _bytes(out) <= _BUDGET
    # the fixed block survives, just trimmed to fit after JSON escaping
    assert out["focused_caption"]
    assert out["hint"]
    assert out["focused_form_id"] == 1
    assert out["step_edit_mode"] == 0
    assert len(out["grid_bits"]) >= 1


def test_step_pagination_terminates_under_budget(ui_stub, channels_zeros):
    start, steps, pages, seen = 0, 64, 0, 0
    while True:
        out = _probe({"op": "step", "steps": steps, "start": start})
        assert _bytes(out) <= _BUDGET
        seen += len(out["grid_bits"])
        pages += 1
        # Wide Unicode caption leaves room for very few bits per page, so
        # pages can be small -- what matters is forward progress, not a
        # fixed page count.
        assert pages <= 40, "pagination made no progress (client would hang)"
        if out["next_start"] is None:
            break
        assert out["next_start"] > start, "page returned no bits and no advance"
        start = out["next_start"]
    assert seen == steps


def test_step_exception_strings_are_budgeted(ui_stub, channels_zeros):
    def raise_hint():
        raise RuntimeError("த" * 80)

    def raise_bit(_ch, step):
        if step == 2:
            raise ValueError("bad bit " + "த" * 60)
        return False

    ui_stub.getHintMsg = raise_hint
    channels_zeros.getGridBit = raise_bit

    out = _probe({"op": "step", "steps": 16, "start": 0})
    assert _bytes(out) <= _BUDGET
    assert out["hint"].startswith("err:")      # prefix survives any truncation
    assert out["grid_error"].startswith("err:")
    assert out["next_start"] is None          # error ends the page
    assert len(out["grid_bits"]) == 2         # bits read before the failure


# ---------------------------------------------------------------------------
# op=jump: restore actual selection in finally (review case 2)
# ---------------------------------------------------------------------------

def test_jump_ignores_caller_back_restores_initial(patterns_stub):
    out = _probe({"op": "jump", "index": 3, "back": 9})
    assert out["start"] == 7                  # actual selection, not back
    assert out["after_jump_number"] == 3
    assert out["restored_number"] == 7
    assert 9 not in patterns_stub["calls"]    # caller `back` never used
    assert patterns_stub["current"] == 7      # project left where it was
    assert "back_ignored" in out


def test_jump_restores_in_finally_when_jump_raises(patterns_stub):
    patterns_stub["fail_on"] = 3
    out = _probe({"op": "jump", "index": 3})
    assert "jump_error" in out
    assert out["restored_number"] == 7        # restore ran despite the raise
    assert patterns_stub["current"] == 7
    assert "after_jump_number" not in out


def test_jump_rejects_negative_index_without_touching_fl(patterns_stub):
    out = _probe({"op": "jump", "index": -5})
    assert "jump_error" in out
    assert "rejected index -5" in out["jump_error"]
    assert patterns_stub["calls"] == []       # never handed FL a bad index
    assert patterns_stub["current"] == 7
