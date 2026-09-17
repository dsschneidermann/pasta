"""Unit tests for the pure FSM evaluator (src.fsm)."""

import pytest

from statemachine import StateMachine
from statemachine import registry as sm_registry

from src import fsm
from src.errors import IllegalCommandError
from src.pagetypes.core.commands import CommandSpec, transition_cmd
from src.pagetypes.core.fields import SectionSpec, _list, _prose
from src.pagetypes.core.pagetype import PageType, element_fsm_sites
from src.pagetypes.core.specs import ElementFSMSpec, FSMSpec
from src.pagetypes._registry import get_page_type, registered_pagetypes

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


def test_machine_class_raises_for_a_spec_no_page_type_declares():
    # Nothing builds a machine on demand any more, so an unowned spec is a programming error
    # rather than a silent per-call rebuild.
    orphaned = FSMSpec(name="Unowned", initial="draft", states=("draft", "open"),
                       transitions=(("open", "draft", "open", "agent"),))
    with pytest.raises(LookupError, match="Unowned"):
        fsm.machine_class(orphaned)


def test_element_fsm_spec_starts_unbuilt_and_keeps_its_identity():
    made = [ElementFSMSpec(name="XItem", initial="todo", states=("todo", "done"),
                           transitions=(("markDone", "todo", "done", "agent"),))
            for _ in range(2)]
    assert made[0].machine is None and made[0].machine_error is None
    # The two fields are declared compare=False, so a built spec and an unbuilt one with the
    # same declaration must not diverge.
    assert made[0] == made[1]
    assert len({made[0], made[1]}) == 1


# --- Page types own their status machine -------------------------------------
def _ad_hoc_type(tag: str, name: str, states: tuple[str, ...], *commands: CommandSpec) -> PageType:
    """An ad-hoc page type whose FSM table derives from the transition commands it is given."""
    return PageType(
        tag=tag, name=name, description="ad-hoc",
        sections=(SectionSpec("body", "Body", (_prose("body"),)),),
        commands=commands,
        fsm=FSMSpec(name=name, initial=states[0], states=states))


def test_a_page_type_builds_its_status_machine_as_it_is_constructed():
    built = _ad_hoc_type("xtest-owned", "XOwned", ("draft", "done"),
                         transition_cmd("finish", "draft → done"))
    assert built.fsm.machine_error is None
    assert issubclass(built.fsm.machine, StateMachine)
    # python-statemachine registers every class it creates, so an entry under this type's own
    # name is the library's record that the build happened.
    assert sm_registry._REGISTRY[f"src.fsm.{built.fsm.name}"] is built.fsm.machine


def test_a_machine_that_cannot_be_built_is_kept_as_an_error_rather_than_raised():
    # `orphan` is unreachable from `draft`, which python-statemachine rejects. Constructing the
    # page type must not raise; the error is held for the validator.
    built = _ad_hoc_type("xtest-orphan", "XOrphan", ("draft", "done", "orphan"),
                         transition_cmd("finish", "draft → done"))
    assert built.fsm.machine is None
    assert "orphan" in str(built.fsm.machine_error)


def _ad_hoc_list_type(tag: str, name: str, element_fsm: ElementFSMSpec) -> PageType:
    """An ad-hoc page type whose one list field carries `element_fsm`."""
    return PageType(
        tag=tag, name=name, description="ad-hoc",
        sections=(SectionSpec("items", "Items", (
            _list("items", element_fields=("text", "status"), element_fsm=element_fsm,
                  description="items"),)),),
        commands=(),
        fsm=FSMSpec(name=name, initial="active", states=("active",)))


def test_every_production_element_machine_is_built(production_mode):
    """Nothing here builds a machine, so finding one on every element spec is evidence they are
    built with the declaration that carries them."""
    for tag, page_type in registered_pagetypes().items():
        for section, field_key, element_fsm in element_fsm_sites(page_type):
            assert element_fsm.machine is not None, (tag, section, field_key)
            assert element_fsm.machine_error is None, (tag, section, field_key)


def test_an_element_spec_declared_twice_is_built_once():
    # One spec object on two page types: it carries one machine, so the second declaration must
    # leave the first's class alone rather than replace it.
    shared = ElementFSMSpec(name="XShared", initial="todo", states=("todo", "done"),
                            transitions=(("markDone", "todo", "done", "agent"),))
    _ad_hoc_list_type("xtest-shared-a", "XSharedA", shared)
    first = shared.machine
    assert first is not None
    _ad_hoc_list_type("xtest-shared-b", "XSharedB", shared)
    assert shared.machine is first


def test_an_element_machine_that_cannot_be_built_is_kept_as_an_error():
    # `orphan` is unreachable from `todo`, which python-statemachine rejects. Constructing the
    # page type must not raise; the error is held for the validator.
    orphan = ElementFSMSpec(name="XOrphanItem", initial="todo",
                            states=("todo", "done", "orphan"),
                            transitions=(("markDone", "todo", "done", "agent"),))
    _ad_hoc_list_type("xtest-orphan-item", "XOrphanItem", orphan)
    assert orphan.machine is None
    assert "orphan" in str(orphan.machine_error)


def test_every_production_page_type_holds_its_machine(production_mode):
    """Nothing in this test builds a machine, so finding one on every type is evidence they were
    built with the declarations rather than on first use."""
    for tag, page_type in registered_pagetypes().items():
        assert page_type.fsm.machine is not None, tag
        assert page_type.fsm.machine_error is None, tag


def test_production_status_evaluation_matches_the_declared_transition_table(production_mode):
    """Legality and the status reached are read off the machine; both must still agree with the
    table the page type derived, for every (page type, status) pair the registry declares."""
    for tag, page_type in registered_pagetypes().items():
        spec = page_type.fsm
        declared = {event for event, _source, _dest, _agency in spec.transitions}
        for status in spec.states:
            legal = {event for event, source, _dest, _agency in spec.transitions if source == status}
            assert fsm.allowed_events(spec, status) == legal, (tag, status)
            for event, source, dest, _agency in spec.transitions:
                if source == status:
                    assert fsm.fire(spec, status, event) == dest, (tag, status, event)
            for event in declared - legal:
                with pytest.raises(IllegalCommandError):
                    fsm.fire(spec, status, event)
