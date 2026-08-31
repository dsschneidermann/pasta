"""Pure FSM evaluation via python-statemachine.

For each `FSMSpec` we build one `StateMachine` subclass (cached), then evaluate a
transition on an *ephemeral* instance seeded at the page's current status. The
machine is the single source of truth for legality and for the resulting state.

Design notes (verified against python-statemachine 3.2.0):

- A state's logical id is carried in `State(value=...)`, while the class attribute
  name is `state_<value>`. This lets a state and an event share a name (bug-report's
  `open` state and `open` event) without an attribute clash. The status is read back
  from `configuration_values` (state *values*), never the attribute name.
- The classic `StateMachine` rejects an out-of-state event with `TransitionNotAllowed`,
  which we surface as an `IllegalCommandError`.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from statemachine import Event, State, StateMachine
from statemachine.exceptions import TransitionNotAllowed

from .errors import IllegalCommandError
from .pagetypes.core.specs import ElementFSMSpec, FSMSpec


@lru_cache(maxsize=None)
def _machine_class(fsm: FSMSpec | ElementFSMSpec) -> type[StateMachine]:
    """Build (once) a StateMachine subclass from an FSM spec. Cached per spec."""
    namespace: dict[str, object] = {}
    state_attr = {value: f"state_{value}" for value in fsm.states}

    # A state with no outgoing transition is terminal. python-statemachine's "trap state"
    # validation requires such states to be declared final=True, so we infer it here rather
    # than making every FSMSpec author remember to. (Cyclic FSMs like architecture/bug-report
    # have no such states, so this leaves them unchanged.)
    sources = {source for _event, source, _dest, _agency in fsm.transitions}

    for value in fsm.states:
        namespace[state_attr[value]] = State(
            value, value=value, initial=(value == fsm.initial), final=(value not in sources)
        )

    # Group transitions by event, OR-combining alternatives that share an event. Each event is
    # wrapped in an Event whose name is its id, so diagrams label edges with the exact command
    # name (e.g. "markStale") instead of python-statemachine's title-cased default ("Markstale").
    # The attribute name still fixes the event id, so send()/allowed_events are unaffected.
    by_event: dict[str, object] = {}
    for event, source, dest, _agency in fsm.transitions:
        segment = namespace[state_attr[source]].to(namespace[state_attr[dest]])  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue, reportUnknownVariableType]
        by_event[event] = segment if event not in by_event else by_event[event] | segment
    for event, transitions in by_event.items():
        namespace[event] = Event(transitions, name=event)  # pyright: ignore[reportArgumentType]

    return type(fsm.name, (StateMachine,), namespace)


def _current_value(machine: StateMachine) -> str:
    """The single active state's value (these FSMs are flat, so there is exactly one)."""
    return next(iter(machine.configuration_values))


def machine_class(fsm: FSMSpec | ElementFSMSpec) -> Any:
    """The concrete (cached) StateMachine subclass for an FSM spec.

    A public handle on the built machine - used by ``src.statecharts`` to expose
    one importable class per page type for docs/introspection.
    """
    return _machine_class(fsm)


def allowed_events(fsm: FSMSpec | ElementFSMSpec, current_status: str) -> set[str]:
    """The set of FSM event ids legal from `current_status` (topology only)."""
    machine = _machine_class(fsm)(start_value=current_status)
    return {event.id for event in machine.allowed_events}


def fire(fsm: FSMSpec | ElementFSMSpec, current_status: str, event: str) -> str:
    """Return the status reached by firing `event` from `current_status`.

    Raises `IllegalCommandError` if the event is not legal from that state.
    """
    machine = _machine_class(fsm)(start_value=current_status)
    try:
        machine.send(event)
    except TransitionNotAllowed as exc:
        raise IllegalCommandError(str(exc)) from exc
    return _current_value(machine)


def is_valid_status(fsm: FSMSpec | ElementFSMSpec, status: str) -> bool:
    return status in fsm.states
