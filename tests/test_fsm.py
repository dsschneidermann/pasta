"""Unit tests for the pure FSM evaluator (src.fsm)."""

import pytest

from src import fsm
from src.errors import IllegalCommandError
from src.pagetypes.core.specs import FSMSpec
from src.pagetypes._registry import get_page_type

# Two hand-authored fixtures cover the FSM engine's cases: `test-child` is a simple 2-state cyclic
# machine (draft <-> ready); `test-flow` is a 3-state cycle whose STATE `open` and EVENT `open`
# deliberately share a name (the engine must keep those distinct).
CHILD = get_page_type("test-child").fsm
FLOW = get_page_type("test-flow").fsm


def test_two_state_cycle_allowed_and_fire():
    assert fsm.allowed_events(CHILD, "draft") == {"markReady"}
    assert fsm.fire(CHILD, "draft", "markReady") == "ready"
    assert fsm.allowed_events(CHILD, "ready") == {"reopen"}
    assert fsm.fire(CHILD, "ready", "reopen") == "draft"


def test_illegal_transition_raises():
    with pytest.raises(IllegalCommandError):
        fsm.fire(CHILD, "draft", "reopen")   # reopen is legal only from ready


def test_shared_state_and_event_name_full_cycle():
    # The event `open` and the state `open` share a name - this must still work.
    assert fsm.allowed_events(FLOW, "draft") == {"open"}
    assert fsm.fire(FLOW, "draft", "open") == "open"
    assert fsm.allowed_events(FLOW, "open") == {"close"}
    assert fsm.fire(FLOW, "open", "close") == "closed"
    assert fsm.allowed_events(FLOW, "closed") == {"reopen"}
    assert fsm.fire(FLOW, "closed", "reopen") == "open"


def test_flow_illegal_transition_raises():
    with pytest.raises(IllegalCommandError):
        fsm.fire(FLOW, "closed", "open")  # can only reopen from closed


def test_is_valid_status():
    assert fsm.is_valid_status(CHILD, "draft")
    assert not fsm.is_valid_status(CHILD, "nonexistent")


def test_status_guidance_keeps_fsmspec_hashable_for_the_machine_cache():
    # A dict field here would raise "unhashable type" at the _machine_class cache.
    made = [FSMSpec(name="Guided", initial="draft", states=("draft", "open"),
                    transitions=(("open", "draft", "open", "agent"),),
                    status_guidance=(("open", "do the open work"),))
            for _ in range(2)]
    assert fsm.machine_class(made[0]) is fsm.machine_class(made[1])
